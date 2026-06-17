#!/usr/bin/env python3
"""
reuse_distribution.py — Shared reuse count distribution plot for any archive.

Shows histograms of reuse counts per dataset at different observation windows,
with stacked bars for complete vs incomplete (right-censored) datasets.

Usage:
    from src.analysis.reuse_distribution import plot_reuse_distribution
    plot_reuse_distribution(reuse, created, output_path, archive_name="CRCNS")
"""

import matplotlib
matplotlib.rcParams["font.family"] = "Helvetica"

from collections import Counter
from datetime import datetime
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from scipy import stats


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
            if c.get("dandiset_id", c.get("dataset_id")) != did:
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


def plot_nb_r_comparison(archives_data, output_path, window_years=5, n_boot=2000):
    """Plot bootstrap CIs for NB r parameter across archives.

    Args:
        archives_data: list of (name, counts_array) tuples
        output_path: Path to save figure
        window_years: observation window used
        n_boot: number of bootstrap resamples
    """
    fig, (ax_mu, ax_r) = plt.subplots(2, 1, figsize=(4.5, 4.0), sharex=False,
                                       gridspec_kw={"height_ratios": [1, 1], "hspace": 0.7})

    rng = np.random.default_rng(42)
    names = []
    r_vals = []
    r_ci_los = []
    r_ci_his = []
    mu_vals = []
    mu_ci_los = []
    mu_ci_his = []

    for name, counts in archives_data:
        if len(counts) < 5 or counts.var() <= counts.mean():
            continue

        r_hat = counts.mean()**2 / (counts.var() - counts.mean())
        mu_hat = counts.mean()

        # Bootstrap
        boot_r = []
        boot_mu = []
        for _ in range(n_boot):
            sample = rng.choice(counts, size=len(counts), replace=True)
            if sample.var() > sample.mean() > 0:
                boot_r.append(sample.mean()**2 / (sample.var() - sample.mean()))
                boot_mu.append(sample.mean())
        boot_r = np.array(boot_r)
        boot_mu = np.array(boot_mu)

        names.append(name)
        r_vals.append(r_hat)
        r_ci_los.append(np.percentile(boot_r, 2.5))
        r_ci_his.append(np.percentile(boot_r, 97.5))
        mu_vals.append(mu_hat)
        mu_ci_los.append(np.percentile(boot_mu, 2.5))
        mu_ci_his.append(np.percentile(boot_mu, 97.5))

    y_pos = np.arange(len(names))
    r_vals = np.array(r_vals)
    r_ci_los = np.array(r_ci_los)
    r_ci_his = np.array(r_ci_his)
    mu_vals = np.array(mu_vals)
    mu_ci_los = np.array(mu_ci_los)
    mu_ci_his = np.array(mu_ci_his)

    # Panel A: mu
    ax_mu.errorbar(mu_vals, y_pos, xerr=[mu_vals - mu_ci_los, mu_ci_his - mu_vals],
                   fmt="o", color="black", markersize=7, capsize=5, linewidth=1.5)
    ax_mu.set_yticks(y_pos)
    ax_mu.set_yticklabels(names)
    ax_mu.set_xlabel("Mean reuse count (μ)")
    ax_mu.set_title(f"A. Mean (μ)", fontweight="bold")
    ax_mu.spines["top"].set_visible(False)
    ax_mu.spines["right"].set_visible(False)

    # Panel B: r
    ax_r.errorbar(r_vals, y_pos, xerr=[r_vals - r_ci_los, r_ci_his - r_vals],
                  fmt="o", color="black", markersize=7, capsize=5, linewidth=1.5)
    ax_r.set_yticks(y_pos)
    ax_r.set_yticklabels(names)
    ax_r.set_xlabel("Dispersion parameter (r)")
    ax_r.set_title(f"B. Dispersion (r)", fontweight="bold")
    ax_r.spines["top"].set_visible(False)
    ax_r.spines["right"].set_visible(False)

    fig.suptitle(f"NB Parameters by Archive ({window_years}-year window)",
                 fontweight="bold", fontsize=12)
    fig.tight_layout()
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=300, bbox_inches="tight")
    plt.close()
    print(f"Saved {output_path}")


