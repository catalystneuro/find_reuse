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
from src.shared.classify_fulltext_reuse import (
    classify_paper_reuse, DEFAULT_MODEL, PROMPT_VERSION, MODE_CITING, MODE_DIRECT,
)

REPO = Path(__file__).resolve().parents[2]

# Direct-mode discovery output covers several archives incidentally; this study
# is scoped to DANDI, so only its references are classified.
DIRECT_ARCHIVE = 'DANDI Archive'


def cache_path(cache_dir: Path, doi: str, dataset_id: str) -> Path:
    safe = f"{doi}__{dataset_id}".replace('/', '_').replace(':', '_').replace('\\', '_')
    return cache_dir / f"{safe}.json"


def select_with_full_text(work: list[dict], limit: int, paper_cache: str,
                          workers: int = 12) -> list[dict]:
    """
    Keep the items whose paper text we can actually retrieve.

    Parallel because this is network-bound, not CPU-bound: metadata-only cache
    entries now expire, so a serial pass refetches thousands of papers one at a
    time and spends over an hour before any classification starts.

    Order is preserved so that `--limit` selects the same items it always did,
    rather than whichever happened to resolve first.
    """
    local = threading.local()

    def fetcher_for_thread() -> PaperFetcher:
        if not hasattr(local, 'fetcher'):
            local.fetcher = PaperFetcher(use_cache=True, cache_dir=paper_cache)
        return local.fetcher

    def status_of(index_item):
        index, item = index_item
        try:
            fetched = fetcher_for_thread().get_paper_text_detailed(item['doi'])
        except Exception:
            return index, item, 0
        if fetched['status'] != 'full_text':
            return index, item, 0
        return index, item, len(fetched['text'])

    resolved: list[tuple[int, dict, int]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        futures = [ex.submit(status_of, pair) for pair in enumerate(work)]
        for fut in tqdm(concurrent.futures.as_completed(futures), total=len(futures),
                        desc='Selecting papers with full text', file=sys.stderr):
            resolved.append(fut.result())

    keep = []
    for _, item, chars in sorted(resolved, key=lambda r: r[0]):
        if chars:
            item['text_chars'] = chars
            keep.append(item)
            if len(keep) >= limit:
                break
    return keep


def build_worklist(results_path: Path, paper_cache: str, limit: int) -> list[dict]:
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

    return select_with_full_text(work, limit, paper_cache)


def build_direct_worklist(path: Path, paper_cache: str, limit: int) -> list[dict]:
    """
    Worklist for the direct pathway, taken from the existing classifications.

    Each (paper, dataset) pair is its own question, because one paper can name
    several dataset identifiers and stand in a different relationship to each:
    primary for its own deposit, reuser of another. `limit` therefore counts
    pairs here, not papers.
    """
    data = json.loads(path.read_text())
    work = []

    if 'classifications' in data:
        # A previous classification run: re-judge the same pairs, carrying the
        # old label so the two methods can be compared.
        for entry in data['classifications']:
            doi = entry.get('citing_doi')
            if not doi:
                continue
            work.append({
                'doi': doi,
                'title': entry.get('citing_title', ''),
                'dandiset_id': entry.get('dandiset_id', ''),
                'dandiset_name': entry.get('dandiset_name', ''),
                'primary_paper_doi': entry.get('cited_doi') or '',
                'matched_patterns': entry.get('match_patterns'),
                'prior_classification': entry.get('classification'),
            })
    else:
        # Raw discovery output. One work item per (paper, dataset) pair, with
        # the strings that actually matched, so the model can recognize a
        # pattern-matching artifact rather than assuming the reference is real.
        for entry in data.get('results', []):
            doi = entry.get('doi')
            if not doi:
                continue
            for archive, info in (entry.get('archives') or {}).items():
                if archive != DIRECT_ARCHIVE:
                    continue
                matches = info.get('matches') or []
                for dataset_id in info.get('dataset_ids', []):
                    patterns = [m.get('matched_string') for m in matches
                                if m.get('id') == dataset_id and m.get('matched_string')]
                    work.append({
                        'doi': doi,
                        'title': entry.get('title', ''),
                        'dandiset_id': dataset_id,
                        'dandiset_name': '',
                        'primary_paper_doi': '',
                        'matched_patterns': patterns or None,
                        'prior_classification': None,
                    })

    return select_with_full_text(work, limit, paper_cache)


def load_cached_results(cache_dir: Path, skip_errors: bool = False) -> list[dict]:
    """Read every cached result, optionally excluding the errors."""
    out = []
    for path in sorted(cache_dir.glob('*.json')):
        try:
            prior = json.loads(path.read_text())
        except Exception:
            continue
        if skip_errors and prior.get('classification') == 'ERROR':
            continue
        out.append(prior)
    return out


def build_retry_worklist(cache_dir: Path) -> list[dict]:
    """
    Rebuild work items straight from cached ERROR results.

    Every cached result carries the DOI and dandiset it was asked about, so a
    retry can be reconstructed without touching the corpus or the network.
    """
    work = []
    for path in sorted(cache_dir.glob('*.json')):
        try:
            prior = json.loads(path.read_text())
        except Exception:
            continue
        if prior.get('classification') != 'ERROR' or not prior.get('citing_doi'):
            continue
        work.append({
            'doi': prior['citing_doi'],
            'title': prior.get('title', ''),
            'dandiset_id': prior.get('dandiset_id', ''),
            'dandiset_name': '',
            'primary_paper_doi': '',
            'matched_patterns': None,
            'prior_classification': prior.get('prior_classification'),
        })
    return work


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=[MODE_CITING, MODE_DIRECT],
                        default=MODE_CITING,
                        help="'citing' asks how a citing paper relates to a dataset; "
                             "'direct' asks whether a paper that names a dataset "
                             "identifier published it or reused it.")
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

    # Keep the two pathways in separate caches and outputs; they answer different
    # questions and their labels are not interchangeable.
    if args.mode == MODE_DIRECT:
        if args.cache_dir == str(REPO / '.fulltext_classification_cache'):
            args.cache_dir = str(REPO / '.fulltext_direct_cache')
        if args.output == str(REPO / 'output/fulltext_classifications.json'):
            args.output = str(REPO / 'output/fulltext_direct_classifications.json')
        if args.results_file == str(REPO / 'output/all_dandiset_papers.json'):
            args.results_file = str(REPO / 'output/direct_ref_classifications.json')

    cache_dir = Path(args.cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)

    fetcher = PaperFetcher(use_cache=True, cache_dir=args.paper_cache)

    # Results already on disk that this run is not re-running. In retry mode the
    # work list is only the failures, so without this the output file would be
    # rewritten with just those and the rest of the corpus would vanish from it.
    # The cache is the source of truth; the output file is a view of it.
    carried: list[dict] = []

    if args.retry_errors:
        # Retrying failures does not need the corpus rescanned. Selection calls
        # the fetcher for every paper, and metadata-only cache entries now
        # expire, so a rescan refetches roughly 1,400 papers over the network to
        # find a hundred cached errors. The errors already record what they were.
        work = build_retry_worklist(cache_dir)
        carried = load_cached_results(cache_dir, skip_errors=True)
        print(f"{len(work)} cached errors to retry, {len(carried)} results carried "
              f"forward ({args.mode} mode)", file=sys.stderr, flush=True)
    else:
        builder = build_direct_worklist if args.mode == MODE_DIRECT else build_worklist
        work = builder(Path(args.results_file), args.paper_cache, args.limit)
        print(f"{len(work)} items with full text selected ({args.mode} mode)",
              file=sys.stderr, flush=True)

    todo = []
    cached_results = []
    stale = 0
    for item in work:
        path = cache_path(cache_dir, item['doi'], item['dandiset_id'])
        if path.exists():
            try:
                prior = json.loads(path.read_text())
            except Exception:
                todo.append(item)
                continue
            # A cached answer to a different question is not an answer to this
            # one. Mixing prompt versions would silently produce a corpus where
            # some rows have same_lab and archive and others cannot.
            if prior.get('prompt_version') != PROMPT_VERSION:
                stale += 1
                todo.append(item)
            elif prior.get('classification') == 'ERROR' and args.retry_errors:
                todo.append(item)
            else:
                cached_results.append(prior)
            continue
        todo.append(item)

    print(f"{len(cached_results)} already classified, {len(todo)} to run"
          + (f" ({stale} stale from an older prompt version)" if stale else ""),
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
                mode=args.mode,
                matched_patterns=item.get('matched_patterns'),
            )
        result['citing_doi'] = item['doi']
        result['dandiset_id'] = item['dandiset_id']
        result['title'] = item['title']
        if item.get('prior_classification'):
            result['prior_classification'] = item['prior_classification']
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

    # `carried` is empty outside retry mode, where `cached_results` already
    # covers everything the work list skipped.
    all_results = carried + cached_results + fresh
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
