#!/usr/bin/env python3
"""
reuse_modeling.py — Shared modeling and projection functions for any archive.

Generates a 2x2 figure:
  A: MCF model fits (Richards/saturating exponential)
  B: Reuse rate (events/dataset/yr) with Poisson CIs
  C: Dataset growth projection (power law)
  D: Projected cumulative reuse (convolution of growth × MCF)

Usage:
    from analysis.reuse_modeling import plot_model_2x2
    plot_model_2x2(delays, created, datasets, output_path, archive_name="CRCNS")
"""

import json
from datetime import datetime, timedelta
from pathlib import Path

import matplotlib
import matplotlib.pyplot as plt
matplotlib.rcParams["font.family"] = "Helvetica"
import numpy as np
from scipy.optimize import curve_fit
from scipy.stats import chi2


def compute_mcf(delay_months_list, obs_months_dict):
    """Compute Nelson-Aalen MCF for recurrent events.

    Args:
        delay_months_list: list of delay values in months
        obs_months_dict: {dataset_id: observation_months}

    Returns (t_months, mcf_values) arrays
    """
    event_times = sorted(delay_months_list)
    t = [0.0]
    mcf = [0.0]
    for et in event_times:
        n_at_risk = sum(1 for obs in obs_months_dict.values() if obs >= et)
        if n_at_risk > 0:
            t.append(et)
            mcf.append(mcf[-1] + 1.0 / n_at_risk)
    return np.array(t), np.array(mcf)


def richards(t, K, r, t0, nu):
    """Richards generalized logistic, constrained to pass through origin."""
    val = K / (1 + nu * np.exp(-r * (t - t0))) ** (1 / nu)
    offset = K / (1 + nu * np.exp(-r * (0 - t0))) ** (1 / nu)
    return val - offset


def sat_exp(t, K, tau):
    """Saturating exponential."""
    return K * (1 - np.exp(-t / tau))


def fit_mcf(t_years, mcf_vals, model="auto"):
    """Fit MCF with Richards or saturating exponential.

    Args:
        model: "richards", "saturating", or "auto" (try both, pick best BIC)

    Returns (model_name, params, t_fit, mcf_fit)
    """
    mask = t_years > 0
    t = t_years[mask]
    m = mcf_vals[mask]

    results = {}

    if model in ("saturating", "auto"):
        try:
            popt, _ = curve_fit(sat_exp, t, m, p0=[m[-1] * 1.5, 5], maxfev=5000)
            pred = sat_exp(t, *popt)
            residuals = m - pred
            n = len(m)
            k = 2
            bic = n * np.log(np.mean(residuals**2)) + k * np.log(n)
            results["saturating"] = {"params": popt, "bic": bic, "func": sat_exp}
        except Exception:
            pass

    if model in ("richards", "auto"):
        try:
            popt, _ = curve_fit(richards, t, m, p0=[m[-1] * 1.5, 1, 3, 2],
                                maxfev=10000, bounds=([0, 0, 0, 0.1], [100, 10, 50, 20]))
            pred = richards(t, *popt)
            residuals = m - pred
            n = len(m)
            k = 4
            bic = n * np.log(np.mean(residuals**2)) + k * np.log(n)
            results["richards"] = {"params": popt, "bic": bic, "func": richards}
        except Exception:
            pass

    if not results:
        return None, None, None, None

    if model == "auto":
        best = min(results, key=lambda k: results[k]["bic"])
    else:
        best = model if model in results else list(results.keys())[0]

    r = results[best]
    t_fit = np.linspace(0, max(t_years[-1] * 1.5, 20), 300)
    mcf_fit = r["func"](t_fit, *r["params"])
    return best, r["params"], t_fit, mcf_fit