def plot_reuse_combined(reuse, created, output_path, archive_name="Archive",
                        analysis_cutoff=None, window_years=5,
                        inset_ranges=None, label_rotation=90,
                        pie_position=None):
    """Two-panel figure: histogram (top) + CCDF (bottom), shared x-axis.

    Top: linear histogram with optional inset zoom and pie chart.
    Bottom: CCDF on log-log with NB fit.
    """
    if analysis_cutoff is None:
        analysis_cutoff = datetime(2025, 10, 7)

    complete, incomplete = _count_reuse_per_dataset(
        reuse, created, analysis_cutoff, window_years)
    counts_c_vals = [n for _, n in complete]
    counts_i_vals = [n for _, n in incomplete]
    all_vals = counts_c_vals + counts_i_vals

    max_count = max(counts_c_vals) if counts_c_vals else 1
    bins = np.arange(0, max_count + 2)
    hist_c, _ = np.histogram(counts_c_vals, bins=bins)

    fig, (ax_hist, ax_ccdf) = plt.subplots(2, 1, figsize=(4.4, 5.6), sharex=True,
                                            gridspec_kw={"height_ratios": [1, 1]})

    # === Top: Histogram (complete datasets only) ===
    ax_hist.bar(bins[:-1], hist_c, width=0.8, color="black", alpha=0.8)
    ax_hist.set_xlim(left=-0.5)
    ax_hist.set_ylabel("Number of datasets")
    ax_hist.set_title("A. Histogram", fontweight="bold")
    ax_hist.spines["top"].set_visible(False)
    ax_hist.spines["right"].set_visible(False)

    # Stats
    if counts_c_vals:
        median = np.median(counts_c_vals)
        mean_val = np.mean(counts_c_vals)
        pct_zero = sum(1 for c in counts_c_vals if c == 0) / len(counts_c_vals) * 100
        ax_hist.text(0.95, 0.95, f"med={median:.0f}\nmean={mean_val:.1f}\n{pct_zero:.0f}% zero",
                     transform=ax_hist.transAxes, ha="right", va="top", fontsize=8)

    # Pie chart
    n_complete = len(counts_c_vals)
    n_incomplete = len(counts_i_vals)
    if n_incomplete > 0:
        _pie_pos = pie_position or [0.40, 0.40, 0.20, 0.35]
        pie_ax = ax_hist.inset_axes(_pie_pos, zorder=10)
        pie_ax.pie([n_complete, n_incomplete], colors=["black", "#BBBBBB"],
                   startangle=90, counterclock=False)
        pie_ax.text(0, -1.3, f"{n_complete} complete", fontsize=7, ha="center", color="black")
        pie_ax.text(0, 1.3, f"{n_incomplete} incomplete", fontsize=7, ha="center", color="#888")
        pie_ax.set_zorder(10)

    # Top dataset annotations (use windowed counts, not all-observed)
    top_datasets = sorted(complete, key=lambda x: -x[1])[:5]
    by_count = {}
    for did, n in top_datasets:
        by_count.setdefault(n, []).append(did)
    for n, dids in by_count.items():
        if n < len(hist_c):
            bar_h = hist_c[n]
        else:
            bar_h = 0
        label = ", ".join(str(d) for d in dids)
        y_pad = ax_hist.get_ylim()[1] * 0.03
        ax_hist.text(n, bar_h + y_pad, label, ha="center", va="bottom", fontsize=6,
                     color="#333", rotation=label_rotation)

    # === Bottom: CCDF ===
    counts = np.array(counts_c_vals)
    if len(counts) > 0:
        n_total = len(counts)
        max_k = int(counts.max())
        k_vals = np.arange(0, max_k + 1)
        ccdf = np.array([np.sum(counts >= k) / n_total for k in k_vals])
        ax_ccdf.step(k_vals, ccdf, where="post", color="black", linewidth=1.5,
                     label=f"{window_years}yr (n={n_total})")
        ax_ccdf.scatter(k_vals, ccdf, color="black", s=15, zorder=3)

        # NB fit
        if len(counts) > 5 and counts.var() > counts.mean() > 0:
            r_fit = counts.mean()**2 / (counts.var() - counts.mean())
            p_fit = counts.mean() / counts.var()
            ccdf_nb = np.array([1 - stats.nbinom.cdf(k - 1, r_fit, p_fit) if k > 0
                                else 1.0 for k in k_vals])
            ax_ccdf.plot(k_vals, ccdf_nb, "--", color="#E53935", linewidth=1.5, alpha=0.7,
                         label=f"NB fit (r={r_fit:.2f})")

    ax_ccdf.set_yscale("log")

    ax_ccdf.set_xlabel("Reuse count per dataset")
    ax_ccdf.set_ylabel("P(X ≥ k)")
    ax_ccdf.set_title("B. Complementary CDF", fontweight="bold")
    ax_ccdf.legend(fontsize=8, frameon=False)
    ax_ccdf.spines["top"].set_visible(False)
    ax_ccdf.spines["right"].set_visible(False)

    fig.suptitle(f"{archive_name} Reuse Count Distribution ({window_years}-year window)",
                 fontweight="bold", fontsize=13)
    fig.tight_layout()
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=300, bbox_inches="tight")
    plt.close()
    print(f"Saved {output_path}")

    _print_nb_stats(reuse, created, output_path, archive_name, analysis_cutoff, (window_years,))


