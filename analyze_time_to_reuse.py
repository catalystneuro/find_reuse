#!/usr/bin/env python3
"""
analyze_time_to_reuse.py - Analyze delay between dandiset creation and secondary publications.

Produces two histograms:
1. Time from dandiset creation to secondary publication (all sources)
2. Time from primary paper publication to secondary publication (citation data only)

Both compare REUSE same lab, REUSE different lab, and MENTION.

Usage:
    python analyze_time_to_reuse.py \
        --refs output/direct_ref_classifications.json \
        --citations output/test_all_classifications.json \
        --dandisets output/dandi_primary_papers_results.json \
        -o output/time_to_reuse_histogram.png --open
"""

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import requests
from tqdm import tqdm

from generate_combined_dashboard import merge_data

DAYS_PER_MONTH = 30.44
DATE_CACHE_PATH = Path(__file__).parent / ".doi_date_cache.json"


def load_primary_paper_dates(dandisets_file: Path) -> dict[str, dict[str, str]]:
    """Build dandiset_id -> {cited_doi: publication_date} lookup for primary papers."""
    with open(dandisets_file) as f:
        data = json.load(f)

    lookup = {}
    for entry in data.get("results", []):
        ds_id = entry.get("dandiset_id")
        if not ds_id:
            continue
        paper_dates = {}
        for rel in entry.get("paper_relations", []):
            doi = rel.get("doi", "")
            pub_date = rel.get("publication_date", "")
            if doi and pub_date:
                paper_dates[doi.lower()] = pub_date
        if paper_dates:
            lookup[ds_id] = paper_dates
    return lookup


def load_dandiset_creation_dates(dandisets_file: Path) -> dict[str, datetime]:
    """Build dandiset_id -> creation datetime lookup."""
    with open(dandisets_file) as f:
        data = json.load(f)

    dates = {}
    for entry in data.get("results", []):
        ds_id = entry.get("dandiset_id")
        created = entry.get("dandiset_created")
        if ds_id and created:
            # Strip trailing Z and parse ISO format
            dates[ds_id] = datetime.fromisoformat(created.replace("Z", "+00:00")).replace(tzinfo=None)
    return dates


def load_date_cache() -> dict[str, str]:
    """Load cached DOI -> publication date mappings."""
    if DATE_CACHE_PATH.exists():
        with open(DATE_CACHE_PATH) as f:
            return json.load(f)
    return {}


def save_date_cache(cache: dict[str, str]):
    """Save DOI -> publication date cache."""
    with open(DATE_CACHE_PATH, "w") as f:
        json.dump(cache, f, indent=2)


def fetch_publication_dates(dois: list[str]) -> dict[str, str]:
    """Fetch publication dates from OpenAlex for a list of DOIs.

    Returns dict mapping DOI -> date string (YYYY-MM-DD).
    Uses a local cache to avoid redundant API calls.
    """
    cache = load_date_cache()

    # Find DOIs not in cache
    missing = [d for d in dois if d not in cache]
    if not missing:
        return {d: cache[d] for d in dois if cache.get(d)}

    print(f"Fetching publication dates for {len(missing)} DOIs from OpenAlex...")
    session = requests.Session()
    session.headers["User-Agent"] = "find_reuse/1.0 (mailto:ben.dichter@catalystneuro.com)"

    for doi in tqdm(missing, desc="Fetching dates"):
        try:
            resp = session.get(
                f"https://api.openalex.org/works/doi:{doi}",
                timeout=10,
            )
            if resp.ok:
                data = resp.json()
                pub_date = data.get("publication_date", "")
                cache[doi] = pub_date if pub_date else ""
            else:
                cache[doi] = ""
        except requests.RequestException:
            cache[doi] = ""
        time.sleep(0.1)  # Rate limiting

    save_date_cache(cache)
    found = sum(1 for d in missing if cache.get(d))
    print(f"Found dates for {found}/{len(missing)} DOIs")

    return {d: cache[d] for d in dois if cache.get(d)}


