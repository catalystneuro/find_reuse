#!/usr/bin/env python3
"""
analyze_crcns.py — Generate analysis figures for CRCNS reuse data.

Generates figures parallel to the DANDI analysis:
- Source archive distribution
- Reuse over time (cumulative)
- Reuse by year
- Mean Cumulative Function
- Reuse rate

Output: output/crcns/figures/
"""

import json
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

OUTPUT_DIR = Path("output/crcns")
FIGURES_DIR = OUTPUT_DIR / "figures"
FIGURES_DIR.mkdir(parents=True, exist_ok=True)

ANALYSIS_CUTOFF = datetime(2025, 10, 7)


def load_data():
    with open(OUTPUT_DIR / "classifications.json") as f:
        cls = json.load(f)
    with open(OUTPUT_DIR / "datasets.json") as f:
        datasets = json.load(f)

    classifications = cls["classifications"]
    reuse = [c for c in classifications if c["classification"] == "REUSE"]
    reuse_diff = [c for c in reuse if c.get("same_lab") is False]
    reuse_same = [c for c in reuse if c.get("same_lab") is True]

    # Dataset creation dates
    created = {}
    for r in datasets["results"]:
        did = r["dandiset_id"]
        date_str = r.get("data_accessible") or r.get("dandiset_created", "")
        if date_str:
            try:
                created[did] = datetime.fromisoformat(date_str.replace("Z", "+00:00")).replace(tzinfo=None)
            except ValueError:
                try:
                    created[did] = datetime.strptime(date_str[:10], "%Y-%m-%d")
                except ValueError:
                    pass

    return classifications, reuse, reuse_diff, reuse_same, created, datasets


def compute_delays(reuse_entries, created):
    """Compute delay in months from dataset creation to reuse publication."""
    delays = []
    for c in reuse_entries:
        did = c.get("dandiset_id", "")
        date_str = c.get("citing_date") or c.get("cached_at", "")[:10]
        if not date_str or did not in created:
            continue
        try:
            pub = datetime.strptime(date_str[:10], "%Y-%m-%d")
        except ValueError:
            continue
        if pub <= created[did] or pub > ANALYSIS_CUTOFF:
            continue
        delay_months = (pub - created[did]).days / 30.44
        delays.append({
            "dandiset_id": did,
            "pub_date": pub,
            "created": created[did],
            "delay_months": delay_months,
            "same_lab": c.get("same_lab"),
        })
    return delays


def plot_source_archives(reuse):
    """Panel A: Source archive distribution."""
    archives = Counter(c.get("source_archive", "unclear") or "unclear" for c in reuse)

    fig, ax = plt.subplots(figsize=(6, 4))
    names = [a for a, _ in archives.most_common(10)]
    counts = [archives[a] for a in names]

    bars = ax.barh(range(len(names)), counts, color="#2196F3")
    ax.set_yticks(range(len(names)))
    ax.set_yticklabels(names, fontsize=9)
    ax.set_xlabel("Number of REUSE papers")
    ax.set_title("Source Archives (CRCNS)", fontweight="bold")
    ax.invert_yaxis()
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)

    fig.tight_layout()
    fig.savefig(FIGURES_DIR / "source_archives.png", dpi=150, bbox_inches="tight")
    plt.close()
    print("Saved source_archives.png")


def plot_reuse_by_year(reuse_diff, reuse_same):
    """Panel B: Reuse papers by year."""
    fig, ax = plt.subplots(figsize=(6, 4))

    for label, entries, color in [
        ("Different lab", reuse_diff, "#2E7D32"),
        ("Same lab", reuse_same, "#7B1FA2"),
    ]:
        years = Counter()
        for c in entries:
            date_str = c.get("citing_date") or c.get("cached_at", "")[:10]
            if date_str:
                try:
                    y = int(date_str[:4])
                    if 2008 <= y <= 2025:
                        years[y] += 1
                except ValueError:
                    pass
        if years:
            ys = sorted(years.keys())
            ax.bar(ys, [years[y] for y in ys], label=label, alpha=0.7, color=color)

    ax.set_xlabel("Year")
    ax.set_ylabel("Reuse papers")
    ax.set_title("CRCNS Reuse Papers by Year", fontweight="bold")
    ax.legend()
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)

    fig.tight_layout()
    fig.savefig(FIGURES_DIR / "reuse_by_year.png", dpi=150, bbox_inches="tight")
    plt.close()
    print("Saved reuse_by_year.png")


