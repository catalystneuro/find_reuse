#!/usr/bin/env python3
"""
render_dandiset_coverage_flow.py - Flowchart showing how dandiset papers are found.

Reads from:
- output/dandi_primary_papers_results.json (formal paper associations)
- .missing_paper_cache.json (LLM-identified papers and test classifications)
- DANDI API (total dandiset count)

Generates: output/dandiset_coverage_flow.png
"""

import json
from pathlib import Path

import graphviz
import requests


def load_counts():
    """Compute all counts for the flowchart."""
    # Total dandisets from API
    session = requests.Session()
    resp = session.get(
        "https://api.dandiarchive.org/api/dandisets/?page_size=1", timeout=15
    )
    total = resp.json()["count"]

    # Formal paper associations (regex/metadata)
    with open("output/dandi_primary_papers_results.json") as f:
        results = json.load(f)
    formal_ids = set(r["dandiset_id"] for r in results["results"])
    n_formal = len(formal_ids)

    # Breakdown of formal: which relation types
    n_described_by = 0
    n_describes = 0
    n_supplement = 0
    n_published_in = 0
    n_description_doi = 0
    n_formal_papers = 0  # total unique papers across all formal dandisets
    for r in results["results"]:
        relations = set(p["relation"] for p in r.get("paper_relations", []))
        sources = set(p.get("source") for p in r.get("paper_relations", []))
        n_formal_papers += len(r.get("paper_relations", []))
        if "description" in sources:
            n_description_doi += 1
        if "dcite:IsDescribedBy" in relations:
            n_described_by += 1
        if "dcite:Describes" in relations:
            n_describes += 1
        if "dcite:IsSupplementTo" in relations:
            n_supplement += 1
        if "dcite:IsPublishedIn" in relations:
            n_published_in += 1

    # Deduplicate formal papers by DOI
    formal_dois = set()
    for r in results["results"]:
        for p in r.get("paper_relations", []):
            doi = p.get("doi")
            if doi:
                formal_dois.add(doi.strip().lower())
    n_formal_unique_papers = len(formal_dois)

    # LLM-identified papers
    cache = {}
    cache_file = Path(".missing_paper_cache.json")
    if cache_file.exists():
        with open(cache_file) as f:
            cache = json.load(f)

    llm_found_entries = [
        v for v in cache.values()
        if v.get("found") and v.get("confidence", 0) >= 6
        and v.get("doi_validated") is True  # only explicitly validated
    ]
    n_llm_found = len(llm_found_entries)
    # Count unique LLM papers by DOI (some may lack DOI)
    llm_dois = set()
    llm_no_doi = 0
    for v in llm_found_entries:
        doi = v.get("doi")
        if doi:
            llm_dois.add(doi.strip().lower())
        else:
            llm_no_doi += 1
    n_llm_unique_papers = len(llm_dois) + llm_no_doi

    # Count invalidated as "no paper found"
    n_llm_invalidated = sum(
        1 for v in cache.values()
        if v.get("found") and v.get("doi_validated") is False
    )
    n_test = sum(1 for v in cache.values() if v.get("reason") == "test_dandiset")
    n_llm_not_found = sum(
        1 for v in cache.values()
        if not v.get("found") and v.get("reason") != "test_dandiset"
    )
    n_llm_checked = len(cache)

    # Remaining unchecked
    n_remaining = total - n_formal - n_llm_checked

    # No paper detectable = not found by LLM + invalidated + remaining
    n_no_paper = n_llm_not_found + n_llm_invalidated + n_remaining

    # Total unique papers (formal + LLM, deduplicated)
    all_dois = formal_dois | llm_dois
    n_total_unique_papers = len(all_dois) + llm_no_doi

    return cache, {
        "total": total,
        "formal": n_formal,
        "formal_unique_papers": n_formal_unique_papers,
        "described_by": n_described_by,
        "describes": n_describes,
        "supplement": n_supplement,
        "published_in": n_published_in,
        "description_doi": n_description_doi,
        "llm_found": n_llm_found,
        "llm_unique_papers": n_llm_unique_papers,
        "llm_not_found": n_llm_not_found,
        "llm_checked": n_llm_checked,
        "test": n_test,
        "no_paper": n_no_paper,
        "remaining": n_remaining,
        "total_unique_papers": n_total_unique_papers,
    }


