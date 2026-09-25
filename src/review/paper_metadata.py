"""
What a DOI is, according to the agency that registered it.

A dandiset names the papers behind it by DOI and by a name its depositor typed,
and that name is often a placeholder ("Publication"), a section heading, or the
title of a different paper. The registrar's record is what the DOI actually
resolves to, so it is what a reviewer is shown.

doi.org answers in CSL-JSON for Crossref, DataCite and the other agencies alike,
so a Zenodo deposit resolves the same way a journal article does.

Records are cached, since rebuilding the candidate list is how a pipeline change
is checked and that has to stay cheap. A DOI no agency knows is cached as None.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import quote

import requests

CSL_JSON = 'application/vnd.citationstyles.csl+json'
USER_AGENT = 'find_reuse/1.0 (mailto:ben.dichter@catalystneuro.com)'

MARKUP = re.compile(r'<[^>]+>')


def summarize(csl: dict) -> dict:
    """The title, the authors' surnames and the year of issue from a CSL record."""
    title = csl.get('title') or ''
    issued = (csl.get('issued') or {}).get('date-parts') or [[None]]
    return {
        'title': ' '.join(MARKUP.sub('', title).split()),
        # A consortium is an author with a name rather than a family name.
        'authors': [author.get('family') or author.get('literal') or author['name']
                    for author in csl.get('author') or []],
        'year': issued[0][0],
    }


def fetch(session: requests.Session, doi: str) -> dict | None:
    """The registrar's record for one DOI, or None where no agency knows it."""
    response = session.get(f'https://doi.org/{quote(doi, safe="/")}',
                           headers={'Accept': CSL_JSON}, timeout=30)
    if response.status_code == 404:
        return None
    response.raise_for_status()
    return summarize(response.json())


def resolve(dois: set[str], cache_path: Path) -> dict[str, dict | None]:
    """Every DOI's record, keyed by the lowercased DOI, fetching the ones not cached."""
    cache = json.loads(cache_path.read_text()) if cache_path.exists() else {}
    missing = sorted({doi.lower() for doi in dois} - set(cache))
    if missing:
        session = requests.Session()
        session.headers['User-Agent'] = USER_AGENT
        for i, doi in enumerate(missing, 1):
            print(f'Resolving cited paper {i}/{len(missing)}: {doi}')
            cache[doi] = fetch(session, doi)
            cache_path.write_text(json.dumps(cache, indent=2, sort_keys=True,
                                             ensure_ascii=False) + '\n')
    return cache


def citation(record: dict) -> str:
    """The paper as a citing paper refers to it: "Churchland et al., 2012"."""
    authors = record['authors']
    names = (authors[0] if len(authors) == 1 else
             f'{authors[0]} & {authors[1]}' if len(authors) == 2 else
             f'{authors[0]} et al.' if authors else '')
    return ', '.join(part for part in (names, str(record['year'] or '')) if part)
