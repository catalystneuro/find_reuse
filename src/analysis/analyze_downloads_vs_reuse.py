#!/usr/bin/env python3
"""
analyze_downloads_vs_reuse.py — Compare DANDI download metrics with publication-based reuse.

Generates a 2-panel figure showing that download volume and citation-based reuse
measure different dimensions of data impact.

Requires: access-summaries repo at ../access-summaries/
Generates: output/figures/downloads_vs_reuse.png
"""

import json
from collections import Counter
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from scipy import stats

SUMMARIES_DIR = Path("../access-summaries/content/summaries")
TOTALS_PATH = Path("../access-summaries/content/totals.json")

# Dandisets with "test" in the name — not real scientific data
TEST_DANDISET_IDS = {
    "000027", "000029", "000032", "000033", "000038", "000047", "000068", "000071",
    "000112", "000116", "000118", "000120", "000123", "000124", "000126", "000135",
    "000144", "000145", "000150", "000151", "000154", "000160", "000161", "000162",
    "000164", "000171", "000241", "000299", "000335", "000346", "000349", "000400",
    "000411", "000445", "000470", "000478", "000490", "000529", "000536", "000539",
    "000543", "000544", "000545", "000567", "000712", "000730", "000733", "000881",
    "000942", "000960", "001022", "001049", "001061", "001066", "001083", "001085",
    "001133", "001175", "001450", "001698", "001758",
}


def _get_nonempty_dandiset_ids():
    """Fetch non-empty dandiset IDs from DANDI API."""
    import requests
    session = requests.Session()
    nonempty = set()
    page = 1
    while True:
        resp = session.get(
            f"https://api.dandiarchive.org/api/dandisets/?page_size=200&page={page}",
            timeout=15,
        )
        data = resp.json()
        for ds in data.get("results", []):
            draft = ds.get("draft_version", {})
            if draft.get("asset_count", 0) > 0 or draft.get("size", 0) > 0:
                nonempty.add(ds["identifier"])
        if not data.get("next"):
            break
        page += 1
    return nonempty


def load_data():
    """Load download metrics and reuse counts."""
    with open(TOTALS_PATH) as f:
        downloads = json.load(f)

    with open("output/all_classifications.json") as f:
        cls_data = json.load(f)

    nonempty_ids = _get_nonempty_dandiset_ids()

    diff_reuse = Counter()
    for c in cls_data["classifications"]:
        if c["classification"] == "REUSE" and c.get("same_lab") is False:
            diff_reuse[c.get("dandiset_id", "")] += 1

    dids = []
    n_days = []
    n_regions = []
    reuse = []
    log_bytes = []

    for did in sorted(downloads.keys()):
        if did in ("undetermined", "unassociated") or did in TEST_DANDISET_IDS or did not in nonempty_ids:
            continue
        day_file = SUMMARIES_DIR / did / "by_day.tsv"
        if not day_file.exists():
            continue
        with open(day_file) as f:
            days = sum(1 for _ in f) - 1

        dids.append(did)
        n_days.append(days)
        n_regions.append(downloads[did].get("number_of_unique_regions", 0))
        reuse.append(diff_reuse.get(did, 0))
        log_bytes.append(np.log10(max(downloads[did]["total_bytes_sent"], 1)))

    return {
        "dids": dids,
        "n_days": np.array(n_days),
        "n_regions": np.array(n_regions),
        "reuse": np.array(reuse),
        "log_bytes": np.array(log_bytes),
    }


def _scatter_panel(ax, log_x, reuse, reuse_jittered, has_reuse, no_reuse,
                   data_dids, labels_offsets, xlabel, title, color, pr_label):
    """Draw one scatter panel."""
    ax.scatter(log_x[no_reuse], reuse_jittered[no_reuse],
               s=12, alpha=0.25, color="#9E9E9E", zorder=1)
    ax.scatter(log_x[has_reuse], reuse[has_reuse],
               s=25, alpha=0.7, color=color, zorder=2, edgecolors="white",
               linewidth=0.3)

    for label_did, offset in labels_offsets:
        if label_did in data_dids:
            idx = data_dids.index(label_did)
            ax.annotate(label_did, (log_x[idx], reuse[idx]),
                        fontsize=7, color="#555555",
                        xytext=offset, textcoords="offset points",
                        arrowprops=dict(arrowstyle="-", color="#999999", lw=0.5))

    pr, pp = stats.pearsonr(log_x, reuse)
    ax.text(0.97, 0.97, f"r = {pr:.2f}, p = {pp:.1e}",
            transform=ax.transAxes, ha="right", va="top", fontsize=8,
            bbox=dict(boxstyle="round,pad=0.3", facecolor="white", alpha=0.8))

    ax.set_xlabel(xlabel, fontsize=10)
    ax.set_ylabel("Different-lab reuse papers", fontsize=10)
    ax.set_title(title, fontsize=11, fontweight="bold", loc="left")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)


