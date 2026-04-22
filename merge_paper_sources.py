#!/usr/bin/env python3
"""
merge_paper_sources.py - Merge formal and LLM-identified paper associations
into a single unified results file for the citation pipeline.

Reads:
- output/dandi_primary_papers_results.json (formal metadata associations)
- .missing_paper_cache.json (LLM-identified papers with validated DOIs)

Writes:
- output/all_dandiset_papers.json (unified, same format as dandi_primary_papers_results.json)

Usage:
    python merge_paper_sources.py
    python merge_paper_sources.py -o output/all_dandiset_papers.json
"""

import argparse
import json
import sys
from pathlib import Path

import requests


def main():
    parser = argparse.ArgumentParser(description="Merge formal and LLM paper associations")
    parser.add_argument("-o", "--output", default="output/all_dandiset_papers.json")
    args = parser.parse_args()

    # Load formal results
    with open("output/dandi_primary_papers_results.json") as f:
        formal_data = json.load(f)
    formal_results = {r["dandiset_id"]: r for r in formal_data["results"]}

    # Load LLM cache (may be absent if step 2 was skipped, e.g. in --limit mode)
    cache_path = Path(".missing_paper_cache.json")
    if cache_path.exists():
        with open(cache_path) as f:
            cache = json.load(f)
    else:
        cache = {}
        print("No .missing_paper_cache.json found — proceeding with formal results only", file=sys.stderr)

    # Get dandiset metadata for LLM-found entries (need created date, etc.)
    llm_found = {
        k: v for k, v in cache.items()
        if v.get("found") and v.get("confidence", 0) >= 6
        and v.get("doi_validated") is True
        and k not in formal_results  # don't duplicate
    }

    print(f"Formal results: {len(formal_results)}", file=sys.stderr)
    print(f"LLM-found (validated, non-overlapping): {len(llm_found)}", file=sys.stderr)

    # Fetch dandiset metadata for LLM entries
    session = requests.Session()
    session.headers.update({"User-Agent": "MergePaperSources/1.0"})

    added = 0
    for did, entry in sorted(llm_found.items()):
        doi = entry["doi"]

        # Get dandiset info from API
        try:
            resp = session.get(
                f"https://api.dandiarchive.org/api/dandisets/{did}/",
                timeout=15,
            )
            if resp.status_code != 200:
                continue
            ds = resp.json()
        except Exception:
            continue

        pub_version = ds.get("most_recent_published_version")
        draft_version = ds.get("draft_version")
        if pub_version:
            version = pub_version["version"]
            version_name = pub_version.get("name", "")
        elif draft_version:
            version = "draft"
            version_name = draft_version.get("name", "")
        else:
            continue

        formal_results[did] = {
            "dandiset_id": did,
            "dandiset_name": version_name,
            "dandiset_version": version,
            "dandiset_url": f"https://dandiarchive.org/dandiset/{did}/{version}",
            "dandiset_doi": None,
            "dandiset_created": ds.get("created"),
            "embargo_status": ds.get("embargo_status"),
            "draft_modified": draft_version.get("modified") if draft_version else None,
            "contact_person": ds.get("contact_person"),
            "paper_relations": [
                {
                    "relation": "llm_identified",
                    "url": f"https://doi.org/{doi}",
                    "name": entry.get("title"),
                    "identifier": doi,
                    "resource_type": None,
                    "doi": doi,
                    "source": "llm",
                    "llm_confidence": entry.get("confidence"),
                    "llm_reasoning": entry.get("reasoning"),
                }
            ],
        }
        added += 1

    print(f"Added {added} LLM entries", file=sys.stderr)
    print(f"Total: {len(formal_results)} dandisets", file=sys.stderr)

    # Write merged output
    output = {
        "count": len(formal_results),
        "results": sorted(formal_results.values(), key=lambda r: r["dandiset_id"]),
    }

    with open(args.output, "w") as f:
        json.dump(output, f, indent=2)
    print(f"Wrote {args.output}", file=sys.stderr)


if __name__ == "__main__":
    main()
