#!/usr/bin/env python3
"""
predict_reuse.py - Predict future reuse paper counts using survival analysis.

Models the cumulative number of reuse papers per dandiset as a function of
dandiset age, using the Mean Cumulative Function (MCF) for recurrent events
with right-censoring. Separately estimates same-lab and different-lab reuse.

Produces:
1. MCF plot: expected cumulative reuse papers per dandiset vs age
2. Prediction plot: total reuse papers over time (historical + forecast)

Usage:
    python predict_reuse.py \
        --refs output/direct_ref_classifications.json \
        --citations output/test_all_classifications.json \
        --dandisets output/dandi_primary_papers_results.json \
        -o output/reuse_prediction.png --open
"""

import argparse
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy.optimize import curve_fit

from analyze_time_to_reuse import (
    backfill_citing_dates,
    load_dandiset_creation_dates,
)
from generate_combined_dashboard import merge_data

TODAY = datetime(2026, 3, 1)


def build_recurrent_event_data(
    classifications: list[dict],
    creation_dates: dict[str, datetime],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Build recurrent event data: one row per reuse paper, plus dandiset ages.

    Returns:
        events_df: DataFrame with columns [dandiset_id, delay_years, same_lab]
                   One row per reuse paper.
        dandisets_df: DataFrame with columns [dandiset_id, age_years]
                      One row per dandiset (observation window).
    """
    events = []
    for entry in classifications:
        if entry.get("classification") != "REUSE":
            continue
        ds_id = entry.get("dandiset_id", "")
        citing_date_str = entry.get("citing_date", "")
        if not ds_id or not citing_date_str or ds_id not in creation_dates:
            continue
        try:
            citing_date = datetime.strptime(citing_date_str, "%Y-%m-%d")
        except ValueError:
            continue
        delay = (citing_date - creation_dates[ds_id]).days / 365.25
        if delay < 0:
            delay = 0.01
        events.append({
            "dandiset_id": ds_id,
            "delay_years": delay,
            "same_lab": entry.get("same_lab") is True,
        })

    events_df = pd.DataFrame(events)

    dandisets = []
    for ds_id, created in creation_dates.items():
        age = (TODAY - created).days / 365.25
        dandisets.append({"dandiset_id": ds_id, "age_years": age})
    dandisets_df = pd.DataFrame(dandisets)

    n_same = events_df["same_lab"].sum() if len(events_df) else 0
    n_diff = (~events_df["same_lab"]).sum() if len(events_df) else 0
    print(f"\nRecurrent event data:")
    print(f"  {len(dandisets_df)} dandisets, {len(events_df)} reuse papers")
    print(f"  Same lab: {n_same}, Different lab: {n_diff}")

    return events_df, dandisets_df


def compute_mcf(
    events_df: pd.DataFrame,
    dandisets_df: pd.DataFrame,
    same_lab_filter: bool | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """Compute the Mean Cumulative Function for recurrent events.

    The MCF at time t is the expected cumulative number of events per subject
    by time t, properly accounting for right-censoring.

    MCF(t) = sum over event times t_i <= t of: 1 / n_at_risk(t_i)

    where n_at_risk(t_i) is the number of dandisets with age >= t_i.

    Args:
        events_df: Reuse events with delay_years and same_lab columns
        dandisets_df: All dandisets with age_years
        same_lab_filter: None for all, True for same-lab only, False for diff-lab only

    Returns:
        times: sorted event times
        mcf_values: cumulative MCF at each event time
    """
    # Filter events
    if same_lab_filter is not None:
        filtered = events_df[events_df["same_lab"] == same_lab_filter]
    else:
        filtered = events_df

    if len(filtered) == 0:
        return np.array([0]), np.array([0])

    ages = dandisets_df["age_years"].values
    event_times = sorted(filtered["delay_years"].values)

    mcf_times = [0.0]
    mcf_vals = [0.0]
    cumulative = 0.0

    for t in event_times:
        n_at_risk = np.sum(ages >= t)
        if n_at_risk == 0:
            continue
        cumulative += 1.0 / n_at_risk
        # Deduplicate: if same time as last entry, update value (not append)
        if mcf_times and mcf_times[-1] == t:
            mcf_vals[-1] = cumulative
        else:
            mcf_times.append(t)
            mcf_vals.append(cumulative)

    return np.array(mcf_times), np.array(mcf_vals)


def power_law(t, a, b):
    """Power law model: E[N(t)] = a * t^b"""
    return a * np.power(t, b)


def fit_mcf_model(
    mcf_times: np.ndarray,
    mcf_values: np.ndarray,
    label: str = "",
) -> tuple[float, float]:
    """Fit a power law to the MCF for extrapolation beyond observed range.

    E[N(t)] = a * t^b

    Returns (a, b) parameters.
    """
    # Skip the leading zero
    mask = mcf_times > 0
    t = mcf_times[mask]
    y = mcf_values[mask]

    if len(t) < 3:
        print(f"  {label}: too few points to fit")
        return 0.0, 1.0

    try:
        popt, _ = curve_fit(power_law, t, y, p0=[0.1, 1.0], maxfev=5000)
        a, b = popt
        print(f"  {label}: E[N(t)] = {a:.4f} * t^{b:.2f}")
        return a, b
    except RuntimeError:
        print(f"  {label}: fit failed, using linear fallback")
        slope = y[-1] / t[-1] if t[-1] > 0 else 0
        return slope, 1.0


class MCFPredictor:
    """Predict expected papers per dandiset using raw MCF with power-law extrapolation.

    Uses linear interpolation of the empirical MCF step function for ages
    within the observed range, and the fitted power law for extrapolation
    beyond it.
    """

    def __init__(self, mcf_times: np.ndarray, mcf_values: np.ndarray, params: tuple[float, float],
                 max_age: float | None = None):
        self.mcf_times = mcf_times
        self.mcf_values = mcf_values
        self.params = params
        # max_age = oldest dandiset age; MCF is flat after last event until this age
        self.max_observed = max_age if max_age is not None else mcf_times[-1]

    def __call__(self, t):
        """Expected cumulative papers per dandiset at age t."""
        t = np.asarray(t, dtype=float)
        scalar = t.ndim == 0
        t = np.atleast_1d(t)

        result = np.zeros_like(t)
        # Within observed range: step function lookup (MCF is flat between events)
        in_range = t <= self.max_observed
        if np.any(in_range):
            # searchsorted('right') - 1 gives the index of the last MCF time <= t
            idx = np.searchsorted(self.mcf_times, t[in_range], side="right") - 1
            idx = np.clip(idx, 0, len(self.mcf_values) - 1)
            result[in_range] = self.mcf_values[idx]
        # Beyond observed range: power law extrapolation
        beyond = ~in_range
        if np.any(beyond):
            result[beyond] = power_law(t[beyond], *self.params)

        return float(result[0]) if scalar else result


def plot_mcf(
    mcf_same: tuple[np.ndarray, np.ndarray],
    mcf_diff: tuple[np.ndarray, np.ndarray],
    params_same: tuple[float, float],
    params_diff: tuple[float, float],
    output_path: Path,
):
    """Plot MCF curves with parametric fits."""
    fig, ax = plt.subplots(figsize=(10, 7))

    t_fit = np.linspace(0.01, 8, 200)

    # Same lab MCF
    ax.step(mcf_same[0], mcf_same[1], color="#e74c3c", linewidth=2,
            where="post", label="Same lab (observed)")
    ax.plot(t_fit, power_law(t_fit, *params_same), color="#e74c3c",
            linestyle="--", linewidth=1.5, alpha=0.7,
            label=f"Same lab fit: {params_same[0]:.3f}t^{params_same[1]:.2f}")

    # Different lab MCF
    ax.step(mcf_diff[0], mcf_diff[1], color="#2196F3", linewidth=2,
            where="post", label="Different lab (observed)")
    ax.plot(t_fit, power_law(t_fit, *params_diff), color="#2196F3",
            linestyle="--", linewidth=1.5, alpha=0.7,
            label=f"Diff lab fit: {params_diff[0]:.3f}t^{params_diff[1]:.2f}")

    ax.set_xlabel("Years since dandiset creation", fontsize=12)
    ax.set_ylabel("Expected cumulative reuse papers per dandiset", fontsize=12)
    ax.set_title("Mean Cumulative Function: Reuse Papers per Dandiset", fontsize=14)
    ax.set_xlim(left=0)
    ax.set_ylim(bottom=0)
    ax.legend(fontsize=10, frameon=False)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)

    plt.tight_layout()
    plt.savefig(output_path, dpi=150)
    print(f"\nMCF plot saved to {output_path}")
    return fig, ax


def project_dandiset_creation(
    creation_dates: dict[str, datetime],
    forecast_years: int = 3,
) -> pd.DataFrame:
    """Fit a linear trend to quarterly dandiset creation rate and extrapolate."""
    quarters = {}
    for ds_id, dt in creation_dates.items():
        q = (dt.month - 1) // 3 + 1
        key = f"{dt.year}-Q{q}"
        quarters[key] = quarters.get(key, 0) + 1

    sorted_keys = sorted(quarters.keys())

    # Extend historical quarters through the current quarter (even if no new dandisets)
    current_q = (TODAY.month - 1) // 3 + 1
    current_key = f"{TODAY.year}-Q{current_q}"
    while sorted_keys[-1] < current_key:
        y, q = int(sorted_keys[-1][:4]), int(sorted_keys[-1][-1])
        q += 1
        if q > 4:
            q = 1
            y += 1
        sorted_keys.append(f"{y}-Q{q}")
        quarters[sorted_keys[-1]] = 0

    counts = [quarters[k] for k in sorted_keys]
    x = np.arange(len(counts))
    slope, intercept = np.polyfit(x, counts, 1)

    n_forecast = forecast_years * 4
    x_forecast = np.arange(len(counts), len(counts) + n_forecast)

    last_year, last_q = int(sorted_keys[-1][:4]), int(sorted_keys[-1][-1])
    forecast_keys = []
    y, q = last_year, last_q
    for _ in range(n_forecast):
        q += 1
        if q > 4:
            q = 1
            y += 1
        forecast_keys.append(f"{y}-Q{q}")

    forecast_counts = np.maximum(slope * x_forecast + intercept, 0).astype(int)

    print(f"\nDandiset creation trend: {slope:.1f}/quarter")
    print(f"Projected new dandisets ({forecast_years}yr): {forecast_counts.sum()}")

    return pd.DataFrame({
        "quarter": sorted_keys + forecast_keys,
        "count": list(counts) + list(forecast_counts),
        "is_forecast": [False] * len(counts) + [True] * len(forecast_keys),
    })


def quarter_to_date(q_str):
    """Convert quarter label to end-of-quarter date."""
    year = int(q_str[:4])
    q = int(q_str[-1])
    # End of quarter: last day of month 3, 6, 9, or 12
    end_month = q * 3
    if end_month == 12:
        return datetime(year, 12, 31)
    return datetime(year, end_month + 1, 1) - timedelta(days=1)


def collect_paper_dates(
    classifications: list[dict],
    creation_dates: dict[str, datetime],
    same_lab_filter: bool | None = None,
) -> list[datetime]:
    """Collect sorted publication dates of reuse papers."""
    dates = []
    for entry in classifications:
        if entry.get("classification") != "REUSE":
            continue
        if same_lab_filter is not None:
            is_same = entry.get("same_lab") is True
            if is_same != same_lab_filter:
                continue
        ds_id = entry.get("dandiset_id", "")
        citing_date_str = entry.get("citing_date", "")
        if not ds_id or not citing_date_str or ds_id not in creation_dates:
            continue
        try:
            dt = datetime.strptime(citing_date_str, "%Y-%m-%d")
        except ValueError:
            continue
        dates.append(dt)
    return sorted(dates)


def predict_papers(
    creation_dates: dict[str, datetime],
    creation_proj: pd.DataFrame,
    mcf_predictor: MCFPredictor,
    include_forecast_dandisets: bool = True,
) -> list[float]:
    """Predict expected cumulative paper count at each quarter.

    For each quarter checkpoint, sum mcf_predictor(age) across all
    dandisets that exist at that time.

    If include_forecast_dandisets is False, only existing (historical)
    dandisets contribute — they continue to age but no new ones appear.
    """
    all_quarters = creation_proj["quarter"].tolist()
    quarter_counts = dict(zip(creation_proj["quarter"], creation_proj["count"]))

    # Group historical dandisets by creation quarter
    ds_by_quarter = {}
    for ds_id, dt in creation_dates.items():
        q = (dt.month - 1) // 3 + 1
        key = f"{dt.year}-Q{q}"
        ds_by_quarter.setdefault(key, []).append(dt)

    results = []

    for q_label in all_quarters:
        q_date = quarter_to_date(q_label)
        is_forecast = creation_proj.loc[
            creation_proj["quarter"] == q_label, "is_forecast"
        ].iloc[0]
        if not is_forecast:
            q_date = min(q_date, TODAY)
        expected = 0.0

        # Historical dandisets
        for q_key, dates in ds_by_quarter.items():
            for created in dates:
                if created > q_date:
                    continue
                age = (q_date - created).days / 365.25
                if age > 0:
                    expected += mcf_predictor(age)

        # Projected future dandisets (placed at mid-quarter)
        is_forecast = creation_proj.loc[
            creation_proj["quarter"] == q_label, "is_forecast"
        ].iloc[0]
        if is_forecast and include_forecast_dandisets:
            for prev_q in all_quarters:
                if prev_q == q_label:
                    break
                prev_is_fc = creation_proj.loc[
                    creation_proj["quarter"] == prev_q, "is_forecast"
                ].iloc[0]
                if prev_is_fc:
                    n = quarter_counts[prev_q]
                    age = (q_date - quarter_to_date(prev_q)).days / 365.25
                    if age > 0:
                        expected += n * mcf_predictor(age)

        results.append(expected)

    return results


def plot_prediction(
    creation_proj: pd.DataFrame,
    pred_same_trend: list[float],
    pred_diff_trend: list[float],
    pred_same_frozen: list[float],
    pred_diff_frozen: list[float],
    dates_same: list[datetime],
    dates_diff: list[datetime],
    output_path: Path,
):
    """Plot historical observed + two forecast scenarios."""
    import matplotlib.dates as mdates

    fig, ax = plt.subplots(figsize=(10, 7))

    all_quarters = creation_proj["quarter"].tolist()
    is_forecast = creation_proj["is_forecast"].tolist()
    n_hist = sum(not f for f in is_forecast)

    # Convert quarter labels to dates for the x-axis
    quarter_dates = [quarter_to_date(q) for q in all_quarters]
    # Cap historical quarters at TODAY
    quarter_dates = [min(d, TODAY) if not fc else d
                     for d, fc in zip(quarter_dates, is_forecast)]

    # Observed: cumulative curve incrementing by 1 at each paper date
    def plot_cumulative(dates, color, label):
        if not dates:
            return
        x = [dates[0]] + [d for d in dates]
        y = [0] + list(range(1, len(dates) + 1))
        ax.step(x, y, color=color, linewidth=2, where="post", label=label)

    plot_cumulative(dates_same, "#e74c3c", "Same lab (observed)")
    plot_cumulative(dates_diff, "#2196F3", "Different lab (observed)")

    # Model fit (historical) — dashed
    hist_dates = quarter_dates[:n_hist]
    ax.plot(hist_dates, pred_same_trend[:n_hist], color="#e74c3c", linewidth=1.5,
            linestyle="--", alpha=0.7, label="Same lab (model)")
    ax.plot(hist_dates, pred_diff_trend[:n_hist], color="#2196F3", linewidth=1.5,
            linestyle="--", alpha=0.7, label="Different lab (model)")

    # Forecast — with trend (solid)
    fc_dates = quarter_dates[n_hist - 1:]
    ax.plot(fc_dates, pred_same_trend[n_hist - 1:], color="#e74c3c", linewidth=2.5,
            label="Same lab (with new dandisets)")
    ax.plot(fc_dates, pred_diff_trend[n_hist - 1:], color="#2196F3", linewidth=2.5,
            label="Different lab (with new dandisets)")

    # Forecast — no new dandisets (dotted)
    ax.plot(fc_dates, pred_same_frozen[n_hist - 1:], color="#e74c3c", linewidth=2,
            linestyle=":", label="Same lab (no new dandisets)")
    ax.plot(fc_dates, pred_diff_frozen[n_hist - 1:], color="#2196F3", linewidth=2,
            linestyle=":", label="Different lab (no new dandisets)")

    # Shade forecast region
    forecast_start = quarter_dates[n_hist - 1]
    ax.axvspan(forecast_start, quarter_dates[-1], alpha=0.05, color="gray")
    ax.axvline(forecast_start, color="gray", linestyle=":", linewidth=1, alpha=0.5)

    # X-axis formatting
    ax.xaxis.set_major_locator(mdates.YearLocator())
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    plt.setp(ax.get_xticklabels(), rotation=45, ha="right")

    ax.set_xlabel("Date", fontsize=12)
    ax.set_ylabel("Cumulative reuse papers", fontsize=12)
    ax.set_title("Observed and Predicted Cumulative Reuse Papers", fontsize=14)
    ax.legend(fontsize=9, frameon=False, ncol=2)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.set_ylim(bottom=0)

    plt.tight_layout()
    plt.savefig(output_path, dpi=150)
    print(f"\nPrediction plot saved to {output_path}")
    return fig, ax


def main():
    parser = argparse.ArgumentParser(
        description="Predict future reuse paper counts using survival analysis"
    )
    parser.add_argument(
        "--refs", default="output/direct_ref_classifications.json",
    )
    parser.add_argument(
        "--citations", default="output/test_all_classifications.json",
    )
    parser.add_argument(
        "--dandisets", default="output/dandi_primary_papers_results.json",
    )
    parser.add_argument(
        "-o", "--output", default="output/reuse_prediction.png",
    )
    parser.add_argument(
        "--forecast-years", type=int, default=3,
    )
    parser.add_argument(
        "--open", action="store_true",
    )
    args = parser.parse_args()

    refs_path = Path(args.refs)
    citations_path = Path(args.citations)
    dandisets_path = Path(args.dandisets)
    output_path = Path(args.output)

    for p in [refs_path, citations_path, dandisets_path]:
        if not p.exists():
            print(f"Error: {p} not found", file=sys.stderr)
            sys.exit(1)

    # Load and merge
    print("Merging classification data...")
    merged = merge_data(refs_path, citations_path)
    classifications = merged["classifications"]
    print(f"Total merged pairs: {len(classifications)}")

    print("Loading dandiset creation dates...")
    creation_dates = load_dandiset_creation_dates(dandisets_path)
    print(f"Loaded creation dates for {len(creation_dates)} dandisets")

    backfill_citing_dates(classifications)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Build recurrent event data
    print("\n=== Building Recurrent Event Data ===")
    events_df, dandisets_df = build_recurrent_event_data(classifications, creation_dates)

    # Compute MCFs
    print("\n=== Computing Mean Cumulative Functions ===")
    mcf_same = compute_mcf(events_df, dandisets_df, same_lab_filter=True)
    mcf_diff = compute_mcf(events_df, dandisets_df, same_lab_filter=False)
    print(f"  Same lab MCF at max obs: {mcf_same[1][-1]:.3f} papers/dandiset")
    print(f"  Diff lab MCF at max obs: {mcf_diff[1][-1]:.3f} papers/dandiset")

    # Fit power law models
    print("\n=== Fitting Power Law Models ===")
    params_same = fit_mcf_model(*mcf_same, label="Same lab")
    params_diff = fit_mcf_model(*mcf_diff, label="Different lab")

    # Print predictions at key timepoints
    print("\nExpected reuse papers per dandiset:")
    for t in [1, 2, 3, 5]:
        n_same = power_law(t, *params_same)
        n_diff = power_law(t, *params_diff)
        print(f"  By {t}yr: {n_same:.2f} same-lab, {n_diff:.2f} diff-lab, {n_same+n_diff:.2f} total")

    # Plot MCF
    mcf_output = output_path.parent / output_path.name.replace(".png", "_mcf.png")
    plot_mcf(mcf_same, mcf_diff, params_same, params_diff, mcf_output)

    # Build MCF predictors (raw MCF for observed ages, power law for extrapolation)
    max_age = dandisets_df["age_years"].max()
    mcf_pred_same = MCFPredictor(*mcf_same, params_same, max_age=max_age)
    mcf_pred_diff = MCFPredictor(*mcf_diff, params_diff, max_age=max_age)

    # Predict future papers — two scenarios
    print("\n=== Generating Predictions ===")
    creation_proj = project_dandiset_creation(creation_dates, args.forecast_years)

    # Scenario 1: with new dandisets following trend
    pred_same_trend = predict_papers(creation_dates, creation_proj, mcf_pred_same, include_forecast_dandisets=True)
    pred_diff_trend = predict_papers(creation_dates, creation_proj, mcf_pred_diff, include_forecast_dandisets=True)

    # Scenario 2: no new dandisets (only existing ones age)
    pred_same_frozen = predict_papers(creation_dates, creation_proj, mcf_pred_same, include_forecast_dandisets=False)
    pred_diff_frozen = predict_papers(creation_dates, creation_proj, mcf_pred_diff, include_forecast_dandisets=False)

    dates_same = collect_paper_dates(classifications, creation_dates, same_lab_filter=True)
    dates_diff = collect_paper_dates(classifications, creation_dates, same_lab_filter=False)

    plot_prediction(
        creation_proj,
        pred_same_trend, pred_diff_trend,
        pred_same_frozen, pred_diff_frozen,
        dates_same, dates_diff, output_path,
    )

    # Summary
    n_hist = sum(not f for f in creation_proj["is_forecast"])
    print(f"\nCurrent observed: {len(dates_same)} same-lab, "
          f"{len(dates_diff)} diff-lab papers")
    print(f"Current modeled:  {pred_same_trend[n_hist-1]:.0f} same-lab, "
          f"{pred_diff_trend[n_hist-1]:.0f} diff-lab papers")
    print(f"\nForecast ({args.forecast_years}yr) — no new dandisets:")
    print(f"  {pred_same_frozen[-1]:.0f} same-lab, {pred_diff_frozen[-1]:.0f} diff-lab papers")
    print(f"Forecast ({args.forecast_years}yr) — with trend growth:")
    print(f"  {pred_same_trend[-1]:.0f} same-lab, {pred_diff_trend[-1]:.0f} diff-lab papers")

    if args.open:
        subprocess.run(["open", str(mcf_output)])
        subprocess.run(["open", str(output_path)])


if __name__ == "__main__":
    main()
