#!/usr/bin/env python3
"""
render_phase2_flow.py - Flowchart showing the citation analysis pipeline (Phase 2).

Starting from primary papers linked to dandisets, shows:
1. Papers that cite these primary papers (via OpenAlex)
2. Text fetching success/failure
3. LLM classification into REUSE / MENTION / NEITHER

Generates: output/phase2_citation_flow.png
"""

import json
from pathlib import Path

import graphviz
import numpy as np


def load_stats():
    """Load stats from pipeline outputs."""
    # Import non-empty filter from phase 1
    from render_dandiset_coverage_flow import get_nonempty_dandiset_ids
    import requests
    session = requests.Session()
    nonempty_ids = get_nonempty_dandiset_ids(session)

    with open("output/all_dandiset_papers.json") as f:
        papers_data = json.load(f)

    with open("output/all_classifications.json") as f:
        cls_data = json.load(f)

    # Filter to non-empty dandisets
    results = [r for r in papers_data["results"] if r["dandiset_id"] in nonempty_ids]
    nonempty_dandiset_ids = set(r["dandiset_id"] for r in results)
    cls = [c for c in cls_data["classifications"] if c.get("dandiset_id") in nonempty_dandiset_ids]

    # Unique primary papers (deduplicate by DOI)
    primary_dois = set()
    for r in results:
        for p in r.get("paper_relations", []):
            doi = p.get("doi")
            if doi:
                primary_dois.add(doi.strip().lower())

    # Citations per primary paper
    cite_counts = []
    for r in results:
        for p in r.get("paper_relations", []):
            c = p.get("citation_count", 0) or 0
            if c > 0:
                cite_counts.append(c)

    # Papers that couldn't be looked up (no citation data)
    n_no_citation_data = sum(
        1 for r in results
        if not any(p.get("citation_count") for p in r.get("paper_relations", []))
        and r.get("paper_relations")
    )

    # Total citing papers
    total_citing = sum(len(r.get("citing_papers", [])) for r in results)

    # Unique citing DOIs
    citing_dois = set()
    for r in results:
        for c in r.get("citing_papers", []):
            doi = c.get("doi")
            if doi:
                citing_dois.add(doi.strip().lower())

    # Text fetching
    n_with_text = sum(1 for c in cls if c.get("text_length", 0) > 200)
    n_no_text = len(cls) - n_with_text

    # Classifications
    from collections import Counter
    counts = Counter(c["classification"] for c in cls)

    reuse = [c for c in cls if c["classification"] == "REUSE"]
    n_same_lab = sum(1 for c in reuse if c.get("same_lab") is True)
    n_diff_lab = sum(1 for c in reuse if c.get("same_lab") is False)

    # REUSE by source
    n_reuse_citation = sum(1 for c in reuse if c.get("source_type") in ("citation", "both", ""))
    n_reuse_direct = sum(1 for c in reuse if c.get("source_type") == "direct_reference")

    # NEITHER that actually went through LLM (exclude no-text)
    n_neither_llm = sum(1 for c in cls if c["classification"] == "NEITHER" and c.get("text_length", 0) > 200)

    return {
        "n_primary_papers": len(primary_dois),
        "n_primary_no_citations": n_no_citation_data,
        "avg_citations": np.mean(cite_counts) if cite_counts else 0,
        "median_citations": np.median(cite_counts) if cite_counts else 0,
        "total_citing_pairs": total_citing,
        "unique_citing_papers": len(citing_dois),
        "n_classified": len(cls),
        "n_with_text": n_with_text,
        "n_no_text": n_no_text,
        "n_same_lab": n_same_lab,
        "n_diff_lab": n_diff_lab,
        "n_diff_dandi": sum(1 for c in reuse if c.get("same_lab") is False and c.get("source_archive") == "DANDI Archive"),
        "n_diff_other": sum(1 for c in reuse if c.get("same_lab") is False and c.get("source_archive") not in ("DANDI Archive", "unclear", None)),
        "n_diff_unclear": sum(1 for c in reuse if c.get("same_lab") is False and c.get("source_archive") in ("unclear", None)),
        "n_same_dandi": sum(1 for c in reuse if c.get("same_lab") is True and c.get("source_archive") == "DANDI Archive"),
        "n_same_other": sum(1 for c in reuse if c.get("same_lab") is True and c.get("source_archive") not in ("DANDI Archive", "unclear", None)),
        "n_same_unclear": sum(1 for c in reuse if c.get("same_lab") is True and c.get("source_archive") in ("unclear", None)),
        "n_neither_llm": n_neither_llm,
        "n_reuse_citation": n_reuse_citation,
        "n_reuse_direct": n_reuse_direct,
        "n_reuse": counts.get("REUSE", 0),
        "n_mention": counts.get("MENTION", 0),
        "n_neither": counts.get("NEITHER", 0),
        "n_primary_cls": counts.get("PRIMARY", 0),
    }