def backfill_citing_dates(classifications: list[dict]):
    """Fill in missing citing_date fields by looking up DOIs via OpenAlex."""
    missing_dois = list(set(
        entry["citing_doi"]
        for entry in classifications
        if not entry.get("citing_date") and entry.get("citing_doi")
    ))

    if not missing_dois:
        return

    print(f"\n{len(missing_dois)} unique DOIs missing citing_date")
    dates = fetch_publication_dates(missing_dois)

    filled = 0
    for entry in classifications:
        if not entry.get("citing_date") and entry.get("citing_doi"):
            date = dates.get(entry["citing_doi"], "")
            if date:
                entry["citing_date"] = date
                filled += 1
    print(f"Backfilled {filled} entries with publication dates")


def compute_delays(classifications: list[dict], creation_dates: dict[str, datetime]) -> dict[str, list[float]]:
    """Compute delay in months for each classification group.

    Returns dict with keys 'reuse_same_lab', 'reuse_diff_lab', 'mention'
    mapping to lists of delay values in months.
    """
    groups = {
        "reuse_same_lab": [],
        "reuse_diff_lab": [],
        "mention": [],
    }

    skipped_no_date = 0
    skipped_no_dandiset = 0

    for entry in classifications:
        classification = entry.get("classification", "")
        dandiset_id = entry.get("dandiset_id", "")
        citing_date_str = entry.get("citing_date", "")

        # Only interested in REUSE and MENTION
        if classification not in ("REUSE", "MENTION"):
            continue

        # Need both dates
        if not citing_date_str:
            skipped_no_date += 1
            continue
        if dandiset_id not in creation_dates:
            skipped_no_dandiset += 1
            continue

        try:
            citing_date = datetime.strptime(citing_date_str, "%Y-%m-%d")
        except ValueError:
            skipped_no_date += 1
            continue

        delay_years = (citing_date - creation_dates[dandiset_id]).days / 365.25

        if classification == "REUSE":
            if entry.get("same_lab") is True:
                groups["reuse_same_lab"].append(delay_years)
            else:
                groups["reuse_diff_lab"].append(delay_years)
        elif classification == "MENTION":
            groups["mention"].append(delay_years)

    print(f"Skipped {skipped_no_date} entries with missing citing_date")
    print(f"Skipped {skipped_no_dandiset} entries with dandiset_id not in primary papers results")

    return groups


def compute_delays_from_primary(
    classifications: list[dict],
    primary_paper_dates: dict[str, dict[str, str]],
) -> dict[str, list[float]]:
    """Compute delay from primary paper publication to citing paper publication.

    Only uses citation-based entries (source_type "citation_analysis" or "both")
    since they have a cited_doi linking to the primary paper.

    Returns dict with keys 'reuse_same_lab', 'reuse_diff_lab', 'mention'.
    """
    groups = {
        "reuse_same_lab": [],
        "reuse_diff_lab": [],
        "mention": [],
    }

    skipped_no_citing_date = 0
    skipped_no_primary_date = 0
    skipped_no_cited_doi = 0

    for entry in classifications:
        classification = entry.get("classification", "")
        if classification not in ("REUSE", "MENTION"):
            continue

        # Only citation-based entries have cited_doi
        cited_doi = entry.get("cited_doi", "")
        if not cited_doi:
            skipped_no_cited_doi += 1
            continue

        citing_date_str = entry.get("citing_date", "")
        if not citing_date_str:
            skipped_no_citing_date += 1
            continue

        dandiset_id = entry.get("dandiset_id", "")
        ds_papers = primary_paper_dates.get(dandiset_id, {})
        primary_date_str = ds_papers.get(cited_doi.lower(), "")
        if not primary_date_str:
            skipped_no_primary_date += 1
            continue

        try:
            citing_date = datetime.strptime(citing_date_str, "%Y-%m-%d")
            primary_date = datetime.strptime(primary_date_str, "%Y-%m-%d")
        except ValueError:
            skipped_no_citing_date += 1
            continue

        delay_years = (citing_date - primary_date).days / 365.25

        if classification == "REUSE":
            if entry.get("same_lab") is True:
                groups["reuse_same_lab"].append(delay_years)
            else:
                groups["reuse_diff_lab"].append(delay_years)
        elif classification == "MENTION":
            groups["mention"].append(delay_years)

    print(f"Skipped {skipped_no_cited_doi} entries with no cited_doi (direct refs)")
    print(f"Skipped {skipped_no_citing_date} entries with missing citing_date")
    print(f"Skipped {skipped_no_primary_date} entries with no primary paper date match")

    return groups


