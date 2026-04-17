#!/usr/bin/env python3
"""
combined_plot.py — Shared 6-panel combined reuse overview figure for any archive.

Panels:
  A: Source archive distribution
  B: Top journals / venues
  C: Cumulative reuse over time
  D: Reuse papers by year
  E: Mean Cumulative Function
  F: Reuse rate (events/dataset/yr) with Poisson CIs

Usage:
    from analysis.combined_plot import plot_combined
    plot_combined(reuse, delays, created, output_path, archive_name="CRCNS")
"""

import json
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

import matplotlib
import matplotlib.pyplot as plt
matplotlib.rcParams["font.family"] = "Helvetica"
import numpy as np
from scipy.stats import chi2

# Normalize source archive names across all archives
ARCHIVE_NORMALIZE = {
    "DANDI": "DANDI Archive",
    "dandi": "DANDI Archive",
    "DANDI archive": "DANDI Archive",
    "crcns": "CRCNS",
    "Crcns": "CRCNS",
    "crcns.org": "CRCNS",
}

# Clean up journal/venue names
JOURNAL_NORMALIZE = {
    "bioRxiv (Cold Spring Harbor Laboratory)": "bioRxiv",
    "Cold Spring Harbor Laboratory": "bioRxiv",
    "arXiv (Cornell University)": "arXiv",
    "Cornell University": "arXiv",
    "medRxiv (Cold Spring Harbor Laboratory)": "medRxiv",
    "Research Square (Research Square)": "Research Square",
}


def normalize_archive(name):
    return ARCHIVE_NORMALIZE.get(name, name)


def normalize_journal(name):
    return JOURNAL_NORMALIZE.get(name, name)


