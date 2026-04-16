#!/usr/bin/env python3
"""
render_direct_ref_flow.py — Flowchart for the direct dandiset reference discovery pipeline.

Shows: full-text search → text analysis → dataset extraction → classification.

Generates: output/direct_ref_flow.png
"""

import json
from collections import Counter

import graphviz


def load_stats():
    with open("output/results_dandi.json") as f:
        refs = json.load(f)
    with open("output/direct_ref_classifications.json") as f:
        direct = json.load(f)

    stats = refs["query_metadata"]["search_stats"]
    epmc = stats["europe_pmc"]["DANDI Archive"]
    oa = stats["openalex"]["DANDI Archive"]

    # Source overlap
    epmc_only = oa_only = both = 0
    all_papers = refs["results"] + refs["papers_without_datasets"]
    for r in all_papers:
        sources = r.get("search_sources", [])
        has_epmc = any("europe_pmc" in s for s in sources)
        has_oa = any("openalex" in s for s in sources)
        if has_epmc and has_oa:
            both += 1
        elif has_epmc:
            epmc_only += 1
        elif has_oa:
            oa_only += 1

    with_ds = len(refs["results"])
    without_ds = len(refs["papers_without_datasets"])
    total = refs["query_metadata"]["total_unique_papers"]

    # Dedup stats
    dedup = refs.get("deduplication", {})
    pre_dedup = dedup.get("original_results_count", with_ds)
    n_deduped = pre_dedup - with_ds

    # Text availability (across ALL papers, not just those with datasets)
    all_papers = refs["results"] + refs["papers_without_datasets"]
    with_text = sum(1 for r in all_papers
                    if r.get("text_length", 0) > 3000 and r.get("source", "") != "crossref")
    no_text = len(all_papers) - with_text

    # Unique dandisets
    all_ds = set()
    for r in refs["results"]:
        for arch in r.get("archives", {}).values():
            all_ds.update(arch.get("dataset_ids", []))

    # Pattern types
    pattern_counts = Counter()
    for r in refs["results"]:
        dandi = r.get("archives", {}).get("DANDI Archive", {})
        for m in dandi.get("matches", []):
            pt = m.get("pattern_type", "unknown")
            if pt in ("url", "gui_url"):
                pattern_counts["URL"] += 1
            elif pt == "doi":
                pattern_counts["DOI"] += 1
            else:
                pattern_counts["Other"] += 1

    cls_counts = Counter(c["classification"] for c in direct["classifications"])

    return {
        "epmc": epmc,
        "oa": oa,
        "total": total,
        "epmc_only": epmc_only,
        "oa_only": oa_only,
        "both": both,
        "with_ds": with_ds,
        "without_ds": without_ds,
        "n_dandisets": len(all_ds),
        "n_pairs": len(direct["classifications"]),
        "n_reuse": cls_counts.get("REUSE", 0),
        "n_primary": cls_counts.get("PRIMARY", 0),
        "n_neither": cls_counts.get("NEITHER", 0),
        "pat_doi": pattern_counts.get("DOI", 0),
        "pat_url": pattern_counts.get("URL", 0),
        "pat_other": pattern_counts.get("Other", 0),
        "pre_dedup": pre_dedup,
        "n_deduped": n_deduped,
        "with_text": with_text,
        "no_text": no_text,
    }


def pct(n, total):
    return f"{100 * n / total:.0f}%" if total > 0 else "0%"


