#!/usr/bin/env python3
"""
analyze_reuse_delays.py - Statistics and plots for DANDI data reuse analysis

Generates:
1. Source archive distribution (other-lab only)
2. Delay histogram: dandiset creation → reuse paper publication (DANDI-sourced)
3. Cumulative reuse over time
4. Reuse by publication year
5. Kaplan-Meier survival curve: time to first other-lab reuse from DANDI
6. Combined multi-panel figure

The Kaplan-Meier analysis excludes the most recent 6 months of data
(before ANALYSIS_CUTOFF) to avoid bias from incomplete publication records.

Usage:
    python analyze_reuse_delays.py
"""

import json
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

import matplotlib.pyplot as plt
import matplotlib.ticker as ticker
import numpy as np
import pandas as pd
from lifelines import KaplanMeierFitter

OUTPUT_DIR = Path("output")
FIGURES_DIR = OUTPUT_DIR / "figures"
FIGURES_DIR.mkdir(exist_ok=True)

# Exclude the most recent 6 months for survival analysis
TODAY = datetime(2026, 4, 8)
ANALYSIS_CUTOFF = TODAY - timedelta(days=183)  # ~6 months


def load_data():
    with open(OUTPUT_DIR / "all_classifications.json") as f:
        data = json.load(f)
    classifications = data["classifications"]

    with open(OUTPUT_DIR / "dandi_reuse_delays.json") as f:
        delays = json.load(f)

    with open(OUTPUT_DIR / "dandi_primary_papers_results.json") as f:
        dandi_data = json.load(f)

    reuse = [c for c in classifications if c.get("classification") == "REUSE"]
    reuse_other_lab = [c for c in reuse if c.get("same_lab") is False]

    # Dandiset creation dates
    dandiset_created = {}
    for r in dandi_data["results"]:
        did = r["dandiset_id"]
        created = r.get("dandiset_created", "")
        if created:
            dandiset_created[did] = datetime.fromisoformat(
                created.replace("Z", "+00:00")
            ).replace(tzinfo=None)

    reuse_same_lab = [c for c in reuse if c.get("same_lab") is True]

    return classifications, reuse, reuse_other_lab, reuse_same_lab, delays, dandiset_created


def apply_cutoff(delays):
    """Filter delays to exclude papers published after ANALYSIS_CUTOFF."""
    return [
        d for d in delays
        if datetime.strptime(d["pub_date"], "%Y-%m-%d") <= ANALYSIS_CUTOFF
    ]


def compute_dandi_fraction(reuse_subset):
    """Compute fraction of known-source papers that used DANDI Archive."""
    known = [c for c in reuse_subset if c.get("source_archive") not in ("unclear", None)]
    dandi = [c for c in known if c.get("source_archive") == "DANDI Archive"]
    return len(dandi) / len(known) if known else 0.0


# ── Panel drawing functions (each takes an ax) ──────────────────────────


def draw_source_archive(ax, reuse_subset, dandi_frac=None):
    """Horizontal bar chart of source archive distribution.

    'unclear' gets orange coloring; all other archives get blue.
    Shows estimated DANDI contribution from unclear as a dashed outline on DANDI bar.
    """
    counts = Counter(c.get("source_archive", "unclear") for c in reuse_subset)
    threshold = 3
    main = {k: v for k, v in counts.items() if v >= threshold}
    other_count = sum(v for k, v in counts.items() if v < threshold)
    if other_count > 0:
        main["Other"] = other_count

    names = sorted(main, key=main.get, reverse=True)
    values = [main[n] for n in names]

    y = list(range(len(names)))
    colors = ["#F57C00" if n == "unclear" else "#2196F3" for n in names]
    bars = ax.barh(y, values, color=colors)

    # Add estimated share of unclear as dashed extension on each archive bar
    if dandi_frac is not None and "unclear" in names:
        unclear_count = counts.get("unclear", 0)
        known = {k: v for k, v in counts.items() if k not in ("unclear", None)}
        total_known = sum(known.values())
        for i, name in enumerate(names):
            if name in ("unclear", "Other") or total_known == 0:
                continue
            archive_count = counts.get(name, 0)
            archive_frac = archive_count / total_known
            estimated = unclear_count * archive_frac
            ax.barh(
                i, estimated, left=values[i],
                color="none", edgecolor=colors[i], linewidth=1.5, linestyle="--",
            )
            ax.text(
                values[i] + estimated + 0.5, i,
                f"~{values[i] + estimated:.0f}",
                va="center", fontsize=7, color=colors[i], style="italic",
            )

    ax.set_yticks(y)
    ax.set_yticklabels(names, fontsize=8)
    ax.invert_yaxis()
    ax.set_xlabel("Number of papers")
    ax.set_title("Source Archive (REUSE papers)", fontsize=10, fontweight="bold")

    from matplotlib.patches import Patch
    handles = [
        Patch(facecolor="#2196F3", label="Known archive"),
        Patch(facecolor="#F57C00", label="Unclear"),
    ]
    if dandi_frac is not None:
        handles.append(Patch(facecolor="none", edgecolor="#2196F3",
                             linestyle="--", label="Est. share of unclear"))
    ax.legend(handles=handles, fontsize=6, loc="lower right")

    for bar, val in zip(bars, values):
        ax.text(bar.get_width() + 0.5, bar.get_y() + bar.get_height() / 2,
                str(val), va="center", fontsize=7)