def plot_reuse_ccdf(reuse, created, output_path, archive_name="Archive",
                    analysis_cutoff=None, windows=(5,),
                    fit_window=5):
    """Plot complementary CDF: P(X >= k) vs k on log-log axes.

    Single axes with multiple observation windows overlaid.
    NB fit shown for fit_window only.

    Args:
        windows: tuple of window years to plot
        fit_window: which window to show NB fit for
    """
    if analysis_cutoff is None:
        analysis_cutoff = datetime(2025, 10, 7)

    fig, ax = plt.subplots(figsize=(4.5, 4.2))

    import matplotlib.cm as cm
    colors = cm.viridis(np.linspace(0.15, 0.85, len(windows)))

    max_k = 0
    fit_counts = None

    for i, window_years in enumerate(windows):
        complete, _ = _count_reuse_per_dataset(
            reuse, created, analysis_cutoff, window_years)
        counts = np.array([n for _, n in complete])

        if len(counts) == 0:
            continue

        n_total = len(counts)
        max_k_w = int(counts.max())
        k_vals = np.arange(0, max_k_w + 1)
        ccdf = np.array([np.sum(counts >= k) / n_total for k in k_vals])

        lw = 2.0 if window_years == fit_window else 1.2
        ax.step(k_vals, ccdf, where="post", color=colors[i], linewidth=lw,
                label=f"{window_years}yr (n={n_total})")
        max_k = max(max_k, max_k_w)

        if window_years == fit_window:
            fit_counts = counts

    # NB fit for fit_window
    if fit_counts is not None and len(fit_counts) > 5 and fit_counts.var() > fit_counts.mean() > 0:
        r_fit = fit_counts.mean()**2 / (fit_counts.var() - fit_counts.mean())
        p_fit = fit_counts.mean() / fit_counts.var()
        max_k_fit = int(fit_counts.max())
        k_fit = np.arange(0, max_k_fit + 1)
        ccdf_nb = np.array([1 - stats.nbinom.cdf(k - 1, r_fit, p_fit) if k > 0
                            else 1.0 for k in k_fit])
        ax.plot(k_fit, ccdf_nb, "--", color="#E53935", linewidth=1.5, alpha=0.7,
                label=f"NB fit (r={r_fit:.2f})")

    ax.set_xscale("log")
    ax.set_yscale("log")

    from matplotlib.ticker import FuncFormatter, FixedLocator
    def _int_fmt(x, _):
        if x <= 0 or abs(x - round(x)) > 0.01:
            return ""
        return f"{int(round(x)):d}"

    x_ticks = [v for v in [1, 2, 5, 10, 20, 50, 100, 200, 500] if v <= max_k * 1.2]
    if not x_ticks:
        x_ticks = [1]
    ax.xaxis.set_major_locator(FixedLocator(x_ticks))
    ax.xaxis.set_major_formatter(FuncFormatter(_int_fmt))
    ax.xaxis.set_minor_formatter(FuncFormatter(lambda x, _: ""))

    ax.set_xlabel("Reuse count k")
    ax.set_ylabel("P(X ≥ k)")
    ax.set_title(f"{archive_name} Reuse Count CCDF", fontweight="bold")
    ax.legend(fontsize=8, frameon=False)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)

    fig.tight_layout()
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=300, bbox_inches="tight")
    plt.close()
    print(f"Saved {output_path}")

    _print_nb_stats(reuse, created, output_path, archive_name, analysis_cutoff, windows)


