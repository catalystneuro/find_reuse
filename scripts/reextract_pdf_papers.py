#!/usr/bin/env python3
"""
Re-extract papers whose cached text came from the Unpaywall PDF path.

PDF extraction used to drop margin line numbers inline, so text from
line-numbered preprints carried integers scattered through the prose. The
fetcher now removes them, but only for fetches made since. Cached text still
carries the artifact, and the positional information needed to strip it lives in
the PDF rather than in what we stored, so the fix requires re-downloading.

Only papers that actually show the pattern are re-fetched, and a re-fetch never
replaces a good cached entry with a worse one: if the new attempt fails or comes
back without an article body, the existing text is kept.

Usage:
    python scripts/reextract_pdf_papers.py --dry-run
    python scripts/reextract_pdf_papers.py --workers 8
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import re
import sys
import threading
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import warnings
warnings.filterwarnings('ignore')

from tqdm import tqdm

from fetch_paper import PaperFetcher, CONTACT_EMAIL, TOOL_NAME, USER_AGENT
from paper_text_fetcher import TextCache, is_full_text

CACHE_DIR = REPO / '.paper_cache'

# A line-numbered page leaves a bare integer alone on its own line, over and
# over. Twenty of them in one paper is well past what prose produces.
LINE_NUMBER_RUN = re.compile(r'\n\s*\d{1,4}\s*\n')
LINE_NUMBER_THRESHOLD = 20


def affected(entry: dict) -> bool:
    """True if this entry came from the PDF path and shows the artifact."""
    if 'unpaywall' not in (entry.get('source') or '').split('+'):
        return False
    return len(LINE_NUMBER_RUN.findall(entry.get('text') or '')) >= LINE_NUMBER_THRESHOLD


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workers', type=int, default=8)
    parser.add_argument('--limit', type=int, default=None)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()

    targets = []
    for path in sorted(CACHE_DIR.glob('*.json')):
        try:
            entry = json.loads(path.read_text())
        except Exception:
            continue
        if entry.get('doi') and affected(entry):
            targets.append((entry['doi'], len(entry.get('text') or ''),
                            len(LINE_NUMBER_RUN.findall(entry.get('text') or ''))))
    if args.limit:
        targets = targets[:args.limit]

    print(f"{len(targets):,} cached papers came from the PDF path and show line numbers",
          file=sys.stderr, flush=True)
    if args.dry_run:
        for doi, chars, hits in targets[:15]:
            print(f"  {doi:44} {chars:>8,} chars  {hits:>4} line-number runs",
                  file=sys.stderr)
        return

    cache = TextCache(CACHE_DIR)
    local = threading.local()

    def fetcher_for_thread() -> PaperFetcher:
        if not hasattr(local, 'f'):
            # use_cache=False so the fetch actually goes out; the write below is
            # explicit so a failed attempt cannot clobber what we already have.
            local.f = PaperFetcher(use_cache=False, cache_dir=CACHE_DIR)
        return local.f

    def redo(target):
        doi, old_chars, old_hits = target
        try:
            result = fetcher_for_thread().get_paper_text_detailed(doi)
        except Exception as e:
            return doi, 'error', old_chars, 0, f'{type(e).__name__}'
        text = result.get('text') or ''
        if result.get('status') != 'full_text' or not is_full_text(text, result.get('source')):
            return doi, 'kept_old', old_chars, 0, result.get('status')
        new_hits = len(LINE_NUMBER_RUN.findall(text))
        cache.put(doi, text, result['source'], True)
        verdict = 'cleaned' if new_hits < old_hits else 'rewritten'
        return doi, verdict, len(text), new_hits, result.get('source')

    outcomes, removed = Counter(), 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as ex:
        futures = [ex.submit(redo, t) for t in targets]
        for fut in tqdm(concurrent.futures.as_completed(futures),
                        total=len(futures), desc='Re-extracting', file=sys.stderr):
            doi, verdict, chars, hits, detail = fut.result()
            outcomes[verdict] += 1
            if verdict == 'cleaned':
                removed += 1

    print(f"\n{dict(outcomes)}", file=sys.stderr)
    print(f"papers with fewer line numbers than before: {removed:,}", file=sys.stderr)


if __name__ == '__main__':
    main()
