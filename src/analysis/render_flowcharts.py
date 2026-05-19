#!/usr/bin/env python3
"""
render_flowcharts.py — Generate pipeline flowcharts for any archive.

Generates:
  - Phase 1: Dataset-to-paper linkage
  - Phase 2: Citation analysis pipeline
  - Direct reference discovery

Usage:
    python -m analysis.render_flowcharts --archive crcns
    python -m analysis.render_flowcharts --archive dandi
"""

import argparse
import json
from collections import Counter
from pathlib import Path

import graphviz


def pct(n, total):
    return f"{100 * n / total:.0f}%" if total > 0 else "0%"


def render_phase1(datasets_path, output_path, archive_name="Archive"):
    """Phase 1: Dataset-to-paper linkage flowchart."""
    with open(datasets_path) as f:
        data = json.load(f)

    total = data["count"]
    has_papers = sum(1 for r in data["results"] if r.get("paper_relations"))
    no_papers = total - has_papers
    primary_dois = set()
    for r in data["results"]:
        for p in r.get("paper_relations", []):
            if p.get("doi"):
                primary_dois.add(p["doi"].lower())

    # Source breakdown
    from_about = sum(1 for r in data["results"]
                     if any(p.get("source") == "about_page" for p in r.get("paper_relations", [])))
    from_llm = sum(1 for r in data["results"]
                   if any(p.get("source") == "llm" for p in r.get("paper_relations", [])))
    from_other = has_papers - from_about - from_llm

    dot = graphviz.Digraph(f"phase1_{archive_name.lower()}", format="png")
    dot.attr(fontname="Helvetica", fontsize="12", bgcolor="white",
             dpi="150", pad="0.4", ranksep="0.6", nodesep="0.5")
    dot.attr("node", fontname="Helvetica", fontsize="10", margin="0.15,0.08",
             shape="box", style="filled,rounded", penwidth="1.5")
    dot.attr("edge", fontname="Helvetica", fontsize="9", penwidth="1.2", arrowsize="0.8")

    dot.node("ALL", f"All {archive_name} datasets\n{total}",
             fillcolor="#c8e6c9", color="#2e7d32", fontcolor="#1b5e20",
             fontsize="13", penwidth="2.5", style="filled,rounded,bold")

    with dot.subgraph() as s:
        s.attr(rank="same")
        s.node("WITH", f"Linked to primary paper\n{has_papers} datasets ({pct(has_papers, total)})\n{len(primary_dois)} unique papers",
               fillcolor="#c8e6c9", color="#2e7d32", fontcolor="#1b5e20", penwidth="2")
        s.node("WITHOUT", f"No paper found\n{no_papers} ({pct(no_papers, total)})",
               fillcolor="#ffcdd2", color="#c62828", fontcolor="#b71c1c")

    dot.edge("ALL", "WITH", label=f"  {has_papers}  ", color="#2e7d32", fontcolor="#2e7d32")
    dot.edge("ALL", "WITHOUT", label=f"  {no_papers}  ", color="#c62828", fontcolor="#c62828")

    dot.render(str(output_path).replace(".png", ""), cleanup=True)
    print(f"Rendered {output_path}")


def render_phase2(datasets_path, classifications_path, output_path, archive_name="Archive"):
    """Phase 2: Citation analysis pipeline flowchart."""
    with open(datasets_path) as f:
        ds = json.load(f)
    with open(classifications_path) as f:
        cls = json.load(f)

    primary_dois = set()
    for r in ds["results"]:
        for p in r.get("paper_relations", []):
            if p.get("doi"):
                primary_dois.add(p["doi"].lower())

    total_citing = sum(len(r.get("citing_papers", [])) for r in ds["results"])
    unique_citing = len(set(c["doi"] for r in ds["results"] for c in r.get("citing_papers", [])))

    counts = Counter(c["classification"] for c in cls["classifications"])
    with_text = sum(1 for c in cls["classifications"] if c.get("text_length", 0) > 200)
    no_text = len(cls["classifications"]) - with_text

    dot = graphviz.Digraph(f"phase2_{archive_name.lower()}", format="png")
    dot.attr(fontname="Helvetica", fontsize="12", bgcolor="white",
             dpi="150", pad="0.4", ranksep="0.6", nodesep="0.5")
    dot.attr("node", fontname="Helvetica", fontsize="10", margin="0.15,0.08",
             shape="box", style="filled,rounded", penwidth="1.5")
    dot.attr("edge", fontname="Helvetica", fontsize="9", penwidth="1.2", arrowsize="0.8")

    dot.node("PRIMARY", f"Primary papers linked to datasets\n{len(primary_dois)} unique papers",
             fillcolor="#c8e6c9", color="#2e7d32", fontcolor="#1b5e20",
             fontsize="13", penwidth="2.5", style="filled,rounded,bold")

    dot.node("OPENALEX", f"Citation lookup (OpenAlex)",
             fillcolor="#e3f2fd", color="#1565c0", fontcolor="#0d47a1", penwidth="2")
    dot.edge("PRIMARY", "OPENALEX", color="#1565c0")

    dot.node("CITING", f"Citing papers found\n{unique_citing} unique papers\n{total_citing} paper-dataset pairs",
             fillcolor="#e3f2fd", color="#1565c0", fontcolor="#0d47a1", penwidth="2")
    dot.edge("OPENALEX", "CITING", color="#1565c0")

    with dot.subgraph() as s:
        s.attr(rank="same")
        s.node("TEXT", f"Full text retrieved\n{with_text} pairs ({pct(with_text, with_text + no_text)})",
               fillcolor="#c8e6c9", color="#2e7d32", fontcolor="#1b5e20", penwidth="2")
        s.node("NOTEXT", f"No text\n{no_text} pairs",
               fillcolor="#ffcdd2", color="#c62828", fontcolor="#b71c1c")
    dot.edge("CITING", "TEXT", label=f"  {with_text}  ", color="#2e7d32", fontcolor="#2e7d32")
    dot.edge("CITING", "NOTEXT", label=f"  {no_text}  ", color="#c62828", fontcolor="#c62828")

    dot.node("LLM", f"LLM classification\n{with_text} pairs",
             fillcolor="#e8d5f5", color="#7b1fa2", fontcolor="#4a148c", penwidth="2")
    dot.edge("TEXT", "LLM", color="#7b1fa2")

    with dot.subgraph() as s:
        s.attr(rank="same")
        n_reuse = counts.get("REUSE", 0)
        n_mention = counts.get("MENTION", 0)
        n_neither = counts.get("NEITHER", 0)
        s.node("REUSE", f"REUSE\n{n_reuse} ({pct(n_reuse, with_text)})",
               fillcolor="#2196F3", color="#1565c0", fontcolor="white", fontsize="11", penwidth="2")
        s.node("MENTION", f"MENTION\n{n_mention} ({pct(n_mention, with_text)})",
               fillcolor="#FF9800", color="#e65100", fontcolor="white", fontsize="11", penwidth="2")
        s.node("NEITHER", f"NEITHER\n{n_neither} ({pct(n_neither, with_text)})",
               fillcolor="#eeeeee", color="#616161", fontcolor="#424242", fontsize="11", penwidth="2")
    dot.edge("LLM", "REUSE", label=f"  {n_reuse}  ", color="#2196F3", fontcolor="#1565c0", penwidth="2")
    dot.edge("LLM", "MENTION", label=f"  {n_mention}  ", color="#FF9800", fontcolor="#e65100")
    dot.edge("LLM", "NEITHER", label=f"  {n_neither}  ", color="#616161", fontcolor="#616161")

    dot.render(str(output_path).replace(".png", ""), cleanup=True)
    print(f"Rendered {output_path}")


