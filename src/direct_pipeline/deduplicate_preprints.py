#!/usr/bin/env python3
"""
deduplicate_preprints.py — Remove preprint/published duplicate entries from classifications.

When a paper exists as both a preprint (bioRxiv/medRxiv/arXiv) and a published journal
article, both may appear as separate citing papers. This script keeps the journal version
and removes the preprint duplicate, matching by normalized title + dandiset_id.

Usage:
    python deduplicate_preprints.py
    python deduplicate_preprints.py --dry-run   # show what would be removed
"""

import argparse
import json
import sys
from collections import defaultdict


def is_preprint_doi(doi):
    """Check if a DOI is from a preprint server."""
    doi_lower = doi.lower()
    return ("10.1101/" in doi_lower or "arxiv" in doi_lower
            or "10.21203/" in doi_lower or "10.2139/" in doi_lower)


def normalize_title(title):
    """Normalize title for matching."""
    return (title or "").strip().lower()


def deduplicate(classifications, dry_run=False):
    """Remove preprint duplicates, preferring journal versions."""
    # Group by (normalized_title, dandiset_id)
    groups = defaultdict(list)
    for i, c in enumerate(classifications):
        title = normalize_title(c.get("citing_title", ""))
        did = c.get("dandiset_id", "")
        if title and len(title) > 20:
            groups[(title, did)].append(i)

    remove_indices = set()
    for (title, did), indices in groups.items():
        if len(indices) <= 1:
            continue

        entries = [(i, classifications[i]) for i in indices]
        preprint_indices = [i for i, c in entries if is_preprint_doi(c["citing_doi"])]
        journal_indices = [i for i, c in entries if not is_preprint_doi(c["citing_doi"])]

        if preprint_indices and journal_indices:
            # Keep journal version(s), remove preprint version(s)
            remove_indices.update(preprint_indices)
        elif len(preprint_indices) > 1 and not journal_indices:
            # Multiple preprints, no journal — keep the first
            remove_indices.update(preprint_indices[1:])

    if dry_run:
        print(f"Would remove {len(remove_indices)} duplicate entries:")
        by_cls = defaultdict(int)
        for i in remove_indices:
            by_cls[classifications[i]["classification"]] += 1
        for cls_name, n in sorted(by_cls.items()):
            print(f"  {cls_name}: {n}")
        print(f"\nExamples:")
        for i in sorted(remove_indices)[:10]:
            c = classifications[i]
            print(f"  {c['citing_doi']} -> {c.get('dandiset_id', '')} ({c['classification']})")
        return classifications

    kept = [c for i, c in enumerate(classifications) if i not in remove_indices]
    return kept


def main():
    parser = argparse.ArgumentParser(description="Deduplicate preprint/published pairs")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be removed")
    args = parser.parse_args()

    with open("output/all_classifications.json") as f:
        data = json.load(f)

    before = len(data["classifications"])
    data["classifications"] = deduplicate(data["classifications"], dry_run=args.dry_run)
    after = len(data["classifications"])

    if args.dry_run:
        return

    data["count"] = after
    with open("output/all_classifications.json", "w") as f:
        json.dump(data, f, indent=2)

    print(f"Deduplicated: {before} -> {after} ({before - after} removed)", file=sys.stderr)


if __name__ == "__main__":
    main()
