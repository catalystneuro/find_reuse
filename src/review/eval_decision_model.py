#!/usr/bin/env python3
"""
Score a decision model as a filter in front of the reuse classifier.

A decision model (Baseten's Elephant 1, for one) answers typed questions about
a state with probabilities, in one forward pass and often for free. It cannot do
what the classifier does: its window is 16K tokens, too small for most papers,
and it quotes nothing, so its calls cannot be checked against the paper. What it
might do is decide which pairs need the classifier at all. Nine pairs in ten come
back MENTION, and a filter that set those aside cheaply would cut the bill by
most of that, as long as it rarely sets aside a pair that was really reuse.

So the model is given the parts of the paper that bear on the question rather
than the paper: every mention of DANDI or the dandiset, every place the dataset's
paper is cited (by author and year, or by its number in the reference list), and
the data and code availability statements. It is asked whether the authors
REUSED the data, MENTIONED the work, or NEITHER, with the classifier's own
definitions, and its probability of REUSE is kept.

Models with a window of 262K tokens or more (Drex Max, Decider, Gemini Decision)
can read the whole paper instead: --input full sends the text, cut head and tail
only when it would overflow the window. Each request asks two questions, the
three-way choice above and a yes/no "did the authors analyze this data", so
either probability can be tried as the filter.

Results land in .model_eval_cache/<model>__<input> and are scored on the frozen
eval set like any other model (see eval_models). The threshold table is the
part that matters for a filter: at each cutoff on P(REUSE), how much confirmed
reuse would still reach the classifier, and what share of the pairs the
production classifier called MENTION or NEITHER would be sent to it anyway.

Usage:
    python -m src.review.eval_decision_model --model nace-ai/drex-max --input full
    python -m src.review.eval_decision_model --model baseten/mammoth-1 --input excerpts --tier full
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import re
import sys
import time
from pathlib import Path

import requests

from src.review.bibliography import cited_numbers, text_entries
from src.review.eval_models import CACHE_ROOT, EVAL_SET, finish, model_slug, tier_pairs
from src.review.paper_metadata import citation, resolve
from src.shared.run_fulltext_classification import cache_path
from src.shared.screen_reuse import ask, context_length, fit, paper_state

REPO = Path(__file__).resolve().parents[2]

# Characters of paper text sent per pair. The window is 16,384 tokens, and this
# keeps a tokenizer that counts three characters a token well inside it.
MAX_CHARS = 36_000
CONTEXT = 600          # characters kept either side of each place the work comes up
AVAILABILITY_CHARS = 1_500

DANDI = re.compile(r'DANDI|dandiarchive|dandiset', re.I)
AVAILABILITY = re.compile(r'(data|code|software)\s+(and\s+code\s+)?availability', re.I)
BRACKETS = re.compile(r'\[[\d,\s–-]+\]')

def alnum(text: str) -> str:
    """Lowercased letters and digits only, so a title matches however it was punctuated."""
    return re.sub(r'[^a-z0-9]+', '', text.lower())


def windows(text: str, spans: list[tuple[int, int]], pad: int) -> list[tuple[int, int]]:
    """The spans widened by pad on each side, with overlapping ones merged."""
    merged: list[list[int]] = []
    for start, end in sorted((max(0, s - pad), min(len(text), e + pad)) for s, e in spans):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(s, e) for s, e in merged]


def excerpts(text: str, primary: dict | None, dandiset_id: str) -> tuple[list[str], list[str]]:
    """
    The passages of a paper that bear on whether it reused a dandiset's data.

    Returns the passages and the kinds of anchor that found them. The order is
    the paper's, so a passage reads in context, and the total stops at
    MAX_CHARS, keeping the availability statements and DANDI mentions first
    because they decide the most cases.
    """
    found: dict[str, list[tuple[int, int]]] = {'dandi': [], 'availability': [], 'citation': []}

    for match in DANDI.finditer(text):
        found['dandi'].append(match.span())
    for match in re.finditer(rf'\b{re.escape(dandiset_id)}\b', text):
        found['dandi'].append(match.span())
    for match in AVAILABILITY.finditer(text):
        found['availability'].append((match.start(), match.start() + AVAILABILITY_CHARS))

    if primary:
        authors, year = primary.get('authors') or [], primary.get('year')
        if authors and year:
            surname = re.escape(authors[0])
            # "Siegle et al., 2021", "Siegle et al. (2021)", "Siegle and de Vries 2021"
            for match in re.finditer(rf'{surname}[^;()\[\]\n]{{0,40}}?\(?{year}', text):
                found['citation'].append(match.span())
            # "Gao et al. 25": numbered styles cite by author and a superscript, no year.
            for match in re.finditer(rf'\b{surname}\s+(et al|and\b|&)', text):
                found['citation'].append(match.span())
        # The reference entry itself, matched on the title's first words however
        # the text punctuated or spaced them, shows the paper does cite the work.
        words = re.findall(r'[A-Za-z0-9]+', primary.get('title') or '')[:6]
        if len(words) >= 3:
            for match in re.finditer(r'\W+'.join(map(re.escape, words)), text, re.I):
                found['citation'].append(match.span())
        title = alnum(' '.join((primary.get('title') or '').split()[:8]))
        doi = (primary.get('doi') or '').lower()
        if title:
            numbers = [n for n, entry in text_entries(text).items()
                       if title in alnum(entry) or (doi and doi in entry.lower())]
            for match in BRACKETS.finditer(text):
                if set(cited_numbers(match.group())) & set(numbers):
                    found['citation'].append(match.span())

    budget, chosen = MAX_CHARS, []
    for kind in ('availability', 'dandi', 'citation'):
        pad = 0 if kind == 'availability' else CONTEXT
        for start, end in windows(text, found[kind], pad):
            if end - start <= budget and not any(s <= start and end <= e for s, e, _ in chosen):
                chosen.append((start, end, kind))
                budget -= end - start
    chosen.sort()
    return ([' '.join(text[s:e].split()) for s, e, _ in chosen],
            sorted({kind for _, _, kind in chosen}))


def classify(pair: dict, text: str | None, primary: dict | None, model: str,
             session: requests.Session, context_tokens: int | None = None) -> dict:
    row = {'citing_doi': pair['doi'], 'dandiset_id': pair['dandiset_id'], 'model': model,
           'classification': 'ERROR', 'usage': {'cost': 0}}
    if not text:
        return {**row, 'error': 'no cached text'}
    if context_tokens:
        passages, anchors = [fit(text, context_tokens)], ['full']
    else:
        passages, anchors = excerpts(text, primary, pair['dandiset_id'])
    state = paper_state(
        passages or ['(No passage of this paper mentions DANDI, the dandiset or the work '
                     'that published it.)'], bool(context_tokens),
        dandiset_id=pair['dandiset_id'], dandiset_name=pair['dandiset_name'],
        published_in=(f"{citation(primary)}, \"{primary['title']}\""
                      if primary else pair['cited_paper_doi']),
        title=pair['title'], doi=pair['doi'])
    try:
        answer = ask(session, model, state)
    except Exception as e:
        return {**row, 'error': f'{type(e).__name__}: {e}'}
    relation = answer['answers']['relation']
    return {**row, 'classification': relation['choice'], 'probabilities': relation['probabilities'],
            'confidence': relation.get('confidence'), 'anchors': anchors,
            'reused': (answer['answers'].get('reused') or {}).get('noul'),
            'excerpt_chars': sum(len(p) for p in passages),
            'usage': {**(answer.get('usage') or {}), 'cost': (answer.get('usage') or {}).get('cost', 0),
                      'prompt_tokens': (answer.get('usage') or {}).get('input_tokens')}}


def threshold_table(pairs: list[dict], results: dict) -> None:
    """Recall and the share of pairs sent on, at each cutoff on each score."""
    def get(pair):
        return results.get((pair['doi'].lower(), pair['dandiset_id'])) or {}

    scores = {
        'P(REUSE)': lambda r: (r.get('probabilities') or {}).get('REUSE', 1.0),
        'P(reused: yes)': lambda r: 1.0 if r.get('reused') is None else r['reused'],
        'larger of the two': lambda r: max((r.get('probabilities') or {}).get('REUSE', 1.0),
                                           1.0 if r.get('reused') is None else r['reused']),
    }
    for name, f in scores.items():
        by_kind = {k: [f(get(x)) for x in pairs if x['kind'] == k]
                   for k in ('reuse', 'ambiguous', 'not_reuse', 'negative')}
        print(f'\nAs a filter on {name} (a pair goes on to the classifier at or above the cutoff):')
        print(f"{'cutoff':>7} {'confirmed reuse kept':>21} {'ambiguous kept':>15} "
              f"{'rejected pairs sent':>20} {'MENTION/NEITHER pairs sent':>27}")
        for cut in (0.5, 0.3, 0.2, 0.1, 0.05, 0.02, 0.01, 0.005, 0.002, 0.001):
            share = lambda xs: (f'{sum(x >= cut for x in xs)}/{len(xs)} '
                                f'{sum(x >= cut for x in xs) / len(xs):.0%}') if xs else '-'
            print(f"{cut:>7} {share(by_kind['reuse']):>21} {share(by_kind['ambiguous']):>15} "
                  f"{share(by_kind['not_reuse']):>20} {share(by_kind['negative']):>27}")


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--model', default='nace-ai/drex-max')
    parser.add_argument('--input', default='full', choices=['full', 'excerpts'],
                        help='send the whole paper, or only the passages that bear on the pair')
    parser.add_argument('--tier', default='quick', choices=['quick', 'full'])
    parser.add_argument('--workers', type=int, default=4)
    parser.add_argument('--paper-cache', default=str(REPO / '.paper_cache'))
    args = parser.parse_args()

    from paper_text_fetcher import PaperFetcher

    eval_set = json.loads(EVAL_SET.read_text())
    pairs = tier_pairs(eval_set, args.tier)
    cache_dir = CACHE_ROOT / model_slug(args.model, args.input)
    cache_dir.mkdir(parents=True, exist_ok=True)
    metadata = resolve({p['cited_paper_doi'] for p in pairs}, REPO / '.paper_metadata_cache.json')
    fetcher = PaperFetcher(use_cache=True, cache_dir=args.paper_cache)
    context_tokens = context_length(args.model) if args.input == 'full' else None
    session = requests.Session()
    session.headers['Authorization'] = f"Bearer {os.environ['OPENROUTER_API_KEY']}"

    def one(pair):
        path = cache_path(cache_dir, pair['doi'], pair['dandiset_id'])
        if path.exists():
            cached = json.loads(path.read_text())
            if cached['classification'] != 'ERROR':
                return cached
        fetched = fetcher.get_paper_text_detailed(pair['doi'])
        text = fetched['text'] if fetched.get('status') == 'full_text' else None
        row = classify(pair, text, metadata.get(pair['cited_paper_doi'].lower()),
                       args.model, session, context_tokens)
        path.write_text(json.dumps(row, indent=1))
        return row

    results = {}
    with concurrent.futures.ThreadPoolExecutor(args.workers) as ex:
        for i, row in enumerate(ex.map(one, pairs), 1):
            results[(row['citing_doi'].lower(), row['dandiset_id'])] = row
            if i % 25 == 0:
                print(f'{i}/{len(pairs)}', file=sys.stderr, flush=True)

    finish(f'{args.model} ({args.input})', args.tier, pairs, results, eval_set,
           model=args.model, effort=args.input)
    threshold_table(pairs, results)


if __name__ == '__main__':
    main()