def pct(n, total):
    return f"{100 * n / total:.1f}%" if total > 0 else "0%"


def render(stats):
    dot = graphviz.Digraph("phase2_citation", format="png")
    dot.attr(
        fontname="Helvetica", fontsize="12", bgcolor="white",
        dpi="150", pad="0.4", ranksep="0.7", nodesep="0.5",
    )
    dot.attr("node", fontname="Helvetica", fontsize="10", margin="0.2,0.1")
    dot.attr("edge", fontname="Helvetica", fontsize="9", penwidth="1.5")

    # Row 0: Primary papers
    dot.node(
        "PRIMARY",
        f'Primary papers linked to dandisets\n{stats["n_primary_papers"]} unique papers',
        shape="box", style="filled,rounded,bold",
        fillcolor="#c8e6c9", color="#2e7d32", fontcolor="#1b5e20",
        fontsize="14", penwidth="2.5",
    )

    # Row 1: OpenAlex citation lookup
    n_lookupable = stats["n_primary_papers"] - stats["n_primary_no_citations"]
    dot.node(
        "OPENALEX",
        f'Citation lookup (OpenAlex)\n'
        f'Median {stats["median_citations"]:.0f} citations/paper\n'
        f'Mean {stats["avg_citations"]:.0f} citations/paper',
        shape="box", style="filled,rounded",
        fillcolor="#e3f2fd", color="#1565c0", fontcolor="#0d47a1",
        penwidth="2",
    )
    if stats["n_primary_no_citations"] > 0:
        dot.node(
            "NO_CITATIONS",
            f'No citation data\n{stats["n_primary_no_citations"]} papers',
            shape="box", style="filled,rounded",
            fillcolor="#ffccbc", color="#e64a19", fontcolor="#bf360c",
        )
        dot.edge("PRIMARY", "NO_CITATIONS",
                 label=f' {stats["n_primary_no_citations"]}',
                 color="#e64a19", fontcolor="#e64a19")

    dot.edge("PRIMARY", "OPENALEX",
             label=f' {n_lookupable}',
             color="#1565c0", fontcolor="#1565c0")

    # Row 2: Citing papers found
    dot.node(
        "CITING",
        f'Citing papers found\n'
        f'{stats["unique_citing_papers"]} unique papers\n'
        f'{stats["total_citing_pairs"]} paper-dandiset pairs',
        shape="box", style="filled,rounded",
        fillcolor="#e3f2fd", color="#1565c0", fontcolor="#0d47a1",
        penwidth="2",
    )
    dot.edge("OPENALEX", "CITING", color="#1565c0")

    # Row 3: Text fetching
    dot.node(
        "WITH_TEXT",
        f'Full text retrieved\n{stats["n_with_text"]} ({pct(stats["n_with_text"], stats["n_classified"])})',
        shape="box", style="filled,rounded",
        fillcolor="#c8e6c9", color="#2e7d32", fontcolor="#1b5e20",
        penwidth="2",
    )
    dot.node(
        "NO_TEXT",
        f'No text available\n{stats["n_no_text"]} ({pct(stats["n_no_text"], stats["n_classified"])})',
        shape="box", style="filled,rounded",
        fillcolor="#ffccbc", color="#e64a19", fontcolor="#bf360c",
    )
    dot.edge("CITING", "WITH_TEXT",
             label=f' {stats["n_with_text"]}',
             color="#2e7d32", fontcolor="#2e7d32")
    dot.edge("CITING", "NO_TEXT",
             label=f' {stats["n_no_text"]}',
             color="#e64a19", fontcolor="#e64a19")

    # Row 4: LLM Classification (only papers with text)
    dot.node(
        "LLM",
        f'LLM classification\n{stats["n_with_text"]} paper-dandiset pairs',
        shape="box", style="filled,rounded",
        fillcolor="#e8d5f5", color="#7b1fa2", fontcolor="#4a148c",
        penwidth="2",
    )
    dot.edge("WITH_TEXT", "LLM", color="#7b1fa2")

    # Row 5: Classification results
    # Direct references (inline dandiset mentions) — separate from citation pipeline
    dot.node(
        "DIRECT_REF",
        f'Direct dandiset references\n(DOI/URL in paper text)\n{stats["n_reuse_direct"]} REUSE',
        shape="box", style="filled,rounded",
        fillcolor="#fff9c4", color="#f9a825", fontcolor="#f57f17",
        penwidth="2",
    )

    with dot.subgraph() as s:
        s.attr(rank="same")
        s.node(
            "REUSE",
            f'REUSE\n{stats["n_reuse"]} total',
            shape="box", style="filled,rounded",
            fillcolor="#2196F3", color="#1565c0", fontcolor="white",
            fontsize="12", penwidth="2",
        )
        s.node(
            "MENTION",
            f'MENTION\n{stats["n_mention"]} ({pct(stats["n_mention"], stats["n_with_text"])})',
            shape="box", style="filled,rounded",
            fillcolor="#FF9800", color="#e65100", fontcolor="white",
            fontsize="12", penwidth="2",
        )
        s.node(
            "NEITHER",
            f'NEITHER\n{stats["n_neither_llm"]} ({pct(stats["n_neither_llm"], stats["n_with_text"])})',
            shape="box", style="filled,rounded",
            fillcolor="#e0e0e0", color="#616161", fontcolor="#424242",
            fontsize="12", penwidth="2",
        )

    dot.edge("LLM", "REUSE",
             label=f' {stats["n_reuse_citation"]}',
             color="#2196F3", fontcolor="#1565c0", penwidth="2")
    dot.edge("DIRECT_REF", "REUSE",
             label=f' {stats["n_reuse_direct"]}',
             color="#f9a825", fontcolor="#f57f17")
    dot.edge("LLM", "MENTION",
             label=f' {stats["n_mention"]}',
             color="#FF9800", fontcolor="#e65100")
    dot.edge("LLM", "NEITHER",
             label=f' {stats["n_neither_llm"]}',
             color="#616161", fontcolor="#616161")

    # Row 6: Same lab vs different lab
    with dot.subgraph() as s:
        s.attr(rank="same")
        s.node(
            "DIFF_LAB",
            f'Different lab\n{stats["n_diff_lab"]} ({pct(stats["n_diff_lab"], stats["n_reuse"])})',
            shape="box", style="filled,rounded",
            fillcolor="#1565c0", color="#0d47a1", fontcolor="white",
            fontsize="11", penwidth="2",
        )
        s.node(
            "SAME_LAB",
            f'Same lab\n{stats["n_same_lab"]} ({pct(stats["n_same_lab"], stats["n_reuse"])})',
            shape="box", style="filled,rounded",
            fillcolor="#90caf9", color="#1565c0", fontcolor="#0d47a1",
            fontsize="11", penwidth="2",
        )

    dot.edge("REUSE", "DIFF_LAB",
             label=f' {stats["n_diff_lab"]}',
             color="#1565c0", fontcolor="#1565c0", penwidth="2")
    dot.edge("REUSE", "SAME_LAB",
             label=f' {stats["n_same_lab"]}',
             color="#90caf9", fontcolor="#1565c0")

    # Row 7: Archive used (under each lab type)
    for prefix, parent, parent_n, color in [
        ("DIFF", "DIFF_LAB", stats["n_diff_lab"], "#1565c0"),
        ("SAME", "SAME_LAB", stats["n_same_lab"], "#1565c0"),
    ]:
        n_dandi = stats[f"n_{prefix.lower()}_dandi"]
        n_other = stats[f"n_{prefix.lower()}_other"]
        n_unclear = stats[f"n_{prefix.lower()}_unclear"]

        dot.node(
            f"{prefix}_DANDI",
            f'DANDI\n{n_dandi}',
            shape="box", style="filled,rounded",
            fillcolor="#2196F3", color="#1565c0", fontcolor="white",
            fontsize="9",
        )
        dot.node(
            f"{prefix}_OTHER",
            f'Other archive\n{n_other}',
            shape="box", style="filled,rounded",
            fillcolor="#616161", color="#424242", fontcolor="white",
            fontsize="9",
        )
        dot.node(
            f"{prefix}_UNCLEAR",
            f'Unclear\n{n_unclear}',
            shape="box", style="filled,rounded",
            fillcolor="#F57C00", color="#e65100", fontcolor="white",
            fontsize="9",
        )
        dot.edge(parent, f"{prefix}_DANDI", color="#2196F3")
        dot.edge(parent, f"{prefix}_OTHER", color="#616161")
        dot.edge(parent, f"{prefix}_UNCLEAR", color="#F57C00")

    dot.render("output/phase2_citation_flow", cleanup=True)
    print("Rendered to output/phase2_citation_flow.png")


if __name__ == "__main__":
    stats = load_stats()
    for k, v in stats.items():
        print(f"  {k}: {v}")
    render(stats)