def plot_cumulative_reuse(reuse_diff, reuse_same):
    """Panel C: Cumulative reuse over time."""
    fig, ax = plt.subplots(figsize=(6, 4))

    for label, entries, color in [
        ("Different lab", reuse_diff, "#2E7D32"),
        ("Same lab", reuse_same, "#7B1FA2"),
    ]:
        dates = []
        for c in entries:
            date_str = c.get("citing_date") or c.get("cached_at", "")[:10]
            if date_str:
                try:
                    dates.append(datetime.strptime(date_str[:10], "%Y-%m-%d"))
                except ValueError:
                    pass
        if dates:
            dates.sort()
            ax.plot(dates, range(1, len(dates) + 1), label=label, color=color, linewidth=2)

    ax.set_xlabel("Date")
    ax.set_ylabel("Cumulative reuse papers")
    ax.set_title("Cumulative CRCNS Reuse", fontweight="bold")
    ax.legend()
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)

    fig.tight_layout()
    fig.savefig(FIGURES_DIR / "cumulative_reuse.png", dpi=150, bbox_inches="tight")
    plt.close()
    print("Saved cumulative_reuse.png")


def plot_mcf(delays, created, lab_type="different"):
    """Mean Cumulative Function (Nelson-Aalen for recurrent events)."""
    if lab_type == "different":
        delay_list = [d for d in delays if d["same_lab"] is False]
        color = "#2E7D32"
    else:
        delay_list = [d for d in delays if d["same_lab"] is True]
        color = "#7B1FA2"

    if not delay_list:
        return

    # Total dandisets at risk at each time
    n_dandisets = len(created)
    obs_months = {}
    for did, c in created.items():
        obs = (ANALYSIS_CUTOFF - c).days / 30.44
        if obs > 0:
            obs_months[did] = obs

    # Event times
    event_times = sorted(d["delay_months"] for d in delay_list)

    # Nelson-Aalen MCF
    t_mcf = [0]
    mcf = [0]
    for et in event_times:
        n_at_risk = sum(1 for obs in obs_months.values() if obs >= et)
        if n_at_risk > 0:
            t_mcf.append(et)
            mcf.append(mcf[-1] + 1.0 / n_at_risk)

    fig, ax = plt.subplots(figsize=(6, 4))
    t_years = [t / 12 for t in t_mcf]
    ax.step(t_years, mcf, where="post", color=color, linewidth=2)
    ax.set_xlabel("Years after dataset creation")
    ax.set_ylabel("Expected reuse papers per dataset")
    lab_label = "Different-lab" if lab_type == "different" else "Same-lab"
    ax.set_title(f"MCF: {lab_label} Reuse (CRCNS)", fontweight="bold")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.set_xlim(0, 15)

    fig.tight_layout()
    fig.savefig(FIGURES_DIR / f"mcf_{lab_type}_lab.png", dpi=150, bbox_inches="tight")
    plt.close()
    print(f"Saved mcf_{lab_type}_lab.png")


def plot_reuse_type(reuse):
    """Reuse type distribution."""
    # Load reuse types from cache -- try multiple filename patterns
    type_cache = Path(".reuse_type_cache")
    types = Counter()
    for c in reuse:
        doi = c["citing_doi"]
        did = c.get("dandiset_id", "")
        safe_doi = doi.replace("/", "_")
        # Try different patterns (DANDI uses one format, CRCNS another)
        found = False
        for pattern in [f"{safe_doi}_{did}.json", f"{safe_doi}__{did}.json"]:
            cache_file = type_cache / pattern
            if cache_file.exists():
                with open(cache_file) as f:
                    d = json.load(f)
                types[d.get("reuse_type", "unknown")] += 1
                found = True
                break
        if not found:
            types["unknown"] += 1

    if not types or all(v == 0 for v in types.values()):
        print("No reuse types available, skipping")
        return

    fig, ax = plt.subplots(figsize=(6, 4))
    names = [t for t, _ in types.most_common() if t != "unknown"]
    counts = [types[t] for t in names]

    bars = ax.barh(range(len(names)), counts, color="#FF9800")
    ax.set_yticks(range(len(names)))
    ax.set_yticklabels(names, fontsize=9)
    ax.set_xlabel("Count")
    ax.set_title("Reuse Types (CRCNS)", fontweight="bold")
    ax.invert_yaxis()
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)

    fig.tight_layout()
    fig.savefig(FIGURES_DIR / "reuse_type.png", dpi=150, bbox_inches="tight")
    plt.close()
    print("Saved reuse_type.png")


