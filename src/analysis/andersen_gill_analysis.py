#!/usr/bin/env python3
"""
andersen_gill_analysis.py — Andersen-Gill Cox PH model for recurrent reuse events.

Identifies which dandiset features predict higher reuse rates, accounting for
right-censoring and the time-varying baseline hazard.

Usage:
    python andersen_gill_analysis.py
"""

import argparse
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import requests
from lifelines import CoxPHFitter

ANALYSIS_CUTOFF = datetime(2025, 10, 7)


def fetch_dandiset_metadata():
    """Fetch species, approach, size, subjects via DANDIAdapter."""
    from archives.dandi import DANDIAdapter

    adapter = DANDIAdapter()

    with open("output/all_dandiset_papers.json") as f:
        papers = json.load(f)

    meta = {}
    for r in papers["results"]:
        did = r["dandiset_id"]
        m = adapter.get_metadata(did)
        if m:
            m["dandiset_created"] = r.get("dandiset_created", "")
            meta[did] = m

    return meta


# Import DANDI-specific constants from adapter
from archives.dandi import NLB_DANDISET_IDS, ALLEN_DANDISET_IDS


def get_citation_counts():
    """Get primary paper citation counts and journal from results file."""
    with open("output/all_dandiset_papers.json") as f:
        papers = json.load(f)

    citations = {}
    for r in papers["results"]:
        did = r["dandiset_id"]
        total = 0
        for p in r.get("paper_relations", []):
            c = p.get("citation_count", 0)
            if c:
                total += c
        citations[did] = total

    return citations


def get_journal_impact_factors(dandiset_ids):
    """Look up primary paper journal impact factor (h-index) via OpenAlex.

    Caches results to .journal_hindex_cache.json to avoid re-fetching.
    """
    import time as _time

    cache_path = Path(".journal_hindex_cache.json")
    if cache_path.exists():
        with open(cache_path) as f:
            cached = json.load(f)
    else:
        cached = {}  # dandiset_id -> h_index

    session = requests.Session()
    session.headers.update({"User-Agent": "FindReuse/1.0"})

    with open("output/all_dandiset_papers.json") as f:
        papers = json.load(f)

    # In-memory cache for journal source IDs
    journal_cache = {}
    impact_factors = {}
    need_fetch = []

    for r in papers["results"]:
        did = r["dandiset_id"]
        if did not in dandiset_ids:
            continue
        if did in cached:
            impact_factors[did] = cached[did]
        else:
            need_fetch.append(r)

    if need_fetch:
        print(f"  Fetching h-index for {len(need_fetch)} dandisets...", file=sys.stderr)

    for i, r in enumerate(need_fetch):
        did = r["dandiset_id"]
        for p in r.get("paper_relations", []):
            doi = p.get("doi", "")
            if not doi:
                continue
            try:
                resp = session.get(f"https://api.openalex.org/works/doi:{doi}", timeout=10)
                if resp.status_code == 200:
                    w = resp.json()
                    source = (w.get("primary_location") or {}).get("source") or {}
                    source_id = source.get("id", "")
                    if source_id and source_id not in journal_cache:
                        api_url = source_id.replace("https://openalex.org/", "https://api.openalex.org/sources/")
                        try:
                            resp2 = session.get(api_url, timeout=10)
                            if resp2.status_code == 200:
                                s = resp2.json()
                                journal_cache[source_id] = s.get("summary_stats", {}).get("h_index", 0) or 0
                            else:
                                journal_cache[source_id] = 0
                        except Exception:
                            journal_cache[source_id] = 0
                    impact_factors[did] = journal_cache.get(source_id, 0)
                    break
            except Exception:
                pass
            _time.sleep(0.05)
        if did not in impact_factors:
            impact_factors[did] = 0

        # Periodic save
        if (i + 1) % 50 == 0:
            print(f"  ... {i + 1}/{len(need_fetch)}", file=sys.stderr)
            cached.update(impact_factors)
            with open(cache_path, "w") as f:
                json.dump(cached, f, indent=2)

    # Final save
    cached.update(impact_factors)
    with open(cache_path, "w") as f:
        json.dump(cached, f, indent=2)

    return impact_factors