def plot_combined(reuse, delays, created, output_path, archive_name="Archive",
                  analysis_cutoff=None, lab_type="all"):
    """Generate 6-panel combined reuse overview.

    Args:
        reuse: list of classification dicts with classification==REUSE
        delays: list of delay dicts with delay_months, same_lab, pub_date, dandiset_id
        created: dict {dataset_id: datetime}
        output_path: Path to save figure
        archive_name: for titles
        analysis_cutoff: datetime cutoff for survival analysis
        lab_type: "all", "different", or "same"
    """
    if analysis_cutoff is None:
        analysis_cutoff = datetime(2025, 10, 7)

    # Filter by lab type
    if lab_type == "different":
        reuse = [c for c in reuse if c.get("same_lab") is False]
        delays = [d for d in delays if d["same_lab"] is False]
        lab_label = "Different-Lab"
    elif lab_type == "same":
        reuse = [c for c in reuse if c.get("same_lab") is True]
        delays = [d for d in delays if d["same_lab"] is True]
        lab_label = "Same-Lab"
    else:
        lab_label = "All Labs"

    fig = plt.figure(figsize=(11.5, 9))
    gs = fig.add_gridspec(2, 2, height_ratios=[0.8, 1],
                          hspace=0.35, wspace=0.35)

    # === Panel A: Source Archives ===
    ax = fig.add_subplot(gs[0, 0])
    archives = Counter(normalize_archive(c.get("source_archive", "unclear") or "unclear") for c in reuse)
    top_archives = archives.most_common(8)
    names = [a for a, _ in top_archives]
    counts_a = [n for _, n in top_archives]
    ax.barh(range(len(names)), counts_a, color="black")
    ax.set_yticks(range(len(names)))
    ax.set_yticklabels(names, fontsize=9)
    ax.set_xlabel("REUSE papers")
    ax.invert_yaxis()

    # === Panel B: Top Journals ===
    ax = fig.add_subplot(gs[0, 1])
    journals = Counter()
    for c in reuse:
        j = normalize_journal(c.get("citing_journal", "") or "")
        if j:
            journals[j] += 1
    top_journals = journals.most_common(10)
    j_names = [j[:30] for j, _ in top_journals]
    j_counts = [n for _, n in top_journals]
    colors_j = ["#FF9800" if "rxiv" in j.lower() else "#2196F3" for j, _ in top_journals]
    ax.barh(range(len(j_names)), j_counts, color=colors_j)
    ax.set_yticks(range(len(j_names)))
    ax.set_yticklabels(j_names, fontsize=8)
    ax.set_xlabel("REUSE papers")
    ax.invert_yaxis()

    # === Panel C: Cumulative Reuse (stacked area by source archive) ===
    ax = fig.add_subplot(gs[1, 0])

    # Build source archive lookup from reuse entries
    def _archive_cat(sa):
        sa = normalize_archive(sa or "unclear")
        if sa == archive_name:
            return "archive"
        elif sa == "unclear":
            return "unclear"
        else:
            return "other"

    # Map (dandiset_id, same_lab, pub_date_str) -> archive category
    reuse_archive_map = {}
    for c in reuse:
        sa = c.get("source_archive", "unclear")
        did = c.get("dandiset_id", "")
        reuse_archive_map[did] = _archive_cat(sa)  # last one wins per dataset

    # Assign each delay to an archive category
    delay_cats = []
    for d in delays:
        cat = reuse_archive_map.get(d["dandiset_id"], "unclear")
        delay_cats.append(cat)

    # Sort by date and build cumulative counts by category
    sorted_indices = sorted(range(len(delays)), key=lambda i: delays[i]["pub_date"])
    sorted_dates = [delays[i]["pub_date"] for i in sorted_indices]
    sorted_cats = [delay_cats[i] for i in sorted_indices]

    if sorted_dates:
        cum_archive = np.cumsum([1 if c == "archive" else 0 for c in sorted_cats])
        cum_unclear = np.cumsum([1 if c == "unclear" else 0 for c in sorted_cats])
        cum_other = np.cumsum([1 if c == "other" else 0 for c in sorted_cats])

        ax.fill_between(sorted_dates, 0, cum_archive, alpha=0.7, color="#2196F3", label=archive_name)
        ax.fill_between(sorted_dates, cum_archive, cum_archive + cum_unclear, alpha=0.7, color="#FF9800", label="Unclear")
        ax.fill_between(sorted_dates, cum_archive + cum_unclear, cum_archive + cum_unclear + cum_other, alpha=0.7, color="#9E9E9E", label="Other")
        ax.legend(fontsize=8, frameon=False)
    ax.set_xlabel("Date")
    ax.set_ylabel("Cumulative reuse papers")

    # === Panel D: Reuse by Year (stacked by source archive) ===
    ax = fig.add_subplot(gs[1, 1])
    years_arch = Counter()
    years_unclear = Counter()
    years_other = Counter()
    for i, d in enumerate(delays):
        try:
            y = d["pub_date"].year
            if 2005 <= y <= 2026:
                cat = delay_cats[i]
                if cat == "archive":
                    years_arch[y] += 1
                elif cat == "unclear":
                    years_unclear[y] += 1
                else:
                    years_other[y] += 1
        except (AttributeError, ValueError):
            pass

    all_years = sorted(set(years_arch) | set(years_unclear) | set(years_other))
    if all_years:
        arch_vals = [years_arch.get(y, 0) for y in all_years]
        unclear_vals = [years_unclear.get(y, 0) for y in all_years]
        other_vals = [years_other.get(y, 0) for y in all_years]
        ax.bar(all_years, arch_vals, color="#2196F3", alpha=0.7, label=archive_name)
        ax.bar(all_years, unclear_vals, bottom=arch_vals, color="#FF9800", alpha=0.7, label="Unclear")
        bottom2 = [a + u for a, u in zip(arch_vals, unclear_vals)]
        ax.bar(all_years, other_vals, bottom=bottom2, color="#9E9E9E", alpha=0.7, label="Other")
        ax.legend(fontsize=8, frameon=False)
        ax.set_xticks([y for y in all_years if y % 2 == 0])
    ax.set_xlabel("Year")
    ax.set_ylabel("Reuse papers")

    # Panel labels and despine
    all_axes = list(fig.axes)
    for label, ax in zip("ABCD", all_axes):
        ax.text(-0.08, 1.08, label, transform=ax.transAxes,
                fontsize=16, fontweight="bold", va="top")
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)

    fig.suptitle(
        f"{archive_name} Data Reuse Analysis — {lab_label}",
        fontsize=15, fontweight="bold", y=0.98,
    )

    fig.tight_layout(rect=[0, 0, 1, 0.95])
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=300, bbox_inches="tight")
    plt.close(fig)
    print(f"Saved {output_path}")