def plot_top_datasets(reuse, datasets):
    """Top 10 most reused datasets, stacked by same/different lab."""
    diff_counts = Counter(c.get("dandiset_id", "") for c in reuse if c.get("same_lab") is False)
    same_counts = Counter(c.get("dandiset_id", "") for c in reuse if c.get("same_lab") is True)
    total_counts = Counter(c.get("dandiset_id", "") for c in reuse)
    top = total_counts.most_common(10)

    name_map = {r["dandiset_id"]: r["dandiset_name"][:40] for r in datasets["results"]}

    fig, ax = plt.subplots(figsize=(7, 4))
    labels = [f"{did}: {name_map.get(did, did)}" for did, _ in top]
    diff_vals = [diff_counts.get(did, 0) for did, _ in top]
    same_vals = [same_counts.get(did, 0) for did, _ in top]
    y_pos = range(len(labels))

    ax.barh(y_pos, diff_vals, color="#2E7D32", alpha=0.8, label="Different lab")
    ax.barh(y_pos, same_vals, left=diff_vals, color="#7B1FA2", alpha=0.8, label="Same lab")
    ax.set_yticks(y_pos)
    ax.set_yticklabels(labels, fontsize=8)
    ax.set_xlabel("Reuse papers")
    ax.set_title("Most Reused CRCNS Datasets", fontweight="bold")
    ax.legend(fontsize=8)
    ax.invert_yaxis()
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)

    fig.tight_layout()
    fig.savefig(FIGURES_DIR / "top_datasets.png", dpi=150, bbox_inches="tight")
    plt.close()
    print("Saved top_datasets.png")