def build_counting_process_data(meta, citations, impact_factors):
    """Build Andersen-Gill counting process dataframe.

    Each row is a time interval for a dandiset.
    Events are reuse papers published in that interval.
    """
    with open("output/all_classifications.json") as f:
        cls_data = json.load(f)

    # Collect different-lab reuse event times per dandiset
    events_by_ds = {}
    for c in cls_data["classifications"]:
        if c["classification"] != "REUSE" or c.get("same_lab") is not True:
            continue
        # Actually we want different-lab
    events_by_ds = {}
    for c in cls_data["classifications"]:
        if c["classification"] != "REUSE" or c.get("same_lab") is not False:
            continue
        did = c.get("dandiset_id", "")
        date_str = c.get("citing_date") or c.get("cached_at", "")[:10]
        if not date_str or did not in meta:
            continue
        try:
            pub = datetime.strptime(date_str[:10], "%Y-%m-%d")
        except ValueError:
            continue
        created_str = meta[did]["dandiset_created"]
        if not created_str:
            continue
        created = datetime.fromisoformat(created_str.replace("Z", "+00:00")).replace(tzinfo=None)
        if pub <= created or pub > ANALYSIS_CUTOFF:
            continue
        age_months = (pub - created).days / 30.44
        events_by_ds.setdefault(did, []).append(age_months)

    # Build interval data
    # For each dandiset: create intervals between events, with final censoring interval
    rows = []
    for did, m in meta.items():
        created_str = m["dandiset_created"]
        if not created_str:
            continue
        created = datetime.fromisoformat(created_str.replace("Z", "+00:00")).replace(tzinfo=None)
        if created >= ANALYSIS_CUTOFF:
            continue

        obs_months = (ANALYSIS_CUTOFF - created).days / 30.44
        event_times = sorted(events_by_ds.get(did, []))

        # Covariates
        log_citations = np.log10(max(citations.get(did, 0), 1))
        log_size = np.log10(max(m["size_gb"], 0.001))
        log_subjects = np.log10(max(m["n_subjects"], 1))
        is_benchmark = 1 if did in NLB_DANDISET_IDS else 0
        is_allen = 1 if did in ALLEN_DANDISET_IDS else 0
        log_impact_factor = np.log10(max(impact_factors.get(did, 0), 1))

        # Create intervals
        prev_time = 0
        for et in event_times:
            if et > obs_months:
                break
            rows.append({
                "dandiset_id": did,
                "start": prev_time,
                "stop": et,
                "event": 1,
                "species_mouse": 1 if m["species"] == "mouse" else 0,
                "species_human": 1 if m["species"] == "human" else 0,
                "species_nhp": 1 if m["species"] == "nhp" else 0,
                "modality_ephys": 1 if m["modality"] == "ephys" else 0,
                "modality_imaging": 1 if m["modality"] == "imaging" else 0,
                "log_citations": log_citations,
                "log_size": log_size,
                "log_subjects": log_subjects,
                "is_benchmark": is_benchmark,
                "is_allen": is_allen,
                "log_impact_factor": log_impact_factor,
                "is_cc0": m.get("is_cc0", 0),
            })
            prev_time = et

        # Final censoring interval
        if prev_time < obs_months:
            rows.append({
                "dandiset_id": did,
                "start": prev_time,
                "stop": obs_months,
                "event": 0,
                "species_mouse": 1 if m["species"] == "mouse" else 0,
                "species_human": 1 if m["species"] == "human" else 0,
                "species_nhp": 1 if m["species"] == "nhp" else 0,
                "modality_ephys": 1 if m["modality"] == "ephys" else 0,
                "modality_imaging": 1 if m["modality"] == "imaging" else 0,
                "log_citations": log_citations,
                "log_size": log_size,
                "log_subjects": log_subjects,
                "is_benchmark": is_benchmark,
                "is_allen": is_allen,
                "log_impact_factor": log_impact_factor,
                "is_cc0": m.get("is_cc0", 0),
            })

    df = pd.DataFrame(rows)
    # Fix zero-length intervals (simultaneous events)
    df.loc[df["stop"] <= df["start"], "stop"] = df["start"] + 0.01
    return df


