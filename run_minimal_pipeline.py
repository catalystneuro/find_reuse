#!/usr/bin/env python3
"""Minimal archive-agnostic reuse pipeline for prototyping manual verification.

Stages:
    1. Discover datasets + primary papers (via archive adapter)
    2. Fetch citing papers from OpenAlex + their full text
    3. Extract citation contexts
    4. Classify each citing paper as REUSE / MENTION / NEITHER (LLM)

Usage:
    python run_minimal_pipeline.py --archive dandi
    python run_minimal_pipeline.py --archive crcns --limit 5
"""

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

from tqdm import tqdm

from archives import get_adapter
from dandi_primary_papers import (
    _make_openalex_session,
    fetch_citing_paper_texts,
    find_citing_papers,
)

CACHE_DIR = Path(".paper_cache")


def stage1_datasets(adapter):
    data = adapter.build_datasets_json()
    data["results"] = sorted(data["results"], key=lambda r: r["dataset_id"])
    data["count"] = len(data["results"])
    (adapter.output_dir / "datasets.json").write_text(json.dumps(data, indent=2))
    print(f"  {data['count']} datasets discovered", file=sys.stderr)


def stage1b_filter_by_citations(adapter, limit, max_citing_papers):
    datasets_path = adapter.output_dir / "datasets.json"
    data = json.loads(datasets_path.read_text())
    session = _make_openalex_session()
    per_dataset_cap = max_citing_papers if max_citing_papers is not None else sys.maxsize

    kept = []
    pbar = tqdm(data["results"], desc="Finding citing papers")
    scanned = 0
    for result in pbar:
        scanned += 1
        find_citing_papers(result, session, max_citing_papers_per_dandiset=per_dataset_cap)
        if result["citing_papers"]:
            kept.append(result)
            pbar.set_postfix({"kept": len(kept)})
            if limit is not None and len(kept) >= limit:
                break

    data["results"] = kept
    data["count"] = len(kept)
    datasets_path.write_text(json.dumps(data, indent=2))
    print(f"  {len(kept)} datasets with ≥1 citing paper selected (scanned {scanned})", file=sys.stderr)


def stage2_citing_papers_and_text(adapter, max_citing_papers):
    datasets_path = adapter.output_dir / "datasets.json"
    data = json.loads(datasets_path.read_text())
    data["results"] = fetch_citing_paper_texts(
        data["results"],
        max_citing_papers_per_dandiset=max_citing_papers if max_citing_papers is not None else sys.maxsize,
        show_progress=True,
        cache_dir=str(CACHE_DIR),
    )
    datasets_path.write_text(json.dumps(data, indent=2))


def stage3_extract_contexts(adapter):
    subprocess.run(
        [
            "python3", "extract_citation_contexts.py",
            "--results-file", str(adapter.output_dir / "datasets.json"),
            "--cache-dir", str(CACHE_DIR),
            "-o", str(adapter.output_dir / "citation_contexts.json"),
        ],
        check=True,
    )


def stage4_classify(adapter):
    classifications_path = adapter.output_dir / "classifications.json"
    subprocess.run(
        [
            "python3", "classify_citing_papers.py",
            "--results-file", str(adapter.output_dir / "datasets.json"),
            "--cache-dir", str(CACHE_DIR),
            "-o", str(classifications_path),
            "--workers", "4",
        ],
        check=True,
    )
    return classifications_path


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--archive", required=True,
                        help="Archive short name (e.g. dandi, crcns, openneuro, sparc).")
    parser.add_argument("--limit", type=int, default=None,
                        help="Cap to first N datasets (sorted by ID) that have ≥1 citing paper. Default: all.")
    parser.add_argument("--max-citing-papers", type=int, default=None,
                        help="Cap citing papers fetched per dataset. Default: all.")
    args = parser.parse_args()

    start = time.time()
    adapter = get_adapter(
        args.archive,
        output_dir=f"output/minimal/{args.archive}",
        verbose=True,
    )
    print(f"Archive: {adapter.name}", file=sys.stderr)
    print(f"Output dir: {adapter.output_dir}", file=sys.stderr)

    stage1_datasets(adapter)
    stage1b_filter_by_citations(adapter, args.limit, args.max_citing_papers)
    stage2_citing_papers_and_text(adapter, args.max_citing_papers)
    stage3_extract_contexts(adapter)
    classifications_path = stage4_classify(adapter)

    print(f"\nDone in {(time.time() - start) / 60:.1f} min. See {classifications_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