def plot_mcf_modeled(delays, created):
    """MCF with model fit and reuse rate derivative, 2-panel figure."""
    from scipy.optimize import curve_fit

    diff_delays = [d for d in delays if d["same_lab"] is False]
    same_delays = [d for d in delays if d["same_lab"] is True]

    obs_months = {did: (ANALYSIS_CUTOFF - c).days / 30.44
                  for did, c in created.items() if (ANALYSIS_CUTOFF - c).days > 0}

    def compute_mcf(delay_list):
        event_times = sorted(d["delay_months"] for d in delay_list)
        t = [0]
        mcf_vals = [0]
        for et in event_times:
            n_at_risk = sum(1 for obs in obs_months.values() if obs >= et)
            if n_at_risk > 0:
                t.append(et)
                mcf_vals.append(mcf_vals[-1] + 1.0 / n_at_risk)
        return np.array(t), np.array(mcf_vals)

    fig, axes = plt.subplots(1, 2, figsize=(12, 5))

    # --- Panel A: MCF with fits ---
    ax = axes[0]
    for label, delay_list, color in [
        ("Different lab", diff_delays, "#2E7D32"),
        ("Same lab", same_delays, "#7B1FA2"),
    ]:
        if not delay_list:
            continue
        t, mcf_vals = compute_mcf(delay_list)
        t_years = t / 12
        ax.step(t_years, mcf_vals, where="post", color=color, linewidth=2, alpha=0.5)

        # Fit saturating exponential: MCF(t) = K * (1 - exp(-t/tau))
        def sat_exp(t, K, tau):
            return K * (1 - np.exp(-t / tau))

        try:
            popt, _ = curve_fit(sat_exp, t_years[1:], mcf_vals[1:],
                                p0=[mcf_vals[-1] * 1.5, 5], maxfev=5000)
            K, tau = popt
            t_fit = np.linspace(0, 20, 200)
            ax.plot(t_fit, sat_exp(t_fit, K, tau), color=color, linewidth=2,
                    label=f"{label} (K={K:.1f}, τ={tau:.0f}yr)")
        except Exception:
            ax.plot([], [], color=color, label=label)

    ax.set_xlabel("Years after dataset creation")
    ax.set_ylabel("Expected reuse papers per dataset")
    ax.set_title("A. MCF: Model Fits", fontweight="bold")
    ax.legend(fontsize=9)
    ax.set_xlim(0, 18)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)

    # --- Panel B: Reuse rate (derivative) ---
    ax = axes[1]
    for label, delay_list, color in [
        ("Different lab", diff_delays, "#2E7D32"),
        ("Same lab", same_delays, "#7B1FA2"),
    ]:
        if not delay_list:
            continue
        # Bin by year and compute rate
        delay_years = [d["delay_months"] / 12 for d in delay_list]
        max_year = int(max(delay_years)) + 1
        bins = np.arange(0, min(max_year + 1, 18))

        counts_per_bin = np.histogram(delay_years, bins=bins)[0]

        # At-risk dandisets per bin
        at_risk = []
        for yr in bins[:-1]:
            n = sum(1 for obs in obs_months.values() if obs >= yr * 12)
            at_risk.append(max(n, 1))
        at_risk = np.array(at_risk)

        rate = counts_per_bin / at_risk
        bin_centers = (bins[:-1] + bins[1:]) / 2

        # Poisson confidence intervals
        from scipy.stats import chi2
        alpha = 0.05
        ci_lo = chi2.ppf(alpha / 2, 2 * counts_per_bin) / (2 * at_risk)
        ci_hi = chi2.ppf(1 - alpha / 2, 2 * (counts_per_bin + 1)) / (2 * at_risk)
        ci_lo = np.nan_to_num(ci_lo, 0)

        ax.bar(bin_centers, rate, width=0.8, alpha=0.6, color=color, label=label)
        ax.vlines(bin_centers, ci_lo, ci_hi, color=color, linewidth=1.5)

    ax.set_xlabel("Years after dataset creation")
    ax.set_ylabel("Reuse rate (events/dataset/yr)")
    ax.set_title("B. Reuse Rate", fontweight="bold")
    ax.legend(fontsize=9)
    ax.set_xlim(-0.5, 17)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)

    fig.tight_layout()
    fig.savefig(FIGURES_DIR / "mcf_model.png", dpi=150, bbox_inches="tight")
    plt.close()
    print("Saved mcf_model.png")


