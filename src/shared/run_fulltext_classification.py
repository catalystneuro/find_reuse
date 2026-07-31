#!/usr/bin/env python3
"""
Batch-run full-text reuse classification over citing papers.

One API call per paper, asking about the dandiset that paper is linked to, so
the output lines up with the existing (paper, dataset) classifications.

Results are cached one JSON file per (paper, dataset) pair, so an interrupted
run resumes without paying for the work already done. ERROR results are cached
too but marked, and `--retry-errors` re-runs only those.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import sys
import threading
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import warnings
warnings.filterwarnings('ignore')

from tqdm import tqdm

from fetch_paper import PaperFetcher
from src.shared.classify_fulltext_reuse import classify_paper_reuse, DEFAULT_MODEL

REPO = Path(__file__).resolve().parents[2]


def cache_path(cache_dir: Path, doi: str, dataset_id: str) -> Path:
    safe = f"{doi}__{dataset_id}".replace('/', '_').replace(':', '_').replace('\\', '_')
    return cache_dir / f"{safe}.json"


def build_worklist(results_path: Path, fetcher: PaperFetcher, limit: int) -> list[dict]:
    """
    Pick the papers to classify: those whose text we actually have.

    A paper cited by several dandisets appears once, against the first dandiset
    that cites it, so `limit` counts papers rather than API calls.
    """
    data = json.loads(results_path.read_text())
    seen: set[str] = set()
    work: list[dict] = []

    for ds in data['results']:
        for paper in ds.get('citing_papers', []):
            doi = paper.get('doi')
            if not doi or doi in seen:
                continue
            seen.add(doi)
            work.append({
                'doi': doi,
                'title': paper.get('title', ''),
                'dandiset_id': ds.get('dandiset_id', ''),
                'dandiset_name': ds.get('dandiset_name', ''),
                'primary_paper_doi': (ds.get('paper_relations') or [{}])[0].get('doi', ''),
            })

    # Only papers with a retrievable body can be classified at all.
    keep = []
    for item in tqdm(work, desc='Selecting papers with full text', file=sys.stderr):
        fetched = fetcher.get_paper_text_detailed(item['doi'])
        if fetched['status'] == 'full_text':
            item['text_chars'] = len(fetched['text'])
            keep.append(item)
        if len(keep) >= limit:
            break
    return keep


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--limit', type=int, default=500)
    parser.add_argument('--workers', type=int, default=8)
    parser.add_argument('--model', default=DEFAULT_MODEL)
    parser.add_argument('--results-file',
                        default=str(REPO / 'output/all_dandiset_papers.json'))
    parser.add_argument('--paper-cache', default=str(REPO / '.paper_cache'))
    parser.add_argument('--cache-dir',
                        default=str(REPO / '.fulltext_classification_cache'))
    parser.add_argument('-o', '--output',
                        default=str(REPO / 'output/fulltext_classifications.json'))
    parser.add_argument('--retry-errors', action='store_true',
                        help='Re-run pairs whose cached result was an ERROR')
    parser.add_argument('--max-tokens', type=int, default=8192,
                        help='Completion budget. This is a reasoning model, so '
                             'the budget covers thinking as well as the answer; '
                             'papers with ambiguous evidence can exhaust 8192 '
                             'and come back as truncated_response.')
    args = parser.parse_args()

    cache_dir = Path(args.cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)

    fetcher = PaperFetcher(use_cache=True, cache_dir=args.paper_cache)
    work = build_worklist(Path(args.results_file), fetcher, args.limit)
    print(f"{len(work)} papers with full text selected", file=sys.stderr, flush=True)

    todo = []
    cached_results = []
    for item in work:
        path = cache_path(cache_dir, item['doi'], item['dandiset_id'])
        if path.exists():
            try:
                prior = json.loads(path.read_text())
            except Exception:
                todo.append(item)
                continue
            if prior.get('classification') == 'ERROR' and args.retry_errors:
                todo.append(item)
            else:
                cached_results.append(prior)
            continue
        todo.append(item)

    print(f"{len(cached_results)} already classified, {len(todo)} to run",
          file=sys.stderr, flush=True)

    thread_local = threading.local()

    def get_fetcher():
        if not hasattr(thread_local, 'fetcher'):
            thread_local.fetcher = PaperFetcher(
                use_cache=True, cache_dir=args.paper_cache)
        return thread_local.fetcher

    def run_one(item):
        fetched = get_fetcher().get_paper_text_detailed(item['doi'])
        if fetched['status'] != 'full_text':
            # Should not happen given the worklist filter, but never guess.
            result = {
                'classification': 'ERROR', 'confidence': 0,
                'evidence_quotes': [], 'quote_warnings': [],
                'hallucinated_quote_count': 0,
                'error': f"no full text at classification time: {fetched['reason']}",
                'error_kind': 'no_full_text',
            }
        else:
            result = classify_paper_reuse(
                fetched['text'],
                dataset_id=item['dandiset_id'],
                dataset_name=item['dandiset_name'],
                primary_paper_doi=item['primary_paper_doi'],
                paper_doi=item['doi'],
                model=args.model,
                max_tokens=args.max_tokens,
            )
        result['citing_doi'] = item['doi']
        result['dandiset_id'] = item['dandiset_id']
        result['title'] = item['title']
        cache_path(cache_dir, item['doi'], item['dandiset_id']).write_text(
            json.dumps(result, indent=2))
        return result

    fresh = []
    t0 = time.time()
    if todo:
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as ex:
            futures = [ex.submit(run_one, i) for i in todo]
            pbar = tqdm(concurrent.futures.as_completed(futures),
                        total=len(futures), desc='Classifying', file=sys.stderr)
            counts = Counter()
            for fut in pbar:
                try:
                    result = fut.result()
                except Exception as e:
                    print(f"  worker crashed: {type(e).__name__}: {e}",
                          file=sys.stderr, flush=True)
                    continue
                fresh.append(result)
                counts[result['classification']] += 1
                pbar.set_postfix({k: v for k, v in counts.most_common(4)})
    elapsed = time.time() - t0

    all_results = cached_results + fresh
    counts = Counter(r['classification'] for r in all_results)
    halluc = sum(r.get('hallucinated_quote_count', 0) for r in all_results)
    with_quotes = sum(1 for r in all_results if r.get('evidence_quotes'))
    tiers = Counter(q['match_type'] for r in all_results
                    for q in r.get('evidence_quotes', []))
    tokens_in = sum((r.get('usage') or {}).get('prompt_tokens', 0) for r in all_results)
    tokens_out = sum((r.get('usage') or {}).get('completion_tokens', 0) for r in all_results)
    cost = tokens_in * 0.14 / 1e6 + tokens_out * 0.28 / 1e6

    summary = {
        'papers': len(all_results),
        'newly_classified': len(fresh),
        'classification_counts': dict(counts),
        'papers_with_quotes': with_quotes,
        'quote_match_tiers': dict(tiers),
        'hallucinated_quotes': halluc,
        'prompt_tokens': tokens_in,
        'completion_tokens': tokens_out,
        'estimated_cost_usd': round(cost, 2),
        'seconds': round(elapsed, 1),
        'model': args.model,
    }

    Path(args.output).write_text(json.dumps(
        {'summary': summary, 'classifications': all_results}, indent=2))

    print('\n=== CLASSIFICATION COMPLETE ===', file=sys.stderr)
    print(json.dumps(summary, indent=2), file=sys.stderr, flush=True)


if __name__ == '__main__':
    main()
