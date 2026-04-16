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

import matplotlib.pyplot as plt
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
                   analysis_cutoff=None, project_years=5, growth_model="auto"):
    """Generate 2x2 modeling figure.

    Args:
        delays: list of dicts with delay_months, same_lab, dandiset_id, pub_date, created
        created: dict {dataset_id: datetime}
        datasets: loaded datasets.json dict
        output_path: Path to save figure
        archive_name: for titles
        analysis_cutoff: datetime cutoff
        project_years: how far to project
    """
    if analysis_cutoff is None:
        analysis_cutoff = datetime(2025, 10, 7)

    obs_months = {did: (analysis_cutoff - c).days / 30.44
                  for did, c in created.items() if (analysis_cutoff - c).days > 0}

    diff_delays = [d["delay_months"] for d in delays if d["same_lab"] is False]
    same_delays = [d["delay_months"] for d in delays if d["same_lab"] is True]

    fig, axes = plt.subplots(2, 2, figsize=(12, 10))

    # === Panel A: MCF Model Fits ===
    ax = axes[0, 0]
    fit_results = {}
    for label, delay_list, color in [
        ("Different lab", diff_delays, "#2E7D32"),
        ("Same lab", same_delays, "#7B1FA2"),
    ]:
        if not delay_list:
            continue
        t, mcf_vals = compute_mcf(delay_list, obs_months)
        t_years = t / 12

        # Plot data
        ax.step(t_years, mcf_vals, where="post", color=color, linewidth=1.5, alpha=0.4)

        # Fit
        model_name, params, t_fit, mcf_fit = fit_mcf(t_years, mcf_vals, model="auto")
        if model_name:
            if model_name == "saturating":
                K, tau = params
                fit_label = f"{label} (K={K:.1f}, τ={tau:.0f}yr)"
            else:
                K, r, t0, nu = params
                fit_label = f"{label} (K={K:.1f})"
            ax.plot(t_fit, mcf_fit, color=color, linewidth=2, label=fit_label)
            fit_results[label] = {"model": model_name, "params": params,
                                  "func": sat_exp if model_name == "saturating" else richards}

    ax.set_xlabel("Years after dataset creation")
    ax.set_ylabel("Expected reuse papers per dataset")
    ax.set_title("A. MCF: Model Fits", fontweight="bold")
    ax.legend(fontsize=9)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)

    # === Panel B: Reuse Rate (points with Poisson CIs) ===
    ax = axes[0, 1]
    max_year = 18
    for label, delay_list, color, marker in [
        ("Different lab", diff_delays, "#2E7D32", "s"),
        ("Same lab", same_delays, "#7B1FA2", "D"),
    ]:
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
                    linewidth=1.2, label=label)

    ax.set_xlabel("Years after dataset creation")
    ax.set_ylabel("Reuse rate (events/dataset/yr)")
    ax.set_title("B. Reuse Rate", fontweight="bold")
    ax.legend(fontsize=9)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)

    # === Panel C: Dataset Growth ===
    ax = axes[1, 0]
    creation_dates = sorted(created.values())
    if creation_dates:
        t0_date = creation_dates[0]
        t_years_growth = np.array([(d - t0_date).days / 365.25 for d in creation_dates])
        cumulative = np.arange(1, len(creation_dates) + 1)

        ax.plot([t0_date + timedelta(days=t * 365.25) for t in t_years_growth],
                cumulative, color="#1565c0", linewidth=2)

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

            def richards_growth(t, K, r, t0, nu):
                return K / (1 + nu * np.exp(-r * (t - t0))) ** (1 / nu)

            try:
                popt_rg, _ = curve_fit(richards_growth, t_fit_data, c_fit_data,
                                       p0=[c_fit_data[-1] * 1.5, 0.5, t_fit_data[-1] / 2, 1],
                                       maxfev=10000,
                                       bounds=([c_fit_data[-1] * 0.5, 0.01, 0, 0.1],
                                               [c_fit_data[-1] * 5, 5, 50, 20]))
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
                ax.plot(dates_proj, fm["func"](t_proj, *fm["params"]), "--", color="#1565c0",
                        linewidth=1.5, label=fm["label"])

                # Store for Panel D projection
                growth_func = fm["func"]
                growth_params = fm["params"]

                ax.axvline(creation_dates[-1], color="gray", linestyle=":", alpha=0.5)
                ax.text(creation_dates[-1], cumulative[-1] * 0.8,
                        f" {len(creation_dates)} today", fontsize=9, color="#1565c0")
                ax.legend(fontsize=9)
            else:
                growth_func = None
                growth_params = None
        else:
            growth_func = None
            growth_params = None

    ax.set_xlabel("Date")
    ax.set_ylabel(f"Cumulative {archive_name} datasets")
    ax.set_title("C. Dataset Growth", fontweight="bold")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)

    # === Panel D: Projected Reuse ===
    ax = axes[1, 1]
    if creation_dates and fit_results:
        now = analysis_cutoff
        t0_date = creation_dates[0]
        future_end = now + timedelta(days=project_years * 365.25)

        for label, color, is_same in [("Different lab", "#2E7D32", False), ("Same lab", "#7B1FA2", True)]:
            if label not in fit_results:
                continue
            fr = fit_results[label]

            # Past: plot observed cumulative counts
            obs_dates = sorted(d["pub_date"] for d in delays if d["same_lab"] == is_same)
            if obs_dates:
                ax.plot(obs_dates, range(1, len(obs_dates) + 1),
                        color=color, linewidth=2,
                        label=f"{label} ({len(obs_dates)})")

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

    fig.tight_layout()
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"Saved {output_path}")
