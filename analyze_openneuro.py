#!/usr/bin/env python3
"""
analyze_openneuro.py — Generate analysis figures for OpenNeuro reuse data.

Uses direct reference classifications only (no citation pipeline yet).
"""

import json
import re
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

import matplotlib
matplotlib.rcParams["font.family"] = "Helvetica"
import matplotlib.pyplot as plt
import numpy as np

OUTPUT_DIR = Path("output/openneuro")
FIGURES_DIR = OUTPUT_DIR / "figures"
FIGURES_DIR.mkdir(parents=True, exist_ok=True)

ANALYSIS_CUTOFF = datetime(2025, 10, 7)


def load_data():
    with open(OUTPUT_DIR / "direct_ref_classifications.json") as f:
        cls = json.load(f)

    # Load datasets for creation dates
    datasets_path = OUTPUT_DIR / "datasets.json"
    if datasets_path.exists():
        with open(datasets_path) as f:
            datasets = json.load(f)
    else:
        datasets = {"results": []}

    classifications = cls["classifications"]
    reuse = [c for c in classifications if c["classification"] == "REUSE"]
    reuse_diff = [c for c in reuse if c.get("same_lab") is False]
    reuse_same = [c for c in reuse if c.get("same_lab") is True]

    # Creation dates from datasets.json or from classification dates
    created = {}
    for r in datasets.get("results", []):
        did = r.get("id", r.get("dandiset_id", ""))
        date_str = r.get("created", "")
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
    print(f"Unique reuse papers: {len(set(c['citing_doi'] for c in reuse))}")
    print(f"Unique reuse datasets: {len(set(c['dandiset_id'] for c in reuse))}")
    print(f"Datasets with creation dates: {len(created)}")

    delays = compute_delays(reuse, created)
    print(f"Delays computed: {len(delays)}")

    # Source archives
    from analysis.combined_plot import plot_combined
    plot_combined(reuse, delays, created, FIGURES_DIR / "combined_all_labs.png",
                  archive_name="OpenNeuro", analysis_cutoff=ANALYSIS_CUTOFF, lab_type="all")

    # Reuse rate model
    from analysis.reuse_modeling import plot_model_2x2
    plot_model_2x2(delays, created, datasets, FIGURES_DIR / "reuse_rate_model.png",
                   archive_name="OpenNeuro", analysis_cutoff=ANALYSIS_CUTOFF,
                   split_labs=False, project_years=6)

    # Reuse distribution
    from analysis.reuse_distribution import plot_reuse_distribution
    plot_reuse_distribution(reuse, created, FIGURES_DIR / "reuse_distribution.png",
                            archive_name="OpenNeuro", analysis_cutoff=ANALYSIS_CUTOFF,
                            windows=(5, None))

    # Flowcharts
    from analysis.render_flowcharts import render_phase1, render_phase2, render_reference_flow
    if (OUTPUT_DIR / "datasets.json").exists():
        render_phase1(OUTPUT_DIR / "datasets.json", FIGURES_DIR / "phase1_coverage.png", "OpenNeuro")
    if (OUTPUT_DIR / "direct_ref_classifications.json").exists():
        render_reference_flow(OUTPUT_DIR / "direct_ref_classifications.json",
                              FIGURES_DIR / "reference_flow.png", "OpenNeuro")

    # Reuse type (if cached)
    type_cache = Path(".reuse_type_cache")
    types = Counter()
    label_map = {
        "TOOL_DEMO": "Tool demo", "NOVEL_ANALYSIS": "Novel analysis",
        "AGGREGATION": "Aggregation", "BENCHMARK": "Benchmark",
        "CONFIRMATORY": "Confirmatory", "SIMULATION": "Simulation",
        "ML_TRAINING": "ML training", "TEACHING": "Teaching",
    }
    types_diff = Counter()
    types_same = Counter()
    for c in reuse:
        doi = c["citing_doi"]
        did = c.get("dandiset_id", "")
        safe_doi = doi.replace("/", "_")
        for pattern in [f"{safe_doi}_{did}.json", f"{safe_doi}__{did}.json"]:
            cache_file = type_cache / pattern
            if cache_file.exists():
                with open(cache_file) as f:
                    d = json.load(f)
                rt = d.get("reuse_type", "unknown")
                if rt != "unknown":
                    if c.get("same_lab") is True:
                        types_same[rt] += 1
                    else:
                        types_diff[rt] += 1
                break

    all_types = types_diff + types_same
    if all_types:
        fig, ax = plt.subplots(figsize=(5.4, 3.2))
        names = [t for t, _ in all_types.most_common()]
        diff_vals = [types_diff.get(t, 0) for t in names]
        same_vals = [types_same.get(t, 0) for t in names]
        y_pos = range(len(names))
        ax.barh(y_pos, diff_vals, color="#2E7D32", alpha=0.8, label="Different lab")
        ax.barh(y_pos, same_vals, left=diff_vals, color="#7B1FA2", alpha=0.8, label="Same lab")
        ax.set_yticks(y_pos)
        ax.set_yticklabels([label_map.get(t, t) for t in names], fontsize=9)
        ax.set_xlabel("Count")
        ax.set_title("Reuse Types (OpenNeuro)", fontweight="bold")
        ax.legend(fontsize=8, frameon=False)
        ax.invert_yaxis()
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        fig.tight_layout()
        fig.savefig(FIGURES_DIR / "reuse_type.png", dpi=300, bbox_inches="tight")
        plt.close()
        print("Saved reuse_type.png")
    else:
        print("No reuse types cached, skipping reuse_type.png")

    # Top datasets
    diff_counts = Counter(c.get("dandiset_id", "") for c in reuse if c.get("same_lab") is not True)
    same_counts = Counter(c.get("dandiset_id", "") for c in reuse if c.get("same_lab") is True)
    total_counts = Counter(c.get("dandiset_id", "") for c in reuse)
    top = total_counts.most_common(10)

    name_map = {}
    for r in datasets.get("results", []):
        did = r.get("id", r.get("dandiset_id", ""))
        name_map[did] = r.get("name", did)[:40]

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
    ax.set_title("Most Reused OpenNeuro Datasets", fontweight="bold")
    ax.legend(fontsize=8, frameon=False)
    ax.invert_yaxis()
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    fig.tight_layout()
    fig.savefig(FIGURES_DIR / "top_datasets.png", dpi=300, bbox_inches="tight")
    plt.close()
    print("Saved top_datasets.png")

    print(f"\nAll figures saved to {FIGURES_DIR}/")


if __name__ == "__main__":
    main()