def _split_dandi_unclear(delays):
    """Split delays into DANDI (confirmed) and unclear."""
    dandi = [d for d in delays if d.get("source_archive") != "unclear"]
    unclear = [d for d in delays if d.get("source_archive") == "unclear"]
    return dandi, unclear


STACK_COLORS = ["#2196F3", "#F57C00"]
STACK_LABELS = ["DANDI Archive", "Unclear source"]


def draw_delay_histogram(ax, delays, dandi_frac=None):
    """Histogram of delay from dandiset creation to reuse paper publication."""
    dandi, unclear = _split_dandi_unclear(delays)
    groups = [[d["delay_months"] for d in dandi], [d["delay_months"] for d in unclear]]
    all_months = [m for g in groups for m in g]

    bins = np.arange(0, max(all_months) + 6, 6)
    ax.hist(
        groups, bins=bins, stacked=True,
        color=STACK_COLORS, edgecolor="white", linewidth=0.8,
        label=STACK_LABELS,
    )

    # Show estimated DANDI-only histogram as dashed step line
    if dandi_frac is not None and unclear:
        # DANDI confirmed + estimated fraction of unclear
        est_months = groups[0] + [m for m in groups[1]]
        unclear_months = np.array(groups[1])
        dandi_months = np.array(groups[0])
        # Compute per-bin: dandi_count + dandi_frac * unclear_count
        dandi_hist, _ = np.histogram(dandi_months, bins=bins)
        unclear_hist, _ = np.histogram(unclear_months, bins=bins)
        est_hist = dandi_hist + dandi_frac * unclear_hist
        bin_centers = (bins[:-1] + bins[1:]) / 2
        ax.step(bins[:-1], est_hist, where="post", color="#1565C0",
                linestyle="--", linewidth=1.5, label=f"Est. DANDI ({dandi_frac:.0%} of unclear)")

    ax.set_xlabel("Months after dandiset creation")
    ax.set_ylabel("Number of papers")
    ax.set_title("Delay: Dandiset Creation → Reuse Publication", fontsize=10, fontweight="bold")

    if groups[0]:
        median = np.median(groups[0])
        ax.axvline(median, color="#E53935", linestyle="--", linewidth=1.5,
                   label=f"Median (DANDI): {median:.0f} mo")
    ax.legend(fontsize=6)

    stats_text = f"DANDI: {len(groups[0])}, Unclear: {len(groups[1])}"
    ax.text(
        0.97, 0.95, stats_text, transform=ax.transAxes, fontsize=8,
        va="top", ha="right",
        bbox=dict(boxstyle="round,pad=0.3", facecolor="wheat", alpha=0.8),
    )


