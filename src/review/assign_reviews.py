#!/usr/bin/env python3
"""
Deal candidate pairs out to reviewers.

Narrows the candidate list to the round you want reviewed, then splits it among
the registered reviewers so each pair belongs to exactly one person.

An assignment is a queue, not a history: it holds what a reviewer still has to
read. Answering a pair takes it out, and the answer file is what accumulates. So
a round is what you were dealt plus whatever you had left over, and it stays
short enough to work through. Pairs are named paper first, then dataset, the way
the answers are.

Dealing is incremental: a pair somebody still owes stays theirs, and a pair
somebody has answered is not dealt again, so a rerun of the pipeline hands out
only what it added. Only queues that actually changed are rewritten.

Usage:
    python -m src.review.assign_reviews --neuro --dandi-source evidenced
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from src.review.build_candidates import CANDIDATES_FILE
from src.shared.classify_fulltext_reuse import MODALITIES, REUSE_TYPES
from src.review.reviewers import (REUSE_CONFIRMATION_DIR, REVIEWERS_FILE,
                                  assignment_path, assignment_paths,
                                  load_reviewers, reviews_path,
                                  select_reviewers)

PATHWAYS = ('indirect', 'direct')


def matches(pair: dict, args: argparse.Namespace) -> bool:
    """Whether a pair belongs to the round these flags describe."""
    if args.pathway and pair['pathway'] != args.pathway:
        return False
    if args.neuro is not None and pair['reused_neurophysiology'] is not args.neuro:
        return False
    if args.modality and not set(args.modality) & set(pair['reused_modalities']):
        return False
    if args.reuse_type and not set(args.reuse_type) & set(pair['reuse_types']):
        return False
    # 'mixed' means the pathways disagreed, which satisfies either side rather
    # than neither: the pair really does have a same-lab record behind it.
    if args.lab == 'same' and pair['same_lab'] not in (True, 'mixed'):
        return False
    if args.lab == 'different' and pair['same_lab'] not in (False, 'mixed'):
        return False
    # Two strengths of the same question, from the funnel: a pair not ruled out
    # as DANDI data, and one with something positively saying so. Naming another
    # archive rules a pair out; naming none does not, since a paper that never
    # says where the data came from may still have got it here. Matched loosely,
    # because the classifier keeps an archive it does not recognise verbatim and
    # some records name two in one string.
    if args.dandi_source == 'possible' and pair['archives'] and not any(
            'dandi archive' in recorded.lower() for recorded in pair['archives']):
        return False
    if args.dandi_source == 'evidenced' and not pair['dandi_reason']:
        return False
    return True


def flatten(nested: dict) -> list[tuple[str, str]]:
    """The (paper, dataset) pairs a paper-then-dataset mapping names."""
    return [(doi, dandiset)
            for doi, dandisets in nested.items() for dandiset in dandisets]


def nest(pairs: list[tuple[str, str]]) -> dict:
    """Pairs as a paper-then-dataset mapping, sorted so a rerun diffs cleanly."""
    nested: dict = {}
    for doi, dandiset in sorted(pairs):
        nested.setdefault(doi, []).append(dandiset)
    return {doi: sorted(dandisets) for doi, dandisets in nested.items()}


def assigned_to(base: Path) -> dict[tuple[str, str], str]:
    """
    Every pair already sitting in an assignment, and whose it is.

    Every assignment on disk counts, not just those of the reviewers in this
    round: a pair someone else holds is spoken for either way.
    """
    holders = {}
    for path in assignment_paths(base):
        assignment = json.loads(path.read_text())
        for pair in flatten(assignment['pairs']):
            holders[pair] = assignment['reviewer']
    return holders


def answered_by(registry: list[dict], base: Path) -> dict[tuple[str, str], str]:
    """
    Every pair somebody has already judged, and who judged it.

    A pair with an answer belongs to whoever gave it. Dealing it to a second
    person would buy nothing and cost them the reading.
    """
    holders = {}
    for reviewer in registry:
        path = reviews_path(reviewer['username'], base)
        if path.exists():
            for pair in flatten(json.loads(path.read_text())['reviews']):
                holders[pair] = reviewer['username']
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
    names = [r['username'] for r in reviewers]
    assigned = {(name, pathway): [] for name in names for pathway in PATHWAYS}
    counts = {(name, pathway): 0 for name in names for pathway in PATHWAYS}
    by_pair = {(p['doi'], p['dandiset']): p for p in pairs}
    for held in (placed, answered):
        for key, holder in held.items():
            pair = by_pair.get(key)
            if pair and (holder, pair['pathway']) in counts:
                counts[(holder, pair['pathway'])] += 1

    unplaced = [p for p in pairs
                if (p['doi'], p['dandiset']) not in placed
                and (p['doi'], p['dandiset']) not in answered]
    dealt = 0
    for pair in unplaced:
        pathway = pair['pathway']
        if limit is not None and dealt >= limit:
            break
        name = min(names, key=lambda n: counts[(n, pathway)])
        assigned[(name, pathway)].append((pair['doi'], pair['dandiset']))
        counts[(name, pathway)] += 1
        dealt += 1
    return assigned


def write_assignment(reviewer: str, pathway: str, pairs: list[tuple[str, str]],
                     generated_at: str, base: Path) -> bool:
    """
    Replace a reviewer's queue with what they now have to read.

    Returns whether the file changed, so a run that deals nothing produces no
    diff. A reviewer with nothing to read gets no file rather than an empty one,
    unless they had a queue that is now finished.
    """
    path = assignment_path(reviewer, pathway, base)
    existing = json.loads(path.read_text())['pairs'] if path.exists() else None
    nested = nest(pairs)
    if existing == nested or (existing is None and not pairs):
        return False

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        'reviewer': reviewer,
        'pathway': pathway,
        'assigned_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'candidates_generated_at': generated_at,
        'pairs': nested,
    }, indent=2, ensure_ascii=False) + '\n')
    return True


def queue_after(reviewer: str, pathway: str, dealt: list[tuple[str, str]],
                answered: dict[tuple[str, str], str], base: Path
                ) -> tuple[list[tuple[str, str]], int]:
    """
    What a reviewer has to read next, and how much of their last round they did.

    Whatever they were holding and have since answered leaves the queue; what
    they never got to stays, so an unfinished round is carried rather than
    forgotten.
    """
    path = assignment_path(reviewer, pathway, base)
    before = flatten(json.loads(path.read_text())['pairs']) if path.exists() else []
    kept = [p for p in before if p not in answered]
    return sorted(set(kept) | set(dealt)), len(before) - len(kept)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reviewers',
                        help='Comma-separated usernames, a subset of the '
                             'registry; all registered reviewers by default.')
    parser.add_argument('--pathway', choices=list(PATHWAYS),
                        help='Only this queue. Both by default, dealt separately.')
    parser.add_argument('--dandi-source', choices=['possible', 'evidenced'],
                        help='Where the data came from. "possible" keeps pairs '
                             'not ruled out: the paper named DANDI, or named no '
                             'archive at all. "evidenced" keeps only those with '
                             'something saying so -- a dandiset identifier in '
                             'the text, DANDI named as the source, or DANDI in '
                             'a passage that is really in the paper.')
    parser.add_argument('--neuro', action=argparse.BooleanOptionalAction,
                        help='Whether neurophysiology was among what was reused, '
                             'as against morphology or transcriptomics alone.')
    parser.add_argument('--modality', action='append', choices=list(MODALITIES),
                        metavar='NAME',
                        help='Which part of the dataset was reused. Repeatable, '
                             'and a pair matches if it reused any named one. '
                             f'One of: {", ".join(MODALITIES)}.')
    parser.add_argument('--reuse-type', action='append', choices=list(REUSE_TYPES),
                        metavar='TYPE',
                        help='What the authors did with the data. Repeatable. '
                             f'One of: {", ".join(REUSE_TYPES)}.')
    parser.add_argument('--lab', choices=['same', 'different', 'any'], default='any',
                        help='Whether the group that reused the data is the one '
                             'that produced it. Default any.')
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

    answered = answered_by(registry, REUSE_CONFIRMATION_DIR)
    dealt = deal(pairs, reviewers, assigned_to(REUSE_CONFIRMATION_DIR),
                 answered, args.limit)

    for (reviewer, pathway), new in sorted(dealt.items()):
        queue, done = queue_after(reviewer, pathway, new, answered,
                                  REUSE_CONFIRMATION_DIR)
        write_assignment(reviewer, pathway, queue,
                         candidates['generated_at'], REUSE_CONFIRMATION_DIR)
        if queue or new or done:
            print(f'  {reviewer:<20} {pathway:<9} '
                  f'+{len(new)} new, {done} finished, {len(queue)} to read')
    print(f'Assignments in {REUSE_CONFIRMATION_DIR}')


if __name__ == '__main__':
    main()
