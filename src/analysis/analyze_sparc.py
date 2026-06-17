#!/usr/bin/env python3
"""
analyze_sparc.py — Generate analysis figures for SPARC reuse data.
"""

import json
from collections import Counter
from datetime import datetime
from pathlib import Path

import matplotlib
matplotlib.rcParams["font.family"] = "Helvetica"
import matplotlib.pyplot as plt
import numpy as np

OUTPUT_DIR = Path("output/sparc")
FIGURES_DIR = OUTPUT_DIR / "figures"
FIGURES_DIR.mkdir(parents=True, exist_ok=True)

ANALYSIS_CUTOFF = datetime(2026, 4, 3)


def load_data():
    # Prefer merged classifications, fallback to direct refs
    cls_path = OUTPUT_DIR / "classifications.json"
    if not cls_path.exists():
        cls_path = OUTPUT_DIR / "direct_ref_classifications.json"
    with open(cls_path) as f:
        cls = json.load(f)
    print(f"Loaded from {cls_path.name}")

    with open(OUTPUT_DIR / "datasets.json") as f:
        datasets = json.load(f)

    classifications = cls["classifications"]
    reuse = [c for c in classifications if c["classification"] == "REUSE"]
    reuse_diff = [c for c in reuse if c.get("same_lab") is False]
    reuse_same = [c for c in reuse if c.get("same_lab") is True]

    # Dataset creation dates
    created = {}
    for r in datasets["results"]:
        did = r.get("dataset_id") or r.get("dandiset_id") or r.get("id", "")
        date_str = r.get("data_accessible") or r.get("dandiset_created") or r.get("created", "")
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
    delays = []
    for c in reuse_entries:
        did = c.get("dandiset_id", c.get("dataset_id", ""))
        date_str = c.get("citing_date") or c.get("cached_at", "")[:10]
        if not date_str or did not in created:
            continue
        try:
            pub = datetime.strptime(date_str[:10], "%Y-%m-%d")
        except ValueError:
            continue
        if pub <= created[did] or pub > ANALYSIS_CUTOFF:
            continue
        delays.append({
            "dandiset_id": did,
            "citing_doi": c.get("citing_doi", ""),
            "pub_date": pub,
            "created": created[did],
            "delay_months": (pub - created[did]).days / 30.44,
            "same_lab": c.get("same_lab"),
        })
    return delays


def main():
    classifications, reuse, reuse_diff, reuse_same, created, datasets = load_data()

    print(f"Total classifications: {len(classifications)}")
    print(f"REUSE: {len(reuse)} (diff: {len(reuse_diff)}, same: {len(reuse_same)})")
    print(f"Unique reuse papers: {len(set(c.get('citing_doi', '') for c in reuse))}")
    print(f"Unique reuse datasets: {len(set(c.get('dandiset_id', c.get('dataset_id', '')) for c in reuse))}")
    print(f"Datasets with creation dates: {len(created)}")

    delays = compute_delays(reuse, created)
    print(f"Delays computed: {len(delays)}")

    # Combined overview plot
    from .combined_plot import plot_combined
    plot_combined(reuse, delays, created, FIGURES_DIR / "combined_all_labs.png",
                  archive_name="SPARC", analysis_cutoff=ANALYSIS_CUTOFF, lab_type="all")

    # Reuse rate model (2x2)
    from .reuse_modeling import plot_model_2x2
    if len(delays) >= 20:
        plot_model_2x2(delays, created, datasets, FIGURES_DIR / "reuse_rate_model.png",
                       archive_name="SPARC", analysis_cutoff=ANALYSIS_CUTOFF,
                       split_labs=False, project_years=5, show_lab_background=False,
                       show_k_lines=False, show_rate_model=False,
                       mcf_model="power_law", mcf_xlim=8)
    else:
        print(f"Skipping reuse_rate_model ({len(delays)} delays — insufficient for modeling)")

    # Reuse distribution
    from .reuse_distribution import plot_reuse_distribution
    plot_reuse_distribution(reuse, created, FIGURES_DIR / "reuse_distribution.png",
                            archive_name="SPARC", analysis_cutoff=ANALYSIS_CUTOFF,
                            windows=(5, None))

    # Flowcharts
    from .render_flowcharts import render_phase1, render_reference_flow
    if (OUTPUT_DIR / "datasets.json").exists():
        render_phase1(OUTPUT_DIR / "datasets.json", FIGURES_DIR / "phase1_coverage.png", "SPARC")
    if cls_path.exists():
        render_reference_flow(str(cls_path), FIGURES_DIR / "reference_flow.png", "SPARC")

    # Top datasets
    diff_counts = Counter(c.get("dandiset_id", c.get("dataset_id", "")) for c in reuse if c.get("same_lab") is not True)
    same_counts = Counter(c.get("dandiset_id", c.get("dataset_id", "")) for c in reuse if c.get("same_lab") is True)
    total_counts = Counter(c.get("dandiset_id", c.get("dataset_id", "")) for c in reuse)
    top = total_counts.most_common(10)

    name_map = {}
    for r in datasets.get("results", []):
        did = r.get("dataset_id") or r.get("dandiset_id") or r.get("id", "")
        name_map[did] = (r.get("dataset_name") or r.get("dandiset_name") or did)[:40]

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
    ax.set_title("Most Reused SPARC Datasets", fontweight="bold")
    ax.legend(fontsize=8, frameon=False)
    ax.invert_yaxis()
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    fig.tight_layout()
    fig.savefig(FIGURES_DIR / "top_datasets.png", dpi=300, bbox_inches="tight")
    plt.close()
    print("Saved top_datasets.png")

    print(f"\nAll figures saved to {FIGURES_DIR}/")


# Need cls_path at module level for flowchart rendering
cls_path = OUTPUT_DIR / "classifications.json"
if not cls_path.exists():
    cls_path = OUTPUT_DIR / "direct_ref_classifications.json"

if __name__ == "__main__":
    main()