def draw_reuse_by_year(ax, delays, dandi_frac=None):
    """Bar chart of reuse papers by publication year, stacked DANDI vs unclear."""
    dandi, unclear = _split_dandi_unclear(delays)
    year_counts = [Counter(d["pub_date"][:4] for d in g) for g in [dandi, unclear]]
    all_years = sorted(set().union(*(yc.keys() for yc in year_counts)))

    dandi_vals = np.array([year_counts[0][y] for y in all_years], dtype=float)
    unclear_vals = np.array([year_counts[1][y] for y in all_years], dtype=float)

    ax.bar(all_years, dandi_vals, color=STACK_COLORS[0], edgecolor="white",
           linewidth=0.8, label=STACK_LABELS[0])
    ax.bar(all_years, unclear_vals, bottom=dandi_vals, color=STACK_COLORS[1],
           edgecolor="white", linewidth=0.8, label=STACK_LABELS[1])

    # Estimated DANDI as dashed horizontal lines on each bar
    if dandi_frac is not None:
        est_vals = dandi_vals + dandi_frac * unclear_vals
        for i in range(len(all_years)):
            if est_vals[i] > dandi_vals[i]:
                ax.plot(
                    [i - 0.4, i + 0.4], [est_vals[i], est_vals[i]],
                    color="#1565C0", linestyle="--", linewidth=1.5,
                )
        # Single legend entry
        ax.plot([], [], color="#1565C0", linestyle="--", linewidth=1.5,
                label=f"Est. DANDI ({dandi_frac:.0%} of unclear)")

    total_vals = dandi_vals + unclear_vals
    for i, y in enumerate(all_years):
        total = int(total_vals[i])
        if total > 0:
            ax.text(i, total + 0.3, str(total), ha="center", va="bottom",
                    fontsize=9, fontweight="bold")

    ax.set_xlabel("Publication year")
    ax.set_ylabel("Number of papers")
    ax.set_title("DANDI Reuse Papers by Year", fontsize=10, fontweight="bold")
    ax.set_xticks(range(len(all_years)))
    ax.set_xticklabels(all_years)
    ax.yaxis.set_major_locator(ticker.MaxNLocator(integer=True))
    ax.legend(fontsize=6)


def draw_cumulative_reuse(ax, delays, dandi_frac=None):
    """Cumulative reuse papers over time, stacked DANDI vs unclear."""
    dandi, unclear = _split_dandi_unclear(delays)
    groups = [dandi, unclear]

    df = pd.DataFrame(delays)
    df["pub_date"] = pd.to_datetime(df["pub_date"])
    dates = sorted(df["pub_date"].unique())

    cum = [np.zeros(len(dates)) for _ in range(2)]
    for gi, group in enumerate(groups):
        group_dates = pd.to_datetime([d["pub_date"] for d in group])
        count = 0
        for di, date in enumerate(dates):
            count += int((group_dates == date).sum())
            cum[gi][di] = count

    # Stacked area
    bottom = np.zeros(len(dates))
    for gi in range(2):
        top = bottom + cum[gi]
        ax.fill_between(dates, bottom, top, step="post", alpha=0.4, color=STACK_COLORS[gi])
        ax.step(dates, top, where="post", color=STACK_COLORS[gi], linewidth=1.5,
                label=STACK_LABELS[gi])
        bottom = top

    # Estimated DANDI line
    if dandi_frac is not None:
        est_cum = cum[0] + dandi_frac * cum[1]
        ax.step(dates, est_cum, where="post", color="#1565C0",
                linestyle="--", linewidth=1.5,
                label=f"Est. DANDI ({dandi_frac:.0%} of unclear)")

    ax.set_xlabel("Date")
    ax.set_ylabel("Cumulative papers")
    ax.set_title("Cumulative DANDI Reuse Over Time", fontsize=10, fontweight="bold")
    ax.yaxis.set_major_locator(ticker.MaxNLocator(integer=True))
    ax.legend(fontsize=6)


def _build_survival_data(delays_cutoff, dandiset_created):
    """Build per-dandiset survival records from delay data.

    Returns (records, events_by_dandiset) where:
      records: list of dicts with dandiset_id, obs_months, has_event (first event)
      events_by_dandiset: dict mapping dandiset_id -> list of event times in months
    """
    # All event times per dandiset (before cutoff)
    events_by_dandiset = defaultdict(list)
    for d in delays_cutoff:
        pub = datetime.strptime(d["pub_date"], "%Y-%m-%d")
        did = d["dandiset_id"]
        if did in dandiset_created:
            t = (pub - dandiset_created[did]).days / 30.44
            events_by_dandiset[did].append(t)

    # One record per dandiset for KM (time-to-first)
    records = []
    for did, created in dandiset_created.items():
        if created >= ANALYSIS_CUTOFF:
            continue
        obs_months = (ANALYSIS_CUTOFF - created).days / 30.44
        if did in events_by_dandiset:
            first = min(events_by_dandiset[did])
            records.append({
                "dandiset_id": did,
                "duration_months": max(first, 0.1),
                "observed": True,
                "obs_months": obs_months,
            })
        else:
            records.append({
                "dandiset_id": did,
                "duration_months": max(obs_months, 0.1),
                "observed": False,
                "obs_months": obs_months,
            })

    return records, events_by_dandiset


