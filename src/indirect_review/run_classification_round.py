#!/usr/bin/env python3
"""Add a new classification round to an existing review round.

Re-classifies the sampled pair set (from review_round_N/sample.json) against the
classifier described by --model / --prompt-file, optionally re-extracting citation
contexts first if a fix lives in extract_citation_contexts.py.

Output goes to review_round_N/classification_rounds/NNN_<slug>/, where NNN is the
next available number and <slug> is derived from --description.

Usage:
    python run_classification_round.py \\
        --archive crcns --review-round 1 \\
        --description "Tighten REUSE rubric: exclude fitted-parameter borrowing"

    python run_classification_round.py \\
        --archive crcns --review-round 1 \\
        --description "Try Opus baseline" --model anthropic/claude-opus-4

    python run_classification_round.py \\
        --archive crcns --review-round 1 \\
        --description "Test extraction fix" --rerun-extraction

This always runs with --no-cache passed through to classify_citing_papers.py: the
whole point of a new classification round is to re-classify, so reading cached
results from a previous run with a different prompt/model would defeat that. The
persistent cache in .classification_cache/ is left untouched.
"""

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

CACHE_DIR = Path(".paper_cache")


def _git_sha() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def _slugify(description: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", description).strip("-").lower()
    return slug[:60] if len(slug) > 60 else slug


def _next_classification_round_dir(review_round_dir: Path, slug: str) -> tuple[Path, str]:
    rounds_root = review_round_dir / "classification_rounds"
    rounds_root.mkdir(parents=True, exist_ok=True)
    existing = sorted(
        path for path in rounds_root.iterdir() if path.is_dir() and path.name[:3].isdigit()
    )
    numbers = [int(path.name[:3]) for path in existing]
    next_number = max(numbers, default=0) + 1
    name = f"{next_number:03d}_{slug}"
    return rounds_root / name, name


def _latest_classification_round_id(review_round_dir: Path) -> str | None:
    rounds_root = review_round_dir / "classification_rounds"
    if not rounds_root.exists():
        return None
    existing = sorted(
        path.name for path in rounds_root.iterdir()
        if path.is_dir() and path.name[:3].isdigit()
    )
    return existing[-1] if existing else None


def _load_sample(review_round_dir: Path) -> tuple[set[tuple[str, str, str]], dict]:
    """Returns (sampled_triples, sample_data). The triple is
    (citing_doi, cited_doi, dandiset_id), which uniquely identifies a row
    when a dataset has multiple primary papers.
    """
    sample_path = review_round_dir / "sample.json"
    sample_data = json.loads(sample_path.read_text())
    triples = {
        (sampled["citing_doi"], sampled.get("cited_doi", ""), sampled["dandiset_id"])
        for sampled in sample_data["sampled_pairs"]
    }
    return triples, sample_data


def _filter_contexts(
    source_contexts_path: Path,
    sampled_triples: set[tuple[str, str, str]],
    output_path: Path,
) -> int:
    """Write a citation_contexts.json containing only the pairs whose triple is sampled."""
    data = json.loads(source_contexts_path.read_text())
    scoped_pairs = [
        pair for pair in data.get("pairs", [])
        if (
            pair.get("citing_doi", ""),
            pair.get("cited_doi", ""),
            pair.get("dandiset_id", ""),
        ) in sampled_triples
    ]
    output = {**data, "pairs": scoped_pairs, "failed_pairs": []}
    output_path.write_text(json.dumps(output, indent=2))
    return len(scoped_pairs)


def _build_filtered_datasets_json(
    snapshot_datasets_path: Path,
    sampled_triples: set[tuple[str, str, str]],
    output_path: Path,
) -> int:
    """Restrict snapshot's datasets.json to only the citing_papers in the sampled set."""
    data = json.loads(snapshot_datasets_path.read_text())
    filtered_results = []
    total = 0
    for dataset in data["results"]:
        dataset_id = dataset["dandiset_id"]
        kept = [
            paper for paper in dataset.get("citing_papers", [])
            if (
                paper.get("doi", ""),
                paper.get("cited_paper_doi", ""),
                dataset_id,
            ) in sampled_triples
        ]
        if kept:
            filtered_results.append({**dataset, "citing_papers": kept})
            total += len(kept)
    data["results"] = filtered_results
    data["count"] = len(filtered_results)
    output_path.write_text(json.dumps(data, indent=2))
    return total


def _run_extract_contexts(filtered_datasets_path: Path, output_path: Path) -> None:
    subprocess.run(
        [
            "python3", "-m", "src.indirect_pipeline.extract_citation_contexts",
            "--results-file", str(filtered_datasets_path),
            "--cache-dir", str(CACHE_DIR),
            "-o", str(output_path),
        ],
        check=True,
    )


def _run_classify(contexts_path: Path, output_path: Path, model: str) -> None:
    command = [
        "python3", "-m", "src.indirect_pipeline.classify_citing_papers",
        "--contexts-file", str(contexts_path),
        "--cache-dir", str(CACHE_DIR),
        "-o", str(output_path),
        "--workers", "4",
        "--model", model,
        "--no-cache",
    ]
    subprocess.run(command, check=True)


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--archive", required=True,
                        help="Archive short name (e.g. dandi, crcns, openneuro, sparc).")
    parser.add_argument("--review-round", type=int, required=True,
                        help="Review round number (1-indexed).")
    parser.add_argument("--description", required=True,
                        help="Short description of what's being tried "
                             "(used to slug-name the directory and recorded in metadata).")
    parser.add_argument("--model", default="google/gemini-3.5-flash",
                        help="OpenRouter model for classification (default: google/gemini-3.5-flash).")
    parser.add_argument("--rerun-extraction", action="store_true",
                        help="Re-run extract_citation_contexts.py on the sampled pairs before classifying. "
                             "Use when a fix lives in the extraction stage.")
    args = parser.parse_args()

    archive_dir = Path("output/indirect") / args.archive
    review_round_dir = archive_dir / "review_rounds" / f"review_round_{args.review_round}"
    if not review_round_dir.exists():
        print(f"Error: {review_round_dir} not found. Run `python -m src.indirect_review.start_review_round` first.",
              file=sys.stderr)
        sys.exit(1)

    sampled_triples, _ = _load_sample(review_round_dir)
    print(f"Loaded {len(sampled_triples)} sampled pairs from {review_round_dir / 'sample.json'}",
          file=sys.stderr)

    slug = _slugify(args.description)
    if not slug:
        print("Error: --description produced an empty slug.", file=sys.stderr)
        sys.exit(1)

    new_round_dir, new_round_id = _next_classification_round_dir(review_round_dir, slug)
    if new_round_dir.exists():
        print(f"Error: {new_round_dir} already exists.", file=sys.stderr)
        sys.exit(1)
    new_round_dir.mkdir(parents=True)
    print(f"Creating classification round {new_round_id} at {new_round_dir}", file=sys.stderr)

    snapshot_dir = review_round_dir / "pipeline_snapshot"

    if args.rerun_extraction:
        filtered_datasets_path = new_round_dir / "filtered_datasets.json"
        retained = _build_filtered_datasets_json(
            snapshot_dir / "datasets.json", sampled_triples, filtered_datasets_path,
        )
        print(f"Filtered datasets.json contains {retained} citing-paper records", file=sys.stderr)
        contexts_path = new_round_dir / "citation_contexts.json"
        _run_extract_contexts(filtered_datasets_path, contexts_path)
    else:
        contexts_path = new_round_dir / "citation_contexts.json"
        retained = _filter_contexts(
            snapshot_dir / "citation_contexts.json", sampled_triples, contexts_path,
        )
        print(f"Filtered citation_contexts.json contains {retained} pairs from snapshot",
              file=sys.stderr)

    classifications_path = new_round_dir / "classifications.json"
    _run_classify(contexts_path, classifications_path, args.model)

    parent = "001_initial"
    parents = sorted(
        path.name for path in (review_round_dir / "classification_rounds").iterdir()
        if path.is_dir() and path.name[:3].isdigit() and path.name != new_round_id
    )
    if parents:
        parent = parents[-1]

    metadata = {
        "id": new_round_id,
        "description": args.description,
        "model": args.model,
        "parent": parent,
        "rerun_extraction": args.rerun_extraction,
        "archive": args.archive,
        "review_round": args.review_round,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "git_sha": _git_sha(),
    }
    (new_round_dir / "metadata.json").write_text(json.dumps(metadata, indent=2))

    print(f"\nClassification round complete: {classifications_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