def plot_reuse_distribution(reuse, created, output_path, archive_name="Archive",
                            analysis_cutoff=None, windows=(5, None),
                            top_n_annotate=4, loglog=False, logy=False,
                            logy_min=0.01, label_rotation=90, broken_y=False,
                            broken_y_ranges=None, inset_ranges=None):
    """Generate reuse count distribution figure.

    Args:
        reuse: list of REUSE classification dicts
        created: dict {dataset_id: datetime}
        output_path: Path to save figure
        archive_name: for title
        analysis_cutoff: datetime cutoff
        windows: tuple of observation windows in years (None = all observed)
        top_n_annotate: number of top datasets to annotate on last panel
        broken_y: if True, use broken y-axis to show zero bar and tail
    """
    if analysis_cutoff is None:
        analysis_cutoff = datetime(2025, 10, 7)

    # Dispatch to broken-y version if requested
    if broken_y and not loglog and not logy:
        return _plot_reuse_distribution_broken_y(
            reuse, created, output_path, archive_name, analysis_cutoff,
            windows, top_n_annotate, label_rotation, broken_y_ranges)

    n_panels = len(windows)
    fig, axes = plt.subplots(1, n_panels, figsize=(4.2 * n_panels, 4.2), sharey=True)
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

        if loglog:
            # Log-log: plot non-zero counts only, as scatter
            all_vals = counts_c_vals + counts_i_vals
            nonzero = [v for v in all_vals if v > 0]
            if nonzero:
                count_freq = Counter(nonzero)
                x_pts = sorted(count_freq.keys())
                y_pts = [count_freq[x] for x in x_pts]
                ax.scatter(x_pts, y_pts, color="black", s=30, zorder=3)

                # NB fit overlay
                c_nonzero = [v for v in counts_c_vals if v > 0]
                c_arr = np.array(counts_c_vals)
                if len(c_arr) > 5 and c_arr.var() > c_arr.mean() > 0:
                    r_fit = c_arr.mean()**2 / (c_arr.var() - c_arr.mean())
                    p_fit = c_arr.mean() / c_arr.var()
                    x_fit = np.arange(1, max_count + 1)
                    y_fit = len(counts_c_vals) * stats.nbinom.pmf(x_fit, r_fit, p_fit)
                    ax.plot(x_fit, y_fit, "--", color="#E53935", alpha=0.7,
                            linewidth=1.5, label=f"NB fit (r={r_fit:.2f})", zorder=5)
                    ax.legend(fontsize=7, frameon=False)

                ax.set_xscale("log")
                ax.set_yscale("log")
                from matplotlib.ticker import FuncFormatter, FixedLocator
                def _int_fmt(x, _):
                    if x <= 0 or abs(x - round(x)) > 0.01:
                        return ""
                    return f"{int(round(x)):d}"

                # X-axis: show 1, 2, 5, 10, 20, 50, 100, 200, 500
                x_max = max(x_pts)
                x_ticks = [v for v in [1, 2, 3, 5, 10, 20, 30, 50, 100, 200, 300, 500] if v <= x_max * 1.2]
                ax.xaxis.set_major_locator(FixedLocator(x_ticks))
                ax.xaxis.set_major_formatter(FuncFormatter(_int_fmt))
                ax.xaxis.set_minor_formatter(FuncFormatter(lambda x, _: ""))

                # Y-axis: show 1, 2, 5, 10, 20, 50, 100
                y_max = max(y_pts)
                y_ticks = [v for v in [1, 2, 5, 10, 20, 50, 100, 200, 500] if v <= y_max * 1.2]
                ax.yaxis.set_major_locator(FixedLocator(y_ticks))
                ax.yaxis.set_major_formatter(FuncFormatter(_int_fmt))
                ax.yaxis.set_minor_formatter(FuncFormatter(lambda x, _: ""))

            n_zero = sum(1 for v in all_vals if v == 0)
            ax.text(0.95, 0.95, f"{n_zero} zeros\nnot shown",
                    transform=ax.transAxes, ha="right", va="top", fontsize=7, color="#888")
            ax.set_xlabel("Reuse count (non-zero)")
            if idx == 0:
                ax.set_ylabel("Number of datasets")
        else:
            ax.bar(bins[:-1], hist_c, width=0.8, color="black", alpha=0.8)
            if counts_i_vals:
                ax.bar(bins[:-1], hist_i, width=0.8, bottom=hist_c, color="#BBBBBB")
            ax.set_xlim(left=-0.5)
            if logy:
                ax.set_yscale("log")
                ax.set_ylim(bottom=logy_min)
                from matplotlib.ticker import FuncFormatter, FixedLocator
                max_y = max(hist_c.max(), hist_i.max() if counts_i_vals else 0)
                y_ticks = [v for v in [0.01, 0.1, 1, 10, 100, 1000, 10000] if v <= max_y * 2 and v >= 1]
                ax.yaxis.set_major_locator(FixedLocator(y_ticks))
                ax.yaxis.set_major_formatter(FuncFormatter(
                    lambda x, _: f"{int(round(x)):d}" if x >= 1 else ""))
                ax.yaxis.set_minor_locator(FixedLocator([]))
            ax.set_xlabel("Reuse count per dataset")
            if idx == 0:
                ax.set_ylabel("Number of datasets")

            # Overlay NB fit on all panels
            if len(counts_c_vals) > 5:
                c_arr = np.array(counts_c_vals)
                if c_arr.var() > c_arr.mean() > 0:
                    r_fit = c_arr.mean()**2 / (c_arr.var() - c_arr.mean())
                    p_fit = c_arr.mean() / c_arr.var()
                    # Extend fit line until it drops below y-axis floor
                    n_fit = len(counts_c_vals)
                    x_end = max_count + 1
                    if logy:
                        while n_fit * stats.nbinom.pmf(x_end, r_fit, p_fit) > logy_min and x_end < 1000:
                            x_end += 1
                    x_fit = np.arange(0, x_end + 1)
                    y_fit = n_fit * stats.nbinom.pmf(x_fit, r_fit, p_fit)
                    ax.plot(x_fit, y_fit, "--", color="#E53935", alpha=0.5,
                            linewidth=1.5, label=f"NB fit (r={r_fit:.2f})", zorder=5)
                    ax.legend(fontsize=7, frameon=False)
                    if logy:
                        ax.set_ylim(bottom=logy_min)

            # Inset zoom panel
            if inset_ranges is not None:
                x_range, y_range = inset_ranges
                inset_y = 0.17 if idx == 0 else 0.29
                inset_ax = ax.inset_axes([0.25, inset_y, 0.6, 0.6])
                inset_ax.bar(bins[:-1], hist_c, width=0.8, color="black", alpha=0.8)
                if counts_i_vals:
                    inset_ax.bar(bins[:-1], hist_i, width=0.8, bottom=hist_c, color="#BBBBBB")
                # NB fit in inset
                if len(counts_c_vals) > 5:
                    c_arr = np.array(counts_c_vals)
                    if c_arr.var() > c_arr.mean() > 0:
                        r_fit = c_arr.mean()**2 / (c_arr.var() - c_arr.mean())
                        p_fit = c_arr.mean() / c_arr.var()
                        x_fit_in = np.arange(0, x_range[1] + 1)
                        y_fit_in = len(counts_c_vals) * stats.nbinom.pmf(x_fit_in, r_fit, p_fit)
                        inset_ax.plot(x_fit_in, y_fit_in, "--", color="#E53935", alpha=0.5,
                                      linewidth=1.5, zorder=5)
                inset_ax.set_xlim(*x_range)
                inset_ax.set_ylim(*y_range)
                inset_ax.spines["top"].set_visible(False)
                inset_ax.spines["right"].set_visible(False)
                inset_ax.tick_params(labelsize=6)
                from matplotlib.ticker import MaxNLocator
                inset_ax.yaxis.set_major_locator(MaxNLocator(integer=True))
                ax.indicate_inset_zoom(inset_ax, edgecolor="gray", alpha=0.5)

            # Pie chart inset (rendered after zoom inset so it's on top)
            n_complete = len(counts_c_vals)
            n_incomplete = len(counts_i_vals)
            if n_incomplete > 0 and not logy:
                pie_ax = ax.inset_axes([0.56, 0.40, 0.22, 0.38], zorder=10)
                pie_ax.pie([n_complete, n_incomplete], colors=["black", "#BBBBBB"],
                           startangle=90, counterclock=False)
                pie_ax.text(0, -1.3, f"{n_complete} complete", fontsize=7, ha="center", color="black")
                pie_ax.text(0, 1.3, f"{n_incomplete} incomplete", fontsize=7, ha="center", color="#888")
                pie_ax.set_zorder(10)

        ax.set_title(title, fontweight="bold")
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)

        # Stats
        if counts_c_vals and not loglog:
            median = np.median(counts_c_vals)
            mean = np.mean(counts_c_vals)
            pct_zero = sum(1 for c in counts_c_vals if c == 0) / len(counts_c_vals) * 100
            ax.text(0.95, 0.95, f"med={median:.0f}\nmean={mean:.1f}\n{pct_zero:.0f}% zero",
                    transform=ax.transAxes, ha="right", va="top", fontsize=7)

        # Annotate top datasets on last panel (linear only)
        if not loglog and idx == n_panels - 1 and top_n_annotate > 0:
            # Group datasets by reuse count
            by_count = {}
            for did, n in top_datasets:
                by_count.setdefault(n, []).append(did)
            for n, dids in by_count.items():
                bar_h = hist_c[n] + (hist_i[n] if counts_i_vals else 0)
                label = ", ".join(str(d) for d in dids)
                y_pad = ax.get_ylim()[1] * 0.03
                ax.text(n, bar_h + y_pad, label, ha="center", va="bottom", fontsize=6,
                        color="#333", rotation=label_rotation)

    fig.suptitle(f"{archive_name} Reuse Count Distribution", fontweight="bold", fontsize=13)
    fig.tight_layout()
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=300, bbox_inches="tight")
    plt.close()
    print(f"Saved {output_path}")

    _print_nb_stats(reuse, created, output_path, archive_name, analysis_cutoff, windows)