def _compute_mcf(records, events_by_dandiset):
    """Compute the Mean Cumulative Function (MCF) for recurrent events.

    Uses the Nelson-Aalen type estimator:
      MCF(t) = sum_{t_j <= t} d_j / n_j

    where d_j = events at time t_j, n_j = dandisets still under observation.

    Returns (times, mcf, mcf_lower, mcf_upper) arrays.
    """
    # Collect all event times and observation end times
    all_events = []
    for did, times in events_by_dandiset.items():
        for t in times:
            all_events.append(t)
    all_events.sort()

    # Observation end time per dandiset
    obs_end = {r["dandiset_id"]: r["obs_months"] for r in records}
    n_total = len(records)

    times = [0.0]
    mcf_vals = [0.0]
    var_vals = [0.0]

    cumulative = 0.0
    cumulative_var = 0.0

    for t in all_events:
        # Number at risk: dandisets with observation time >= t
        n_at_risk = sum(1 for r in records if r["obs_months"] >= t)
        if n_at_risk == 0:
            break
        increment = 1.0 / n_at_risk
        cumulative += increment
        cumulative_var += 1.0 / (n_at_risk ** 2)
        times.append(t)
        mcf_vals.append(cumulative)
        var_vals.append(cumulative_var)

    times = np.array(times)
    mcf_vals = np.array(mcf_vals)
    se = np.sqrt(np.array(var_vals))
    mcf_lower = mcf_vals - 1.96 * se
    mcf_upper = mcf_vals + 1.96 * se

    return times, mcf_vals, mcf_lower, mcf_upper


def draw_survival(ax_km, ax_mcf, delays_cutoff, dandiset_created):
    """Draw both KM survival curve and MCF on two axes.

    ax_km: Kaplan-Meier (time to first reuse)
    ax_mcf: Mean Cumulative Function (expected reuse count per dandiset)

    Returns (kmf, km_df) for stats printing.
    """
    records, events_by_dandiset = _build_survival_data(delays_cutoff, dandiset_created)
    df = pd.DataFrame(records)
    n_total = len(df)
    n_events = int(df["observed"].sum())
    n_censored = n_total - n_events
    total_reuse_events = sum(len(v) for v in events_by_dandiset.values())

    # ── Kaplan-Meier (time to first reuse) ──
    kmf = KaplanMeierFitter()
    kmf.fit(
        durations=df["duration_months"],
        event_observed=df["observed"],
        label="Time to first DANDI-sourced reuse",
    )

    kmf.plot_survival_function(ax=ax_km, color="#2196F3", linewidth=2)
    ax_km.fill_between(
        kmf.survival_function_.index,
        kmf.confidence_interval_.iloc[:, 0],
        kmf.confidence_interval_.iloc[:, 1],
        alpha=0.15, color="#2196F3",
    )

    ax_km.set_xlabel("Months after dandiset creation")
    ax_km.set_ylabel("P(no reuse yet)")
    ax_km.set_title(
        f"Kaplan-Meier: Time to First Reuse",
        fontsize=10, fontweight="bold",
    )

    median_survival = kmf.median_survival_time_
    if not np.isinf(median_survival):
        ax_km.axhline(0.5, color="gray", linestyle=":", linewidth=1, alpha=0.5)
        ax_km.axvline(median_survival, color="gray", linestyle=":", linewidth=1, alpha=0.5)
        ax_km.annotate(
            f"Median: {median_survival:.0f} mo",
            xy=(median_survival, 0.5), xytext=(15, -20),
            textcoords="offset points", fontsize=9, color="#E53935",
            fontweight="bold", arrowprops=dict(arrowstyle="->", color="#E53935"),
        )

    km_lines = [
        f"n={n_total} dandisets",
        f"{n_events} with reuse, {n_censored} censored",
    ]
    if np.isinf(median_survival):
        km_lines.append("Median: not yet reached")
    for t, label in [(12, "1yr"), (24, "2yr"), (36, "3yr"), (48, "4yr"), (60, "5yr")]:
        try:
            pct = (1 - kmf.predict(t)) * 100
            km_lines.append(f"Reused by {label}: {pct:.0f}%")
        except Exception:
            pass

    ax_km.text(
        0.97, 0.97, "\n".join(km_lines), transform=ax_km.transAxes, fontsize=7,
        va="top", ha="right",
        bbox=dict(boxstyle="round,pad=0.3", facecolor="wheat", alpha=0.8),
    )
    ax_km.set_ylim(-0.02, 1.02)
    ax_km.grid(axis="both", alpha=0.2)
    ax_km.legend(loc="lower left", fontsize=8)

    # ── Mean Cumulative Function (recurrent events) ──
    times, mcf_vals, mcf_lower, mcf_upper = _compute_mcf(records, events_by_dandiset)

    ax_mcf.step(times, mcf_vals, where="post", color="#2196F3", linewidth=2,
                label="Mean cumulative reuse")
    ax_mcf.fill_between(times, mcf_lower, mcf_upper, step="post",
                        alpha=0.15, color="#2196F3")

    ax_mcf.set_xlabel("Months after dandiset creation")
    ax_mcf.set_ylabel("Expected reuse papers per dandiset")
    ax_mcf.set_title(
        "Mean Cumulative Function: Expected Reuse Count",
        fontsize=10, fontweight="bold",
    )
    ax_mcf.grid(axis="both", alpha=0.2)
    ax_mcf.legend(loc="upper left", fontsize=8)

    mcf_lines = [
        f"n={n_total} dandisets, {total_reuse_events} total reuse events",
        f"cutoff: {ANALYSIS_CUTOFF.strftime('%Y-%m-%d')}",
    ]
    for t, label in [(12, "1yr"), (24, "2yr"), (36, "3yr"), (48, "4yr"), (60, "5yr")]:
        idx = np.searchsorted(times, t, side="right") - 1
        if idx >= 0 and idx < len(mcf_vals):
            mcf_lines.append(f"E[reuse] by {label}: {mcf_vals[idx]:.2f}")

    ax_mcf.text(
        0.03, 0.97, "\n".join(mcf_lines), transform=ax_mcf.transAxes, fontsize=7,
        va="top", ha="left",
        bbox=dict(boxstyle="round,pad=0.3", facecolor="wheat", alpha=0.8),
    )

    return kmf, df


