#!/usr/bin/env python3
"""
Re-run citing-paper discovery for every dandiset, then fetch text for the DOIs
we do not already have.

This is the indirect pathway's search step: for each dandiset, find the papers
that cite its primary publication. It is separate from the direct search, which
looks for papers naming a dandiset identifier outright.

Two things this guards against, both learned the hard way:

  * OpenAlex answers a spent daily quota with 429 and a Retry-After measured in
    hours. Discovery run while throttled returns silently truncated citing-paper
    lists, which is worse than not running: two runs twenty minutes apart once
    disagreed by 1,321 DOIs. A preflight check aborts rather than produce that.

  * Discovery takes several minutes and fetching takes far longer. The discovery
    pass is written to disk before fetching starts, so an interrupted fetch does
    not cost the discovery.

Usage:
    python scripts/rediscover_citing_papers.py
    python scripts/rediscover_citing_papers.py --discovery-only
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

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import warnings
warnings.filterwarnings('ignore')

from tqdm import tqdm

from src.indirect_pipeline.openalex import (
    _make_openalex_session,
    find_citing_papers,
    _fetch_full_text_only,
)
from src.direct_pipeline.find_reuse import ArchiveFinder

SRC = REPO / 'output/all_dandiset_papers.json'
DISCOVERY = REPO / 'output/all_dandiset_papers_discovered.json'
DST = REPO / 'output/all_dandiset_papers_refreshed.json'
REPORT = REPO / 'output/rediscovery_report.json'
CACHE = str(REPO / '.paper_cache')


def openalex_is_available(session) -> bool:
    """Abort rather than run discovery against a spent quota."""
    resp = session.get(
        'https://api.openalex.org/works?per_page=1'
        '&mailto=ben.dichter@catalystneuro.com', timeout=30)
    if resp.status_code == 200:
        return True
    wait = resp.headers.get('retry-after', '?')
    try:
        detail = f"{int(wait)}s ({int(wait) / 3600:.1f}h)"
    except (TypeError, ValueError):
        detail = str(wait)
    print(f"ABORT: OpenAlex returned HTTP {resp.status_code}, Retry-After {detail}. "
          "Discovery would return silently truncated results, so it is not being "
          "run. Retry after the quota resets.", file=sys.stderr, flush=True)
    return False


def run_discovery() -> dict:
    data = json.loads(SRC.read_text())
    results = data['results']

    known = {p['doi'] for r in results for p in r.get('citing_papers', [])
             if p.get('doi')}
    print(f"{len(results)} datasets, {len(known):,} citing DOIs from the previous run",
          file=sys.stderr, flush=True)

    session = _make_openalex_session()
    if not openalex_is_available(session):
        raise SystemExit(2)

    errors = 0
    for result in tqdm(results, desc='OpenAlex discovery', file=sys.stderr):
        result['citing_papers'] = []          # force a fresh answer
        try:
            find_citing_papers(result, session, max_citing_papers_per_dandiset=999)
        except Exception as e:
            errors += 1
            print(f"  discovery failed for {result.get('dandiset_id')}: {e}",
                  file=sys.stderr, flush=True)

    data['_discovery'] = {'known_before': sorted(known), 'errors': errors}
    DISCOVERY.write_text(json.dumps(data))
    print(f"discovery saved to {DISCOVERY.name} ({errors} dataset errors)",
          file=sys.stderr, flush=True)
    return data


def cached(doi: str) -> bool:
    from paper_text_fetcher import cache_filename, legacy_cache_filename
    return ((Path(CACHE) / cache_filename(doi)).exists()
            or (Path(CACHE) / legacy_cache_filename(doi)).exists())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workers', type=int, default=8)
    parser.add_argument('--discovery-only', action='store_true',
                        help='Stop after discovery, before fetching any text.')
    parser.add_argument('--reuse-discovery', action='store_true',
                        help='Skip discovery and use the saved pass, if present.')
    args = parser.parse_args()

    if args.reuse_discovery and DISCOVERY.exists():
        print(f"reusing saved discovery from {DISCOVERY.name}", file=sys.stderr)
        data = json.loads(DISCOVERY.read_text())
    else:
        data = run_discovery()

    results = data['results']
    known = set(data.get('_discovery', {}).get('known_before', []))
    discovery_errors = data.get('_discovery', {}).get('errors', 0)

    current = {p['doi'] for r in results for p in r.get('citing_papers', [])
               if p.get('doi')}
    new_dois = sorted(current - known)
    dropped = sorted(known - current)
    print(f"{len(current):,} DOIs now, {len(new_dois):,} new, "
          f"{len(dropped):,} no longer returned", file=sys.stderr, flush=True)

    if args.discovery_only:
        print("stopping before fetch (--discovery-only)", file=sys.stderr)
        return

    to_fetch = [d for d in sorted(current) if not cached(d)]
    print(f"{len(to_fetch):,} DOIs have no cache entry, fetching with "
          f"{args.workers} workers", file=sys.stderr, flush=True)

    local = threading.local()

    def finder_for_thread():
        if not hasattr(local, 'finder'):
            local.finder = ArchiveFinder(verbose=False, use_cache=True, cache_dir=CACHE)
        return local.finder

    def fetch_one(doi):
        try:
            return doi, _fetch_full_text_only(finder_for_thread(), doi,
                                              sleep_when_live=True)
        except Exception as e:
            return doi, {'text': None, 'source': None, 'text_length': 0,
                         'has_full_text': False, 'text_status': 'unavailable',
                         'error': f'{type(e).__name__}: {e}'}

    fetched, status_counts = {}, Counter()
    started = time.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as ex:
        futures = [ex.submit(fetch_one, d) for d in to_fetch]
        for fut in tqdm(concurrent.futures.as_completed(futures),
                        total=len(futures), desc='Fetching text', file=sys.stderr):
            doi, info = fut.result()
            fetched[doi] = info
            status_counts[info['text_status']] += 1
    elapsed = time.time() - started

    finder = ArchiveFinder(verbose=False, use_cache=True, cache_dir=CACHE)
    corpus_counts, new_counts = Counter(), Counter()
    new_set = set(new_dois)
    for result in results:
        for paper in result.get('citing_papers', []):
            doi = paper.get('doi')
            if not doi:
                paper.update(text_status='unavailable', has_full_text=False)
                corpus_counts['no_doi'] += 1
                continue
            info = fetched.get(doi) or _fetch_full_text_only(finder, doi)
            paper['text_source'] = info.get('source')
            paper['text_length'] = info.get('text_length', 0)
            paper['has_full_text'] = info.get('has_full_text', False)
            paper['text_status'] = info.get('text_status', 'unavailable')
            if info.get('error'):
                paper['text_error'] = info['error']
            corpus_counts[paper['text_status']] += 1
            if doi in new_set:
                new_counts[paper['text_status']] += 1

    data.pop('_discovery', None)
    DST.write_text(json.dumps(data, indent=2))

    report = {
        'dois_before': len(known),
        'dois_after': len(current),
        'new_dois': len(new_dois),
        'dois_no_longer_returned': len(dropped),
        'discovery_errors': discovery_errors,
        'newly_fetched': len(to_fetch),
        'newly_fetched_status': dict(status_counts),
        'new_doi_status': dict(new_counts),
        'corpus_status': dict(corpus_counts),
        'fetch_seconds': round(elapsed, 1),
    }
    REPORT.write_text(json.dumps(report, indent=2))
    print('\n=== REDISCOVERY COMPLETE ===', file=sys.stderr)
    print(json.dumps(report, indent=2), file=sys.stderr, flush=True)


if __name__ == '__main__':
    main()
