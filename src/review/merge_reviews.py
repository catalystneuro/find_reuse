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

A pair carries `source`, saying whether a classifier proposed it or a reviewer
found it by reading the paper. Both are ground truth and both are confirmed the
same way, by how many people called the pair reuse; only the classifier's own
are counted when tallying how often it was right.

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


def collect_added(base: Path) -> dict[tuple[str, str], str]:
    """
    Every pair a reviewer put on the list themselves, and what DANDI calls it.

    These are not in the candidate list and never will be: no classifier
    proposed them, a person reading the paper did. The dataset's name comes
    along because nothing else in the tree holds it for a dataset the pipeline
    never reached.
    """
    found: dict[tuple[str, str], str] = {}
    for path in reviews_paths(base):
        data = json.loads(path.read_text())
        for doi, datasets in (data.get('added') or {}).items():
            for dandiset, record in datasets.items():
                found[(doi, dandiset)] = record.get('dandiset_name', '')
    return found


def settled_call(calls: dict[str, dict]) -> str | None:
    """
    What the reviewers agreed a pair is, or nothing where they did not.

    A reviewer who wrote a note and made no call has not judged the pair, so
    they settle nothing. The note is kept; it is often what says why the call
    was left unmade.
    """
    distinct = {review['call'] for review in calls.values() if review.get('call')}
    return distinct.pop() if len(distinct) == 1 else None


def blank_fields(sample: dict) -> dict:
    """
    A candidate record's fields, emptied.

    An added pair has no classifier to have answered them, and carrying them
    empty keeps one shape for every record in the file, so reading a field does
    not depend on where the pair came from. A field holding a list comes back an
    empty list, since that is what reading it expects to find.
    """
    return {field: [] if isinstance(value, list) else None
            for field, value in sample.items()}


def reviewer_record(doi: str, dandiset: str, dandiset_name: str,
                    blank: dict, papers: dict[str, dict]) -> dict:
    """
    The record an added pair carries, shaped like the record a candidate has.

    The paper is one the pipeline did reach, so its title and the DOI its text
    was fetched under come from a pair already built on it.
    """
    paper = papers.get(doi, {})
    return {**blank,
            'doi': doi, 'dandiset': dandiset, 'dandiset_name': dandiset_name,
            'pathway': 'added',
            'title': paper.get('title', ''),
            'fetched_doi': paper.get('fetched_doi', doi)}


def merge(candidates: list[dict],
          reviews: dict[tuple[str, str], dict[str, dict]],
          added: dict[tuple[str, str], str] | None = None
          ) -> tuple[list[dict], list[tuple[str, str]]]:
    """
    Every reviewed pair, carrying the record it was judged on.

    `source` says where the pair came from. A classifier proposed most of them;
    a reviewer reading the paper found the rest, and those have no classifier
    record to carry, so their fields are empty. Keeping the two apart is what
    lets the precision of the classifier be counted over the pairs it actually
    claimed.

    A pair somebody reviewed that is in neither list comes back separately
    rather than disappearing: it means the pipeline was re-run and the
    classifier changed its mind about a pair a person had already read. The
    reviews outlive any one run of the classifier, which is the point of them,
    but there is no record left to carry.
    """
    added = added or {}
    records = {(pair['doi'], pair['dandiset']): pair for pair in candidates}
    papers: dict[str, dict] = {}
    for pair in candidates:
        papers.setdefault(pair['doi'], pair)
    blank = blank_fields(candidates[0]) if candidates else {}

    merged, orphaned = [], []
    for pair in sorted(reviews):
        if pair in records:
            record = {**records[pair], 'source': 'classifier'}
        elif pair in added:
            record = {**reviewer_record(*pair, added[pair], blank, papers),
                      'source': 'reviewer'}
        else:
            orphaned.append(pair)
            continue
        given = reviews[pair]
        merged.append({
            **record,
            'call': settled_call(given),
            'calls': {username: review['call'] for username, review in given.items()
                      if review.get('call')},
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

    Only the pairs a classifier proposed are counted. A pair a reviewer found by
    reading the paper was never a claim the classifier made, so crediting it
    here would measure the classifier against work it did not do.

    Pairs the reviewers disagreed about are counted as their own outcome, since
    they have no call and are not evidence either way until somebody settles
    them.
    """
    counts: dict[str, int] = {}
    for pair in pairs:
        if pair.get('source') != 'classifier':
            continue
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
    added = collect_added(REUSE_CONFIRMATION_DIR)
    pairs, orphaned = merge(candidates['pairs'], reviews, added)
    confirmed_pairs = confirmed(pairs, args.min_reviewers)

    counts = tally(pairs)
    found = sum(1 for pair in pairs if pair['source'] == 'reviewer')
    header = {
        'candidates_generated_at': candidates['generated_at'],
        'inputs': candidates['inputs'],
        'reviewers': [reviewer['username'] for reviewer in registry
                      if any(reviewer['username'] in pair['calls'] for pair in pairs)],
    }
    changed = {
        ALL_REVIEWS_FILE: write_stamped(ALL_REVIEWS_FILE, {
            **header, 'reviewed': len(pairs) - found, 'added': found,
            'calls': counts, 'pairs': pairs}),
        CONFIRMED_FILE: write_stamped(CONFIRMED_FILE, {
            **header, 'min_reviewers': args.min_reviewers,
            'confirmed': len(confirmed_pairs), 'pairs': confirmed_pairs}),
    }

    print(f'{len(pairs) - found} of {len(candidates["pairs"])} candidate pairs '
          f'reviewed: '
          f'{", ".join(f"{count} {outcome}" for outcome, count in counts.items())}')
    if found:
        print(f'{found} pair{"" if found == 1 else "s"} reviewers added while '
              f'reading the papers, counted as reuse and not as the classifier')
    for pair in pairs:
        if pair['call'] is None:
            said = ', '.join(f'{username} {call}'
                             for username, call in pair['calls'].items())
            print(f'  disputed: {pair["doi"]} {pair["dandiset"]} -- {said}')
    for doi, dandiset in orphaned:
        print(f'  reviewed, and the classifier no longer proposes it: '
              f'{doi} {dandiset}')
    print(f'{len(confirmed_pairs)} confirmed reuse, at '
          f'{args.min_reviewers} reviewer{"" if args.min_reviewers == 1 else "s"}')
    for path, wrote in changed.items():
        print(f'{"Wrote" if wrote else "Unchanged:"} {path}')


if __name__ == '__main__':
    main()
