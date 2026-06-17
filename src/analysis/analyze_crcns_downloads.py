#!/usr/bin/env python3
"""
analyze_crcns_downloads.py — CRCNS download metrics vs publication-based reuse.

Parallel to analyze_downloads_vs_reuse.py (DANDI). Source TSV is monthly per-dataset
access stats from CRCNS; "regions" here means unique_networks (ASN/CIDR-block
diversity), used as a proxy for geographic+organizational reach since the TSV is
already aggregated and lacks raw IPs.

Generates: output/crcns/figures/downloads_vs_reuse.png
"""

import csv
import json
from collections import Counter, defaultdict
from pathlib import Path

import matplotlib
import matplotlib.pyplot as plt
matplotlib.rcParams["font.family"] = "Helvetica"
import numpy as np
from scipy import stats

TSV_PATH = Path("/Users/bdichter/Downloads/crcns-overall-20260427.tsv")
CRCNS_DIR = Path("output/crcns")
FIG_PATH = CRCNS_DIR / "figures" / "downloads_vs_reuse.png"

# TSV "dataset" column has parse-artifact variants (e.g. "aa-1Download",
# "alm-3http:"); strip these so they merge into the canonical id.
ARTIFACT_SUFFIXES = ("Download", "http:")


def load_data():
    with open(CRCNS_DIR / "datasets.json") as f:
        ds_meta = json.load(f)
    known = {d["dandiset_id"] for d in ds_meta["results"]}

    with open(CRCNS_DIR / "classifications.json") as f:
        cls = json.load(f)
    diff_reuse = Counter()
    total_reuse = Counter()
    for c in cls["classifications"]:
        if c["classification"] != "REUSE":
            continue
        did = c.get("dandiset_id", "")
        total_reuse[did] += 1
        if c.get("same_lab") is False:
            diff_reuse[did] += 1

    agg = defaultdict(lambda: {"bytes": 0, "requests": 0,
                               "ip_months": 0, "net_months": 0, "months": 0})
    with open(TSV_PATH) as f:
        for row in csv.DictReader(f, delimiter="\t"):
            did = row["dataset"]
            for suf in ARTIFACT_SUFFIXES:
                if did.endswith(suf):
                    did = did[:-len(suf)]
            if did not in known:
                continue
            a = agg[did]
            a["bytes"] += int(row["bytes_total"])
            a["requests"] += int(row["requests_total"])
            a["ip_months"] += int(row["unique_ips"])
            a["net_months"] += int(row["unique_networks"])
            a["months"] += 1

    dids, bytes_, requests_, ip_months, net_months, reuse_d, reuse_t = ([] for _ in range(7))
    for did in sorted(agg.keys()):
        a = agg[did]
        if a["bytes"] == 0 and a["requests"] == 0:
            continue
        dids.append(did)
        bytes_.append(a["bytes"])
        requests_.append(a["requests"])
        ip_months.append(a["ip_months"])
        net_months.append(a["net_months"])
        reuse_d.append(diff_reuse.get(did, 0))
        reuse_t.append(total_reuse.get(did, 0))

    return {
        "dids": dids,
        "bytes": np.array(bytes_),
        "requests": np.array(requests_),
        "ip_months": np.array(ip_months),
        "net_months": np.array(net_months),
        "reuse_diff": np.array(reuse_d),
        "reuse_total": np.array(reuse_t),
    }


