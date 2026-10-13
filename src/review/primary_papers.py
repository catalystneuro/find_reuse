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