def fit_model(df):
    """Fit Andersen-Gill Cox PH model and save results to JSON."""
    covariates = [
        "species_mouse", "species_human", "species_nhp",
        "modality_ephys", "modality_imaging",
        "log_citations", "log_size", "log_subjects",
        "is_benchmark", "is_allen", "log_impact_factor",
    ]

    cph = CoxPHFitter()
    cph.fit(
        df[["start", "stop", "event"] + covariates],
        duration_col="stop",
        event_col="event",
        entry_col="start",
        show_progress=False,
    )

    print(cph.summary[["coef", "exp(coef)", "se(coef)", "z", "p", "exp(coef) lower 95%", "exp(coef) upper 95%"]])
    print(f"\nConcordance: {cph.concordance_index_:.3f}")

    # Save results to JSON for separate plotting
    results = {
        "concordance": cph.concordance_index_,
        "n_intervals": len(df),
        "n_events": int(df["event"].sum()),
        "n_dandisets": int(df["dandiset_id"].nunique()),
        "covariates": {},
    }
    for cov in covariates:
        results["covariates"][cov] = {
            "coef": float(cph.summary.loc[cov, "coef"]),
            "hr": float(cph.summary.loc[cov, "exp(coef)"]),
            "se": float(cph.summary.loc[cov, "se(coef)"]),
            "z": float(cph.summary.loc[cov, "z"]),
            "p": float(cph.summary.loc[cov, "p"]),
            "hr_lower": float(cph.summary.loc[cov, "exp(coef) lower 95%"]),
            "hr_upper": float(cph.summary.loc[cov, "exp(coef) upper 95%"]),
        }

    Path("output").mkdir(exist_ok=True)
    with open("output/andersen_gill_results.json", "w") as f:
        json.dump(results, f, indent=2)
    print("\nSaved output/andersen_gill_results.json")

    return cph


