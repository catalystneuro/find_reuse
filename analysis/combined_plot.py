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

import matplotlib.pyplot as plt
import numpy as np
from scipy.stats import chi2


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

    delays_cutoff = [d for d in delays if d["pub_date"] <= analysis_cutoff]
    obs_months = {did: (analysis_cutoff - c).days / 30.44
                  for did, c in created.items() if (analysis_cutoff - c).days > 0}

    fig = plt.figure(figsize=(11.5, 13))
    gs = fig.add_gridspec(3, 2, height_ratios=[0.8, 1, 1],
                          hspace=0.35, wspace=0.35)

    # === Panel A: Source Archives ===
    ax = fig.add_subplot(gs[0, 0])
    archives = Counter(c.get("source_archive", "unclear") or "unclear" for c in reuse)
    top_archives = archives.most_common(8)
    names = [a for a, _ in top_archives]
    counts = [n for _, n in top_archives]
    ax.barh(range(len(names)), counts, color="#2196F3")
    ax.set_yticks(range(len(names)))
    ax.set_yticklabels(names, fontsize=9)
    ax.set_xlabel("REUSE papers")
    ax.invert_yaxis()

    # === Panel B: Top Journals ===
    ax = fig.add_subplot(gs[0, 1])
    journals = Counter()
    for c in reuse:
        j = c.get("citing_journal", "") or ""
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

    # === Panel C: Cumulative Reuse ===
    ax = fig.add_subplot(gs[1, 0])
    pub_dates = sorted(d["pub_date"] for d in delays)
    if pub_dates:
        ax.plot(pub_dates, range(1, len(pub_dates) + 1), color="#2E7D32", linewidth=2)
    ax.set_xlabel("Date")
    ax.set_ylabel("Cumulative reuse papers")

    # Gray shading for incomplete recent data
    shade_start = analysis_cutoff
    shade_end = pub_dates[-1] if pub_dates else analysis_cutoff
    if shade_end > shade_start:
        ax.axvspan(shade_start, shade_end, color="gray", alpha=0.1)

    # === Panel D: Reuse by Year ===
    ax = fig.add_subplot(gs[1, 1])
    years = Counter()
    for d in delays:
        try:
            y = d["pub_date"].year
            if 2005 <= y <= 2025:
                years[y] += 1
        except (AttributeError, ValueError):
            pass
    if years:
        ys = sorted(years.keys())
        ax.bar(ys, [years[y] for y in ys], color="#2E7D32", alpha=0.8)
    ax.set_xlabel("Year")
    ax.set_ylabel("Reuse papers")

    # === Panel E: MCF ===
    ax = fig.add_subplot(gs[2, 0])
    event_times = sorted(d["delay_months"] for d in delays_cutoff)
    t_mcf = [0.0]
    mcf = [0.0]
    for et in event_times:
        n_at_risk = sum(1 for obs in obs_months.values() if obs >= et)
        if n_at_risk > 0:
            t_mcf.append(et)
            mcf.append(mcf[-1] + 1.0 / n_at_risk)

    t_years = [t / 12 for t in t_mcf]
    ax.step(t_years, mcf, where="post", color="#2E7D32", linewidth=2)
    ax.set_xlabel("Years after dataset creation")
    ax.set_ylabel("Expected reuse papers\nper dataset")

    # === Panel F: Reuse Rate ===
    ax = fig.add_subplot(gs[2, 1])
    delay_years = [d["delay_months"] / 12 for d in delays_cutoff]
    if delay_years:
        max_yr = min(int(max(delay_years)) + 2, 20)
        bins = np.arange(0, max_yr)
        counts_per_bin = np.histogram(delay_years, bins=bins)[0]
        at_risk = np.array([max(sum(1 for obs in obs_months.values() if obs >= yr * 12), 1)
                            for yr in bins[:-1]])
        rate = counts_per_bin / at_risk
        centers = (bins[:-1] + bins[1:]) / 2

        alpha_ci = 0.05
        ci_lo = chi2.ppf(alpha_ci / 2, 2 * counts_per_bin) / (2 * at_risk)
        ci_hi = chi2.ppf(1 - alpha_ci / 2, 2 * (counts_per_bin + 1)) / (2 * at_risk)
        ci_lo = np.nan_to_num(ci_lo, 0)

        ax.errorbar(centers, rate, yerr=[rate - ci_lo, ci_hi - rate],
                    fmt="s", color="#2E7D32", markersize=5, capsize=3, linewidth=1.2)

    ax.set_xlabel("Years after dataset creation")
    ax.set_ylabel("Reuse rate\n(events/dataset/yr)")

    # Panel labels and despine
    all_axes = list(fig.axes)
    for label, ax in zip("ABCDEF", all_axes):
        ax.text(-0.08, 1.08, label, transform=ax.transAxes,
                fontsize=16, fontweight="bold", va="top")
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)

    fig.suptitle(
        f"{archive_name} Data Reuse Analysis — {lab_label}",
        fontsize=15, fontweight="bold", y=0.93,
    )

    fig.tight_layout(rect=[0, 0, 1, 0.92])
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"Saved {output_path}")