def run_andersen_gill(classifications, created, datasets):
    """Run Andersen-Gill Cox PH regression for CRCNS."""
    import time as _time
    import requests
    import pandas as pd
    from lifelines import CoxPHFitter
    from archives.crcns import CRCNSAdapter

    adapter = CRCNSAdapter()

    # Get metadata for each dataset
    print("Fetching metadata for Andersen-Gill...", flush=True)
    meta = {}
    for ds in datasets["results"]:
        did = ds["dandiset_id"]
        m = adapter._parse_metadata_from_description(ds)
        m["dandiset_created"] = ds.get("dandiset_created", ds.get("data_accessible", ""))
        meta[did] = m

    # Get citation counts from datasets.json
    citations = {}
    for ds in datasets["results"]:
        did = ds["dandiset_id"]
        citations[did] = sum(p.get("citation_count", 0) or 0 for p in ds.get("paper_relations", []))

    # Get journal h-index via OpenAlex
    print("Fetching journal h-index...", flush=True)
    session = requests.Session()
    session.headers.update({"User-Agent": "FindReuse/1.0"})
    journal_cache = {}
    impact_factors = {}

    for ds in datasets["results"]:
        did = ds["dandiset_id"]
        for p in ds.get("paper_relations", []):
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
                                journal_cache[source_id] = resp2.json().get("summary_stats", {}).get("h_index", 0) or 0
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

    print(f"  h-index for {sum(1 for v in impact_factors.values() if v > 0)}/{len(impact_factors)} datasets")

    # Build counting process data (different-lab only)
    diff_events = {}
    for c in classifications:
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
        created_str = meta[did].get("dandiset_created", "")
        if not created_str:
            continue
        try:
            c_date = datetime.fromisoformat(created_str.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            try:
                c_date = datetime.strptime(created_str[:10], "%Y-%m-%d")
            except ValueError:
                continue
        if pub <= c_date or pub > ANALYSIS_CUTOFF:
            continue
        age_months = (pub - c_date).days / 30.44
        diff_events.setdefault(did, []).append(age_months)

    rows = []
    for did, m in meta.items():
        created_str = m.get("dandiset_created", "")
        if not created_str:
            continue
        try:
            c_date = datetime.fromisoformat(created_str.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            try:
                c_date = datetime.strptime(created_str[:10], "%Y-%m-%d")
            except ValueError:
                continue
        if c_date >= ANALYSIS_CUTOFF:
            continue

        obs_months = (ANALYSIS_CUTOFF - c_date).days / 30.44
        event_times = sorted(diff_events.get(did, []))

        log_citations = np.log10(max(citations.get(did, 0), 1))
        log_impact = np.log10(max(impact_factors.get(did, 0), 1))

        prev_time = 0
        for et in event_times:
            if et > obs_months:
                break
            rows.append({
                "dandiset_id": did, "start": prev_time, "stop": et, "event": 1,
                "species_mouse": 1 if m.get("species") == "mouse" else 0,
                "species_human": 1 if m.get("species") == "human" else 0,
                "species_nhp": 1 if m.get("species") == "nhp" else 0,
                "modality_imaging": 1 if m.get("modality") == "imaging" else 0,
                "log_citations": log_citations,
                "log_impact_factor": log_impact,
            })
            prev_time = et

        if prev_time < obs_months:
            rows.append({
                "dandiset_id": did, "start": prev_time, "stop": obs_months, "event": 0,
                "species_mouse": 1 if m.get("species") == "mouse" else 0,
                "species_human": 1 if m.get("species") == "human" else 0,
                "species_nhp": 1 if m.get("species") == "nhp" else 0,
                "modality_imaging": 1 if m.get("modality") == "imaging" else 0,
                "log_citations": log_citations,
                "log_impact_factor": log_impact,
            })

    df = pd.DataFrame(rows)
    if df.empty:
        print("No data for Andersen-Gill model")
        return
    df.loc[df["stop"] <= df["start"], "stop"] = df["start"] + 0.01

    print(f"  {len(df)} intervals, {df['event'].sum():.0f} events, {df['dandiset_id'].nunique()} datasets")

    covariates = [
        "species_mouse", "species_human", "species_nhp",
        "modality_imaging", "log_citations", "log_impact_factor",
    ]

    # Drop zero-variance columns
    for cov in covariates[:]:
        if df[cov].nunique() <= 1:
            print(f"  Dropping {cov} (zero variance)")
            covariates.remove(cov)

    cph = CoxPHFitter()
    cph.fit(
        df[["start", "stop", "event"] + covariates],
        duration_col="stop", event_col="event", entry_col="start",
        show_progress=False,
    )

    print(cph.summary[["coef", "exp(coef)", "p", "exp(coef) lower 95%", "exp(coef) upper 95%"]])
    print(f"Concordance: {cph.concordance_index_:.3f}")

    # Save results
    results = {"concordance": cph.concordance_index_, "covariates": {}}
    for cov in covariates:
        results["covariates"][cov] = {
            "hr": float(cph.summary.loc[cov, "exp(coef)"]),
            "p": float(cph.summary.loc[cov, "p"]),
            "hr_lower": float(cph.summary.loc[cov, "exp(coef) lower 95%"]),
            "hr_upper": float(cph.summary.loc[cov, "exp(coef) upper 95%"]),
        }
    with open(OUTPUT_DIR / "andersen_gill_results.json", "w") as f:
        json.dump(results, f, indent=2)

    # Forest plot
    labels = {
        "log_citations": "Primary paper citations\n(per 10x increase)",
        "log_impact_factor": "Journal impact factor\n(per 10x increase in h-index)",
        "species_nhp": "Non-human primate\n(vs other species)",
        "species_human": "Human\n(vs other species)",
        "species_mouse": "Mouse\n(vs other species)",
        "modality_imaging": "Calcium imaging\n(vs other modality)",
    }

    order = sorted(covariates, key=lambda c: -results["covariates"][c]["hr"])
    y_pos = np.arange(len(order))
    hr = np.array([results["covariates"][c]["hr"] for c in order])
    ci_lo = np.array([results["covariates"][c]["hr_lower"] for c in order])
    ci_hi = np.array([results["covariates"][c]["hr_upper"] for c in order])
    pvals = np.array([results["covariates"][c]["p"] for c in order])
    names = [labels.get(c, c) for c in order]

    colors = ["#2E7D32" if p < 0.05 and h > 1 else "#E53935" if p < 0.05 and h < 1 else "#9E9E9E"
              for p, h in zip(pvals, hr)]

    fig, ax = plt.subplots(figsize=(8, 5))
    for i in range(len(hr)):
        ax.plot([ci_lo[i], ci_hi[i]], [y_pos[i], y_pos[i]], color=colors[i], linewidth=2.5, solid_capstyle="round")
    ax.scatter(hr, y_pos, color=colors, s=90, zorder=3, edgecolors="white", linewidth=0.5)
    ax.axvline(1.0, color="gray", linestyle="--", linewidth=1, zorder=1)
    ax.set_xscale("log")
    from matplotlib.ticker import FixedLocator, FixedFormatter
    ax.xaxis.set_major_locator(FixedLocator([0.5, 1, 2, 3, 4, 5]))
    ax.xaxis.set_major_formatter(FixedFormatter(["0.5", "1", "2", "3", "4", "5"]))
    ax.xaxis.set_minor_locator(FixedLocator([]))
    ax.grid(axis="x", alpha=0.2)

    for i, (h, p) in enumerate(zip(hr, pvals)):
        sig = "***" if p < 0.001 else "**" if p < 0.01 else "*" if p < 0.05 else ""
        if sig:
            ax.text(ci_hi[i] * 1.1, i, sig, va="center", fontsize=10, fontweight="bold",
                    color="#2E7D32" if h > 1 else "#E53935")

    ax.set_yticks(y_pos)
    ax.set_yticklabels(names, fontsize=9)
    ax.set_xlabel("Hazard Ratio (log scale)", fontsize=11)
    ax.set_title("Predictors of Different-Lab Reuse (CRCNS)\nAndersen-Gill Cox Proportional Hazards",
                 fontsize=11, fontweight="bold")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.grid(axis="x", alpha=0.2, which="both")

    fig.tight_layout()
    fig.savefig(FIGURES_DIR / "andersen_gill_forest.png", dpi=150, bbox_inches="tight")
    plt.close()
    print("Saved andersen_gill_forest.png")


def main():
    classifications, reuse, reuse_diff, reuse_same, created, datasets = load_data()

    print(f"Total classifications: {len(classifications)}")
    print(f"REUSE: {len(reuse)} (diff: {len(reuse_diff)}, same: {len(reuse_same)})")
    print(f"Unique reuse papers: {len(set(c['citing_doi'] for c in reuse))}")
    print(f"Datasets with creation dates: {len(created)}")

    delays = compute_delays(reuse, created)
    print(f"Delays computed: {len(delays)}")

    plot_source_archives(reuse)
    plot_reuse_by_year(reuse_diff, reuse_same)
    plot_cumulative_reuse(reuse_diff, reuse_same)
    plot_mcf(delays, created, "different")
    plot_mcf(delays, created, "same")
    plot_mcf_modeled(delays, created)
    plot_reuse_type(reuse)
    plot_top_datasets(reuse, datasets)

    # 2x2 modeling figure (shared method)
    from analysis.reuse_modeling import plot_model_2x2
    plot_model_2x2(delays, created, datasets, FIGURES_DIR / "reuse_rate_model.png",
                   archive_name="CRCNS", analysis_cutoff=ANALYSIS_CUTOFF, split_labs=False)

    # 6-panel combined figures (shared method)
    from analysis.combined_plot import plot_combined
    plot_combined(reuse, delays, created, FIGURES_DIR / "combined_all_labs.png",
                  archive_name="CRCNS", analysis_cutoff=ANALYSIS_CUTOFF, lab_type="all")
    plot_combined(reuse, delays, created, FIGURES_DIR / "combined_different_lab.png",
                  archive_name="CRCNS", analysis_cutoff=ANALYSIS_CUTOFF, lab_type="different")
    plot_combined(reuse, delays, created, FIGURES_DIR / "combined_same_lab.png",
                  archive_name="CRCNS", analysis_cutoff=ANALYSIS_CUTOFF, lab_type="same")

    run_andersen_gill(classifications, created, datasets)

    print(f"\nAll figures saved to {FIGURES_DIR}/")


if __name__ == "__main__":
    main()
