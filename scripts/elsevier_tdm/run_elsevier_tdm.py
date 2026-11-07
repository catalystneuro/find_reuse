#!/usr/bin/env python3
"""
Fetch Elsevier full text under your institution's subscription and classify it.

Run this on your institution's network (or VPN), with your own Elsevier API key.
Full texts stay on your machine. The only output to send back is the
classifications file, which holds labels, short supporting quotes and the
model's reasoning.

Setup, from a find_reuse checkout:

    pip install -e ../paper-text-fetcher -r requirements.txt
    export ELSEVIER_API_KEY=...      # your own key, from https://dev.elsevier.com
    export OPENROUTER_API_KEY=...    # the key Ben gave you for this run

Run:

    python scripts/elsevier_tdm/run_elsevier_tdm.py --check      # one article, to confirm access
    python scripts/elsevier_tdm/run_elsevier_tdm.py               # everything

Outputs, in scripts/elsevier_tdm/work/:
    <archive>_classifications.json   send these back
    texts/                           the fetched articles; keep these local
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import quote

import requests

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
INPUTS = HERE / 'inputs'
WORK = HERE / 'work'
TEXTS = WORK / 'texts'
API = 'https://api.elsevier.com/content/article/doi/'
MIN_CHARS = 6000          # shorter than this is an abstract or a landing page, not an article
INTERVAL = 1.0            # seconds between requests


def fetch(doi: str, key: str, session: requests.Session) -> tuple[str | None, str]:
    """Return (text, status) for one DOI from the Elsevier full-text API."""
    resp = session.get(API + quote(doi, safe='/'), params={'view': 'FULL'},
                       headers={'X-ELS-APIKey': key, 'Accept': 'text/plain'}, timeout=60)
    if resp.status_code != 200:
        return None, f'HTTP {resp.status_code} {resp.headers.get("X-ELS-Status", "")}'.strip()
    text = resp.text.strip()
    if len(text) < MIN_CHARS:
        return None, f'only {len(text)} characters (not entitled to the full text?)'
    return text, 'full_text'


def check(key: str):
    """Fetch one subscription article and say whether this network is entitled."""
    doi = '10.1016/j.pnpbp.2023.110759'
    text, status = fetch(doi, key, requests.Session())
    if text:
        print(f'OK: full text for {doi} ({len(text):,} characters). This network is entitled.')
    else:
        print(f'Not entitled from here: {status}. Are you on the campus network or VPN?')
        sys.exit(1)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('--check', action='store_true', help='fetch one article to confirm access, then stop')
    ap.add_argument('--fetch-only', action='store_true', help='fetch texts but do not classify')
    args = ap.parse_args()
    key = os.environ.get('ELSEVIER_API_KEY')
    if not key:
        sys.exit('Set ELSEVIER_API_KEY to your own Elsevier API key.')
    if args.check:
        check(key)
        return

    sys.path.insert(0, str(REPO))
    from paper_text_fetcher.cache import TextCache
    cache = TextCache(TEXTS)
    session = requests.Session()
    WORK.mkdir(exist_ok=True)
    inputs = sorted(INPUTS.glob('*_elsevier.json'))
    dois = sorted({p['doi'] for f in inputs for r in json.loads(f.read_text())['results']
                   for p in r['citing_papers']})
    print(f'{len(dois)} articles to fetch')
    got = 0
    log = {}
    for i, doi in enumerate(dois, 1):
        cached = cache.get(doi)
        if cached and cached[2]:
            got += 1
            continue
        text, status = fetch(doi, key, session)
        log[doi] = status
        if text:
            cache.put(doi, text, 'elsevier', True)
            got += 1
        if i % 25 == 0 or i == len(dois):
            print(f'  {i}/{len(dois)} tried, {got} with full text', flush=True)
        time.sleep(INTERVAL)
    (WORK / 'fetch_log.json').write_text(json.dumps(log, indent=1))
    print(f'{got} of {len(dois)} articles fetched; see work/fetch_log.json for the rest')
    if args.fetch_only:
        return

    for f in inputs:
        name = f.stem.replace('_elsevier', '')
        data = json.loads(f.read_text())
        for r in data['results']:
            for p in r['citing_papers']:
                c = cache.get(p['doi'])
                if c and c[2]:
                    p['text_status'], p['has_full_text'], p['text_source'] = 'full_text', True, 'elsevier'
        marked = WORK / f'{name}_elsevier_marked.json'
        marked.write_text(json.dumps(data))
        out = WORK / f'{name}_classifications.json'
        subprocess.run([sys.executable, '-m', 'src.shared.run_fulltext_classification',
                        '--results-file', str(marked), '--paper-cache', str(TEXTS),
                        '--cache-dir', str(WORK / f'{name}_classification_cache'),
                        '--reasoning-effort', 'high', '--limit', '100000', '--workers', '16',
                        '-o', str(out)], cwd=REPO, check=True)
        print(f'wrote {out}: send this file back')


if __name__ == '__main__':
    main()