def plot(data):
    fig, axes = plt.subplots(1, 3, figsize=(14, 4.5))

    reuse = data["reuse"]
    has_reuse = reuse > 0
    no_reuse = ~has_reuse

    log_bytes = data["log_bytes"]
    log_days = np.log10(data["n_days"] + 1)
    log_regions = np.log10(data["n_regions"] + 1)

    rng = np.random.default_rng(42)
    reuse_jittered = reuse.astype(float).copy()
    reuse_jittered[no_reuse] = rng.uniform(-0.3, 0.3, no_reuse.sum())

    dids = data["dids"]

    # --- Panel A: Total bytes downloaded vs reuse ---
    _scatter_panel(
        axes[0], log_bytes, reuse, reuse_jittered, has_reuse, no_reuse, dids,
        labels_offsets=[("000108", (8, 3)), ("000253", (8, 5)),
                        ("000020", (8, -8)), ("000768", (-55, 5))],
        xlabel="Total bytes downloaded", title="A. Download volume",
        color="#E65100", pr_label="log bytes vs reuse",
    )
    axes[0].set_xticks([6, 8, 10, 12, 14])
    axes[0].set_xticklabels(["1 MB", "100 MB", "10 GB", "1 TB", "100 TB"])

    # --- Panel B: Unique download days vs reuse ---
    _scatter_panel(
        axes[1], log_days, reuse, reuse_jittered, has_reuse, no_reuse, dids,
        labels_offsets=[("000253", (8, 5)), ("000020", (8, -8)),
                        ("000768", (-55, 5)), ("000108", (8, 3))],
        xlabel="Unique download days", title="B. Download frequency",
        color="#2196F3", pr_label="log days vs reuse",
    )
    axes[1].set_xticks([np.log10(v + 1) for v in [1, 10, 100, 1000]])
    axes[1].set_xticklabels(["1", "10", "100", "1000"])

    # --- Panel C: Unique regions vs reuse ---
    _scatter_panel(
        axes[2], log_regions, reuse, reuse_jittered, has_reuse, no_reuse, dids,
        labels_offsets=[("000253", (8, 5)), ("000020", (8, -8)),
                        ("000768", (-55, 5)), ("000108", (8, 3))],
        xlabel="Unique download regions", title="C. Geographic reach",
        color="#2E7D32", pr_label="log regions vs reuse",
    )
    axes[2].set_xticks([np.log10(v + 1) for v in [1, 3, 10, 30, 100, 300, 1000]])
    axes[2].set_xticklabels(["1", "3", "10", "30", "100", "300", "1000"])

    fig.tight_layout()
    Path("output/figures").mkdir(parents=True, exist_ok=True)
    fig.savefig("output/figures/downloads_vs_reuse.png", dpi=150, bbox_inches="tight")
    plt.close()
    print("Saved output/figures/downloads_vs_reuse.png")


def main():
    data = load_data()
    reuse = data["reuse"]
    print(f"Dandisets: {len(data['dids'])}")
    print(f"  with reuse > 0: {(reuse > 0).sum()}")

    sr1, sp1 = stats.spearmanr(data["n_days"], reuse)
    sr2, sp2 = stats.spearmanr(data["n_regions"], reuse)
    sr3, sp3 = stats.spearmanr(data["log_bytes"], reuse)
    print(f"\nSpearman (all dandisets):")
    print(f"  Download days vs reuse:  r={sr1:.3f}, p={sp1:.2e}")
    print(f"  Unique regions vs reuse: r={sr2:.3f}, p={sp2:.2e}")
    print(f"  Total bytes vs reuse:    r={sr3:.3f}, p={sp3:.2e}")

    # Pearson: log(x) vs reuse
    pr0, pp0 = stats.pearsonr(data["log_bytes"], reuse)
    pr1, pp1 = stats.pearsonr(np.log10(data["n_days"] + 1), reuse)
    pr2, pp2 = stats.pearsonr(np.log10(data["n_regions"] + 1), reuse)
    print(f"\nPearson (log x vs reuse, all dandisets):")
    print(f"  log(bytes) vs reuse:     r={pr0:.3f}, p={pp0:.2e}")
    print(f"  log(days+1) vs reuse:    r={pr1:.3f}, p={pp1:.2e}")
    print(f"  log(regions+1) vs reuse: r={pr2:.3f}, p={pp2:.2e}")

    # Top 10 overlap
    top_days = sorted(range(len(data["dids"])), key=lambda i: data["n_days"][i], reverse=True)[:10]
    top_reuse = sorted(range(len(data["dids"])), key=lambda i: reuse[i], reverse=True)[:10]
    overlap = set(data["dids"][i] for i in top_days) & set(data["dids"][i] for i in top_reuse)
    print(f"\nTop-10 overlap (days vs reuse): {len(overlap)}/10")

    plot(data)


if __name__ == "__main__":
    main()