# ── Individual figure saving (for standalone PNGs) ──────────────────────


def draw_per_dandiset(ax, delays, dandiset_created):
    """Dot plot of delay per dandiset with unobserved time shown."""
    df = pd.DataFrame(delays)

    grouped = (
        df.groupby("dandiset_id")
        .agg(
            name=("dandiset_name", "first"),
            count=("delay_months", "size"),
            median=("delay_months", "median"),
        )
        .reset_index()
    )

    # Only show dandisets with 2+ reuse papers, sorted by dandiset ID
    grouped = grouped[grouped["count"] >= 2].sort_values("dandiset_id", ascending=False)

    labels = []
    for i, (_, row) in enumerate(grouped.iterrows()):
        did = row["dandiset_id"]
        name = row["name"][:45]
        subset = df[df["dandiset_id"] == did]["delay_months"]

        # Observed reuse points
        ax.scatter(
            subset, [i] * len(subset), color="#2196F3", alpha=0.7,
            s=80, marker="|", linewidths=1.5, zorder=3,
        )
        ax.plot(
            [subset.min(), subset.max()], [i, i],
            color="#90CAF9", linewidth=2, zorder=2,
        )
        ax.scatter(
            [row["median"]], [i], color="#E53935", s=100, marker="|",
            linewidths=2.5, zorder=4,
        )

        # Unobserved time: from dandiset creation to today
        if did in dandiset_created:
            total_months = (TODAY - dandiset_created[did]).days / 30.44
            last_observed = subset.max()
            if total_months > last_observed:
                ax.plot(
                    [last_observed, total_months], [i, i],
                    color="#BDBDBD", linewidth=2, linestyle=":", zorder=1,
                )
                ax.scatter(
                    [total_months], [i], color="#BDBDBD", s=20,
                    marker=">", zorder=1,
                )

        labels.append(f"{did} \u2014 {name} (n={row['count']:.0f})")

    ax.set_yticks(range(len(grouped)))
    ax.set_yticklabels(labels, fontsize=8)
    ax.set_xlabel("Months after dandiset creation")
    ax.set_title(
        "Reuse Delay per Dandiset (red=median, gray=time remaining until present)",
        fontsize=10, fontweight="bold",
    )
    ax.grid(axis="x", alpha=0.3)


