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

        # Pie chart inset (only if there are incomplete datasets)
        n_complete = len(counts_c_vals)
        n_incomplete = len(counts_i_vals)
        if n_incomplete > 0:
            inset = ax.inset_axes([0.42, 0.40, 0.22, 0.38])
            inset.pie([n_complete, n_incomplete], colors=["black", "#BBBBBB"],
                      startangle=90, counterclock=False)
            inset.text(0, -1.6, f"{n_complete} complete", fontsize=7, ha="center", color="black")
            inset.text(0, 1.6, f"{n_incomplete} incomplete", fontsize=7, ha="center", color="#888")

        # On the primary window panel (first), overlay NB fit
        if idx == 0 and len(counts_c_vals) > 5:
            c_arr = np.array(counts_c_vals)
            if c_arr.var() > c_arr.mean() > 0:
                r_fit = c_arr.mean()**2 / (c_arr.var() - c_arr.mean())
                p_fit = c_arr.mean() / c_arr.var()
                x_fit = np.arange(0, max_count + 1)
                y_fit = len(counts_c_vals) * stats.nbinom.pmf(x_fit, r_fit, p_fit)
                ax.plot(x_fit, y_fit, "--", color="#E53935", alpha=0.5,
                        linewidth=1.5, label=f"NB fit (r={r_fit:.2f})", zorder=5)
                ax.legend(fontsize=7, frameon=False)

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

        # Log-likelihoods and BIC
        ll_poisson = stats.poisson.logpmf(counts_arr, mean).sum()
        ll_nb = stats.nbinom.logpmf(counts_arr, r_nb, p_nb).sum()
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
        print(f"  BIC: Poisson={bic_poisson:.0f}, NB={bic_nb:.0f} -> {dist_stats['preferred_model']}")
        print(f"  Saved {stats_path}")
