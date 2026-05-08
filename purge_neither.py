#!/usr/bin/env python3
"""Purge NEITHER pairs from a review round.

Removes all NEITHER-classified pairs from a review round's bookkeeping:
  - sample.json:                  drop NEITHER entries from sampled_pairs,
                                  set include_neither=false
  - review_state.json:            drop keys for NEITHER pairs
  - classification_rounds/<NNN>_*/classifications.json:
                                  drop entries whose triple was NEITHER in
                                  the original sample, recompute
                                  metadata.classification_counts and
                                  metadata.total_pairs

Use this when a review round was originally seeded with include_neither=true
but the scope has narrowed to REUSE/MENTION only. Leaving unreviewed NEITHER
pairs in place pollutes transition matrices in render_review_flow.py whenever
a later classification round relabels them as MENTION.

Idempotent: re-running on an already-purged round is a no-op.

Usage:
    python purge_neither.py \\
        --review-round-dir output/minimal/crcns/review_rounds/review_round_1
"""
import argparse
import json
from collections import Counter
from pathlib import Path


def _purge_sample(sample_path: Path) -> tuple[set[str], set[tuple[str, str, str]]]:
    sample_data = json.loads(sample_path.read_text())
    neither_composite_keys = {
        f"{pair['citing_doi']}|{pair['dandiset_id']}"
        for pair in sample_data["sampled_pairs"]
        if pair["classification"] == "NEITHER"
    }
    neither_triples = {
        (pair["citing_doi"], pair.get("cited_doi", ""), pair["dandiset_id"])
        for pair in sample_data["sampled_pairs"]
        if pair["classification"] == "NEITHER"
    }
    sample_data["sampled_pairs"] = [
        pair for pair in sample_data["sampled_pairs"]
        if pair["classification"] != "NEITHER"
    ]
    sample_data["include_neither"] = False
    sample_path.write_text(json.dumps(sample_data, indent=2))
    return neither_composite_keys, neither_triples


def _purge_review_state(review_state_path: Path, neither_composite_keys: set[str]) -> int:
    if not review_state_path.exists():
        return 0
    review_state = json.loads(review_state_path.read_text())
    removed = sum(1 for key in review_state if key in neither_composite_keys)
    cleaned = {key: value for key, value in review_state.items() if key not in neither_composite_keys}
    review_state_path.write_text(json.dumps(cleaned, indent=2))
    return removed


def _purge_classification_round(
    classifications_path: Path,
    neither_triples: set[tuple[str, str, str]],
) -> int:
    data = json.loads(classifications_path.read_text())
    original_count = len(data["classifications"])
    kept = [
        entry for entry in data["classifications"]
        if (entry["citing_doi"], entry.get("cited_doi", ""), entry["dandiset_id"]) not in neither_triples
    ]
    data["classifications"] = kept
    metadata = data.get("metadata", {})
    if "classification_counts" in metadata:
        counts = Counter(entry["classification"] for entry in kept)
        metadata["classification_counts"] = {
            label: counts.get(label, 0) for label in metadata["classification_counts"]
        }
    if "total_pairs" in metadata:
        metadata["total_pairs"] = len(kept)
    classifications_path.write_text(json.dumps(data, indent=2))
    return original_count - len(kept)


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--review-round-dir", required=True, type=Path,
                        help="Path to the review round directory.")
    args = parser.parse_args()
    review_round_dir = args.review_round_dir

    sample_path = review_round_dir / "sample.json"
    neither_composite_keys, neither_triples = _purge_sample(sample_path)
    print(f"sample.json: removed {len(neither_triples)} NEITHER pairs")

    review_state_path = review_round_dir / "review_state.json"
    removed_review = _purge_review_state(review_state_path, neither_composite_keys)
    print(f"review_state.json: removed {removed_review} NEITHER keys")

    rounds_root = review_round_dir / "classification_rounds"
    for round_dir in sorted(rounds_root.iterdir()):
        if not (round_dir.is_dir() and round_dir.name[:3].isdigit()):
            continue
        classifications_path = round_dir / "classifications.json"
        removed = _purge_classification_round(classifications_path, neither_triples)
        print(f"{round_dir.name}/classifications.json: removed {removed} NEITHER entries")


if __name__ == "__main__":
    main()