def save_individual_plots(reuse_other_lab, reuse_same_lab, delays, delays_cutoff, dandiset_created):
    """Save each panel as a separate PNG."""
    for name, draw_fn, args, size in [
        ("source_archive_distribution", draw_source_archive, (reuse_other_lab, reuse_same_lab), (8, 5)),
        ("reuse_delay_histogram", draw_delay_histogram, (delays,), (10, 5)),
        ("reuse_by_year", draw_reuse_by_year, (delays,), (8, 5)),
        ("cumulative_reuse", draw_cumulative_reuse, (delays,), (10, 5)),
        ("per_dandiset_delay", draw_per_dandiset, (delays, dandiset_created), (10, 7)),
    ]:
        fig, ax = plt.subplots(figsize=size)
        draw_fn(ax, *args)
        fig.tight_layout()
        fig.savefig(FIGURES_DIR / f"{name}.png", dpi=150, bbox_inches="tight")
        plt.close(fig)
        print(f"  Saved {name}.png")

    # Survival analysis: two-panel figure (KM + MCF)
    fig, (ax_km, ax_mcf) = plt.subplots(1, 2, figsize=(16, 6))
    draw_survival(ax_km, ax_mcf, delays_cutoff, dandiset_created)
    fig.suptitle(
        f"Survival Analysis: DANDI Data Reuse (cutoff {ANALYSIS_CUTOFF.strftime('%Y-%m-%d')})",
        fontsize=12, fontweight="bold",
    )
    fig.tight_layout()
    fig.savefig(FIGURES_DIR / "survival_analysis.png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    print("  Saved survival_analysis.png")


# ── Combined multi-panel figure ─────────────────────────────────────────


def plot_combined_for_lab_type(
    reuse_subset, delays_subset, delays_cutoff_subset, dandiset_created,
    lab_label, filename,
):
    """Create a 6-panel combined figure for one lab type (same or different)."""
    dandi_frac = compute_dandi_fraction(reuse_subset)

    fig = plt.figure(figsize=(18, 17))
    gs = fig.add_gridspec(3, 2, height_ratios=[1, 1, 1], hspace=0.35, wspace=0.3)

    ax1 = fig.add_subplot(gs[0, 0])
    draw_source_archive(ax1, reuse_subset, dandi_frac=dandi_frac)

    ax2 = fig.add_subplot(gs[0, 1])
    draw_delay_histogram(ax2, delays_subset, dandi_frac=dandi_frac)

    ax3 = fig.add_subplot(gs[1, 0])
    draw_reuse_by_year(ax3, delays_subset, dandi_frac=dandi_frac)

    ax4 = fig.add_subplot(gs[1, 1])
    draw_cumulative_reuse(ax4, delays_subset, dandi_frac=dandi_frac)

    ax5 = fig.add_subplot(gs[2, 0])
    ax6 = fig.add_subplot(gs[2, 1])
    draw_survival(ax5, ax6, delays_cutoff_subset, dandiset_created)

    for label, ax in zip("ABCDEF", [ax1, ax2, ax3, ax4, ax5, ax6]):
        ax.text(
            -0.08, 1.08, label, transform=ax.transAxes,
            fontsize=16, fontweight="bold", va="top",
        )

    fig.suptitle(
        f"DANDI Archive Data Reuse Analysis — {lab_label}\n"
        f"Papers that explicitly accessed data from DANDI",
        fontsize=15, fontweight="bold", y=0.99,
    )

    fig.savefig(FIGURES_DIR / filename, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved {filename}")


# ── Summary stats ───────────────────────────────────────────────────────


def print_summary_stats(delays, reuse, reuse_other_lab, kmf, km_df):
    """Print summary statistics to stdout."""
    months = [d["delay_months"] for d in delays]
    n = len(months)

    print()
    print("=" * 70)
    print("DANDI DATA REUSE ANALYSIS \u2014 SUMMARY STATISTICS")
    print(f"(KM survival cutoff: {ANALYSIS_CUTOFF.strftime('%Y-%m-%d')})")
    print("=" * 70)

    print(f"\n--- Overall REUSE counts ---")
    print(f"  Total REUSE paper-dandiset pairs:       {len(reuse)}")
    print(
        f"  Same lab:                               "
        f"{sum(1 for c in reuse if c.get('same_lab') is True)}"
    )
    print(f"  Different lab:                          {len(reuse_other_lab)}")

    print(f"\n--- Source archive (other-lab REUSE) ---")
    archive_counts = Counter(
        c.get("source_archive", "unclear") for c in reuse_other_lab
    )
    for name, count in archive_counts.most_common():
        pct = 100 * count / len(reuse_other_lab)
        print(f"  {name:<30s} {count:4d}  ({pct:4.1f}%)")

    print(f"\n--- Delay analysis (DANDI-sourced, other-lab, n={n}) ---")
    print(f"  Unique papers:     {len(set(d['citing_doi'] for d in delays))}")
    print(f"  Unique dandisets:  {len(set(d['dandiset_id'] for d in delays))}")
    print(
        f"  Median delay:      {np.median(months):.1f} months "
        f"({np.median(months)/12:.1f} years)"
    )
    print(
        f"  Mean delay:        {np.mean(months):.1f} months "
        f"({np.mean(months)/12:.1f} years)"
    )
    print(f"  Std deviation:     {np.std(months, ddof=1):.1f} months")
    print(f"  Min:               {min(months):.1f} months")
    print(f"  Q1 (25th pctl):    {np.percentile(months, 25):.1f} months")
    print(f"  Q3 (75th pctl):    {np.percentile(months, 75):.1f} months")
    print(f"  Max:               {max(months):.1f} months")

    print(f"\n--- Reuse by publication year (DANDI-sourced, other-lab) ---")
    year_counts = Counter(d["pub_date"][:4] for d in delays)
    for y in sorted(year_counts):
        print(f"  {y}: {year_counts[y]}")

    print(f"\n--- Kaplan-Meier survival analysis (time to first reuse) ---")
    n_total = len(km_df)
    n_events = int(km_df["observed"].sum())
    n_censored = n_total - n_events
    print(f"  Dandisets analyzed:          {n_total}")
    print(f"  Events (reuse observed):     {n_events}")
    print(f"  Censored (no reuse yet):     {n_censored}")

    median_surv = kmf.median_survival_time_
    if not np.isinf(median_surv):
        print(
            f"  KM median time to reuse:     {median_surv:.0f} months "
            f"({median_surv/12:.1f} years)"
        )
    else:
        print("  KM median time to reuse:     not yet reached")

    print(f"\n  Probability of reuse by timepoint:")
    for t_months, t_label in [
        (12, "1 year"), (24, "2 years"), (36, "3 years"),
        (48, "4 years"), (60, "5 years"),
    ]:
        try:
            surv = kmf.predict(t_months)
            reuse_pct = (1 - surv) * 100
            print(f"    Within {t_label}: {reuse_pct:.1f}%")
        except Exception:
            pass


# ── Main ────────────────────────────────────────────────────────────────


def main():
    classifications, reuse, reuse_other_lab, reuse_same_lab, delays, dandiset_created = load_data()

    # Split delays by lab type
    other_delays = [d for d in delays if not d.get("same_lab")]
    same_delays = [d for d in delays if d.get("same_lab")]
    other_delays_cutoff = apply_cutoff(other_delays)
    same_delays_cutoff = apply_cutoff(same_delays)

    print(
        f"Delays: {len(delays)} total ({len(other_delays)} other-lab, "
        f"{len(same_delays)} same-lab)"
    )

    print("\nGenerating different-lab figure...")
    plot_combined_for_lab_type(
        reuse_other_lab, other_delays, other_delays_cutoff, dandiset_created,
        "Different Lab", "combined_different_lab.png",
    )

    print("\nGenerating same-lab figure...")
    plot_combined_for_lab_type(
        reuse_same_lab, same_delays, same_delays_cutoff, dandiset_created,
        "Same Lab", "combined_same_lab.png",
    )

    # Stats from other-lab survival analysis
    fig_tmp, (ax_tmp1, ax_tmp2) = plt.subplots(1, 2)
    kmf, km_df = draw_survival(ax_tmp1, ax_tmp2, other_delays_cutoff, dandiset_created)
    plt.close(fig_tmp)

    print_summary_stats(delays, reuse, reuse_other_lab, kmf, km_df)
    print(f"\nAll figures saved to {FIGURES_DIR}/")


if __name__ == "__main__":
    main()
