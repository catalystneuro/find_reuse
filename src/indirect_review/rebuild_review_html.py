#!/usr/bin/env python3
"""Rebuild review.html for a review round against a specific classification round.

Use this after running a new classification round when you want to see the new
model's reasoning in the review UI. The original review.html (written by
start_review_round.py against 001_initial) is left untouched; the rebuilt HTML
is written alongside the classification round's classifications.json by default.

Usage:
    python -m src.indirect_review.rebuild_review_html --archive crcns --review-round 2
    python -m src.indirect_review.rebuild_review_html --archive crcns --review-round 2 --classification-round 002_some-slug
    python -m src.indirect_review.rebuild_review_html --archive crcns --review-round 2 --output some/path/review.html
"""

import argparse
import sys
from pathlib import Path

from .review_builder import build_review_html


def _latest_classification_round_id(review_round_dir: Path) -> str | None:
    rounds_root = review_round_dir / "classification_rounds"
    if not rounds_root.exists():
        return None
    existing = sorted(
        path.name for path in rounds_root.iterdir()
        if path.is_dir() and path.name[:3].isdigit()
    )
    return existing[-1] if existing else None


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--archive", required=True,
                        help="Archive short name (e.g. dandi, crcns, openneuro, sparc).")
    parser.add_argument("--review-round", type=int, required=True,
                        help="Review round number (1-indexed).")
    parser.add_argument("--classification-round", default=None,
                        help="Classification round directory name to render "
                             "(default: the latest one in classification_rounds/).")
    parser.add_argument("--output", type=Path, default=None,
                        help="Where to write the rebuilt HTML "
                             "(default: <review_round_dir>/classification_rounds/<round>/review.html, "
                             "which keeps the original review.html intact).")
    args = parser.parse_args()

    review_round_dir = (
        Path("output/indirect") / args.archive / "review_rounds"
        / f"review_round_{args.review_round}"
    )
    if not review_round_dir.exists():
        print(f"Error: {review_round_dir} not found.", file=sys.stderr)
        sys.exit(1)

    classification_round = args.classification_round or _latest_classification_round_id(review_round_dir)
    if classification_round is None:
        print(f"Error: no classification rounds found in {review_round_dir / 'classification_rounds'}.",
              file=sys.stderr)
        sys.exit(1)

    classification_round_dir = review_round_dir / "classification_rounds" / classification_round
    if not classification_round_dir.exists():
        print(f"Error: {classification_round_dir} not found.", file=sys.stderr)
        sys.exit(1)

    output_path = args.output or (classification_round_dir / "review.html")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    build_review_html(
        review_round_dir=review_round_dir,
        classification_round=classification_round,
        archive_name=args.archive,
        output_path=output_path,
    )


if __name__ == "__main__":
    main()
