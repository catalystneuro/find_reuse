"""
The primary papers a person confirmed for the dandisets DANDI names none for.

Where DANDI names no paper, a model was asked to pick one, and most of its picks
are DOIs of papers other than the one it named. Every paper citing a pick is
then a candidate reuse of the dandiset, so a wrong pick fills the review queue
with papers citing something else entirely.

The review in run_primary_review records a call on each candidate paper for
those dandisets, and this is where the calls take effect: a model's pick never
seeds discovery, and a dandiset it was the only paper for is searched from the
papers a reviewer confirmed or not at all.
"""

from __future__ import annotations

import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PRIMARY_PAPER_CONFIRMATION_DIR = REPO / 'primary_paper_confirmation'
CANDIDATES_FILE = PRIMARY_PAPER_CONFIRMATION_DIR / 'primary_paper_candidates.json'
CONFIRMED_FILE = PRIMARY_PAPER_CONFIRMATION_DIR / 'confirmed_primary_papers.json'

CALLS = ('primary', 'not_primary', 'unsure')

DANDI_API = 'https://api.dandiarchive.org/api'


def load_confirmed(path: Path = CONFIRMED_FILE) -> dict:
    """Every reviewed dandiset's calls, keyed by dandiset."""
    return json.loads(path.read_text())['dandisets']


def confirmed_dois(review: dict) -> list[str]:
    """The papers a reviewer called primary for one dandiset, in the order given."""
    return [doi for doi, call in review['calls'].items() if call == 'primary']


def confirmed_relation(doi: str) -> dict:
    """A confirmed paper, shaped like every other relation discovery reads."""
    return {
        'relation': 'confirmed',
        'url': f'https://doi.org/{doi}',
        'name': None,
        'identifier': doi,
        'resource_type': None,
        'doi': doi,
        'source': 'review',
    }


def unlisted_confirmed_dandisets(results: list[dict], confirmed: dict) -> list[str]:
    """The dandisets a reviewer confirmed a paper for that have no record in the corpus."""
    listed = {record['dandiset_id'] for record in results}
    return sorted(dandiset for dandiset, review in confirmed.items()
                  if confirmed_dois(review) and dandiset not in listed)


def dandiset_record(dandiset: str, session) -> dict:
    """A corpus record with no papers yet, built from what DANDI holds for the dandiset."""
    response = session.get(f'{DANDI_API}/dandisets/{dandiset}/', timeout=30)
    response.raise_for_status()
    info = response.json()
    published = info['most_recent_published_version']
    version = published['version'] if published else 'draft'
    name = (published or info['draft_version'])['name']
    return {
        'dandiset_id': dandiset,
        'dandiset_name': name,
        'dandiset_version': version,
        'dandiset_url': f'https://dandiarchive.org/dandiset/{dandiset}/{version}',
        'dandiset_doi': None,
        'dandiset_created': info['created'],
        'embargo_status': info['embargo_status'],
        'draft_modified': info['draft_version']['modified'],
        'contact_person': info['contact_person'],
        'paper_relations': [],
    }


def apply_confirmed_primary_papers(results: list[dict], confirmed: dict) -> list[dict]:
    """
    The corpus with every model's pick replaced by what a reviewer confirmed.

    A dandiset left with no paper is dropped, as one DANDI names no paper for
    never enters the corpus: there is nothing to search from.
    """
    corrected = []
    for record in results:
        relations = [relation for relation in record.get('paper_relations') or []
                     if relation.get('relation') != 'llm_identified']
        review = confirmed.get(record['dandiset_id'])
        if review:
            present = {(relation.get('doi') or '').lower() for relation in relations}
            relations += [confirmed_relation(doi) for doi in confirmed_dois(review)
                          if doi.lower() not in present]
        if relations:
            corrected.append({**record, 'paper_relations': relations})
    return corrected