def _plot_reuse_distribution_broken_y(reuse, created, output_path, archive_name,
                                       analysis_cutoff, windows, top_n_annotate,
                                       label_rotation, broken_y_ranges=None):
    """Broken y-axis version: top strip for the zero bar, bottom for the tail."""
    import matplotlib.gridspec as gridspec

    n_panels = len(windows)
    panel_labels = "ABCDEFGH"

    all_complete, _ = _count_reuse_per_dataset(reuse, created, analysis_cutoff, None)
    top_datasets = sorted(all_complete, key=lambda x: -x[1])[:top_n_annotate]

    fig = plt.figure(figsize=(3.2 * n_panels, 4.0))
    outer_gs = gridspec.GridSpec(1, n_panels, figure=fig, wspace=0.3)

    for idx, window_years in enumerate(windows):
        if window_years is None:
            title = f"{panel_labels[idx]}. All observed"
        else:
            title = f"{panel_labels[idx]}. {window_years}-year window"

        complete, incomplete = _count_reuse_per_dataset(
            reuse, created, analysis_cutoff, window_years)
        counts_c_vals = [n for _, n in complete]
        counts_i_vals = [n for _, n in incomplete]

        max_count = max(counts_c_vals + counts_i_vals) if (counts_c_vals + counts_i_vals) else 1
        bins = np.arange(0, max_count + 2)
        hist_c, _ = np.histogram(counts_c_vals, bins=bins)
        hist_i, _ = np.histogram(counts_i_vals, bins=bins) if counts_i_vals else (np.zeros_like(hist_c), None)

        total_hist = hist_c + (hist_i if counts_i_vals else 0)
        peak = total_hist.max()

        if broken_y_ranges:
            break_lo = broken_y_ranges[0][1]   # top of bottom range
            break_hi = broken_y_ranges[1][0]   # bottom of top range
            y_top_max = broken_y_ranges[1][1]  # top of top range
        else:
            sorted_vals = sorted(total_hist, reverse=True)
            second = sorted_vals[1] if len(sorted_vals) > 1 else peak
            break_lo = second * 1.3
            break_hi = peak * 0.88
            y_top_max = peak * 1.12

        ratio_top = y_top_max - break_hi
        ratio_bot = break_lo
        inner_gs = outer_gs[idx].subgridspec(2, 1,
            height_ratios=[ratio_top, ratio_bot * 2], hspace=0.08)
        ax_top = fig.add_subplot(inner_gs[0])
        ax_bot = fig.add_subplot(inner_gs[1], sharex=ax_top)

        # Draw bars on both axes
        for ax in [ax_top, ax_bot]:
            ax.bar(bins[:-1], hist_c, width=0.8, color="black", alpha=0.8)
            if counts_i_vals:
                ax.bar(bins[:-1], hist_i, width=0.8, bottom=hist_c, color="#BBBBBB")

        ax_top.set_ylim(break_hi, y_top_max)
        ax_bot.set_ylim(0, break_lo)

        # Hide spines at the break
        ax_top.spines["bottom"].set_visible(False)
        ax_bot.spines["top"].set_visible(False)
        ax_top.spines["top"].set_visible(False)
        ax_top.spines["right"].set_visible(False)
        ax_bot.spines["right"].set_visible(False)
        ax_top.tick_params(bottom=False, labelbottom=False)

        # Draw break marks
        d = 0.015
        kwargs = dict(transform=ax_top.transAxes, color="k", clip_on=False, linewidth=0.8)
        ax_top.plot((-d, +d), (-d*3, +d*3), **kwargs)
        ax_top.plot((1 - d, 1 + d), (-d*3, +d*3), **kwargs)
        kwargs = dict(transform=ax_bot.transAxes, color="k", clip_on=False, linewidth=0.8)
        ax_bot.plot((-d, +d), (1 - d*3, 1 + d*3), **kwargs)
        ax_bot.plot((1 - d, 1 + d), (1 - d*3, 1 + d*3), **kwargs)

        ax_bot.set_xlabel("Reuse count per dataset")
        if idx == 0:
            ax_bot.set_ylabel("Number of datasets")
            ax_bot.yaxis.set_label_coords(-0.18, 0.7)
        ax_top.set_title(title, fontweight="bold")

        # NB fit on bottom panel
        if len(counts_c_vals) > 5:
            c_arr = np.array(counts_c_vals)
            if c_arr.var() > c_arr.mean() > 0:
                r_fit = c_arr.mean()**2 / (c_arr.var() - c_arr.mean())
                p_fit = c_arr.mean() / c_arr.var()
                x_fit = np.arange(0, max_count + 1)
                y_fit = len(counts_c_vals) * stats.nbinom.pmf(x_fit, r_fit, p_fit)
                ax_bot.plot(x_fit, y_fit, "--", color="#E53935", alpha=0.5,
                            linewidth=1.5, label=f"NB fit (r={r_fit:.2f})", zorder=5)
                ax_top.plot(x_fit, y_fit, "--", color="#E53935", alpha=0.5,
                            linewidth=1.5, zorder=5)
                ax_bot.legend(fontsize=7, frameon=False)

        # Stats
        if counts_c_vals:
            median = np.median(counts_c_vals)
            mean = np.mean(counts_c_vals)
            pct_zero = sum(1 for c in counts_c_vals if c == 0) / len(counts_c_vals) * 100
            ax_top.text(0.95, 0.85, f"med={median:.0f}\nmean={mean:.1f}\n{pct_zero:.0f}% zero",
                        transform=ax_top.transAxes, ha="right", va="top", fontsize=7)

        # Pie chart inset
        n_complete = len(counts_c_vals)
        n_incomplete = len(counts_i_vals)
        if n_incomplete > 0:
            inset = ax_bot.inset_axes([0.42, 0.50, 0.22, 0.42])
            inset.pie([n_complete, n_incomplete], colors=["black", "#BBBBBB"],
                      startangle=90, counterclock=False)
            inset.text(0, -1.6, f"{n_complete} complete", fontsize=7, ha="center", color="black")
            inset.text(0, 1.6, f"{n_incomplete} incomplete", fontsize=7, ha="center", color="#888")

        # Annotate top datasets on last panel
        if idx == n_panels - 1 and top_n_annotate > 0:
            by_count = {}
            for did, n in top_datasets:
                by_count.setdefault(n, []).append(did)
            for n, dids in by_count.items():
                bar_h = hist_c[n] + (hist_i[n] if counts_i_vals else 0)
                label = ", ".join(str(d) for d in dids)
                ax_bot.text(n, bar_h + 1, label, ha="center", va="bottom", fontsize=6,
                            color="#333", rotation=label_rotation)

    fig.suptitle(f"{archive_name} Reuse Count Distribution", fontweight="bold", fontsize=13)
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=300, bbox_inches="tight")
    plt.close()
    print(f"Saved {output_path}")

    _print_nb_stats(reuse, created, output_path, archive_name, analysis_cutoff, windows)