def plot_forest(results_path="output/andersen_gill_results.json"):
    """Generate forest plot from saved model results."""
    with open(results_path) as f:
        results = json.load(f)

    covs = results["covariates"]

    labels = {
        "log_citations": "Primary paper citations\n(per 10x increase)",
        "is_benchmark": "Benchmark dataset\n(NLB)",
        "is_allen": "Allen Institute\ndataset",
        "log_impact_factor": "Journal impact factor\n(per 10x increase in h-index)",
        "species_nhp": "Non-human primate\n(vs other species)",
        "species_human": "Human\n(vs other species)",
        "species_mouse": "Mouse\n(vs other species)",
        "log_subjects": "Number of subjects\n(per 10x increase)",
        "log_size": "Dataset size\n(per 10x increase)",
        "modality_ephys": "Electrophysiology\n(vs other modality)",
        "modality_imaging": "Calcium imaging\n(vs other modality)",
        "is_cc0": "CC-0 license\n(vs CC-BY)",
    }

    # Order by effect size descending
    order = sorted(covs.keys(), key=lambda c: -covs[c]["hr"])

    y_pos = np.arange(len(order))
    hr = np.array([covs[c]["hr"] for c in order])
    ci_lo = np.array([covs[c]["hr_lower"] for c in order])
    ci_hi = np.array([covs[c]["hr_upper"] for c in order])
    pvals = np.array([covs[c]["p"] for c in order])
    names = [labels.get(c, c) for c in order]

    colors = ["#2E7D32" if p < 0.05 and h > 1 else "#E53935" if p < 0.05 and h < 1 else "#9E9E9E"
              for p, h in zip(pvals, hr)]

    fig, ax = plt.subplots(figsize=(8, 6))

    for i in range(len(hr)):
        ax.plot([ci_lo[i], ci_hi[i]], [y_pos[i], y_pos[i]], color=colors[i], linewidth=2.5, solid_capstyle="round")
    ax.scatter(hr, y_pos, color=colors, s=90, zorder=3, edgecolors="white", linewidth=0.5)
    ax.axvline(1.0, color="gray", linestyle="--", linewidth=1, zorder=1)
    ax.set_xscale("log")

    # Add significance stars on right
    for i, (h, p) in enumerate(zip(hr, pvals)):
        sig = "***" if p < 0.001 else "**" if p < 0.01 else "*" if p < 0.05 else ""
        if sig:
            ax.text(ci_hi[i] * 1.1, i, sig, va="center", fontsize=10, fontweight="bold",
                    color="#2E7D32" if h > 1 else "#E53935")

    ax.set_yticks(y_pos)
    ax.set_yticklabels(names, fontsize=9)
    ax.set_xlabel("Hazard Ratio (log scale)", fontsize=11)
    ax.set_title("Predictors of Different-Lab Reuse\nAndersen-Gill Cox Proportional Hazards",
                 fontsize=11, fontweight="bold")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.set_xticks([0.1, 0.2, 0.5, 1, 2, 5, 10])
    ax.set_xticklabels(["0.1", "0.2", "0.5", "1", "2", "5", "10"])
    ax.grid(axis="x", alpha=0.2, which="both")

    # Shade regions
    ax.axvspan(ax.get_xlim()[0], 1.0, alpha=0.03, color="#E53935", zorder=0)
    ax.axvspan(1.0, ax.get_xlim()[1], alpha=0.03, color="#2E7D32", zorder=0)
    ax.text(0.35, -0.8, "Less reuse", fontsize=8, color="#E53935", ha="center")
    ax.text(3.0, -0.8, "More reuse", fontsize=8, color="#2E7D32", ha="center")

    fig.tight_layout()
    Path("output/figures").mkdir(parents=True, exist_ok=True)
    fig.savefig("output/figures/andersen_gill_forest.png", dpi=150, bbox_inches="tight")
    plt.close()
    print("Saved output/figures/andersen_gill_forest.png")


def main():
    parser = argparse.ArgumentParser(description="Andersen-Gill Cox PH model for recurrent reuse")
    parser.add_argument("--plot-only", action="store_true",
                        help="Only regenerate forest plot from cached results")
    args = parser.parse_args()

    if args.plot_only:
        plot_forest()
        return

    print("Fetching dandiset metadata...", file=sys.stderr)
    meta = fetch_dandiset_metadata()
    print(f"  Got metadata for {len(meta)} dandisets", file=sys.stderr)

    print("Getting citation counts...", file=sys.stderr)
    citations = get_citation_counts()

    print("Getting journal impact factors...", file=sys.stderr)
    impact_factors = get_journal_impact_factors(set(meta.keys()))
    nonzero = sum(1 for v in impact_factors.values() if v > 0)
    print(f"  Got h-index for {nonzero}/{len(impact_factors)} dandisets", file=sys.stderr)

    print("Building counting process data...", file=sys.stderr)
    df = build_counting_process_data(meta, citations, impact_factors)
    print(f"  {len(df)} intervals, {df['event'].sum():.0f} events, {df['dandiset_id'].nunique()} dandisets",
          file=sys.stderr)

    # Summary of covariates
    print("\nCovariate summary:", file=sys.stderr)
    for col in ["species_mouse", "species_human", "species_nhp",
                "modality_ephys", "modality_imaging",
                "is_benchmark", "is_allen", "is_cc0"]:
        n = df.groupby("dandiset_id")[col].first().sum()
        print(f"  {col}: {n:.0f} dandisets", file=sys.stderr)
    median_if = df.groupby("dandiset_id")["log_impact_factor"].first().median()
    print(f"  median log_impact_factor: {median_if:.2f}", file=sys.stderr)

    print("\nFitting Andersen-Gill model...", file=sys.stderr)
    fit_model(df)
    plot_forest()


if __name__ == "__main__":
    main()
