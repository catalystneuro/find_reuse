#!/usr/bin/env python3
"""
run_archive_pipeline.py — Run the reuse analysis pipeline for any supported archive.

Usage:
    python run_archive_pipeline.py --archive dandi
    python run_archive_pipeline.py --archive crcns
    python run_archive_pipeline.py --archive crcns --step discover-datasets
    python run_archive_pipeline.py --archive crcns --step fetch-citations
"""

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

from archives import get_adapter


def run(cmd, desc):
    """Run a command, printing status."""
    print(f"\n{'='*60}", file=sys.stderr)
    print(f"  {desc}", file=sys.stderr)
    print(f"{'='*60}", file=sys.stderr, flush=True)
    result = subprocess.run(cmd, shell=isinstance(cmd, str))
    if result.returncode != 0:
        print(f"  WARNING: {desc} exited with code {result.returncode}", file=sys.stderr)
    return result.returncode


def step_discover_datasets(adapter):
    """Step 1: Discover datasets and link to primary papers."""
    data = adapter.build_datasets_json()
    print(f"  {data['count']} datasets saved to {adapter.output_dir}/datasets.json", file=sys.stderr)


def step_fetch_citations(adapter):
    """Step 2: Fetch citing papers from OpenAlex for each primary paper."""
    import requests
    datasets_path = adapter.output_dir / "datasets.json"
    with open(datasets_path) as f:
        data = json.load(f)

    session = requests.Session()
    session.headers.update({"User-Agent": "FindReuse/1.0"})

    total_citing = 0
    for i, r in enumerate(data["results"]):
        if not r.get("paper_relations"):
            continue

        for p in r["paper_relations"]:
            doi = p.get("doi", "")
            if not doi:
                continue

            # Resolve to OpenAlex work ID
            try:
                resp = session.get(f"https://api.openalex.org/works/doi:{doi}", timeout=10)
                if resp.status_code != 200:
                    continue
                work = resp.json()
                oa_id = work.get("id", "").replace("https://openalex.org/", "")
                citation_count = work.get("cited_by_count", 0)
                p["citation_count"] = citation_count
                p["publication_date"] = work.get("publication_date", "")

                if not oa_id or citation_count == 0:
                    continue

                # Fetch citing papers
                created = r.get("dandiset_created", r.get("data_accessible", ""))[:10]
                citing = []
                cursor = "*"
                while True:
                    params = {
                        "filter": f"cites:{oa_id}",
                        "per_page": 200,
                        "cursor": cursor,
                        "mailto": "ben.dichter@catalystneuro.com",
                    }
                    if created:
                        params["filter"] += f",from_publication_date:{created}"

                    resp2 = session.get("https://api.openalex.org/works", params=params, timeout=30)
                    if resp2.status_code != 200:
                        break
                    page_data = resp2.json()
                    for w in page_data.get("results", []):
                        w_doi = (w.get("doi") or "").replace("https://doi.org/", "")
                        if w_doi:
                            citing.append({
                                "doi": w_doi,
                                "title": w.get("title", ""),
                                "publication_date": w.get("publication_date", ""),
                                "journal": (w.get("primary_location") or {}).get("source", {}).get("display_name", ""),
                                "openalex_id": w.get("id", ""),
                                "cited_paper_doi": doi,
                            })
                    cursor = page_data.get("meta", {}).get("next_cursor")
                    if not cursor or not page_data.get("results"):
                        break
                    time.sleep(0.1)

                r.setdefault("citing_papers", []).extend(citing)
                total_citing += len(citing)

            except Exception as e:
                adapter.log(f"Error fetching citations for {doi}: {e}")
            time.sleep(0.1)

        if (i + 1) % 20 == 0:
            adapter.log(f"  {i + 1}/{len(data['results'])} datasets processed, {total_citing} citing papers")
            # Periodic save
            with open(datasets_path, "w") as f:
                json.dump(data, f, indent=2)

    # Update totals
    for r in data["results"]:
        r["total_citations"] = sum(p.get("citation_count", 0) or 0 for p in r.get("paper_relations", []))
        r["total_citations_after_created"] = len(r.get("citing_papers", []))

    with open(datasets_path, "w") as f:
        json.dump(data, f, indent=2)
    print(f"  {total_citing} total citing papers found", file=sys.stderr)


def step_direct_refs(adapter):
    """Step 3: Discover direct dataset references via full-text search."""
    output_path = adapter.output_dir / "direct_refs.json"
    run(
        ["python3", "find_reuse.py", "--discover",
         "--archives", adapter.name, "--deduplicate",
         "-o", str(output_path), "-v"],
        f"Direct reference discovery for {adapter.name}",
    )


def step_classify(adapter):
    """Step 4: Fetch text, classify, merge, deduplicate."""
    # This step reuses the generic pipeline but with archive-specific paths
    datasets_path = adapter.output_dir / "datasets.json"
    output_path = adapter.output_dir / "classifications.json"

    run(
        ["python3", "fetch_and_classify_new.py",
         "--datasets", str(datasets_path),
         "--output", str(output_path)],
        f"Text fetch + classification for {adapter.name}",
    )


def main():
    parser = argparse.ArgumentParser(description="Run reuse analysis for any archive")
    parser.add_argument("--archive", required=True, help="Archive name (dandi, crcns)")
    parser.add_argument("--step", help="Run only a specific step")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    adapter = get_adapter(args.archive, verbose=args.verbose)
    print(f"Archive: {adapter.name}", file=sys.stderr)
    print(f"Output dir: {adapter.output_dir}", file=sys.stderr)

    start = time.time()

    steps = {
        "discover-datasets": step_discover_datasets,
        "fetch-citations": step_fetch_citations,
        "direct-refs": step_direct_refs,
    }

    if args.step:
        if args.step not in steps:
            print(f"Unknown step: {args.step}. Available: {list(steps.keys())}", file=sys.stderr)
            sys.exit(1)
        steps[args.step](adapter)
    else:
        for step_name, step_fn in steps.items():
            step_fn(adapter)

    elapsed = time.time() - start
    print(f"\nCompleted in {elapsed/60:.1f} minutes", file=sys.stderr)


if __name__ == "__main__":
    main()
