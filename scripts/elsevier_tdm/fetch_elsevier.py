#!/usr/bin/env python3
"""
Fetch Elsevier articles' full text from a subscribing institution's network.

Needs only Python 3 and `requests`. Run it on the campus network or VPN with
your own Elsevier API key:

    export ELSEVIER_API_KEY=...          # from https://dev.elsevier.com
    python fetch_elsevier.py --check     # one article, to confirm access
    python fetch_elsevier.py             # all DOIs in dois.txt

It writes the elsevier_texts/ folder next to it: one JSON file per article that
came back as full text, plus fetch_log.json with the status of every DOI. A rerun
skips articles already fetched. The texts stay on this machine; classify_local.py
reads them and writes the decisions that are shared.
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import requests

HERE = Path(__file__).resolve().parent
API = 'https://api.elsevier.com/content/article/doi/'
MIN_CHARS = 6000   # shorter than this is an abstract or a landing page, not an article
INTERVAL = 1.0     # seconds between requests
CHECK_DOI = '10.1016/j.pnpbp.2023.110759'   # a subscription article


def fetch(doi, key, session):
    """Return (text, status) for one DOI."""
    try:
        resp = session.get(API + quote(doi, safe='/'), params={'view': 'FULL'},
                           headers={'X-ELS-APIKey': key, 'Accept': 'text/plain'}, timeout=60)
    except requests.RequestException as e:
        return None, f'error: {e}'
    if resp.status_code != 200:
        return None, f'HTTP {resp.status_code} {resp.headers.get("X-ELS-Status", "")}'.strip()
    text = resp.text.strip()
    if len(text) < MIN_CHARS:
        return None, f'only {len(text)} characters'
    return text, 'full_text'


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('--check', action='store_true', help='fetch one article to confirm access, then stop')
    ap.add_argument('--dois', default=str(HERE / 'dois.txt'))
    ap.add_argument('--out', default=str(HERE / 'elsevier_texts'))
    args = ap.parse_args()
    key = os.environ.get('ELSEVIER_API_KEY')
    if not key:
        sys.exit('Set ELSEVIER_API_KEY to your own Elsevier API key.')
    session = requests.Session()

    if args.check:
        text, status = fetch(CHECK_DOI, key, session)
        if text:
            print(f'OK: full text for {CHECK_DOI} ({len(text):,} characters). This network is entitled.')
            return
        sys.exit(f'Not entitled from here ({status}). Are you on the campus network or VPN?')

    dois = [d.strip() for d in Path(args.dois).read_text().splitlines() if d.strip()]
    texts = Path(args.out)                         # one JSON file per article, kept for reruns
    texts.mkdir(exist_ok=True)
    path_for = lambda doi: texts / f'{quote(doi, safe="")}.json'  # noqa: E731
    log_path = texts / 'fetch_log.json'
    log = json.loads(log_path.read_text()) if log_path.exists() else {}
    todo = [d for d in dois if not path_for(d).exists()]
    print(f'{len(dois)} DOIs, {len(dois) - len(todo)} already fetched, {len(todo)} to go')
    for i, doi in enumerate(todo, 1):
        text, status = fetch(doi, key, session)
        log[doi] = status
        if text:
            path_for(doi).write_text(json.dumps({
                'doi': doi, 'text': text, 'source': 'elsevier', 'has_full_text': True,
                'cached_at': datetime.now(timezone.utc).isoformat()}))
        if i % 25 == 0 or i == len(todo):
            got = sum(1 for d in dois if path_for(d).exists())
            print(f'  {i}/{len(todo)} tried, {got} fetched', flush=True)
            log_path.write_text(json.dumps(log, indent=1))
        time.sleep(INTERVAL)
    log_path.write_text(json.dumps(log, indent=1))
    got = sum(1 for d in dois if path_for(d).exists())
    print(f'Done: {got} of {len(dois)} articles in {texts}. Next: python classify_local.py')

if __name__ == '__main__':
    main()
