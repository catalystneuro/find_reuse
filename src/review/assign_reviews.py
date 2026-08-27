#!/usr/bin/env python3
"""
Deal candidate pairs out to reviewers.

Narrows the candidate list to the round you want reviewed, then splits it among
the registered reviewers so each pair belongs to exactly one person.

Dealing is incremental: a pair somebody already holds stays with them, so a
rerun of the pipeline reassigns only what it added and nobody re-reviews work
they have already done. Only assignments that actually changed are rewritten,
so a run that adds nothing leaves the tree untouched.

Usage:
    python -m src.review.assign_reviews --dandi-hosted --lab different
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from src.review.build_candidates import CANDIDATES_FILE
from src.review.reviewers import (ASSIGNMENTS_DIR, REVIEWERS_FILE, REVIEWS_DIR,
                                  answers_path, assignment_path, load_reviewers,
                                  select_reviewers)

PATHWAYS = ('indirect', 'direct')


def matches(pair: dict, args: argparse.Namespace) -> bool:
    """Whether a pair belongs to the round these flags describe."""
    if args.pathway and pair['pathway'] != args.pathway:
        return False
    for value, field in ((args.dandi_hosted, 'reused_dandi_hosted'),
                         (args.neuro, 'reused_neurophysiology')):
        if value is not None and pair[field] is not value:
            return False
    if args.modality and not set(args.modality) & set(pair['reused_modalities']):
        return False
    if args.archive and not set(args.archive) & set(pair['archives']):
        return False
    if args.reuse_type and not set(args.reuse_type) & set(pair['reuse_types']):
        return False
    # 'mixed' means the pathways disagreed, which satisfies either side rather
    # than neither: the pair really does have a same-lab record behind it.
    if args.lab == 'same' and pair['same_lab'] not in (True, 'mixed'):
        return False
    if args.lab == 'different' and pair['same_lab'] not in (False, 'mixed'):
        return False
    if args.min_confidence is not None and pair['confidence'] < args.min_confidence:
        return False
    if args.exclude_unverifiable_quotes and pair['unverifiable_quotes']:
        return False
    return True


def assigned_to(assignments_dir: Path) -> dict[str, str]:
    """
    Every key already sitting in an assignment, and whose it is.

    Every assignment on disk counts, not just those of the reviewers in this
    round: a pair someone else holds is spoken for either way.
    """
    holders = {}
    for path in sorted(assignments_dir.glob('*.json')):
        assignment = json.loads(path.read_text())
        for key in assignment['keys']:
            holders[key] = assignment['reviewer']
    return holders


def answered_by(registry: list[dict], reviews_dir: Path) -> dict[str, str]:
    """
    Every key somebody has already judged, and who judged it.

    A pair with an answer belongs to whoever gave it. Dealing it to a second
    person would buy nothing and cost them the reading.
    """
    holders = {}
    for reviewer in registry:
        path = answers_path(reviewer['name'], reviews_dir)
        if path.exists():
            for key in json.loads(path.read_text())['calls']:
                holders[key] = reviewer['name']
    return holders


def deal(pairs: list[dict], reviewers: list[dict], placed: dict[str, str],
         answered: dict[str, str], limit: int | None
         ) -> dict[tuple[str, str], list[str]]:
    """
    Give each unplaced pair to whoever holds the fewest of its pathway.

    A pair someone has already answered goes to them instead of into the deal,
    so their own work stays in their queue for them to look back over. A pair
    answered by someone sitting this round out is left alone entirely.

    Ties go to the reviewer the registry lists first, so the same inputs deal
    the same way every time. Counting per pathway keeps each queue split evenly,
    which splits the round evenly too.
    """
    names = [r['name'] for r in reviewers]
    assigned = {(name, pathway): [] for name in names for pathway in PATHWAYS}
    counts = {(name, pathway): 0 for name in names for pathway in PATHWAYS}
    by_key = {p['key']: p for p in pairs}
    for key, holder in placed.items():
        pair = by_key.get(key)
        if pair and (holder, pair['pathway']) in counts:
            counts[(holder, pair['pathway'])] += 1

    unplaced, dealt = [], 0
    for pair in pairs:
        if pair['key'] in placed:
            continue
        owner = answered.get(pair['key'])
        if owner is None:
            unplaced.append(pair)
        elif owner in names:
            assigned[(owner, pair['pathway'])].append(pair['key'])
            counts[(owner, pair['pathway'])] += 1

    for pair in unplaced:
        if limit is not None and dealt >= limit:
            break
        pathway = pair['pathway']
        name = min(names, key=lambda n: counts[(n, pathway)])
        assigned[(name, pathway)].append(pair['key'])
        counts[(name, pathway)] += 1
        dealt += 1
    return assigned


def write_assignment(reviewer: str, pathway: str, new_keys: list[str],
                     generated_at: str, assignments_dir: Path) -> tuple[int, int]:
    """
    Add a reviewer's new keys to their assignment, if there are any.

    Returns (newly assigned, total held). The file is left alone when nothing
    changed, so a run that deals nothing produces no diff.
    """
    path = assignment_path(reviewer, pathway, assignments_dir)
    existing = json.loads(path.read_text())['keys'] if path.exists() else []
    if not new_keys:
        return 0, len(existing)

    keys = sorted(set(existing) | set(new_keys))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        'reviewer': reviewer,
        'pathway': pathway,
        'assigned_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'candidates_generated_at': generated_at,
        'keys': keys,
    }, indent=2, ensure_ascii=False) + '\n')
    return len(new_keys), len(keys)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reviewers',
                        help='Comma-separated subset of the registry; '
                             'all registered reviewers by default.')
    parser.add_argument('--pathway', choices=list(PATHWAYS),
                        help='Only this queue. Both by default, dealt separately.')
    parser.add_argument('--dandi-hosted', action=argparse.BooleanOptionalAction,
                        help='Whether the reused data was hosted on DANDI.')
    parser.add_argument('--neuro', action=argparse.BooleanOptionalAction,
                        help='Whether neurophysiology was among what was reused.')
    parser.add_argument('--modality', action='append',
                        help='Keep pairs reusing this modality; repeatable.')
    parser.add_argument('--archive', action='append',
                        help='Keep pairs sourced from this archive; repeatable.')
    parser.add_argument('--reuse-type', action='append',
                        help='Keep pairs of this reuse type; repeatable.')
    parser.add_argument('--lab', choices=['same', 'different', 'any'], default='any',
                        help='Whether the reusing group produced the data.')
    parser.add_argument('--min-confidence', type=int,
                        help="Drop pairs the classifier was less sure of.")
    parser.add_argument('--exclude-unverifiable-quotes', action='store_true',
                        help='Drop pairs whose every quote is missing from the paper.')
    parser.add_argument('--limit', type=int,
                        help='Assign at most this many new pairs.')
    args = parser.parse_args()

    candidates = json.loads(CANDIDATES_FILE.read_text())
    registry = load_reviewers(REVIEWERS_FILE)
    reviewers = select_reviewers(registry, args.reviewers)
    pairs = [p for p in candidates['pairs'] if matches(p, args)]
    print(f'{len(pairs)} of {len(candidates["pairs"])} candidate pairs match')

    assigned = deal(pairs, reviewers, assigned_to(ASSIGNMENTS_DIR),
                    answered_by(registry, REVIEWS_DIR), args.limit)

    for (reviewer, pathway), keys in sorted(assigned.items()):
        new, total = write_assignment(reviewer, pathway, keys,
                                      candidates['generated_at'], ASSIGNMENTS_DIR)
        if total:
            print(f'  {reviewer:<20} {pathway:<9} '
                  f'+{new} new, {total} assigned in total')
    print(f'Assignments in {ASSIGNMENTS_DIR}')


if __name__ == '__main__':
    main()
