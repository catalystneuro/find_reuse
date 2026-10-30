#!/usr/bin/env python3
"""
Say where the classifier's REUSE calls went wrong, from what the reviewers wrote.

Every candidate is a pair the classifier called REUSE, so a reviewer who called
one anything else found an error, and the note they left says what kind. This
reads the reviews against the candidate list and tabulates the reviewers' calls
by the properties a pair was built with, then sorts each rejection into a cause.

A cause is assigned by rule, from the pair's own structure where that settles it
and from the wording of the note where it does not. The rules are below, in the
order they are tried, and a rejection none of them reaches is listed as it
stands. The causes are meant to say which part of the pipeline to change, so
they follow the pipeline's steps and not the reviewers' vocabulary.

Usage:
    python -m src.review.analyze_reviews
    python -m src.review.analyze_reviews --reviews path/to/rly-reviews.json \\
        --reviews path/to/pauladkisson-reviews.json
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from src.review.build_candidates import CANDIDATES_FILE
from src.review.reviewers import reviews_paths

REJECTED = ('neither', 'mention', 'primary')
CALLS = ('reuse', 'ambiguous_reuse', *REJECTED, 'unsure')

# A DOI minted for something published alongside a paper: an eLife assessment,
# review or author response, or an OSF registration.
NOT_A_PAPER = re.compile(r'\.sa\d+$|^10\.17605/osf\.io/', re.I)

DANDISET_IN_NOTE = re.compile(r'(?:DANDI:|dandiset[ /])\s*(\d{6})', re.I)

# Tried in order against the note, after the rules that read the pair itself.
NOTE_CAUSES = [
    ('not a paper', re.compile(
        r'not a paper|review doi|peer review|elife assessment|author response'
        r'|not the paper itself|not full text|abstract/intro only', re.I)),
    ('preprint of a paper also listed', re.compile(
        r'preprint[- ](?:duplicate|of\b|for\b)|duplicate (?:paper|of)'
        r'|is a duplicate', re.I)),
    ('citing paper is the source', re.compile(
        r'(?:is|it) (?:a|the) primary (?:paper|source)|cannot cite itself'
        r'|generated this data', re.I)),
    ('data taken from elsewhere', re.compile(
        r'zenodo|dryad|figshare|g-node|\bgin\b|crcns|physionet|\bONE\b'
        r'|made available to us by|allensdk|brain-map', re.I)),
    ('cited without reuse', re.compile(
        r'background|does not (?:appear to )?reuse|not reuse|no evidence'
        r'|replot|just replotted|only citation|doesn.t look like'
        r'|different (?:paper|recordings)|unrelated|no connection'
        r'|did not reuse|most recent version does not', re.I)),
]


def load_reviews(paths: list[Path]) -> tuple[dict, dict]:
    """
    Every pair's call and note by reviewer, and the pairs reviewers added.

    A pair a reviewer added is one the pipeline missed, so it is kept apart from
    the calls made on the pipeline's own candidates.
    """
    calls: dict = defaultdict(dict)
    added: dict = {}
    for path in paths:
        data = json.loads(Path(path).read_text())
        mine = {(doi, dandiset) for doi, datasets in data.get('added', {}).items()
                for dandiset in datasets}
        for doi, datasets in data['reviews'].items():
            for dandiset, review in datasets.items():
                if (doi, dandiset) in mine:
                    added[(doi, dandiset)] = review.get('note', '')
                else:
                    calls[(doi, dandiset)][data['reviewer']] = review
    return calls, added


def settled(reviews: dict) -> tuple[str | None, str]:
    """The call the reviewers agree on, or None, and their notes together."""
    given = {review['call'] for review in reviews.values()}
    notes = ' '.join(review.get('note', '') for review in reviews.values())
    return (given.pop() if len(given) == 1 else None), notes


def siblings(row: dict) -> set[str]:
    """The other dandisets that name the paper this pair was built from."""
    shared = row.get('shared_paper') or {}
    return {d['dandiset'] for d in shared.get('dandisets', [])} - {row['dandiset']}


def cause(row: dict, note: str, accepted: set[str]) -> str:
    """
    Why a rejected pair was a wrong call, as far as a rule can tell.

    `accepted` is the dandisets the reviewers did credit this paper with, by
    calling the pair reuse or by adding it. A rejected pair beside one of those
    was the wrong dataset for a real reuse, which is a different failure from a
    paper that reused nothing.
    """
    if NOT_A_PAPER.search(row['doi']):
        return 'not a paper'
    for name, pattern in NOTE_CAUSES[:3]:
        if pattern.search(note):
            return name
    named = set(DANDISET_IN_NOTE.findall(note)) - {row['dandiset']}
    if siblings(row) & (accepted | named):
        return 'reuse of a dandiset sharing the paper'
    if siblings(row) and re.search(r'consistent only with|only references'
                                   r'|leaderboard|unit count|specifically cites',
                                   note, re.I):
        return 'reuse of a dandiset sharing the paper'
    if named:
        return 'reuse of another dandiset'
    for name, pattern in NOTE_CAUSES[3:]:
        if pattern.search(note):
            return name
    # A pair rejected without saying why, on a paper credited with other data.
    if accepted - {row['dandiset']}:
        return 'reuse of another dandiset'
    return 'no rule'


def table(title: str, rows: dict[str, Counter]) -> str:
    """One breakdown as a Markdown table, a row per value and a column per call."""
    lines = [f'### {title}', '',
             '| | pairs | ' + ' | '.join(c.replace('_', ' ') for c in CALLS)
             + ' | confirmed |',
             '|---|---:|' + '---:|' * (len(CALLS) + 1)]
    for value, counts in rows.items():
        judged = sum(counts[c] for c in CALLS)
        decided = judged - counts['unsure']
        share = f'{100 * counts["reuse"] / decided:.0f}%' if decided else ''
        lines.append(f'| {value} | {judged} | '
                     + ' | '.join(str(counts[c]) for c in CALLS) + f' | {share} |')
    return '\n'.join(lines) + '\n'


def analyze(candidates: list[dict], calls: dict, added: dict) -> str:
    """The whole report, as Markdown."""
    by_pair = {(row['doi'], row['dandiset']): row for row in candidates}
    judged = []
    disagreed = 0
    for pair, reviews in calls.items():
        call, note = settled(reviews)
        if call is None:
            disagreed += 1
        elif pair in by_pair:
            judged.append((by_pair[pair], call, note))

    accepted: dict = defaultdict(set)
    for row, call, _ in judged:
        if call in ('reuse', 'ambiguous_reuse'):
            accepted[row['doi']].add(row['dandiset'])
    for doi, dandiset in added:
        accepted[doi].add(dandiset)

    def breakdown(key) -> dict[str, Counter]:
        rows: dict = defaultdict(Counter)
        for row, call, _ in judged:
            rows[key(row)][call] += 1
        return dict(sorted(rows.items()))

    out = [f'{len(judged)} candidate pairs judged, {disagreed} left out because '
           f'their reviewers disagree, {len(added)} pairs added by reviewers.\n',
           table('All candidates', breakdown(lambda row: 'all')),
           table('By pathway', breakdown(lambda row: row['pathway'])),
           table('By how many other dandisets name the same paper', breakdown(
               lambda row: ('none', '1 other', '2 or more')[
                   min(len(siblings(row)), 2)])),
           table('By why the pair counts as DANDI data', breakdown(
               lambda row: row.get('dandi_reason') or 'nothing says so')),
           table('By how the dandiset names its paper', breakdown(
               lambda row: row.get('cited_source') or 'direct pair'))]

    causes: Counter = Counter()
    papers: dict = defaultdict(set)
    unruled = []
    for row, call, note in judged:
        if call not in REJECTED:
            continue
        why = cause(row, note, accepted[row['doi']])
        causes[why] += 1
        papers[why].add(row['doi'])
        if why == 'no rule':
            unruled.append((row, call, note))
    out += ['### Why the rejected pairs were wrong', '',
            '| cause | pairs | papers |', '|---|---:|---:|']
    out += [f'| {why} | {n} | {len(papers[why])} |' for why, n in causes.most_common()]
    out.append('')

    worst = Counter(row['dandiset'] for row, call, _ in judged if call in REJECTED)
    names = {row['dandiset']: row['dandiset_name'] for row in candidates}
    totals = Counter(row['dandiset'] for row, _, _ in judged)
    out += ['### Dandisets with the most rejected pairs', '',
            '| dandiset | rejected | judged | name |', '|---|---:|---:|---|']
    out += [f'| {dandiset} | {n} | {totals[dandiset]} | {names[dandiset][:60]} |'
            for dandiset, n in worst.most_common(12)]
    out.append('')

    listed = {row['doi'] for row in candidates}
    reached = Counter(row['dandiset'] for row in candidates)
    out += ['### Pairs the reviewers added', '',
            f'{len(added)} pairs on {len({doi for doi, _ in added})} papers. '
            f'{sum(doi in listed for doi, _ in added)} are on a paper the '
            f'pipeline proposed for some other dandiset, and '
            f'{sum(not reached[dandiset] for _, dandiset in added)} name a '
            f'dandiset that has no candidate pair at all.\n',
            '### Across the whole candidate list', '',
            f'{sum(bool(NOT_A_PAPER.search(row["doi"])) for row in candidates)} of '
            f'{len(candidates)} candidate pairs are on a DOI that is not a paper.\n']

    if unruled:
        out += ['### Rejections no rule reached', '']
        out += [f'- {row["doi"]} / {row["dandiset"]} ({call}): '
                f'{" ".join(note.split())[:200] or "no note"}'
                for row, call, note in unruled]
        out.append('')
    return '\n'.join(out)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reviews', action='append',
                        help='A reviews file; repeat for each reviewer. '
                             'Defaults to every one under reuse_confirmation/.')
    parser.add_argument('--candidates', default=str(CANDIDATES_FILE))
    args = parser.parse_args()

    candidates = json.loads(Path(args.candidates).read_text())['pairs']
    calls, added = load_reviews(args.reviews or reviews_paths())
    print(analyze(candidates, calls, added))


if __name__ == '__main__':
    main()
