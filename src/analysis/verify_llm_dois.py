#!/usr/bin/env python3
"""
verify_llm_dois.py - Check the DOIs an LLM supplied for dandisets that declare none.

159 of the 365 dandisets have no DataCite relation and no DOI in their description,
so their primary paper was inferred by an LLM. Those entries carry the paper's
title and a DOI. This resolves each DOI and compares what it actually is against
what the LLM said it was.

Writes llm_doi_verdicts.json beside this file, which analyze_indirect_neither
reads. This is the one part of the NEITHER analysis that needs the network, which
is why it is a separate script run on demand rather than part of the offline path.
Re-run when the discovery output changes.

Usage:
    python -m src.analysis.verify_llm_dois
"""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

import requests

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from fetch_paper import CONTACT_EMAIL
from src.analysis.neither_common import load_discovery

OUT = Path(__file__).resolve().parent / 'llm_doi_verdicts.json'

# Titles are compared on content words rather than exactly, because the LLM's
# recorded name and the publisher's registered title differ in punctuation,
# subtitles and casing even when they are the same paper.
STOPWORDS = frozenset('the a an of and for in on with to by using from is are as at'.split())
MATCH_THRESHOLD = 0.4


def content_words(text: str) -> set:
    return {word for word in re.findall(r'[a-z0-9]+', (text or '').lower())
            if len(word) > 3 and word not in STOPWORDS}


def title_matches(claimed: str, actual: str) -> bool:
    claimed_words = content_words(claimed)
    if not claimed_words:
        return False
    return len(claimed_words & content_words(actual)) / len(claimed_words) >= MATCH_THRESHOLD


def main() -> int:
    dandisets = load_discovery()['dandisets']
    pending = {}
    for dandiset_id, record in dandisets.items():
        for relation in (record.get('paper_relations') or []):
            if (relation or {}).get('source') == 'llm' and relation.get('doi'):
                pending[dandiset_id] = (relation['doi'].strip(),
                                        relation.get('name') or record['dandiset_name'])
    print(f'{len(pending)} llm-identified links to verify', file=sys.stderr)

    session = requests.Session()
    verdicts = {}
    for index, (dandiset_id, (doi, claimed)) in enumerate(sorted(pending.items()), start=1):
        actual = None
        try:
            response = session.get(f'https://api.crossref.org/works/{doi}',
                                   params={'mailto': CONTACT_EMAIL}, timeout=20)
            if response.status_code == 200:
                actual = (response.json()['message'].get('title') or [''])[0]
        except Exception:
            pass
        time.sleep(0.15)

        if actual is None:
            verdict = 'unresolvable'
        elif title_matches(claimed, actual):
            verdict = 'matches'
        else:
            verdict = 'wrong_paper'
        verdicts[dandiset_id] = {'doi': doi, 'claimed': claimed,
                                 'actual': actual, 'verdict': verdict}
        if index % 40 == 0:
            print(f'  {index}/{len(pending)}', file=sys.stderr)

    OUT.write_text(json.dumps(verdicts, indent=2, sort_keys=True) + '\n')
    tally = {}
    for entry in verdicts.values():
        tally[entry['verdict']] = tally.get(entry['verdict'], 0) + 1
    print(f'wrote {OUT}: {tally}', file=sys.stderr)
    return 0


if __name__ == '__main__':
    sys.exit(main())