def plot_model_2x2(delays, created, datasets, output_path, archive_name="Archive",
                   analysis_cutoff=None, project_years=5, growth_model="auto",
                   split_labs=True):
    """Generate 2x2 modeling figure for any archive.

    Panels:
      A: MCF model fits (saturating exponential or Richards)
      B: Reuse rate (events/dataset/yr) with Poisson CIs and model derivative
      C: Dataset creation growth (power law, logistic, or Richards)
      D: Projected cumulative reuse (observed past + convolution projection)

    Args:
        delays: list of dicts with delay_months, same_lab, dandiset_id, pub_date, created
        created: dict {dataset_id: datetime}
        datasets: loaded datasets.json dict
        output_path: Path to save figure
        archive_name: for titles
        analysis_cutoff: datetime cutoff
        project_years: how far to project
        growth_model: "auto", "power_law", "logistic", or "richards" for Panel C
        split_labs: if True, show separate same/different lab curves; if False, combine
    """
    if analysis_cutoff is None:
        analysis_cutoff = datetime(2025, 10, 7)

    obs_months = {did: (analysis_cutoff - c).days / 30.44
                  for did, c in created.items() if (analysis_cutoff - c).days > 0}

    diff_delays = [d["delay_months"] for d in delays if d["same_lab"] is False]
    same_delays = [d["delay_months"] for d in delays if d["same_lab"] is True]
    all_delay_months = [d["delay_months"] for d in delays]

    if split_labs:
        mcf_series = [("Different lab", diff_delays, "#2E7D32"),
                      ("Same lab", same_delays, "#7B1FA2")]
    else:
        mcf_series = [("All labs", all_delay_months, "#000000")]

    fig = plt.figure(figsize=(8.6, 7.2))
    # Layout: A (top-left) + B (bottom-left) share x-axis
    #          C (top-right) + D (bottom-right) share x-axis
    gs = fig.add_gridspec(2, 2, hspace=0.2, wspace=0.35)
    ax_a = fig.add_subplot(gs[0, 0])
    ax_b = fig.add_subplot(gs[1, 0], sharex=ax_a)
    ax_c = fig.add_subplot(gs[0, 1])
    ax_d = fig.add_subplot(gs[1, 1], sharex=ax_c)

    # === Panel A: MCF Model Fits ===
    ax = ax_a
    fit_results = {}

    # When combined, show same/diff lab MCF curves with CIs and fits as background
    if not split_labs and diff_delays and same_delays:
        rng_bg = np.random.default_rng(123)
        ds_ids = list(obs_months.keys())
        for bg_label, bg_delays, bg_color in [
            ("Different lab", diff_delays, "#2E7D32"),
            ("Same lab", same_delays, "#7B1FA2"),
        ]:
            t_bg, mcf_bg = compute_mcf(bg_delays, obs_months)
            t_bg_yr = t_bg / 12
            ax.step(t_bg_yr, mcf_bg, where="post", color=bg_color, linewidth=1.2, alpha=0.5, label=bg_label)

            # Bootstrap CI
            boot_curves_bg = []
            for _ in range(200):
                sample_ids = rng_bg.choice(ds_ids, size=len(ds_ids), replace=True)
                sample_obs = {f"{did}_{j}": obs_months[did] for j, did in enumerate(sample_ids)}
                sample_d = rng_bg.choice(bg_delays, size=len(bg_delays), replace=True).tolist()
                t_b, mcf_b = compute_mcf(sample_d, sample_obs)
                t_grid_bg = np.linspace(0, t_bg_yr[-1], 100)
                boot_curves_bg.append(np.interp(t_grid_bg, t_b / 12, mcf_b))
            boot_bg = np.array(boot_curves_bg)
            t_grid_bg = np.linspace(0, t_bg_yr[-1], 100)
            ax.fill_between(t_grid_bg, np.percentile(boot_bg, 2.5, axis=0),
                            np.percentile(boot_bg, 97.5, axis=0), color=bg_color, alpha=0.1)

            # Richards fit
            model_bg, params_bg, t_fit_bg, mcf_fit_bg = fit_mcf(t_bg_yr, mcf_bg, model="auto")
            if model_bg:
                K_bg = params_bg[0]
                ax.plot(t_fit_bg, mcf_fit_bg, "--", color=bg_color, linewidth=1.2, alpha=0.5)
                ax.axhline(K_bg, color=bg_color, linestyle=":", linewidth=0.8, alpha=0.4)
                ax.text(0.3, K_bg, f" K={K_bg:.1f}", fontsize=7, color=bg_color, va="bottom", alpha=0.7)

    for label, delay_list, color in mcf_series:
        if not delay_list:
            continue
        t, mcf_vals = compute_mcf(delay_list, obs_months)
        t_years = t / 12

        # Plot data
        mcf_label = "Combined" if not split_labs else None
        ax.step(t_years, mcf_vals, where="post", color="black", linewidth=1.5, label=mcf_label)

        # Bootstrap confidence interval for MCF
        rng = np.random.default_rng(42)
        ds_ids = list(obs_months.keys())
        # Group events by dataset
        events_by_ds = {}
        for d_months in delay_list:
            # Find which dataset this came from (approximate by matching)
            events_by_ds.setdefault("_all", []).append(d_months)
        n_boot = 200
        boot_curves = []
        for _ in range(n_boot):
            # Resample datasets with replacement
            sample_ids = rng.choice(ds_ids, size=len(ds_ids), replace=True)
            sample_obs = {f"{did}_{j}": obs_months[did] for j, did in enumerate(sample_ids)}
            # Resample events proportionally
            sample_delays = rng.choice(delay_list, size=len(delay_list), replace=True).tolist()
            t_b, mcf_b = compute_mcf(sample_delays, sample_obs)
            # Interpolate to common grid
            t_grid = np.linspace(0, t_years[-1], 100)
            mcf_interp = np.interp(t_grid, t_b / 12, mcf_b)
            boot_curves.append(mcf_interp)

        boot_arr = np.array(boot_curves)
        ci_lo = np.percentile(boot_arr, 2.5, axis=0)
        ci_hi = np.percentile(boot_arr, 97.5, axis=0)
        t_grid = np.linspace(0, t_years[-1], 100)
        ax.fill_between(t_grid, ci_lo, ci_hi, color="gray", alpha=0.2)

        # Fit
        model_name, params, t_fit, mcf_fit = fit_mcf(t_years, mcf_vals, model="auto")
        if model_name:
            if model_name == "saturating":
                K, tau = params
                fit_label = f"Saturating exp. (K={K:.1f}, τ={tau:.0f}yr)"
            else:
                K, r, t0, nu = params
                fit_label = f"Richards (K={K:.1f})"
            ax.plot(t_fit, mcf_fit, "--", color=color, linewidth=2, label=fit_label)
            ax.axhline(K, color=color, linestyle=":", linewidth=0.8, alpha=0.5)
            ax.text(0.3, K, f" K={K:.1f}", fontsize=7, color=color, va="bottom", alpha=0.8)
            fit_results[label] = {"model": model_name, "params": params,
                                  "func": sat_exp if model_name == "saturating" else richards}

    ax.set_ylabel("Expected reuse papers\nper dataset")
    ax.set_title("A. MCF: Model Fits", fontweight="bold")
    ax.legend(fontsize=9)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.tick_params(labelbottom=False)

    # === Panel B: Reuse Rate (data points + model derivative) ===
    ax = ax_b
    max_year = 18
    if split_labs:
        rate_series = [("Different lab", diff_delays, "#2E7D32", "s"),
                       ("Same lab", same_delays, "#7B1FA2", "D")]
    else:
        rate_series = [("All labs", all_delay_months, "#000000", "o")]
    for label, delay_list, color, marker in rate_series:
        if not delay_list:
            continue
        delay_years = [d / 12 for d in delay_list]
        bins = np.arange(0, min(int(max(delay_years)) + 2, max_year))
        counts = np.histogram(delay_years, bins=bins)[0]
        at_risk = np.array([max(sum(1 for obs in obs_months.values() if obs >= yr * 12), 1) for yr in bins[:-1]])
        rate = counts / at_risk
        centers = (bins[:-1] + bins[1:]) / 2

        # Poisson CIs
        alpha = 0.05
        ci_lo = chi2.ppf(alpha / 2, 2 * counts) / (2 * at_risk)
        ci_hi = chi2.ppf(1 - alpha / 2, 2 * (counts + 1)) / (2 * at_risk)
        ci_lo = np.nan_to_num(ci_lo, 0)

        ax.errorbar(centers, rate, yerr=[rate - ci_lo, ci_hi - rate],
                    fmt=marker, color=color, markersize=5, capsize=3,
                    linewidth=1.2, label=label, alpha=0.5)

        # Overlay derivative of MCF fit from Panel A
        if label in fit_results:
            fr = fit_results[label]
            t_smooth = np.linspace(0.1, 20, 200)
            mcf_smooth = fr["func"](t_smooth, *fr["params"])
            # Numerical derivative (per year)
            dt = t_smooth[1] - t_smooth[0]
            rate_smooth = np.gradient(mcf_smooth, dt)
            ax.plot(t_smooth, rate_smooth, "--", color=color, linewidth=2)

    # When combined, also show same/diff lab derivatives
    if not split_labs and diff_delays and same_delays:
        for bg_label, bg_delays, bg_color in [
            ("Different lab", diff_delays, "#2E7D32"),
            ("Same lab", same_delays, "#7B1FA2"),
        ]:
            t_bg, mcf_bg = compute_mcf(bg_delays, obs_months)
            model_bg, params_bg, t_fit_bg, mcf_fit_bg = fit_mcf(t_bg / 12, mcf_bg, model="auto")
            if model_bg:
                func_bg = sat_exp if model_bg == "saturating" else richards
                t_smooth_bg = np.linspace(0.1, 20, 200)
                mcf_smooth_bg = func_bg(t_smooth_bg, *params_bg)
                dt_bg = t_smooth_bg[1] - t_smooth_bg[0]
                rate_bg = np.gradient(mcf_smooth_bg, dt_bg)
                ax.plot(t_smooth_bg, rate_bg, "--", color=bg_color, linewidth=1.2, alpha=0.5)

    ax.set_xlabel("Years after dataset creation")
    ax.set_ylabel("Reuse rate (events/dataset/yr)")
    ax.set_title("B. Reuse Rate", fontweight="bold")
    if split_labs:
        ax.legend(fontsize=9)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)

    # === Panel C: Dataset Growth ===
    ax = ax_c
    creation_dates = sorted(created.values())
    if creation_dates:
        t0_date = creation_dates[0]
        t_years_growth = np.array([(d - t0_date).days / 365.25 for d in creation_dates])
        cumulative = np.arange(1, len(creation_dates) + 1)

        growth_color = "#000000" if not split_labs else "#1565c0"
        ax.plot([t0_date + timedelta(days=t * 365.25) for t in t_years_growth],
                cumulative, color=growth_color, linewidth=2)

        mask = t_years_growth > 0
        t_fit_data = t_years_growth[mask]
        c_fit_data = cumulative[mask]

        if len(t_fit_data) > 5:
            # Try both power law and logistic, pick best BIC
            fit_models = {}

            def power_law(t, a, b):
                return a * t ** b

            def logistic_growth(t, K, r, t0):
                return K / (1 + np.exp(-r * (t - t0)))

            try:
                popt_pl, _ = curve_fit(power_law, t_fit_data, c_fit_data,
                                       p0=[10, 1.5], maxfev=5000)
                pred = power_law(t_fit_data, *popt_pl)
                bic_pl = len(t_fit_data) * np.log(np.mean((c_fit_data - pred)**2)) + 2 * np.log(len(t_fit_data))
                fit_models["power_law"] = {"params": popt_pl, "bic": bic_pl, "func": power_law,
                                           "label": f"N ∝ t^{popt_pl[1]:.2f}"}
            except Exception:
                pass

            try:
                popt_lg, _ = curve_fit(logistic_growth, t_fit_data, c_fit_data,
                                       p0=[c_fit_data[-1] * 1.5, 0.5, t_fit_data[-1] / 2],
                                       maxfev=5000, bounds=([c_fit_data[-1] * 0.5, 0.01, 0], [c_fit_data[-1] * 5, 5, 50]))
                pred = logistic_growth(t_fit_data, *popt_lg)
                bic_lg = len(t_fit_data) * np.log(np.mean((c_fit_data - pred)**2)) + 3 * np.log(len(t_fit_data))
                fit_models["logistic"] = {"params": popt_lg, "bic": bic_lg, "func": logistic_growth,
                                          "label": f"Logistic (K={popt_lg[0]:.0f})"}
            except Exception:
                pass

            n_current = c_fit_data[-1]

            def richards_growth(t, K, r, t0, nu):
                return K / (1 + nu * np.exp(-r * (t - t0))) ** (1 / nu)

            # Constrain K >= current count so the fit passes through today's value
            try:
                popt_rg, _ = curve_fit(richards_growth, t_fit_data, c_fit_data,
                                       p0=[n_current * 1.5, 0.5, t_fit_data[-1] / 2, 1],
                                       maxfev=10000,
                                       bounds=([n_current, 0.01, 0, 0.1],
                                               [n_current * 5, 5, 50, 20]))
                pred = richards_growth(t_fit_data, *popt_rg)
                bic_rg = len(t_fit_data) * np.log(np.mean((c_fit_data - pred)**2)) + 4 * np.log(len(t_fit_data))
                fit_models["richards"] = {"params": popt_rg, "bic": bic_rg, "func": richards_growth,
                                          "label": f"Richards (K={popt_rg[0]:.0f})"}
            except Exception:
                pass

            if growth_model == "auto" and fit_models:
                best = min(fit_models, key=lambda k: fit_models[k]["bic"])
            elif growth_model in fit_models:
                best = growth_model
            elif fit_models:
                best = list(fit_models.keys())[0]
            else:
                best = None

            if best:
                fm = fit_models[best]
                max_t = t_fit_data[-1] + project_years
                t_proj = np.linspace(0.1, max_t, 200)
                dates_proj = [t0_date + timedelta(days=t * 365.25) for t in t_proj]
                ax.plot(dates_proj, fm["func"](t_proj, *fm["params"]), "--", color=growth_color,
                        linewidth=1.5, label=fm["label"])

                # Store for Panel D projection
                growth_func = fm["func"]
                growth_params = fm["params"]

                ax.axvline(creation_dates[-1], color="gray", linestyle=":", alpha=0.5)
                ax.text(creation_dates[-1], cumulative[-1] * 0.8,
                        f" {len(creation_dates)} today", fontsize=9, color=growth_color)

                # Add DANDI start annotation for non-DANDI archives
                if archive_name != "DANDI":
                    dandi_start = datetime(2019, 9, 1)
                    if t0_date < dandi_start < creation_dates[-1]:
                        ax.axvline(dandi_start, color="gray", linestyle=":", alpha=0.7)
                        ax.text(dandi_start, cumulative[-1] * 0.4, " DANDI\n launched",
                                fontsize=8, color="gray", ha="left")
                ax.legend(fontsize=9)
            else:
                growth_func = None
                growth_params = None
        else:
            growth_func = None
            growth_params = None

    ax.set_ylabel(f"Cumulative {archive_name} datasets")
    ax.set_title("C. Dataset Growth", fontweight="bold")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.tick_params(labelbottom=False)

    # === Panel D: Projected Reuse ===
    ax = ax_d
    if creation_dates and fit_results:
        now = analysis_cutoff
        t0_date = creation_dates[0]
        future_end = now + timedelta(days=project_years * 365.25)

        # When combined, show same/diff lab observed lines as background
        if not split_labs:
            for bg_label, bg_color, bg_same in [("Different lab", "#2E7D32", False), ("Same lab", "#7B1FA2", True)]:
                bg_dates = sorted(d["pub_date"] for d in delays if d["same_lab"] == bg_same)
                if bg_dates:
                    ax.plot(bg_dates, range(1, len(bg_dates) + 1),
                            color=bg_color, linewidth=1.2, alpha=0.5, label=f"{bg_label} ({len(bg_dates)})")

        if split_labs:
            proj_series = [("Different lab", "#2E7D32", False), ("Same lab", "#7B1FA2", True)]
        else:
            proj_series = [("All labs", "#000000", None)]
        for label, color, is_same in proj_series:
            if label not in fit_results:
                continue
            fr = fit_results[label]

            # Past: plot observed cumulative counts
            if is_same is None:
                obs_dates = sorted(d["pub_date"] for d in delays)
            else:
                obs_dates = sorted(d["pub_date"] for d in delays if d["same_lab"] == is_same)
            if obs_dates:
                obs_label = f"{label} ({len(obs_dates)})" if split_labs else f"Observed ({len(obs_dates)})"
                ax.plot(obs_dates, range(1, len(obs_dates) + 1),
                        color=color, linewidth=2, label=obs_label)

            # Future: project from now using growth model + MCF
            future_dates = [now + timedelta(days=d) for d in
                            range(0, int((future_end - now).days), 30)]
            proj = []
            for eval_date in future_dates:
                total = 0
                t_eval_years = (eval_date - t0_date).days / 365.25

                if growth_func is not None:
                    n_datasets = int(growth_func(t_eval_years, *growth_params))
                else:
                    n_datasets = len(creation_dates)

                # Existing datasets
                for c_date in creation_dates:
                    age_years = (eval_date - c_date).days / 365.25
                    if age_years > 0:
                        total += fr["func"](age_years, *fr["params"])
                # Projected new datasets
                for j in range(len(creation_dates), n_datasets):
                    frac = (j - len(creation_dates)) / max(n_datasets - len(creation_dates), 1)
                    c_date = now + timedelta(days=frac * (eval_date - now).days)
                    age_years = (eval_date - c_date).days / 365.25
                    if age_years > 0:
                        total += fr["func"](age_years, *fr["params"])
                proj.append(total)

            ax.plot(future_dates, proj, "--", color=color, linewidth=2,
                    label=f"Proj. (~{int(proj[-1])})")

        ax.axvline(now, color="gray", linestyle=":", alpha=0.5)
        ax.set_xlabel("Date")
        ax.set_ylabel(f"Est. cumulative {archive_name} reuse papers")
        ax.set_title("D. Projected Reuse", fontweight="bold")
        ax.legend(fontsize=8)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)

    fig.suptitle(f"{archive_name} Reuse Modeling and Projections",
                 fontsize=15, fontweight="bold", y=1.0)
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=300, bbox_inches="tight")
    plt.close()
    print(f"Saved {output_path}")
