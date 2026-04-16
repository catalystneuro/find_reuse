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


def plot_top_datasets(reuse_diff, datasets):
    """Top 10 most reused datasets."""
    counts = Counter(c.get("dandiset_id", "") for c in reuse_diff)
    top = counts.most_common(10)

    # Get names
    name_map = {r["dandiset_id"]: r["dandiset_name"][:40] for r in datasets["results"]}

    fig, ax = plt.subplots(figsize=(7, 4))
    labels = [f"{did}: {name_map.get(did, did)}" for did, _ in top]
    values = [n for _, n in top]

    ax.barh(range(len(labels)), values, color="#2196F3")
    ax.set_yticks(range(len(labels)))
    ax.set_yticklabels(labels, fontsize=8)
    ax.set_xlabel("Different-lab reuse papers")
    ax.set_title("Most Reused CRCNS Datasets", fontweight="bold")
    ax.invert_yaxis()
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)

    fig.tight_layout()
    fig.savefig(FIGURES_DIR / "top_datasets.png", dpi=150, bbox_inches="tight")
    plt.close()
    print("Saved top_datasets.png")


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
    plot_reuse_type(reuse)
    plot_top_datasets(reuse_diff, datasets)

    print(f"\nAll figures saved to {FIGURES_DIR}/")


if __name__ == "__main__":
    main()
