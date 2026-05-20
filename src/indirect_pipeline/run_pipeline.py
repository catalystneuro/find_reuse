#!/usr/bin/env python3
"""Indirect (citation-based) archive-agnostic reuse pipeline.

This is the indirect counterpart to the direct pipeline (which searches paper
text for dataset mentions). Here, reuse is inferred from citations to the
dataset's primary paper.

Stages:
    1. Discover datasets (via archive adapter)
    2. Find citing papers (OpenAlex, filter to datasets with ≥1 hit)
    3. Fetch citing paper texts
    4. Extract citation contexts
    5. Classify each citing paper as REUSE / MENTION / NEITHER (LLM)

Usage:
    python -m src.indirect_pipeline.run_pipeline --archive dandi
    python -m src.indirect_pipeline.run_pipeline --archive crcns --limit 5
"""

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

from tqdm import tqdm

from ..archives import get_adapter
from .openalex import (
    _make_openalex_session,
    fetch_citing_paper_texts,
    find_citing_papers,
)

CACHE_DIR = Path(".paper_cache")


def stage1_discover_datasets(adapter):
    data = adapter.build_datasets_json()
    data["results"] = sorted(data["results"], key=lambda r: r["dataset_id"])
    data["count"] = len(data["results"])
    (adapter.output_dir / "datasets.json").write_text(json.dumps(data, indent=2))
    print(f"  {data['count']} datasets discovered", file=sys.stderr)


def stage2_find_citing_papers(adapter, limit, max_citing_papers):
    datasets_path = adapter.output_dir / "datasets.json"
    data = json.loads(datasets_path.read_text())
    # Preserve the pre-filter dataset count for downstream renderers.
    # If this stage runs on already-filtered data, keep the existing value.
    total_before_filter = data.get("total_before_filter", data["count"])
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
    data["total_before_filter"] = total_before_filter
    data["max_citing_papers"] = max_citing_papers
    datasets_path.write_text(json.dumps(data, indent=2))
    print(f"  {len(kept)} datasets with ≥1 citing paper selected (scanned {scanned})", file=sys.stderr)


def stage3_fetch_paper_texts(adapter, max_citing_papers):
    datasets_path = adapter.output_dir / "datasets.json"
    data = json.loads(datasets_path.read_text())
    data["results"] = fetch_citing_paper_texts(
        data["results"],
        max_citing_papers_per_dandiset=max_citing_papers if max_citing_papers is not None else sys.maxsize,
        show_progress=True,
        cache_dir=str(CACHE_DIR),
    )
    datasets_path.write_text(json.dumps(data, indent=2))


def stage4_extract_contexts(adapter):
    subprocess.run(
        [
            "python3", "-m", "src.indirect_pipeline.extract_citation_contexts",
            "--results-file", str(adapter.output_dir / "datasets.json"),
            "--cache-dir", str(CACHE_DIR),
            "-o", str(adapter.output_dir / "citation_contexts.json"),
        ],
        check=True,
    )


def stage5_classify(adapter, model, clear_cache):
    classifications_path = adapter.output_dir / "classifications.json"
    command = [
        "python3", "-m", "src.indirect_pipeline.classify_citing_papers",
        "--contexts-file", str(adapter.output_dir / "citation_contexts.json"),
        "--cache-dir", str(CACHE_DIR),
        "-o", str(classifications_path),
        "--workers", "4",
        "--model", model,
    ]
    if clear_cache:
        command.append("--clear-cache")
    subprocess.run(command, check=True)
    return classifications_path


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--archive", required=True,
                        help="Archive short name (e.g. dandi, crcns, openneuro, sparc).")
    parser.add_argument("--limit", type=int, default=None,
                        help="Cap to first N datasets (sorted by ID) that have ≥1 citing paper. Default: all.")
    parser.add_argument("--max-citing-papers", type=int, default=None,
                        help="Cap citing papers fetched per dataset. Default: all.")
    parser.add_argument("--model", default="google/gemini-3.5-flash",
                        help="OpenRouter model for classification (default: google/gemini-3.5-flash).")
    parser.add_argument("--clear-cache", action="store_true",
                        help="Clear classification cache before running.")
    parser.add_argument("--stop-after", choices=["discover", "find-citing", "fetch", "contexts"],
                        default=None,
                        help="Stop after the named stage instead of running the full pipeline.")
    args = parser.parse_args()

    start = time.time()
    adapter = get_adapter(
        args.archive,
        output_dir=f"output/indirect/{args.archive}",
        verbose=True,
    )
    print(f"Archive: {adapter.name}", file=sys.stderr)
    print(f"Output dir: {adapter.output_dir}", file=sys.stderr)

    def _maybe_stop(stage):
        if args.stop_after == stage:
            print(f"\nStopped after '{stage}' in {(time.time() - start) / 60:.1f} min "
                  f"(--stop-after {stage}).", file=sys.stderr)
            return True
        return False

    stage1_discover_datasets(adapter)
    if _maybe_stop("discover"):
        return
    stage2_find_citing_papers(adapter, args.limit, args.max_citing_papers)
    if _maybe_stop("find-citing"):
        return
    stage3_fetch_paper_texts(adapter, args.max_citing_papers)
    if _maybe_stop("fetch"):
        return
    stage4_extract_contexts(adapter)
    if _maybe_stop("contexts"):
        return
    classifications_path = stage5_classify(adapter, args.model, args.clear_cache)

    print(f"\nDone in {(time.time() - start) / 60:.1f} min. See {classifications_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
