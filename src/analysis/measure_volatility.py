#!/usr/bin/env python3
"""
measure_volatility.py - Do the residual NEITHER rows reproduce?

The rows carrying `citation_not_found` and no other cause have no known defect
behind them. One possibility is that they are not stable answers at all: the
classifier runs at temperature 0.1, and a row that lands on NEITHER once might
land elsewhere on a re-run.

This re-sends production's own prompt, unchanged, N times per row and reports how
often the cached label reproduces. There is no second arm -- the question is
reproducibility, not the effect of an intervention.

Fidelity matters here, so the prompt is built the way `build_worklist` builds it,
including its use of `paper_relations[0]` for the primary paper DOI rather than
the relation the pair was actually created from. Those differ for 37 dandisets,
and using the corrected DOI would silently test a fix instead of the status quo.

Writes volatility_verdicts.json beside this file, which analyze_indirect_neither
reads for its `unstable_classification` cause. Results are written incrementally,
so an interrupted run keeps what it paid for and a re-run tops up rows whose calls
failed. This costs money and needs the network, which is why it is a separate
script run on demand rather than part of the offline path.

Usage:
    python -m src.analysis.measure_volatility --repeats 3
    python -m src.analysis.measure_volatility --repeats 3 --limit 10   # cheap dry run
    python -m src.analysis.measure_volatility --report                 # summarise, no calls
"""

from __future__ import annotations

import argparse
import collections
import concurrent.futures
import json
import sys
import threading
from pathlib import Path

from tqdm import tqdm

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / 'tmp'))  # probe_citation.classify

from probe_citation import classify
from src.analysis.analyze_indirect_neither import PIPELINE
from src.analysis.neither_common import described_rows, load_discovery
from src.shared.classify_fulltext_reuse import DEFAULT_MODEL, MODE_CITING, build_prompt

RESULTS = Path(__file__).resolve().parent / 'volatility_verdicts.json'
RESIDUAL_CAUSES = ('citation_not_found',)


def production_prompt(described: dict) -> str:
    """The prompt `build_worklist` + `classify_paper_reuse` would have produced."""
    record = load_discovery()['dandisets'].get(described['dandiset_id']) or {}
    relations = record.get('paper_relations') or [{}]
    return build_prompt(
        described['text'],
        dataset_id=described['dandiset_id'],
        dataset_name=record.get('dandiset_name', ''),
        primary_paper_doi=(relations[0] or {}).get('doi', ''),
        mode=MODE_CITING,
    )


def load_results() -> dict:
    return json.loads(RESULTS.read_text()) if RESULTS.exists() else {}


def summarise(results: dict) -> None:
    if not results:
        print('no results yet')
        return
    buckets = collections.Counter()
    flipped_to = collections.Counter()
    for entry in results.values():
        labels = entry['labels']
        neither = labels.count('NEITHER')
        if neither == len(labels):
            buckets['reproduces NEITHER every time'] += 1
        elif neither == 0:
            buckets['never NEITHER on re-run'] += 1
        else:
            buckets['unstable (mixed)'] += 1
        for label in labels:
            if label != 'NEITHER':
                flipped_to[label] += 1

    total = len(results)
    print(f'\n{total} residual rows re-run\n')
    for name, count in buckets.most_common():
        print(f'  {count:5d}  {count / total:5.0%}  {name}')
    if flipped_to:
        print(f'\nwhen it was not NEITHER it was: {dict(flipped_to)}')

    # Production asked about paper_relations[0], which for 37 dandisets is not
    # the paper the pair was built from. Those rows are a different question.
    dandisets = load_discovery()['dandisets']
    split = collections.defaultdict(collections.Counter)
    for key, entry in results.items():
        record = dandisets.get(entry['dandiset_id']) or {}
        asked = ((record.get('paper_relations') or [{}])[0] or {}).get('doi', '').strip()
        group = 'asked about the pair\'s own paper' if asked == entry['cited_doi'] \
            else 'asked about a different paper'
        split[group]['n'] += 1
        split[group]['stable NEITHER'] += entry['labels'].count('NEITHER') == len(entry['labels'])
    print()
    for group, counts in sorted(split.items()):
        n = counts['n']
        print(f'  {group:34s} n={n:4d}   reproduces NEITHER: '
              f'{counts["stable NEITHER"]}/{n} ({counts["stable NEITHER"] / n:.0%})')


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--limit', type=int)
    parser.add_argument('--workers', type=int, default=24)
    parser.add_argument('--model', default=DEFAULT_MODEL)
    parser.add_argument('--report', action='store_true', help='summarise saved results only')
    args = parser.parse_args()

    if args.report:
        summarise(load_results())
        return 0

    rows = [r for r in described_rows(PIPELINE) if r['causes'] == RESIDUAL_CAUSES]
    results = load_results()
    # A row is done only when it has all its repeats, so a topped-up re-run
    # fills in rows whose calls failed last time.
    todo = [r for r in rows
            if len(results.get(f'{r["citing_doi"]}|{r["dandiset_id"]}', {}).get('labels', []))
            < args.repeats]
    if args.limit:
        todo = todo[:args.limit]
    print(f'{len(rows)} residual rows, {len(results)} already done, {len(todo)} to run '
          f'x{args.repeats} = {len(todo) * args.repeats} calls', file=sys.stderr)
    if not todo:
        summarise(results)
        return 0

    lock = threading.Lock()
    spent = [0.0]
    failures = [0]
    labels: dict = {f'{r["citing_doi"]}|{r["dandiset_id"]}': [] for r in todo}
    by_key = {f'{r["citing_doi"]}|{r["dandiset_id"]}': r for r in todo}

    def run_call(key):
        """One call. A row that fails is recorded and skipped, never fatal."""
        described = by_key[key]
        try:
            result = classify(production_prompt(described), args.model)
            return key, result['classification'], result['cost']
        except BaseException:
            return key, None, 0.0

    def save(key):
        described = by_key[key]
        results[key] = {
            'citing_doi': described['citing_doi'],
            'dandiset_id': described['dandiset_id'],
            'cited_doi': described['cited_doi'],
            'cached': described['classification'],
            'labels': labels[key],
        }
        RESULTS.write_text(json.dumps(results, indent=2, sort_keys=True) + '\n')

    # One task per call rather than per row: the repeats are independent, and
    # nesting them inside a worker serialised three slow calls per slot.
    tasks = [key for key in labels for _ in range(args.repeats)]
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(run_call, key) for key in tasks]
        with tqdm(total=len(futures), unit='call', desc='re-running') as bar:
            for future in concurrent.futures.as_completed(futures):
                key, label, cost = future.result()
                with lock:
                    if label is None:
                        failures[0] += 1
                    else:
                        labels[key].append(label)
                        spent[0] += cost
                        save(key)
                    bar.set_postfix(spent=f'${spent[0]:.2f}', failed=failures[0],
                                    flipped=sum(1 for v in results.values()
                                                if v['labels'] and 'NEITHER' not in v['labels']))
                    bar.update(1)

    if failures[0]:
        print(f'{failures[0]} calls failed after retries; their rows have fewer repeats. '
              'Re-run to top them up.', file=sys.stderr)
    print(f'\nspent ${spent[0]:.2f}; results in {RESULTS}', file=sys.stderr)
    summarise(results)
    return 0


if __name__ == '__main__':
    sys.exit(main())