def plot_histogram(
    groups: dict[str, list[float]],
    output_path: Path,
    title: str = "Time from Dandiset Creation to Secondary Publication",
    xlabel: str = "Years since dandiset creation",
):
    """Plot density lines with tick-and-whisker summary underneath."""
    fig, (ax_hist, ax_box) = plt.subplots(
        2, 1, figsize=(10, 7), gridspec_kw={"height_ratios": [3, 1]}, sharex=True,
    )

    # Determine shared bin edges
    all_values = []
    for v in groups.values():
        all_values.extend(v)

    if not all_values:
        print("No data to plot!")
        return fig, ax_hist

    bin_max = max(all_values)
    bins = np.arange(
        0,  # start at 0
        min(bin_max + 0.5, 10),  # clip at 10 years
        0.5,  # 6-month bins
    )

    colors = {"reuse_same_lab": "#e74c3c", "reuse_diff_lab": "#2196F3", "mention": "#95a5a6"}

    def make_label(name, vals):
        arr = np.array(vals)
        return f"{name} (n={len(vals)}, {np.mean(arr):.1f}\u00b1{np.std(arr):.1f} yr)"

    labels = {
        "reuse_same_lab": make_label("Reuse, same lab", groups["reuse_same_lab"]) if groups["reuse_same_lab"] else "Reuse, same lab",
        "reuse_diff_lab": make_label("Reuse, different lab", groups["reuse_diff_lab"]) if groups["reuse_diff_lab"] else "Reuse, different lab",
        "mention": make_label("Mention", groups["mention"]) if groups["mention"] else "Mention",
    }

    # Density lines
    for key in ["mention", "reuse_diff_lab", "reuse_same_lab"]:
        if groups[key]:
            counts, bin_edges = np.histogram(groups[key], bins=bins, density=True)
            bin_centers = (bin_edges[:-1] + bin_edges[1:]) / 2
            ax_hist.plot(
                bin_centers,
                counts,
                color=colors[key],
                label=labels[key],
                linewidth=2,
            )

    ax_hist.set_ylabel("Density", fontsize=12)
    ax_hist.set_title(title, fontsize=14)
    ax_hist.legend(fontsize=10, frameon=False)
    ax_hist.set_xlim(left=0)
    ax_hist.spines["top"].set_visible(False)
    ax_hist.spines["right"].set_visible(False)

    # Box plots underneath
    plot_order = ["reuse_same_lab", "reuse_diff_lab", "mention"]
    plot_labels = ["Reuse,\nsame lab", "Reuse,\ndiff lab", "Mention"]
    box_data = [groups[k] for k in plot_order]
    positions = list(range(len(plot_order)))

    bp = ax_box.boxplot(
        box_data,
        positions=positions,
        vert=False,
        widths=0.6,
        patch_artist=True,
        showfliers=False,
        medianprops={"color": "black", "linewidth": 1.5},
        whiskerprops={"linewidth": 1.2},
        capprops={"linewidth": 1.2},
    )
    for patch, key in zip(bp["boxes"], plot_order):
        patch.set_facecolor(colors[key])
        patch.set_alpha(0.4)
        patch.set_edgecolor(colors[key])

    # Plot means as diamond markers
    for i, key in enumerate(plot_order):
        if groups[key]:
            mean = np.mean(groups[key])
            ax_box.plot(mean, i, "D", color=colors[key], markersize=7, zorder=5)

    ax_box.set_yticks(positions)
    ax_box.set_yticklabels(plot_labels, fontsize=10)
    ax_box.set_xlabel(xlabel, fontsize=12)
    ax_box.set_xlim(left=0)
    ax_box.spines["top"].set_visible(False)
    ax_box.spines["right"].set_visible(False)
    ax_box.invert_yaxis()

    plt.tight_layout()
    plt.savefig(output_path, dpi=150)
    print(f"\nHistogram saved to {output_path}")

    return fig, ax_hist


