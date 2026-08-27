#!/usr/bin/env python3
"""
Deal candidate pairs out to reviewers.

Narrows the candidate list to the round you want reviewed, then splits it among
the registered reviewers so each pair belongs to exactly one person.

An assignment is a queue, not a history: it holds what a reviewer still has to
read. Answering a pair takes it out, and the answer file is what accumulates. So
a round is what you were dealt plus whatever you had left over, and it stays
short enough to work through.

Dealing is incremental: a pair somebody still owes stays theirs, and a pair
somebody has answered is not dealt again, so a rerun of the pipeline hands out
only what it added. Only queues that actually changed are rewritten.

Usage:
    python -m src.review.assign_reviews --dandi-hosted --lab different
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from src.review.build_candidates import CANDIDATES_FILE
from src.shared.classify_fulltext_reuse import MODALITIES, REUSE_TYPES
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

    A pair someone has already answered is spoken for and is not dealt at all:
    asking a second person to read it would buy nothing. It does not go into an
    assignment either, since it is not work anybody is being asked to do — the
    answer is already recorded, and the session picks it up from there.

    Ties go to the reviewer the registry lists first, so the same inputs deal
    the same way every time. What someone still owes and what they have already
    answered both count as their share, so a round goes to whoever has done and
    been given least. Counting per pathway keeps each queue split evenly, which
    splits the round evenly too.
    """
    names = [r['name'] for r in reviewers]
    assigned = {(name, pathway): [] for name in names for pathway in PATHWAYS}
    counts = {(name, pathway): 0 for name in names for pathway in PATHWAYS}
    by_key = {p['key']: p for p in pairs}
    for key, holder in placed.items():
        pair = by_key.get(key)
        if pair and (holder, pair['pathway']) in counts:
            counts[(holder, pair['pathway'])] += 1

    for key, holder in answered.items():
        pair = by_key.get(key)
        if pair and (holder, pair['pathway']) in counts:
            counts[(holder, pair['pathway'])] += 1

    unplaced = [p for p in pairs
                if p['key'] not in placed and p['key'] not in answered]
    dealt = 0
    for pair in unplaced:
        if limit is not None and dealt >= limit:
            break
        pathway = pair['pathway']
        name = min(names, key=lambda n: counts[(n, pathway)])
        assigned[(name, pathway)].append(pair['key'])
        counts[(name, pathway)] += 1
        dealt += 1
    return assigned


def write_assignment(reviewer: str, pathway: str, keys: list[str],
                     generated_at: str, assignments_dir: Path) -> bool:
    """
    Replace a reviewer's queue with what they now have to read.

    Returns whether the file changed, so a run that deals nothing produces no
    diff. A reviewer with nothing to read gets no file rather than an empty one,
    unless they had a queue that is now finished.
    """
    path = assignment_path(reviewer, pathway, assignments_dir)
    existing = json.loads(path.read_text())['keys'] if path.exists() else None
    if existing == keys or (existing is None and not keys):
        return False

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        'reviewer': reviewer,
        'pathway': pathway,
        'assigned_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'candidates_generated_at': generated_at,
        'keys': keys,
    }, indent=2, ensure_ascii=False) + '\n')
    return True


def queue_after(reviewer: str, pathway: str, dealt: list[str],
                answered: dict[str, str], assignments_dir: Path
                ) -> tuple[list[str], int]:
    """
    What a reviewer has to read next, and how much of their last round they did.

    Whatever they were holding and have since answered leaves the queue; what
    they never got to stays, so an unfinished round is carried rather than
    forgotten.
    """
    path = assignment_path(reviewer, pathway, assignments_dir)
    before = json.loads(path.read_text())['keys'] if path.exists() else []
    kept = [k for k in before if k not in answered]
    return sorted(set(kept) | set(dealt)), len(before) - len(kept)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reviewers',
                        help='Comma-separated subset of the registry; '
                             'all registered reviewers by default.')
    parser.add_argument('--pathway', choices=list(PATHWAYS),
                        help='Only this queue. Both by default, dealt separately.')
    parser.add_argument('--dandi-hosted', action=argparse.BooleanOptionalAction,
                        help='Whether DANDI held the part of the dataset that '
                             'was reused, rather than another archive.')
    parser.add_argument('--neuro', action=argparse.BooleanOptionalAction,
                        help='Whether neurophysiology was among what was reused, '
                             'as against morphology or transcriptomics alone.')
    parser.add_argument('--modality', action='append', choices=list(MODALITIES),
                        metavar='NAME',
                        help='Which part of the dataset was reused. Repeatable, '
                             'and a pair matches if it reused any named one. '
                             f'One of: {", ".join(MODALITIES)}.')
    parser.add_argument('--archive', action='append', metavar='NAME',
                        help='Where the authors say they got the data, as it '
                             'appears in the candidate list, e.g. "DANDI '
                             'Archive", CRCNS, Zenodo. Repeatable.')
    parser.add_argument('--reuse-type', action='append', choices=list(REUSE_TYPES),
                        metavar='TYPE',
                        help='What the authors did with the data. Repeatable. '
                             f'One of: {", ".join(REUSE_TYPES)}.')
    parser.add_argument('--lab', choices=['same', 'different', 'any'], default='any',
                        help='Whether the group that reused the data is the one '
                             'that produced it. Default any.')
    parser.add_argument('--min-confidence', type=int, metavar='N',
                        help='Drop pairs the classifier scored below N out of 10.')
    parser.add_argument('--exclude-unverifiable-quotes', action='store_true',
                        help='Drop pairs where no quoted passage could be found '
                             'in the paper, so nothing supports the call.')
    parser.add_argument('--limit', type=int, metavar='N',
                        help='Deal at most N new pairs, to size a round to what '
                             'somebody will finish. Work already owed is kept '
                             'either way.')
    args = parser.parse_args()

    candidates = json.loads(CANDIDATES_FILE.read_text())
    registry = load_reviewers(REVIEWERS_FILE)
    reviewers = select_reviewers(registry, args.reviewers)
    pairs = [p for p in candidates['pairs'] if matches(p, args)]
    print(f'{len(pairs)} of {len(candidates["pairs"])} candidate pairs match')

    answered = answered_by(registry, REVIEWS_DIR)
    dealt = deal(pairs, reviewers, assigned_to(ASSIGNMENTS_DIR), answered, args.limit)

    for (reviewer, pathway), keys in sorted(dealt.items()):
        queue, done = queue_after(reviewer, pathway, keys, answered, ASSIGNMENTS_DIR)
        write_assignment(reviewer, pathway, queue,
                         candidates['generated_at'], ASSIGNMENTS_DIR)
        if queue or keys or done:
            print(f'  {reviewer:<20} {pathway:<9} '
                  f'+{len(keys)} new, {done} finished, {len(queue)} to read')
    print(f'Assignments in {ASSIGNMENTS_DIR}')


if __name__ == '__main__':
    main()
