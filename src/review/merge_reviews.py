#!/usr/bin/env python3
"""
Put everybody's reviews back together, and say what came out confirmed.

Reviewers work separately and judge independently, so their reviews are written
one file per person. This is where those meet, keyed on the pair, so that a pair
carries what everyone who read it said about it.

Two files come out, because two questions are being asked of the same reviews.
all_reviews.json is every pair anybody judged and what they called it -- the
rejections included, since how often the classifier was wrong is a result too.
confirmed_reuse.json is the narrower thing the project is for: the pairs that
came out reuse.

Usage:
    python -m src.review.merge_reviews
    python -m src.review.merge_reviews --min-reviewers 2
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.review.assign_reviews import flatten
from src.review.build_candidates import CANDIDATES_FILE, write_stamped
from src.review.reviewers import (REUSE_CONFIRMATION_DIR, REVIEWERS_FILE,
                                  load_reviewers, reviews_paths)

ALL_REVIEWS_FILE = REUSE_CONFIRMATION_DIR / 'all_reviews.json'
CONFIRMED_FILE = REUSE_CONFIRMATION_DIR / 'confirmed_reuse.json'


def collect_reviews(registry: list[dict], base: Path
                    ) -> dict[tuple[str, str], dict[str, dict]]:
    """
    Every review anybody has given, keyed by pair and then by who gave it.

    Reviewers come out in registry order, so a rerun writes the same bytes
    whatever order the files were found in.

    Reviews on disk under a username that is not registered are refused rather
    than merged: an unregistered username is a typo, and merging one folds
    somebody's reading of a paper into the record under a name nothing else
    knows.
    """
    given = {}
    for path in reviews_paths(base):
        data = json.loads(path.read_text())
        given[data['reviewer']] = data['reviews']

    registered = [reviewer['username'] for reviewer in registry]
    unregistered = [username for username in given if username not in registered]
    if unregistered:
        raise SystemExit(
            f'Reviews on disk from unregistered reviewer'
            f'{"s" if len(unregistered) > 1 else ""}: {", ".join(unregistered)}. '
            f'Registered: {", ".join(registered)}. Add them to {REVIEWERS_FILE}, '
            f'or their reviews are merged under a name nothing else knows.')

    reviews: dict[tuple[str, str], dict[str, dict]] = {}
    for username in registered:
        nested = given.get(username, {})
        for doi, dandiset in flatten(nested):
            reviews.setdefault((doi, dandiset), {})[username] = nested[doi][dandiset]
    return reviews


def settled_call(calls: dict[str, dict]) -> str | None:
    """What the reviewers agreed a pair is, or nothing where they did not."""
    distinct = {review['call'] for review in calls.values()}
    return distinct.pop() if len(distinct) == 1 else None


def merge(candidates: list[dict], reviews: dict[tuple[str, str], dict[str, dict]]
          ) -> tuple[list[dict], list[tuple[str, str]]]:
    """
    Every reviewed pair, carrying the record it was judged on.

    A pair somebody reviewed that the candidate list no longer holds comes back
    separately rather than disappearing: it means the pipeline was re-run and
    the classifier changed its mind about a pair a person had already read. The
    reviews outlive any one run of the classifier, which is the point of them,
    but there is no record left to carry.
    """
    records = {(pair['doi'], pair['dandiset']): pair for pair in candidates}
    merged, orphaned = [], []
    for pair in sorted(reviews):
        if pair not in records:
            orphaned.append(pair)
            continue
        given = reviews[pair]
        merged.append({
            **records[pair],
            'call': settled_call(given),
            'calls': {username: review['call'] for username, review in given.items()},
            'notes': {username: review['note'] for username, review in given.items()
                      if review.get('note')},
        })
    return merged, orphaned


def confirmed(pairs: list[dict], min_reviewers: int) -> list[dict]:
    """
    The pairs enough people read and called reuse.

    A dissenting call does not veto. What confirms a pair is how many people
    looked at it and said yes; that somebody else said otherwise is a
    disagreement to settle rather than a reason to discard the reading that was
    done, and it stays visible in all_reviews.json either way.
    """
    return [pair for pair in pairs
            if sum(1 for call in pair['calls'].values() if call == 'reuse')
            >= min_reviewers]


def tally(pairs: list[dict]) -> dict[str, int]:
    """
    How many pairs came out each way, which is the classifier's precision.

    Pairs the reviewers disagreed about are counted as their own outcome, since
    they have no call and are not evidence either way until somebody settles
    them.
    """
    counts: dict[str, int] = {}
    for pair in pairs:
        outcome = pair['call'] or 'disputed'
        counts[outcome] = counts.get(outcome, 0) + 1
    return dict(sorted(counts.items()))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--min-reviewers', type=int, default=1, metavar='N',
                        help='How many people have to call a pair reuse for it '
                             'to be confirmed. Default 1, which is what a round '
                             'dealt out disjointly can produce; raise it once '
                             'pairs have been read more than once.')
    args = parser.parse_args()

    candidates = json.loads(CANDIDATES_FILE.read_text())
    registry = load_reviewers(REVIEWERS_FILE)
    reviews = collect_reviews(registry, REUSE_CONFIRMATION_DIR)
    pairs, orphaned = merge(candidates['pairs'], reviews)
    confirmed_pairs = confirmed(pairs, args.min_reviewers)

    counts = tally(pairs)
    header = {
        'candidates_generated_at': candidates['generated_at'],
        'inputs': candidates['inputs'],
        'reviewers': [reviewer['username'] for reviewer in registry
                      if any(reviewer['username'] in pair['calls'] for pair in pairs)],
    }
    changed = {
        ALL_REVIEWS_FILE: write_stamped(ALL_REVIEWS_FILE, {
            **header, 'reviewed': len(pairs), 'calls': counts, 'pairs': pairs}),
        CONFIRMED_FILE: write_stamped(CONFIRMED_FILE, {
            **header, 'min_reviewers': args.min_reviewers,
            'confirmed': len(confirmed_pairs), 'pairs': confirmed_pairs}),
    }

    print(f'{len(pairs)} of {len(candidates["pairs"])} candidate pairs reviewed: '
          f'{", ".join(f"{count} {outcome}" for outcome, count in counts.items())}')
    for pair in pairs:
        if pair['call'] is None:
            said = ', '.join(f'{username} {call}'
                             for username, call in pair['calls'].items())
            print(f'  disputed: {pair["doi"]} {pair["dandiset"]} -- {said}')
    for doi, dandiset in orphaned:
        print(f'  reviewed but no longer a candidate: {doi} {dandiset}')
    print(f'{len(confirmed_pairs)} confirmed reuse, at '
          f'{args.min_reviewers} reviewer{"" if args.min_reviewers == 1 else "s"}')
    for path, wrote in changed.items():
        print(f'{"Wrote" if wrote else "Unchanged:"} {path}')


if __name__ == '__main__':
    main()