def _scatter_panel(ax, x, reuse, has_reuse, no_reuse, dids, label_dids,
                   xlabel, title, color):
    rng = np.random.default_rng(42)
    reuse_jit = reuse.astype(float).copy()
    reuse_jit[no_reuse] = rng.uniform(-0.3, 0.3, no_reuse.sum())

    log_x = np.log10(np.maximum(x, 1))

    ax.scatter(log_x[no_reuse], reuse_jit[no_reuse],
               s=14, alpha=0.3, color="#9E9E9E", zorder=1)
    ax.scatter(log_x[has_reuse], reuse[has_reuse],
               s=28, alpha=0.75, color=color, zorder=2,
               edgecolors="white", linewidth=0.3)

    for ld in label_dids:
        if ld in dids:
            i = dids.index(ld)
            ax.annotate(ld, (log_x[i], reuse[i]),
                        fontsize=7, color="#444",
                        xytext=(6, 4), textcoords="offset points",
                        arrowprops=dict(arrowstyle="-", color="#999", lw=0.5))

    pr, pp = stats.pearsonr(log_x, reuse)
    sr, sp = stats.spearmanr(x, reuse)
    ax.text(0.97, 0.97,
            f"Pearson r = {pr:.2f} (p={pp:.1e})\nSpearman ρ = {sr:.2f} (p={sp:.1e})",
            transform=ax.transAxes, ha="right", va="top", fontsize=8,
            bbox=dict(boxstyle="round,pad=0.3", facecolor="white", alpha=0.85))

    ax.set_xlabel(xlabel, fontsize=10)
    ax.set_ylabel("Different-lab reuse papers", fontsize=10)
    ax.set_title(title, fontsize=11, fontweight="bold", loc="left")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)


def plot(data):
    fig, axes = plt.subplots(1, 3, figsize=(15, 4.6))

    reuse = data["reuse_diff"]
    has = reuse > 0
    no = ~has
    dids = data["dids"]

    # Top reuse datasets get labeled
    top_idx = np.argsort(reuse)[::-1][:6]
    label_dids = [dids[i] for i in top_idx]

    _scatter_panel(axes[0], data["bytes"], reuse, has, no, dids, label_dids,
                   xlabel="Total bytes downloaded (log)", title="A. Volume (bytes)",
                   color="#E65100")
    axes[0].set_xticks([6, 7, 8, 9, 10])
    axes[0].set_xticklabels(["1 MB", "10 MB", "100 MB", "1 GB", "10 GB"])

    _scatter_panel(axes[1], data["requests"], reuse, has, no, dids, label_dids,
                   xlabel="Total HTTP requests (log)", title="B. Volume (requests)",
                   color="#2196F3")

    _scatter_panel(axes[2], data["net_months"], reuse, has, no, dids, label_dids,
                   xlabel="Unique networks (network-months, log)",
                   title="C. Network reach (proxy for geographic diversity)",
                   color="#2E7D32")

    fig.suptitle("CRCNS: download metrics vs different-lab reuse",
                 fontsize=12, fontweight="bold", y=1.02)
    fig.tight_layout()
    FIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(FIG_PATH, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"Saved {FIG_PATH}")


def main():
    data = load_data()
    n = len(data["dids"])
    print(f"Datasets matched (TSV ∩ CRCNS metadata): {n}")
    print(f"  with different-lab reuse > 0: {(data['reuse_diff'] > 0).sum()}")
    print(f"  with any reuse > 0:           {(data['reuse_total'] > 0).sum()}")
    print()

    print("Spearman correlations vs different-lab reuse:")
    for name, x in [("bytes", data["bytes"]),
                    ("requests", data["requests"]),
                    ("ip-months", data["ip_months"]),
                    ("network-months", data["net_months"])]:
        r, p = stats.spearmanr(x, data["reuse_diff"])
        print(f"  {name:<16} ρ = {r:+.3f}  p = {p:.2e}")

    print("\nSpearman correlations vs total reuse (same+different lab):")
    for name, x in [("bytes", data["bytes"]),
                    ("requests", data["requests"]),
                    ("network-months", data["net_months"])]:
        r, p = stats.spearmanr(x, data["reuse_total"])
        print(f"  {name:<16} ρ = {r:+.3f}  p = {p:.2e}")

    # Top-10 overlap
    top_vol = set(np.array(data["dids"])[np.argsort(data["bytes"])[::-1][:10]])
    top_net = set(np.array(data["dids"])[np.argsort(data["net_months"])[::-1][:10]])
    top_reu = set(np.array(data["dids"])[np.argsort(data["reuse_diff"])[::-1][:10]])
    print(f"\nTop-10 overlap with reuse:")
    print(f"  bytes:    {len(top_vol & top_reu)}/10  ({sorted(top_vol & top_reu)})")
    print(f"  networks: {len(top_net & top_reu)}/10  ({sorted(top_net & top_reu)})")

    plot(data)


if __name__ == "__main__":
    main()