def _print_nb_stats(reuse, created, output_path, archive_name, analysis_cutoff, windows):
    """Print and save NB distribution stats."""
    # Fit negative binomial to the primary window (first non-None) and print stats
    primary_window = windows[0]
    complete, incomplete = _count_reuse_per_dataset(reuse, created, analysis_cutoff, primary_window)
    counts_arr = np.array([n for _, n in complete])

    if len(counts_arr) > 5 and counts_arr.var() > counts_arr.mean() > 0:
        n_obs = len(counts_arr)
        mean = counts_arr.mean()
        var = counts_arr.var()
        n_zeros = (counts_arr == 0).sum()

        # Negative binomial fit (method of moments)
        r_nb = mean**2 / (var - mean)
        p_nb = mean / var

        # Cameron-Trivedi dispersion test
        aux = ((counts_arr - mean)**2 - counts_arr) / mean
        alpha_hat = aux.mean() / mean
        t_stat = alpha_hat / (aux.std() / np.sqrt(n_obs) / mean)

        # Log-likelihoods, LRT, and BIC
        ll_poisson = stats.poisson.logpmf(counts_arr, mean).sum()
        ll_nb = stats.nbinom.logpmf(counts_arr, r_nb, p_nb).sum()
        # Likelihood ratio test: Poisson is nested in NB (1 extra param)
        lr_stat = 2 * (ll_nb - ll_poisson)
        lr_pvalue = stats.chi2.sf(lr_stat, df=1)
        bic_poisson = -2 * ll_poisson + 1 * np.log(n_obs)
        bic_nb = -2 * ll_nb + 2 * np.log(n_obs)

        expected_zeros_poisson = n_obs * stats.poisson.pmf(0, mean)
        expected_zeros_nb = n_obs * stats.nbinom.pmf(0, r_nb, p_nb)

        # Save stats
        dist_stats = {
            "archive": archive_name,
            "window_years": primary_window,
            "n_datasets_complete": n_obs,
            "n_datasets_incomplete": len(incomplete),
            "mean": round(mean, 2),
            "variance": round(var, 2),
            "variance_mean_ratio": round(var / mean, 1),
            "n_zeros": int(n_zeros),
            "pct_zero": round(100 * n_zeros / n_obs, 1),
            "nb_r": round(r_nb, 3),
            "nb_p": round(p_nb, 3),
            "nb_expected_zeros": round(expected_zeros_nb, 0),
            "poisson_expected_zeros": round(expected_zeros_poisson, 0),
            "dispersion_test_alpha": round(alpha_hat, 3),
            "dispersion_test_t": round(t_stat, 2),
            "dispersion_test_significant": bool(t_stat > 1.96),
            "ll_poisson": round(ll_poisson, 1),
            "ll_nb": round(ll_nb, 1),
            "bic_poisson": round(bic_poisson, 1),
            "bic_nb": round(bic_nb, 1),
            "lr_stat": round(lr_stat, 1),
            "lr_pvalue": float(f"{lr_pvalue:.2e}"),
            "preferred_model": "negative_binomial" if bic_nb < bic_poisson else "poisson",
        }

        import json
        stats_path = Path(output_path).parent / "reuse_distribution_stats.json"
        with open(stats_path, "w") as f:
            json.dump(dist_stats, f, indent=2)

        print(f"\n{'='*60}")
        print(f"  {archive_name} Reuse Distribution Stats ({primary_window}-year window)")
        print(f"{'='*60}")
        print(f"  Datasets: {n_obs} complete, {len(incomplete)} incomplete")
        print(f"  Mean: {mean:.2f}, Variance: {var:.2f} (ratio: {var/mean:.1f})")
        print(f"  Zeros: {n_zeros}/{n_obs} ({100*n_zeros/n_obs:.0f}%)")
        print(f"  NB fit: r={r_nb:.3f}, p={p_nb:.3f}")
        print(f"  NB expected zeros: {expected_zeros_nb:.0f} (Poisson: {expected_zeros_poisson:.0f})")
        print(f"  Dispersion test: alpha={alpha_hat:.3f}, t={t_stat:.2f} ({'*' if t_stat > 1.96 else 'ns'})")
        print(f"  LRT: chi2={lr_stat:.1f}, p={lr_pvalue:.2e}")
        print(f"  BIC: Poisson={bic_poisson:.0f}, NB={bic_nb:.0f} -> {dist_stats['preferred_model']}")
        print(f"  Saved {stats_path}")