def print_stats(groups: dict[str, list[float]]):
    """Print summary statistics for each group."""
    print("\n--- Summary Statistics (years) ---")
    for key, label in [
        ("reuse_same_lab", "Reuse (same lab)"),
        ("reuse_diff_lab", "Reuse (different lab)"),
        ("mention", "Mention"),
    ]:
        vals = groups[key]
        if vals:
            arr = np.array(vals)
            print(f"\n{label} (n={len(vals)}):")
            print(f"  Median: {np.median(arr):.2f}")
            print(f"  Mean:   {np.mean(arr):.2f}")
            print(f"  Std:    {np.std(arr):.2f}")
            print(f"  Min:    {np.min(arr):.2f}")
            print(f"  Max:    {np.max(arr):.2f}")
        else:
            print(f"\n{label}: no data")


def main():
    parser = argparse.ArgumentParser(
        description="Analyze time between dandiset creation and secondary publications"
    )
    parser.add_argument(
        "--refs",
        default="output/direct_ref_classifications.json",
        help="Direct reference classifications file",
    )
    parser.add_argument(
        "--citations",
        default="output/test_all_classifications.json",
        help="Citation-based classifications file",
    )
    parser.add_argument(
        "--dandisets",
        default="output/dandi_primary_papers_results.json",
        help="Dandiset primary papers results file",
    )
    parser.add_argument(
        "-o", "--output",
        default="output/time_to_reuse_histogram.png",
        help="Output histogram path",
    )
    parser.add_argument(
        "--open", action="store_true",
        help="Open the histogram after generating",
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

    # Merge classifications from both sources
    print("Merging classification data...")
    merged = merge_data(refs_path, citations_path)
    classifications = merged["classifications"]
    print(f"Total merged pairs: {len(classifications)}")

    # Load dandiset creation dates
    print("Loading dandiset creation dates...")
    creation_dates = load_dandiset_creation_dates(dandisets_path)
    print(f"Loaded creation dates for {len(creation_dates)} dandisets")

    # Load primary paper publication dates
    print("Loading primary paper publication dates...")
    primary_paper_dates = load_primary_paper_dates(dandisets_path)
    print(f"Loaded primary paper dates for {len(primary_paper_dates)} dandisets")

    # Backfill missing citing dates from OpenAlex
    backfill_citing_dates(classifications)

    output_path.parent.mkdir(parents=True, exist_ok=True)

    # --- Plot 1: Time from dandiset creation ---
    print("\n=== Plot 1: Time from dandiset creation ===")
    print("Computing delays from dandiset creation...")
    groups1 = compute_delays(classifications, creation_dates)
    print_stats(groups1)
    plot_histogram(
        groups1, output_path,
        title="Time from Dandiset Creation to Secondary Publication",
        xlabel="Years since dandiset creation",
    )

    # --- Plot 2: Time from primary paper publication (citation data only) ---
    print("\n=== Plot 2: Time from primary paper publication ===")
    print("Computing delays from primary paper publication...")
    groups2 = compute_delays_from_primary(classifications, primary_paper_dates)
    print_stats(groups2)
    output2 = output_path.parent / output_path.name.replace(".png", "_from_primary.png")
    plot_histogram(
        groups2, output2,
        title="Time from Primary Paper to Secondary Publication",
        xlabel="Years since primary paper publication",
    )

    if args.open:
        subprocess.run(["open", str(output_path)])
        subprocess.run(["open", str(output2)])


if __name__ == "__main__":
    main()