def render(s):
    dot = graphviz.Digraph("direct_ref_flow", format="png")
    dot.attr(
        fontname="Helvetica", fontsize="11", bgcolor="white",
        dpi="150", pad="0.5", ranksep="0.55", nodesep="0.6",
    )
    dot.attr("node", fontname="Helvetica", fontsize="10", margin="0.15,0.08",
             shape="box", style="filled,rounded", penwidth="1.5")
    dot.attr("edge", fontname="Helvetica", fontsize="9", penwidth="1.2",
             arrowsize="0.8")

    # Colors
    BLUE = "#1565c0"
    BLUE_FILL = "#e3f2fd"
    BLUE_TEXT = "#0d47a1"
    GREEN = "#2e7d32"
    GREEN_FILL = "#c8e6c9"
    GREEN_TEXT = "#1b5e20"
    RED = "#c62828"
    RED_FILL = "#ffcdd2"
    RED_TEXT = "#b71c1c"
    PURPLE = "#7b1fa2"
    PURPLE_FILL = "#e8d5f5"
    PURPLE_TEXT = "#4a148c"
    GRAY = "#616161"
    GRAY_FILL = "#eeeeee"

    # ── Row 0: Search ──
    dot.node(
        "SEARCH",
        "Full-text search for DANDI references\n"
        "(\"dandiset\", \"dandiarchive.org\", \"10.48324/dandi\")",
        fillcolor=GREEN_FILL, color=GREEN, fontcolor=GREEN_TEXT,
        fontsize="13", penwidth="2.5", style="filled,rounded,bold",
    )

    # ── Row 1: Sources ──
    with dot.subgraph() as sub:
        sub.attr(rank="same")
        sub.node("EPMC", f"Europe PMC\n{s['epmc']} papers",
                 fillcolor=BLUE_FILL, color=BLUE, fontcolor=BLUE_TEXT)
        sub.node("OA", f"OpenAlex\n{s['oa']} papers",
                 fillcolor=BLUE_FILL, color=BLUE, fontcolor=BLUE_TEXT)

    dot.edge("SEARCH", "EPMC", color=BLUE)
    dot.edge("SEARCH", "OA", color=BLUE)

    # ── Row 2: Dedup ──
    dot.node("DEDUP", f"Deduplicate across sources\n{s['total']} unique papers",
             fillcolor=BLUE_FILL, color=BLUE, fontcolor=BLUE_TEXT, penwidth="2")
    dot.edge("EPMC", "DEDUP",
             label=f"  {s['epmc_only']} unique  ", color=BLUE, fontcolor=BLUE)
    dot.edge("OA", "DEDUP",
             label=f"  {s['oa_only']} unique  ", color=BLUE, fontcolor=BLUE)

    # ── Row 3: Text retrieval ──
    with dot.subgraph() as sub:
        sub.attr(rank="same")
        sub.node("WITH_TEXT", f"Full text retrieved\n{s['with_text']} papers",
                 fillcolor=GREEN_FILL, color=GREEN, fontcolor=GREEN_TEXT, penwidth="2")
        sub.node("NO_TEXT", f"No full text\n{s['no_text']} papers",
                 fillcolor=RED_FILL, color=RED, fontcolor=RED_TEXT)

    dot.edge("DEDUP", "WITH_TEXT",
             label=f"  {s['with_text']}  ", color=GREEN, fontcolor=GREEN)
    dot.edge("DEDUP", "NO_TEXT",
             label=f"  {s['no_text']}  ", color=RED, fontcolor=RED)

    # ── Row 4: Pattern extraction ──
    pre = s["pre_dedup"]
    with dot.subgraph() as sub:
        sub.attr(rank="same")
        sub.node("WITH_DS",
                 f"Dandiset ID extracted\n{pre} papers, {s['n_dandisets']} unique dandisets",
                 fillcolor=GREEN_FILL, color=GREEN, fontcolor=GREEN_TEXT, penwidth="2")
        sub.node("NO_DS",
                 f"No specific dandiset found\n{s['without_ds']} papers",
                 fillcolor=RED_FILL, color=RED, fontcolor=RED_TEXT)

    dot.edge("WITH_TEXT", "WITH_DS",
             label=f"  {pre}  ", color=GREEN, fontcolor=GREEN)
    dot.edge("WITH_TEXT", "NO_DS",
             label=f"  {s['without_ds']}  ", color=RED, fontcolor=RED)

    # ── Row 5: Preprint dedup ──
    with dot.subgraph() as sub:
        sub.attr(rank="same")
        sub.node("DEDUP2",
                 f"Deduplicate preprint/published pairs\n{s['with_ds']} papers",
                 fillcolor=BLUE_FILL, color=BLUE, fontcolor=BLUE_TEXT, penwidth="2")
        sub.node("REMOVED",
                 f"Preprint duplicates\n{s['n_deduped']} removed",
                 fillcolor=RED_FILL, color=RED, fontcolor=RED_TEXT)
    dot.edge("WITH_DS", "DEDUP2", color=GREEN)
    dot.edge("WITH_DS", "REMOVED",
             label=f"  {s['n_deduped']}  ", color=RED, fontcolor=RED)

    # ── Row 6: Classification ──
    dot.node("CLASSIFY",
             f"LLM classification\n{s['n_pairs']} paper-dandiset pairs",
             fillcolor=PURPLE_FILL, color=PURPLE, fontcolor=PURPLE_TEXT, penwidth="2")
    dot.edge("DEDUP2", "CLASSIFY", color=PURPLE)

    # ── Row 7: Results ──
    with dot.subgraph() as sub:
        sub.attr(rank="same")
        sub.node("REUSE",
                 f"REUSE\n{s['n_reuse']} ({pct(s['n_reuse'], s['n_pairs'])})",
                 fillcolor="#2196F3", color=BLUE, fontcolor="white",
                 fontsize="11", penwidth="2")
        sub.node("PRIMARY",
                 f"PRIMARY\n{s['n_primary']} ({pct(s['n_primary'], s['n_pairs'])})",
                 fillcolor="#FF9800", color="#e65100", fontcolor="white",
                 fontsize="11", penwidth="2")
        sub.node("NEITHER",
                 f"NEITHER\n{s['n_neither']} ({pct(s['n_neither'], s['n_pairs'])})",
                 fillcolor=GRAY_FILL, color=GRAY, fontcolor="#424242",
                 fontsize="11", penwidth="2")

    dot.edge("CLASSIFY", "REUSE",
             label=f"  {s['n_reuse']}  ", color="#2196F3", fontcolor=BLUE, penwidth="2")
    dot.edge("CLASSIFY", "PRIMARY",
             label=f"  {s['n_primary']}  ", color="#FF9800", fontcolor="#e65100")
    dot.edge("CLASSIFY", "NEITHER",
             label=f"  {s['n_neither']}  ", color=GRAY, fontcolor=GRAY)

    dot.render("output/direct_ref_flow", cleanup=True)
    print("Rendered to output/direct_ref_flow.png")


if __name__ == "__main__":
    stats = load_stats()
    for k, v in sorted(stats.items()):
        print(f"  {k}: {v}")
    render(stats)
