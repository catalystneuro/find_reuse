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

    # When data became publicly accessible:
    # Use embargoedUntil (actual unembargo date) if available, else dandiset_created
    dandiset_created = {}
    for r in dandi_data["results"]:
        did = r["dandiset_id"]
        accessible = r.get("data_accessible") or r.get("dandiset_created", "")
        if accessible:
            dandiset_created[did] = datetime.fromisoformat(
                accessible.replace("Z", "+00:00")
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
    """Compute estimated fraction of unclear papers that used DANDI Archive.

    Uses constrained estimation: NLB unclear → DANDI, Allen capped,
    remainder distributed proportionally among non-Allen archives.
    """
    unclear = [c for c in reuse_subset if c.get("source_archive") in ("unclear", None)]
    if not unclear:
        return 0.0
    estimates = estimate_unclear_per_archive(reuse_subset)
    dandi_est = estimates.get("DANDI Archive", 0)
    return dandi_est / len(unclear) if len(unclear) > 0 else 0.0


# Dandiset IDs known to be Allen Institute datasets
ALLEN_DANDISET_IDS = {
    "000020", "000039", "000049", "000108", "000253", "000336",
    "000340", "000569", "000570", "000635", "000711", "000768",
    "000769", "000871", "000934", "001046", "001245", "001351",
    "001359", "001464", "001475", "001625", "001626",
}

# Neural Latents Benchmark dandiset IDs — data only on DANDI
NLB_DANDISET_IDS = {
    "000128", "000129", "000127", "000130", "000138", "000139",
    "000140", "000688", "000950", "000954",
}


def estimate_unclear_per_archive(reuse_subset):
    """Estimate how unclear-source papers distribute across archives.

    Uses constrained estimation:
    - Allen Institute: capped at actual unclear count from Allen dandisets
    - Neural Latents Benchmark: assigned to DANDI (only available on DANDI)
    - Remaining: distributed proportionally among non-Allen archives

    Returns dict of {archive_name: estimated_additional_from_unclear}.
    """
    from collections import Counter

    unclear = [c for c in reuse_subset if c.get("source_archive") in ("unclear", None)]
    known = [c for c in reuse_subset if c.get("source_archive") not in ("unclear", None)]

    if not unclear or not known:
        return {}

    n_unclear = len(unclear)

    # Count unclear from Allen dandisets and NLB dandisets
    n_unclear_allen = sum(1 for c in unclear if c.get("dandiset_id") in ALLEN_DANDISET_IDS)
    n_unclear_nlb = sum(1 for c in unclear if c.get("dandiset_id") in NLB_DANDISET_IDS)
    n_unclear_other = n_unclear - n_unclear_allen - n_unclear_nlb

    # Known archive counts (excluding Allen Institute for proportional dist)
    known_counts = Counter(c.get("source_archive") for c in known)
    known_non_allen = {k: v for k, v in known_counts.items() if k != "Allen Institute"}
    total_known_non_allen = sum(known_non_allen.values())

    estimates = {}

    # Allen: capped at actual unclear from Allen dandisets
    estimates["Allen Institute"] = n_unclear_allen

    # NLB unclear → DANDI
    dandi_from_nlb = n_unclear_nlb

    # Remaining unclear distributed proportionally among non-Allen archives
    for archive, count in known_non_allen.items():
        frac = count / total_known_non_allen if total_known_non_allen > 0 else 0
        est = n_unclear_other * frac
        if archive == "DANDI Archive":
            est += dandi_from_nlb
        estimates[archive] = est

    return estimates


# ── Panel drawing functions (each takes an ax) ──────────────────────────


def draw_source_archive(ax, reuse_subset, dandi_frac=None):
    """Horizontal bar chart of source archive distribution.

    'unclear' gets orange coloring; all other archives get blue.
    Shows estimated DANDI contribution from unclear as a dashed outline on DANDI bar.
    """
    counts = Counter(c.get("source_archive", "unclear") for c in reuse_subset)
    threshold = 5
    main = {k: v for k, v in counts.items() if v >= threshold}
    other_count = sum(v for k, v in counts.items() if v < threshold)
    if other_count > 0:
        main["Other"] = other_count

    names = sorted(main, key=main.get, reverse=True)
    # Move "Other" to the bottom
    if "Other" in names:
        names.remove("Other")
        names.append("Other")
    values = [main[n] for n in names]

    y = list(range(len(names)))
    colors = []
    for n in names:
        if n == "unclear":
            colors.append("#F57C00")
        elif n == "DANDI Archive":
            colors.append("#2196F3")
        else:
            colors.append("#616161")
    bars = ax.barh(y, values, color=colors)

    # Add estimated share of unclear per archive (constrained estimation)
    if dandi_frac is not None and "unclear" in names:
        archive_estimates = estimate_unclear_per_archive(reuse_subset)
        for i, name in enumerate(names):
            if name in ("unclear", "Other") or name not in archive_estimates:
                continue
            estimated = archive_estimates[name]
            if estimated < 0.5:
                continue
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
    ax.grid(axis="x", alpha=0.3)

    from matplotlib.patches import Patch
    handles = [
        Patch(facecolor="#2196F3", label="DANDI Archive"),
        Patch(facecolor="#F57C00", label="Unclear"),
        Patch(facecolor="#616161", label="Other archive"),
    ]
    if dandi_frac is not None:
        handles.append(Patch(facecolor="none", edgecolor="#616161",
                             linestyle="--", label="Est. share of unclear"))
    ax.legend(handles=handles, fontsize=6, loc="lower right", frameon=False)

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

    # Median line using estimated DANDI distribution:
    # DANDI confirmed + dandi_frac fraction of unclear
    if groups[0] and dandi_frac is not None:
        # Weight unclear by repeating each value int(round(dandi_frac * N)) times
        # via bootstrap approximation: sample dandi_frac of unclear
        n_unclear_est = int(round(len(groups[1]) * dandi_frac))
        rng = np.random.default_rng(42)
        unclear_sample = list(rng.choice(groups[1], size=n_unclear_est, replace=False)) if n_unclear_est > 0 and groups[1] else []
        est_months = groups[0] + unclear_sample
        median = np.median(est_months)
        ax.axvline(median, color="#E53935", linestyle="--", linewidth=1.5)
    elif groups[0]:
        median = np.median(groups[0])
        ax.axvline(median, color="#E53935", linestyle="--", linewidth=1.5)

    stats_text = (
        f"DANDI: {len(groups[0])}, Unclear: {len(groups[1])}\n"
        f"Est. median: {median:.0f} mo"
    )
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

    # Shade years affected by incomplete data (within last 6 months)
    cutoff_year = ANALYSIS_CUTOFF.year
    for i, y in enumerate(all_years):
        if int(y) >= cutoff_year:
            ax.axvspan(i - 0.5, i + 0.5, alpha=0.15, color="gray", zorder=0)

    ax.set_xlabel("Publication year")
    ax.set_ylabel("Number of papers")
    ax.set_title("DANDI Reuse Papers by Year", fontsize=10, fontweight="bold")
    ax.set_xticks(range(len(all_years)))
    ax.set_xticklabels(all_years)
    ax.yaxis.set_major_locator(ticker.MaxNLocator(integer=True))
    ax.grid(axis="y", alpha=0.3)


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
    ax.set_ylim(bottom=0)
    ax.set_xlim(dates[0], dates[-1])
    ax.yaxis.set_major_locator(ticker.MultipleLocator(100))
    ax.grid(axis="y", alpha=0.3)

    # Shade recent 6 months as incomplete data
    ax.axvspan(pd.Timestamp(ANALYSIS_CUTOFF), dates[-1],
               alpha=0.15, color="gray", zorder=0)


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


def _compute_rate_function(records, events_by_dandiset, bandwidth=3.0):
    """Compute kernel-smoothed reuse rate function (derivative of MCF).

    The rate function gives the expected number of reuse events per dandiset
    per month at each time point. A Gaussian kernel with the given bandwidth
    (in months) is used for smoothing.

    Confidence bands use the variance of the kernel-smoothed Nelson-Aalen
    increments (1/n_at_risk^2 per event), which naturally grows when fewer
    dandisets are under observation.

    Returns (eval_times, rate, rate_lower, rate_upper) arrays.
    """
    all_events = []
    for did, times in events_by_dandiset.items():
        for t in times:
            all_events.append(t)
    all_events.sort()

    if not all_events:
        return np.array([0]), np.array([0]), np.array([0]), np.array([0])

    # Compute raw increments and variances
    event_times = []
    increments = []
    variances = []
    for t in all_events:
        n_at_risk = sum(1 for r in records if r["obs_months"] >= t)
        if n_at_risk == 0:
            break
        event_times.append(t)
        increments.append(1.0 / n_at_risk)
        variances.append(1.0 / (n_at_risk ** 2))

    event_times = np.array(event_times)
    increments = np.array(increments)
    variances = np.array(variances)

    # Evaluate on a regular grid up to the max observation time
    max_t = max(r["obs_months"] for r in records)
    eval_times = np.linspace(0, max_t, 200)

    # Boundary-corrected Gaussian kernel smoothing of rate and variance.
    # At each eval point, normalize the kernel by the portion that falls
    # within the observation window [0, max_t], preventing boundary bias.
    rate = np.zeros_like(eval_times)
    rate_var = np.zeros_like(eval_times)
    for t_e, inc, var in zip(event_times, increments, variances):
        raw_weights = np.exp(-0.5 * ((eval_times - t_e) / bandwidth) ** 2)
        # Compute normalization: fraction of kernel within [0, max_t]
        # for each eval point (accounts for truncation at boundaries)
        from scipy.stats import norm
        norm_factor = (norm.cdf((max_t - eval_times) / bandwidth)
                       - norm.cdf((0 - eval_times) / bandwidth))
        norm_factor = np.maximum(norm_factor, 1e-10)
        weights = raw_weights / (bandwidth * np.sqrt(2 * np.pi) * norm_factor)
        rate += inc * weights
        rate_var += var * weights ** 2

    rate_se = np.sqrt(rate_var)
    rate_lower = np.maximum(rate - 1.96 * rate_se, 0)
    rate_upper = rate + 1.96 * rate_se

    return eval_times, rate, rate_lower, rate_upper


def draw_survival(ax_km, ax_mcf, delays_cutoff, dandiset_created, dandi_frac=None):
    """Draw both KM survival curve and MCF on two axes.

    Shows three layers:
    - Blue solid: DANDI-only (confirmed)
    - Orange solid: DANDI + unclear (upper bound)
    - Blue dashed: estimated (DANDI + dandi_frac * unclear)

    Returns (kmf, km_df) for stats printing.
    """
    # Split into DANDI-confirmed and unclear
    dandi_delays = [d for d in delays_cutoff if d.get("source_archive") != "unclear"]
    all_delays = delays_cutoff  # includes both DANDI and unclear

    # Build survival data for both subsets
    records_dandi, events_dandi = _build_survival_data(dandi_delays, dandiset_created)
    records_all, events_all = _build_survival_data(all_delays, dandiset_created)

    df_dandi = pd.DataFrame(records_dandi)
    n_total = len(df_dandi)
    n_events_dandi = int(df_dandi["observed"].sum())

    # ── Kaplan-Meier ──
    # Fit KM for DANDI-only (blue)
    kmf_dandi = KaplanMeierFitter()
    kmf_dandi.fit(
        durations=df_dandi["duration_months"],
        event_observed=df_dandi["observed"],
        label="DANDI Archive",
    )

    # Fit KM for all (DANDI + unclear) = upper bound (orange)
    df_all = pd.DataFrame(records_all)
    kmf_all = KaplanMeierFitter()
    kmf_all.fit(
        durations=df_all["duration_months"],
        event_observed=df_all["observed"],
        label="DANDI + unclear",
    )

    # Plot as CDF: P(>=1 reuse) = 1 - survival
    # Upper bound (orange) - DANDI + unclear
    t_all = kmf_all.survival_function_.index.values
    cdf_all = 1 - kmf_all.survival_function_.values.ravel()
    ci_all_lo = 1 - kmf_all.confidence_interval_.iloc[:, 1].values  # flipped
    ci_all_hi = 1 - kmf_all.confidence_interval_.iloc[:, 0].values
    ax_km.step(t_all, cdf_all, where="post", color="#F57C00", linewidth=1.5)
    ax_km.fill_between(t_all, ci_all_lo, ci_all_hi, step="post", alpha=0.1, color="#F57C00")

    # DANDI-only (blue)
    t_dandi = kmf_dandi.survival_function_.index.values
    cdf_dandi = 1 - kmf_dandi.survival_function_.values.ravel()
    ci_d_lo = 1 - kmf_dandi.confidence_interval_.iloc[:, 1].values
    ci_d_hi = 1 - kmf_dandi.confidence_interval_.iloc[:, 0].values
    ax_km.step(t_dandi, cdf_dandi, where="post", color="#2196F3", linewidth=2)
    ax_km.fill_between(t_dandi, ci_d_lo, ci_d_hi, step="post", alpha=0.15, color="#2196F3")

    # Estimated CDF (dashed)
    if dandi_frac is not None:
        common_times = np.union1d(t_dandi, t_all)
        surv_dandi = kmf_dandi.predict(common_times)
        surv_all = kmf_all.predict(common_times)
        surv_est = surv_dandi - dandi_frac * (surv_dandi - surv_all)
        cdf_est = 1 - surv_est
        ax_km.step(common_times, cdf_est, where="post", color="#1565C0",
                   linestyle="--", linewidth=1.5)

    ax_km.set_xlabel("Months after dandiset creation")
    ax_km.set_ylabel("P(\u22651 reuse)")
    ax_km.set_title("Kaplan-Meier: Time to First Reuse", fontsize=10, fontweight="bold")

    km_lines = [f"n={n_total} dandisets"]
    for t, label in [(12, "1yr"), (24, "2yr"), (36, "3yr"), (48, "4yr"), (60, "5yr")]:
        try:
            pct_d = (1 - kmf_dandi.predict(t)) * 100
            pct_a = (1 - kmf_all.predict(t)) * 100
            km_lines.append(f"By {label}: {pct_d:.0f}%\u2013{pct_a:.0f}%")
        except Exception:
            pass

    ax_km.text(
        0.03, 0.97, "\n".join(km_lines), transform=ax_km.transAxes, fontsize=7,
        va="top", ha="left",
        bbox=dict(boxstyle="round,pad=0.3", facecolor="wheat", alpha=0.8),
    )
    ax_km.set_ylim(-0.02, 1.02)
    ax_km.margins(x=0)
    ax_km.grid(axis="both", alpha=0.2)

    # ── Mean Cumulative Function ──
    # MCF for DANDI-only (blue)
    times_d, mcf_d, mcf_d_lo, mcf_d_hi = _compute_mcf(records_dandi, events_dandi)
    # MCF for all (orange)
    times_a, mcf_a, mcf_a_lo, mcf_a_hi = _compute_mcf(records_all, events_all)

    # Plot upper bound (orange) first
    ax_mcf.step(times_a, mcf_a, where="post", color="#F57C00", linewidth=1.5)
    ax_mcf.fill_between(times_a, mcf_a_lo, mcf_a_hi, step="post",
                        alpha=0.1, color="#F57C00")

    # DANDI-only (blue)
    ax_mcf.step(times_d, mcf_d, where="post", color="#2196F3", linewidth=2)
    ax_mcf.fill_between(times_d, mcf_d_lo, mcf_d_hi, step="post",
                        alpha=0.15, color="#2196F3")

    # Estimated MCF (dashed)
    if dandi_frac is not None:
        common_t = np.union1d(times_d, times_a)
        mcf_d_interp = np.interp(common_t, times_d, mcf_d)
        mcf_a_interp = np.interp(common_t, times_a, mcf_a)
        mcf_est = mcf_d_interp + dandi_frac * (mcf_a_interp - mcf_d_interp)
        ax_mcf.step(common_t, mcf_est, where="post", color="#1565C0",
                    linestyle="--", linewidth=1.5)

    ax_mcf.set_xlabel("Months after dandiset creation")
    ax_mcf.set_ylabel("Expected reuse papers per dandiset")
    ax_mcf.set_title("MCF: Expected Reuse Count", fontsize=10, fontweight="bold")
    ax_mcf.margins(x=0)
    ax_mcf.grid(axis="both", alpha=0.2)

    return kmf_dandi, df_dandi


def _draw_mcf_only(ax, delays_cutoff, dandiset_created, dandi_frac=None):
    """Draw just the MCF panel."""
    dandi_delays = [d for d in delays_cutoff if d.get("source_archive") != "unclear"]
    all_delays = delays_cutoff

    records_dandi, events_dandi = _build_survival_data(dandi_delays, dandiset_created)
    records_all, events_all = _build_survival_data(all_delays, dandiset_created)

    times_d, mcf_d, mcf_d_lo, mcf_d_hi = _compute_mcf(records_dandi, events_dandi)
    times_a, mcf_a, mcf_a_lo, mcf_a_hi = _compute_mcf(records_all, events_all)

    ax.step(times_a / 12, mcf_a, where="post", color="#F57C00", linewidth=1.5)
    ax.fill_between(times_a / 12, mcf_a_lo, mcf_a_hi, step="post", alpha=0.1, color="#F57C00")

    ax.step(times_d / 12, mcf_d, where="post", color="#2196F3", linewidth=2)
    ax.fill_between(times_d / 12, mcf_d_lo, mcf_d_hi, step="post", alpha=0.15, color="#2196F3")

    if dandi_frac is not None:
        common_t = np.union1d(times_d, times_a)
        mcf_d_interp = np.interp(common_t, times_d, mcf_d)
        mcf_a_interp = np.interp(common_t, times_a, mcf_a)
        mcf_est = mcf_d_interp + dandi_frac * (mcf_a_interp - mcf_d_interp)
        ax.step(common_t / 12, mcf_est, where="post", color="#1565C0",
                linestyle="--", linewidth=1.5)

    ax.set_xlabel("Years after dandiset creation")
    ax.set_ylabel("Expected reuse papers per dandiset")
    ax.set_title("MCF: Expected Reuse Count", fontsize=10, fontweight="bold")
    ax.margins(x=0)
    ax.grid(axis="both", alpha=0.2)


def draw_rate_function(ax, delays_cutoff, dandiset_created, dandi_frac=None, bin_width=12):
    """Draw binned reuse rate (events / dandiset-months at risk)."""
    dandi_delays = [d for d in delays_cutoff if d.get("source_archive") != "unclear"]
    unclear_delays = [d for d in delays_cutoff if d.get("source_archive") == "unclear"]
    all_delays = delays_cutoff

    # Build records (all dandisets, for exposure calculation)
    records = []
    for did, created in dandiset_created.items():
        if created >= ANALYSIS_CUTOFF:
            continue
        obs = (ANALYSIS_CUTOFF - created).days / 30.44
        records.append({"dandiset_id": did, "obs_months": obs})

    max_t = max(r["obs_months"] for r in records)
    bins = np.arange(0, max_t + bin_width, bin_width)

    # Exposure per bin (dandiset-months at risk)
    risk_months = np.zeros(len(bins) - 1)
    for i in range(len(bins) - 1):
        for r in records:
            overlap = min(bins[i + 1], r["obs_months"]) - bins[i]
            if overlap > 0:
                risk_months[i] += overlap

    def _bin_rate(delay_list):
        event_times = []
        for d in delay_list:
            did = d["dandiset_id"]
            if did in dandiset_created:
                t = (datetime.strptime(d["pub_date"], "%Y-%m-%d") - dandiset_created[did]).days / 30.44
                event_times.append(t)
        counts, _ = np.histogram(event_times, bins=bins)
        return np.where(risk_months > 0, counts / risk_months, 0)

    rate_dandi = _bin_rate(dandi_delays)
    rate_all = _bin_rate(all_delays)
    rate_unclear = rate_all - rate_dandi

    # Omit last bin (too few dandisets at risk)
    n_bins = len(bins) - 2  # skip last bin
    x = bins[:n_bins]
    w = bin_width * 0.9
    centers = x + bin_width / 2

    rate_dandi = rate_dandi[:n_bins] * 12  # convert to per-year
    rate_all = rate_all[:n_bins] * 12
    rate_unclear = rate_all - rate_dandi
    risk_months_trimmed = risk_months[:n_bins]

    # Stacked bars: DANDI (blue) + unclear (orange)
    ax.bar(x, rate_dandi, width=w, align="edge", color="#2196F3")
    ax.bar(x, rate_unclear, width=w, align="edge", bottom=rate_dandi, color="#F57C00")

    # Poisson CIs around estimated DANDI rate
    from scipy.stats import chi2
    if dandi_frac is not None:
        # Event counts per bin
        all_event_times = [
            (datetime.strptime(d["pub_date"], "%Y-%m-%d") - dandiset_created[d["dandiset_id"]]).days / 30.44
            for d in all_delays if d["dandiset_id"] in dandiset_created
        ]
        dandi_event_times = [
            (datetime.strptime(d["pub_date"], "%Y-%m-%d") - dandiset_created[d["dandiset_id"]]).days / 30.44
            for d in dandi_delays if d["dandiset_id"] in dandiset_created
        ]
        unclear_event_times = [
            (datetime.strptime(d["pub_date"], "%Y-%m-%d") - dandiset_created[d["dandiset_id"]]).days / 30.44
            for d in unclear_delays if d["dandiset_id"] in dandiset_created
        ]

        counts_dandi, _ = np.histogram(dandi_event_times, bins=bins)
        counts_unclear, _ = np.histogram(unclear_event_times, bins=bins)
        counts_dandi = counts_dandi[:n_bins]
        counts_unclear = counts_unclear[:n_bins]

        # Estimated count = dandi_count + dandi_frac * unclear_count
        # Use exact Poisson CIs on each component, then combine
        # For Poisson count k: CI_lo = chi2(0.025, 2k)/2, CI_hi = chi2(0.975, 2(k+1))/2
        est_count = counts_dandi + dandi_frac * counts_unclear

        ci_lo_dandi = np.where(counts_dandi > 0, chi2.ppf(0.025, 2 * counts_dandi) / 2, 0)
        ci_hi_dandi = chi2.ppf(0.975, 2 * (counts_dandi + 1)) / 2
        ci_lo_unclear = np.where(counts_unclear > 0, chi2.ppf(0.025, 2 * counts_unclear) / 2, 0)
        ci_hi_unclear = chi2.ppf(0.975, 2 * (counts_unclear + 1)) / 2

        # Combine: est = dandi + frac*unclear, propagate Poisson CIs
        ci_lo_count = ci_lo_dandi + dandi_frac * ci_lo_unclear
        ci_hi_count = ci_hi_dandi + dandi_frac * ci_hi_unclear

        rate_est = np.where(risk_months_trimmed > 0, est_count / risk_months_trimmed * 12, 0)
        ci_lo_rate = np.where(risk_months_trimmed > 0, ci_lo_count / risk_months_trimmed * 12, 0)
        ci_hi_rate = np.where(risk_months_trimmed > 0, ci_hi_count / risk_months_trimmed * 12, 0)

        # Draw estimate dashed lines and error bars
        for i in range(n_bins):
            ax.plot(
                [x[i], x[i] + w], [rate_est[i], rate_est[i]],
                color="#1565C0", linestyle="--", linewidth=1.5,
            )
        ax.errorbar(
            centers, rate_est,
            yerr=[rate_est - ci_lo_rate, ci_hi_rate - rate_est],
            fmt="none", ecolor="#1565C0", elinewidth=1, capsize=3, capthick=1,
        )

    ax.set_xlabel("Years after dandiset creation")
    ax.set_ylabel("Reuse rate (events/dandiset/yr)")
    ax.set_title("Reuse Rate", fontsize=10, fontweight="bold")
    ax.set_ylim(bottom=0)
    ax.set_xticks([12, 24, 36, 48, 60])
    ax.set_xticklabels(["1", "2", "3", "4", "5"])
    ax.grid(axis="y", alpha=0.3)


# ── Individual figure saving (for standalone PNGs) ──────────────────────


def _normalize_journal(name):
    """Normalize journal name variants."""
    name = name.strip()
    if 'biorxiv' in name.lower() or 'cold spring harbor' in name.lower():
        return 'bioRxiv'
    if 'arxiv' in name.lower() and 'cornell' in name.lower():
        return 'arXiv'
    if 'research square' in name.lower():
        return 'Research Square'
    if 'medrxiv' in name.lower():
        return 'medRxiv'
    if 'ssrn' in name.lower():
        return 'SSRN'
    return name


PREPRINT_SERVERS = {'bioRxiv', 'arXiv', 'Research Square', 'medRxiv', 'SSRN'}

JOURNAL_ABBREVIATIONS = {
    'Proceedings of the National Academy of Sciences': 'PNAS',
    'PLoS Computational Biology': 'PLoS Comp. Biol.',
    'Nature Communications': 'Nat. Commun.',
    'Nature Neuroscience': 'Nat. Neurosci.',
    'Nature Methods': 'Nat. Methods',
    'Nature Computational Science': 'Nat. Comput. Sci.',
    'Cell Reports': 'Cell Rep.',
    'Journal of Neuroscience': 'J. Neurosci.',
    'Frontiers in Computational Neuroscience': 'Front. Comput. Neurosci.',
    'Scientific Reports': 'Sci. Rep.',
    'Scientific Data': 'Sci. Data',
    'Journal of Open Source Education': 'JOSE',
    'Frontiers in Human Neuroscience': 'Front. Hum. Neurosci.',
    'Journal of Neural Engineering': 'J. Neural Eng.',
    'Journal of Neuroscience Methods': 'J. Neurosci. Methods',
    'Current Opinion in Neurobiology': 'Curr. Opin. Neurobiol.',
    'Communications Biology': 'Commun. Biol.',
    'PLoS ONE': 'PLoS ONE',
    'Research Square': 'Res. Sq.',
}


def draw_journals(ax, reuse_subset, dandi_frac=None):
    """Horizontal bar chart of top journals, stacked DANDI vs unclear with estimate."""
    subset = [c for c in reuse_subset if c.get('citing_journal')]
    dandi_sub = [c for c in subset if c.get('source_archive') != 'unclear']
    unclear_sub = [c for c in subset if c.get('source_archive') == 'unclear']

    dandi_journals = Counter(_normalize_journal(c['citing_journal']) for c in dandi_sub)
    unclear_journals = Counter(_normalize_journal(c['citing_journal']) for c in unclear_sub)

    # Rank by total
    all_journals = Counter()
    for j in [dandi_journals, unclear_journals]:
        all_journals.update(j)

    top = all_journals.most_common(15)
    names_raw = [n for n, _ in reversed(top)]
    names = [JOURNAL_ABBREVIATIONS.get(n, n) for n in names_raw]

    dandi_vals = [dandi_journals[n] for n in names_raw]
    unclear_vals = [unclear_journals[n] for n in names_raw]

    y = range(len(names))
    ax.barh(y, dandi_vals, color="#2196F3")
    ax.barh(y, unclear_vals, left=dandi_vals, color="#F57C00")

    # Dashed estimate rectangles
    if dandi_frac is not None:
        for i, name in enumerate(names):
            est = dandi_vals[i] + dandi_frac * unclear_vals[i]
            if unclear_vals[i] > 0:
                ax.plot(
                    [est, est], [i - 0.4, i + 0.4],
                    color="#1565C0", linestyle="--", linewidth=1.5,
                )

    ax.set_yticks(y)
    ax.set_yticklabels(names, fontsize=8)
    ax.set_xlabel("Number of papers")
    ax.set_title("Top Journals (REUSE papers)", fontsize=10, fontweight="bold")
    ax.grid(axis="x", alpha=0.3)

    for i in range(len(names)):
        total = dandi_vals[i] + unclear_vals[i]
        ax.text(total + 0.3, i, str(total), va="center", fontsize=7)


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

    # Panels C-F use only DANDI+unclear delays (matching the blue/orange stacking)
    dandi_unclear_delays = [
        d for d in delays_subset
        if d.get("source_archive") in ("DANDI Archive", "unclear", None)
    ]
    dandi_unclear_cutoff = [
        d for d in delays_cutoff_subset
        if d.get("source_archive") in ("DANDI Archive", "unclear", None)
    ]

    fig = plt.figure(figsize=(11.5, 13))
    gs = fig.add_gridspec(
        3, 2,
        height_ratios=[0.8, 1, 1],
        width_ratios=[1, 1],
        hspace=0.35, wspace=0.35,
    )

    ax1 = fig.add_subplot(gs[0, 0])
    draw_source_archive(ax1, reuse_subset, dandi_frac=dandi_frac)

    ax2 = fig.add_subplot(gs[0, 1])
    draw_journals(ax2, reuse_subset, dandi_frac=dandi_frac)

    ax3 = fig.add_subplot(gs[1, 0])
    draw_cumulative_reuse(ax3, dandi_unclear_delays, dandi_frac=dandi_frac)

    ax4 = fig.add_subplot(gs[1, 1])
    draw_reuse_by_year(ax4, dandi_unclear_delays, dandi_frac=dandi_frac)

    ax5 = fig.add_subplot(gs[2, 0])
    _draw_mcf_only(ax5, dandi_unclear_cutoff, dandiset_created, dandi_frac=dandi_frac)

    ax6 = fig.add_subplot(gs[2, 1])
    draw_rate_function(ax6, dandi_unclear_cutoff, dandiset_created, dandi_frac=dandi_frac)

    axes = [ax1, ax2, ax3, ax4, ax5, ax6]
    for label, ax in zip("ABCDEF", axes):
        ax.text(
            -0.08, 1.08, label, transform=ax.transAxes,
            fontsize=16, fontweight="bold", va="top",
        )
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)

    fig.suptitle(
        f"DANDI Archive Data Reuse Analysis — {lab_label}\n"
        f"Papers that explicitly accessed data from DANDI",
        fontsize=15, fontweight="bold", y=0.93,
    )

    fig.tight_layout(rect=[0, 0, 1, 0.92])
    fig.savefig(FIGURES_DIR / filename, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved {filename}")

    # Separate delay figure (histogram + rate)
    delay_filename = filename.replace("combined_", "delay_")
    fig_d, (ax_hist, ax_rate) = plt.subplots(1, 2, figsize=(12, 5))
    draw_delay_histogram(ax_hist, delays_subset, dandi_frac=dandi_frac)
    draw_rate_function(ax_rate, delays_cutoff_subset, dandiset_created, dandi_frac=dandi_frac)
    for label, ax in zip("AB", [ax_hist, ax_rate]):
        ax.text(-0.08, 1.08, label, transform=ax.transAxes,
                fontsize=16, fontweight="bold", va="top")
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
    fig_d.suptitle(
        f"Reuse Delay Analysis — {lab_label}",
        fontsize=13, fontweight="bold",
    )
    fig_d.tight_layout()
    fig_d.savefig(FIGURES_DIR / delay_filename, dpi=150, bbox_inches="tight")
    plt.close(fig_d)
    print(f"  Saved {delay_filename}")

    # Separate survival figure (KM + MCF)
    surv_filename = filename.replace("combined_", "survival_")
    fig_s, (ax_km, ax_mcf) = plt.subplots(1, 2, figsize=(12, 5))
    draw_survival(ax_km, ax_mcf, delays_cutoff_subset, dandiset_created, dandi_frac=dandi_frac)
    for label, ax in zip("AB", [ax_km, ax_mcf]):
        ax.text(-0.08, 1.08, label, transform=ax.transAxes,
                fontsize=16, fontweight="bold", va="top")
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
    fig_s.suptitle(
        f"Survival Analysis — {lab_label}",
        fontsize=13, fontweight="bold",
    )
    fig_s.tight_layout()
    fig_s.savefig(FIGURES_DIR / surv_filename, dpi=150, bbox_inches="tight")
    plt.close(fig_s)
    print(f"  Saved {surv_filename}")


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

    print("\nGenerating combined (all labs) figure...")
    all_delays_cutoff = apply_cutoff(delays)
    plot_combined_for_lab_type(
        reuse, delays, all_delays_cutoff, dandiset_created,
        "All Labs", "combined_all_labs.png",
    )

    # Stats from other-lab survival analysis
    fig_tmp, (ax_tmp1, ax_tmp2) = plt.subplots(1, 2)
    dandi_frac_other = compute_dandi_fraction(reuse_other_lab)
    kmf, km_df = draw_survival(ax_tmp1, ax_tmp2, other_delays_cutoff, dandiset_created, dandi_frac=dandi_frac_other)
    plt.close(fig_tmp)

    print_summary_stats(delays, reuse, reuse_other_lab, kmf, km_df)
    print(f"\nAll figures saved to {FIGURES_DIR}/")


if __name__ == "__main__":
    main()
