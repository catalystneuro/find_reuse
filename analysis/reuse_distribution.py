#!/usr/bin/env python3
"""
reuse_distribution.py — Shared reuse count distribution plot for any archive.

Shows histograms of reuse counts per dataset at different observation windows,
with stacked bars for complete vs incomplete (right-censored) datasets.

Usage:
    from analysis.reuse_distribution import plot_reuse_distribution
    plot_reuse_distribution(reuse, created, output_path, archive_name="CRCNS")
"""

import matplotlib
matplotlib.rcParams["font.family"] = "Helvetica"

from collections import Counter
from datetime import datetime
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np


def _count_reuse_per_dataset(reuse, created, cutoff, window_years=None):
    """Count reuse events per dataset within an observation window.

    Returns (counts_complete, counts_incomplete) lists.
    """
    counts_complete = []
    counts_incomplete = []

    for did, c_date in created.items():
        age_years = (cutoff - c_date).days / 365.25
        n = 0
        for c in reuse:
            if c.get("dandiset_id") != did:
                continue
            date_str = c.get("citing_date") or c.get("cached_at", "")[:10]
            if not date_str:
                continue
            try:
                pub = datetime.strptime(date_str[:10], "%Y-%m-%d")
            except ValueError:
                continue
            delay_years = (pub - c_date).days / 365.25
            if delay_years <= 0:
                continue
            if window_years is None or delay_years <= window_years:
                n += 1

        if window_years is None or age_years >= window_years:
            counts_complete.append((did, n))
        else:
            counts_incomplete.append((did, n))

    return counts_complete, counts_incomplete


def plot_reuse_distribution(reuse, created, output_path, archive_name="Archive",
                            analysis_cutoff=None, windows=(5, 10, None),
                            top_n_annotate=4):
    """Generate reuse count distribution figure.

    Args:
        reuse: list of REUSE classification dicts
        created: dict {dataset_id: datetime}
        output_path: Path to save figure
        archive_name: for title
        analysis_cutoff: datetime cutoff
        windows: tuple of observation windows in years (None = all observed)
        top_n_annotate: number of top datasets to annotate on last panel
    """
    if analysis_cutoff is None:
        analysis_cutoff = datetime(2025, 10, 7)

    n_panels = len(windows)
    fig, axes = plt.subplots(1, n_panels, figsize=(3.2 * n_panels, 3.2), sharey=True)
    if n_panels == 1:
        axes = [axes]

    panel_labels = "ABCDEFGH"

    # Pre-compute all-observed counts for annotation
    all_complete, _ = _count_reuse_per_dataset(reuse, created, analysis_cutoff, None)
    top_datasets = sorted(all_complete, key=lambda x: -x[1])[:top_n_annotate]

    for idx, (ax, window_years) in enumerate(zip(axes, windows)):
        if window_years is None:
            title = f"{panel_labels[idx]}. All observed"
        else:
            title = f"{panel_labels[idx]}. {window_years}-year window"

        complete, incomplete = _count_reuse_per_dataset(
            reuse, created, analysis_cutoff, window_years
        )
        counts_c_vals = [n for _, n in complete]
        counts_i_vals = [n for _, n in incomplete]

        max_count = max(counts_c_vals + counts_i_vals) if (counts_c_vals + counts_i_vals) else 1
        bins = np.arange(0, max_count + 2)
        hist_c, _ = np.histogram(counts_c_vals, bins=bins)
        hist_i, _ = np.histogram(counts_i_vals, bins=bins) if counts_i_vals else (np.zeros_like(hist_c), None)

        ax.bar(bins[:-1], hist_c, width=0.8, color="black", alpha=0.8)
        if counts_i_vals:
            ax.bar(bins[:-1], hist_i, width=0.8, bottom=hist_c, color="#BBBBBB")

        ax.set_xlabel("Reuse count per dataset")
        if idx == 0:
            ax.set_ylabel("Number of datasets")
        ax.set_title(title, fontweight="bold")
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)

        # Stats
        if counts_c_vals:
            median = np.median(counts_c_vals)
            mean = np.mean(counts_c_vals)
            pct_zero = sum(1 for c in counts_c_vals if c == 0) / len(counts_c_vals) * 100
            ax.text(0.95, 0.95, f"med={median:.0f}\nmean={mean:.1f}\n{pct_zero:.0f}% zero",
                    transform=ax.transAxes, ha="right", va="top", fontsize=7)

        # Pie chart inset
        n_complete = len(counts_c_vals)
        n_incomplete = len(counts_i_vals)
        inset = ax.inset_axes([0.42, 0.40, 0.22, 0.38])
        if n_incomplete > 0:
            inset.pie([n_complete, n_incomplete], colors=["black", "#BBBBBB"],
                      startangle=90, counterclock=False)
            inset.text(0, -1.6, f"{n_complete} complete", fontsize=7, ha="center", color="black")
            inset.text(0, 1.6, f"{n_incomplete} incomplete", fontsize=7, ha="center", color="#888")
        else:
            inset.pie([1], colors=["black"], startangle=90)
            inset.text(0, -1.6, f"{n_complete} complete", fontsize=7, ha="center", color="black")
            inset.text(0, 1.6, "0 incomplete", fontsize=7, ha="center", color="#888")

        # Annotate top datasets on last panel
        if idx == n_panels - 1 and top_n_annotate > 0:
            for did, n in top_datasets:
                bar_h = hist_c[n] + (hist_i[n] if counts_i_vals else 0)
                ax.text(n, bar_h + 1, did, ha="center", va="bottom", fontsize=6,
                        color="#333", rotation=45)

    fig.suptitle(f"{archive_name} Reuse Count Distribution", fontweight="bold", fontsize=13)
    fig.tight_layout()
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=300, bbox_inches="tight")
    plt.close()
    print(f"Saved {output_path}")