def render_reference_flow(classifications_path, output_path, archive_name="Archive"):
    """How papers reference datasets: citation only / both / direct only."""
    with open(classifications_path) as f:
        cls = json.load(f)

    reuse = [c for c in cls["classifications"] if c["classification"] == "REUSE"]
    paper_sources = {}
    for c in reuse:
        doi = c["citing_doi"]
        st = c.get("source_type", "")
        if doi not in paper_sources:
            paper_sources[doi] = set()
        paper_sources[doi].add(st)

    total = len(paper_sources)
    only_cit = sum(1 for s in paper_sources.values() if s <= {"citation", ""})
    only_dir = sum(1 for s in paper_sources.values() if s == {"direct_reference"})
    has_both = total - only_cit - only_dir

    dot = graphviz.Digraph(f"refs_{archive_name.lower()}", format="png")
    dot.attr(fontname="Helvetica", fontsize="12", bgcolor="white",
             dpi="150", pad="0.4", ranksep="0.6", nodesep="0.5")
    dot.attr("node", fontname="Helvetica", fontsize="10", margin="0.15,0.08",
             shape="box", style="filled,rounded", penwidth="1.5")
    dot.attr("edge", fontname="Helvetica", fontsize="9", penwidth="1.2", arrowsize="0.8")

    dot.node("ROOT", f"Data Reuse Papers\n{total} unique papers",
             fillcolor="#e8d5f5", color="#7b1fa2", fontcolor="#4a148c",
             fontsize="14", penwidth="2.5", style="filled,rounded,bold")

    with dot.subgraph() as s:
        s.attr(rank="same")
        s.node("CIT", f"Cite associated paper only\n{only_cit} ({pct(only_cit, total)})",
               fillcolor="#e3f2fd", color="#1565c0", fontcolor="#0d47a1", penwidth="2")
        s.node("BOTH", f"Both citation and\ndirect dataset reference\n{has_both} ({pct(has_both, total)})",
               fillcolor="#c8e6c9", color="#2e7d32", fontcolor="#1b5e20", penwidth="2")
        s.node("DIR", f"Direct dataset reference only\n{only_dir} ({pct(only_dir, total)})",
               fillcolor="#fff9c4", color="#f9a825", fontcolor="#f57f17", penwidth="2")

    dot.edge("ROOT", "CIT", label=f"  {only_cit}  ", color="#1565c0", fontcolor="#1565c0",
             penwidth=str(max(2, only_cit * 4 / max(total, 1))))
    dot.edge("ROOT", "BOTH", label=f"  {has_both}  ", color="#2e7d32", fontcolor="#2e7d32")
    dot.edge("ROOT", "DIR", label=f"  {only_dir}  ", color="#f9a825", fontcolor="#f9a825")

    dot.render(str(output_path).replace(".png", ""), cleanup=True)
    print(f"Rendered {output_path}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", required=True)
    args = parser.parse_args()

    archive = args.archive.lower()
    output_dir = Path(f"output/{archive}")
    figures_dir = output_dir / "figures"
    figures_dir.mkdir(parents=True, exist_ok=True)

    datasets_path = output_dir / "datasets.json"
    classifications_path = output_dir / "classifications.json"

    archive_name = {"dandi": "DANDI", "crcns": "CRCNS"}.get(archive, archive.upper())

    if datasets_path.exists():
        render_phase1(datasets_path, figures_dir / "phase1_coverage.png", archive_name)

    if datasets_path.exists() and classifications_path.exists():
        render_phase2(datasets_path, classifications_path,
                      figures_dir / "phase2_citation_flow.png", archive_name)
        render_reference_flow(classifications_path,
                              figures_dir / "reference_flow.png", archive_name)


if __name__ == "__main__":
    main()
