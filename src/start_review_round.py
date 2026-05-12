#!/usr/bin/env python3
"""Start a new review round from the current pipeline output.

Snapshots the latest output of run_indirect_pipeline.py into a fresh review_round_N/
directory, draws a stratified sample, projects the snapshot's classifications onto
the sample (recorded as classification_rounds/001_initial/), and builds review.html.

Run run_indirect_pipeline.py first; this script does not run the pipeline.

Usage:
    python -m src.start_review_round --archive crcns
    python -m src.start_review_round --archive crcns --samples-per-class 50 --include-neither false
"""

import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from src.build_indirect_review import build_review_html
from src.sampling import SAMPLING_SEED, stratified_sample


def _str_to_bool(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in ("true", "1", "yes", "y"):
        return True
    if normalized in ("false", "0", "no", "n"):
        return False
    raise argparse.ArgumentTypeError(f"Expected true/false, got {value!r}")


def _git_sha() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def _next_review_round_dir(archive_dir: Path) -> Path:
    review_rounds_dir = archive_dir / "review_rounds"
    review_rounds_dir.mkdir(parents=True, exist_ok=True)
    existing = [
        path for path in review_rounds_dir.iterdir()
        if path.is_dir() and path.name.startswith("review_round_")
    ]
    numbers = [int(path.name.removeprefix("review_round_")) for path in existing]
    next_number = max(numbers, default=0) + 1
    return review_rounds_dir / f"review_round_{next_number}"


def _snapshot_pipeline_files(archive_dir: Path, review_round_dir: Path) -> Path:
    snapshot_dir = review_round_dir / "pipeline_snapshot"
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    for filename in ("datasets.json", "citation_contexts.json", "classifications.json"):
        source = archive_dir / filename
        if not source.exists():
            print(
                f"Error: {source} not found. Run `python -m src.run_indirect_pipeline --archive "
                f"{archive_dir.name}` first.",
                file=sys.stderr,
            )
            sys.exit(1)
        shutil.copy2(source, snapshot_dir / filename)
    return snapshot_dir


def _sample_classifications(
    classifications: list[dict], samples_per_class: int, include_neither: bool,
) -> list[dict]:
    """Sample full classification dicts (preserving cited_doi and all other fields)
    so that two records sharing (citing_doi, dandiset_id) but differing in cited_doi
    remain distinct rows. stratified_sample mutates the dicts with sample_order.

    The sort below matches the legacy build_minimal_review.py ordering and is
    load-bearing: stratified_sample shuffles each class's group in place with a
    fixed seed, so the shuffle output (and therefore the sampled 150) depends on
    the input order. To stay byte-identical to legacy review-round samples, the
    candidate list must be ordered by (dandiset_id, citing_doi) (stable, ties
    keep classifications.json file order).
    """
    candidates = [
        classification for classification in classifications
        if include_neither or classification.get("classification") != "NEITHER"
    ]
    candidates.sort(
        key=lambda c: (c.get("dandiset_id", ""), c.get("citing_doi", "")),
    )
    print(f"Stratified sampling (samples_per_class={samples_per_class}, seed={SAMPLING_SEED}):")
    return stratified_sample(candidates, samples_per_class)


def _write_sample_json(
    review_round_dir: Path,
    archive: str,
    samples_per_class: int,
    include_neither: bool,
    sampled: list[dict],
) -> Path:
    sample_path = review_round_dir / "sample.json"
    payload = {
        "archive": archive,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "seed": SAMPLING_SEED,
        "samples_per_class": samples_per_class,
        "include_neither": include_neither,
        "git_sha": _git_sha(),
        "sampled_pairs": [
            {
                "key": f"{entry['citing_doi']}|{entry['dandiset_id']}",
                "citing_doi": entry["citing_doi"],
                "cited_doi": entry.get("cited_doi", ""),
                "dandiset_id": entry["dandiset_id"],
                "classification": entry["classification"],
                "sample_order": entry["sample_order"],
            }
            for entry in sampled
        ],
    }
    sample_path.write_text(json.dumps(payload, indent=2))
    print(f"Wrote {sample_path} ({len(sampled)} sampled pairs)")
    return sample_path


def _write_initial_classification_round(
    review_round_dir: Path,
    snapshot_metadata: dict,
    sampled_classifications: list[dict],
    archive: str,
) -> Path:
    """Persist the exact sampled classification dicts (no re-filtering) as the
    initial classification round of this review round.
    """
    counts = {"REUSE": 0, "MENTION": 0, "NEITHER": 0}
    for classification in sampled_classifications:
        label = classification.get("classification", "NEITHER")
        counts[label] = counts.get(label, 0) + 1

    classification_round_dir = review_round_dir / "classification_rounds" / "001_initial"
    classification_round_dir.mkdir(parents=True, exist_ok=True)
    classifications_path = classification_round_dir / "classifications.json"
    classifications_path.write_text(json.dumps({
        "metadata": {
            **snapshot_metadata,
            "scoped_to_sample": True,
            "total_pairs": len(sampled_classifications),
            "classification_counts": counts,
        },
        "classifications": sampled_classifications,
    }, indent=2))

    metadata_path = classification_round_dir / "metadata.json"
    metadata_path.write_text(json.dumps({
        "id": "001_initial",
        "description": "Initial pipeline classifications, scoped to the review round's sample.",
        "model": snapshot_metadata.get("model", ""),
        "parent": None,
        "rerun_extraction": False,
        "archive": archive,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "git_sha": _git_sha(),
        "source": "pipeline_snapshot/classifications.json",
    }, indent=2))
    print(f"Wrote {classifications_path} ({len(sampled_classifications)} entries)")
    return classification_round_dir


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--archive", required=True,
                        help="Archive short name (e.g. dandi, crcns, openneuro, sparc).")
    parser.add_argument("--samples-per-class", type=int, default=50,
                        help="Number of entries to sample per classification (default: 50).")
    parser.add_argument("--include-neither", type=_str_to_bool, default=True,
                        help="Whether NEITHER pairs are included in the sample (default: true).")
    args = parser.parse_args()

    archive_dir = Path("output/indirect") / args.archive
    review_round_dir = _next_review_round_dir(archive_dir)
    review_round_dir.mkdir(parents=True, exist_ok=True)
    print(f"Creating {review_round_dir}", file=sys.stderr)

    snapshot_dir = _snapshot_pipeline_files(archive_dir, review_round_dir)
    snapshot_classifications_path = snapshot_dir / "classifications.json"

    snapshot_data = json.loads(snapshot_classifications_path.read_text())
    sampled = _sample_classifications(
        snapshot_data["classifications"], args.samples_per_class, args.include_neither,
    )

    _write_sample_json(
        review_round_dir, args.archive,
        args.samples_per_class, args.include_neither, sampled,
    )

    _write_initial_classification_round(
        review_round_dir, snapshot_data.get("metadata", {}), sampled, args.archive,
    )

    build_review_html(
        review_round_dir=review_round_dir,
        classification_round="001_initial",
        archive_name=args.archive,
    )

    print(f"\nReview round ready: {review_round_dir}", file=sys.stderr)
    print(f"  Open {review_round_dir / 'review.html'} to begin manual review.", file=sys.stderr)


if __name__ == "__main__":
    main()