def pct(n, total):
    return f"{100 * n / total:.1f}%" if total > 0 else "0%"


def render(counts, cache):
    total = counts["total"]
    dot = graphviz.Digraph("dandiset_coverage", format="png")
    dot.attr(
        fontname="Helvetica", fontsize="12", bgcolor="white",
        dpi="150", pad="0.4", ranksep="0.7", nodesep="0.5",
    )
    dot.attr("node", fontname="Helvetica", fontsize="10", margin="0.2,0.1")
    dot.attr("edge", fontname="Helvetica", fontsize="9", penwidth="1.5")

    # Root
    dot.node(
        "ROOT",
        f"All DANDI Dandisets\n{total}",
        shape="box", style="filled,rounded,bold",
        fillcolor="#e8d5f5", color="#7b1fa2", fontcolor="#4a148c",
        fontsize="14", penwidth="2.5",
    )

    # Level 1: Formal vs No formal
    n_no_formal = total - counts["formal"]
    dot.node(
        "FORMAL",
        f'Formal paper association\n{counts["formal"]} dandisets ({pct(counts["formal"], total)})\n{counts["formal_unique_papers"]} unique papers',
        shape="box", style="filled,rounded",
        fillcolor="#c8e6c9", color="#2e7d32", fontcolor="#1b5e20",
        penwidth="2",
    )
    dot.node(
        "NO_FORMAL",
        f"No formal association\n{n_no_formal} ({pct(n_no_formal, total)})",
        shape="box", style="filled,rounded",
        fillcolor="#fff9c4", color="#f9a825", fontcolor="#f57f17",
        penwidth="2",
    )
    dot.edge("ROOT", "FORMAL", label=f" {counts['formal']}", color="#2e7d32", fontcolor="#2e7d32")
    dot.edge("ROOT", "NO_FORMAL", label=f" {n_no_formal}", color="#f9a825", fontcolor="#f9a825")

    # Level 2a: Formal breakdown
    dot.node(
        "DESCRIBED_BY",
        f'dcite:IsDescribedBy\n{counts["described_by"]}',
        shape="box", style="filled,rounded",
        fillcolor="#a5d6a7", color="#2e7d32", fontcolor="#1b5e20",
        fontsize="9",
    )
    dot.node(
        "DESCRIBES",
        f'dcite:Describes\n{counts["describes"]}',
        shape="box", style="filled,rounded",
        fillcolor="#a5d6a7", color="#2e7d32", fontcolor="#1b5e20",
        fontsize="9",
    )
    dot.node(
        "SUPPLEMENT",
        f'dcite:IsSupplementTo\n{counts["supplement"]}',
        shape="box", style="filled,rounded",
        fillcolor="#a5d6a7", color="#2e7d32", fontcolor="#1b5e20",
        fontsize="9",
    )
    dot.node(
        "DESC_DOI",
        f'DOI in description\n{counts["description_doi"]}',
        shape="box", style="filled,rounded",
        fillcolor="#a5d6a7", color="#2e7d32", fontcolor="#1b5e20",
        fontsize="9",
    )

    dot.edge("FORMAL", "DESCRIBED_BY", color="#43a047")
    dot.edge("FORMAL", "DESCRIBES", color="#43a047")
    dot.edge("FORMAL", "SUPPLEMENT", color="#43a047")
    dot.edge("FORMAL", "DESC_DOI", color="#43a047")

    # Level 2b: No formal breakdown
    n_llm_all = sum(
        1 for v in cache.values()
        if v.get("found") and v.get("confidence", 0) >= 6
    )
    dot.node(
        "LLM_FOUND",
        f'LLM-identified paper\n{n_llm_all} dandisets ({pct(n_llm_all, total)})',
        shape="box", style="filled,rounded",
        fillcolor="#bbdefb", color="#1565c0", fontcolor="#0d47a1",
        penwidth="2",
    )
    dot.node(
        "TEST",
        f'Test/placeholder dandiset\n{counts["test"]} ({pct(counts["test"], total)})',
        shape="box", style="filled,rounded",
        fillcolor="#e0e0e0", color="#616161", fontcolor="#424242",
        penwidth="2",
    )
    dot.node(
        "NO_PAPER",
        f'No detectable paper\n{counts["no_paper"]} ({pct(counts["no_paper"], total)})',
        shape="box", style="filled,rounded",
        fillcolor="#ffccbc", color="#e64a19", fontcolor="#bf360c",
        penwidth="2",
    )

    # LLM sub-breakdown: validated DOI vs unresolvable DOI
    n_llm_validated = sum(
        1 for v in cache.values()
        if v.get("found") and v.get("confidence", 0) >= 6 and v.get("doi_validated") is True
    )
    n_llm_invalid = sum(
        1 for v in cache.values()
        if v.get("found") and v.get("confidence", 0) >= 6 and v.get("doi_validated") is False
    )

    dot.edge("NO_FORMAL", "LLM_FOUND", label=f' {n_llm_validated + n_llm_invalid}', color="#1565c0", fontcolor="#1565c0")
    dot.edge("NO_FORMAL", "TEST", label=f' {counts["test"]}', color="#616161", fontcolor="#616161")
    dot.edge("NO_FORMAL", "NO_PAPER", label=f' {counts["no_paper"]}', color="#e64a19", fontcolor="#e64a19")

    dot.node(
        "LLM_VALID",
        f'DOI validated or recovered\n{n_llm_validated}',
        shape="box", style="filled,rounded",
        fillcolor="#90caf9", color="#1565c0", fontcolor="#0d47a1",
        fontsize="9",
    )
    dot.node(
        "LLM_INVALID",
        f'DOI unresolvable\n{n_llm_invalid}',
        shape="box", style="filled,rounded",
        fillcolor="#ffccbc", color="#e64a19", fontcolor="#bf360c",
        fontsize="9",
    )
    dot.edge("LLM_FOUND", "LLM_VALID", color="#1565c0")
    dot.edge("LLM_FOUND", "LLM_INVALID", color="#e64a19")

    # Note about remaining
    if counts["remaining"] > 0:
        dot.node(
            "NOTE",
            f'({counts["remaining"]} not yet checked by LLM)',
            shape="note", style="filled",
            fillcolor="#fffde7", color="#f9a825", fontcolor="#827717",
            fontsize="8",
        )
        dot.edge("NO_PAPER", "NOTE", style="dotted", arrowhead="none", color="#f9a825")

    # Bottom: total dandisets with linked papers (formal + LLM)
    n_with_paper = counts["formal"] + counts["llm_found"]
    dot.node(
        "ALL_PAPERS",
        f"All dandisets with linked papers\n{n_with_paper} dandisets ({pct(n_with_paper, total)})\n{counts['total_unique_papers']} unique papers",
        shape="box", style="filled,rounded,bold",
        fillcolor="#e3f2fd", color="#1565c0", fontcolor="#0d47a1",
        fontsize="14", penwidth="2.5",
    )
    dot.edge("FORMAL", "ALL_PAPERS", color="#2e7d32", penwidth="2")
    dot.edge("LLM_VALID", "ALL_PAPERS", color="#1565c0", penwidth="2")

    dot.render("output/dandiset_coverage_flow", cleanup=True)
    print("Rendered to output/dandiset_coverage_flow.png")
    return counts


if __name__ == "__main__":
    cache, counts = load_counts()
    print(f"Total dandisets: {counts['total']}")
    print(f"Formal paper association: {counts['formal']} dandisets, {counts['formal_unique_papers']} unique papers")
    print(f"  IsDescribedBy: {counts['described_by']}")
    print(f"  Describes: {counts['describes']}")
    print(f"  IsSupplementTo: {counts['supplement']}")
    print(f"  IsPublishedIn: {counts['published_in']}")
    print(f"  DOI in description: {counts['description_doi']}")
    print(f"LLM-identified (validated): {counts['llm_found']} dandisets, {counts['llm_unique_papers']} unique papers")
    print(f"Test dandisets: {counts['test']}")
    print(f"No paper found: {counts['no_paper']}")
    print(f"Remaining unchecked: {counts['remaining']}")
    print(f"Total with papers: {counts['formal'] + counts['llm_found']} dandisets, {counts['total_unique_papers']} unique papers")
    render(counts, cache)
