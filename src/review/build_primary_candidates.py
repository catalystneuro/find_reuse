#!/usr/bin/env python3
"""
Build the list of dandisets whose primary paper a person has to confirm.

These are the dandisets DANDI names no paper for, where a model was asked to
pick one. Each gets one card holding every paper put forward as its primary,
with the case for each:

  * the model's pick, with the title the model gave it beside the title the
    DOI actually resolves to, since most picks are DOIs of some other paper;
  * papers the direct pathway classified PRIMARY, with the passages that name
    the dandiset as the authors' own deposit.

A dandiset can have several primary papers, so the card asks about each paper
rather than for one answer.

Writes primary_paper_confirmation/primary_paper_candidates.json.

Usage:
    python -m src.review.build_primary_candidates
"""

from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from difflib import SequenceMatcher
from pathlib import Path

import requests

from src.review import paper_metadata
from src.review.build_candidates import PAPER_METADATA_CACHE, RESULTS_FILE
from src.review.primary_papers import CANDIDATES_FILE

REPO = Path(__file__).resolve().parents[2]
DIRECT_CLASSIFICATIONS_FILE = REPO / 'output/fulltext_direct_openalex.json'
DANDI_API = 'https://api.dandiarchive.org/api'

# How alike two titles must be, once case and punctuation are set aside, to be
# the same title: close enough to forgive a subtitle's punctuation, not so close
# that "... of twenty-five human brain areas" passes for "... of Alzheimer's
# disease".
SAME_TITLE = 0.9
NOT_A_WORD = re.compile(r'[^a-z0-9]+')


def llm_identified_dandisets(results: list[dict]) -> list[dict]:
    """The corpus records whose paper a model picked."""
    return [record for record in results
            if any(relation.get('relation') == 'llm_identified'
                   for relation in record.get('paper_relations') or [])]


def direct_primaries(classifications: list[dict]) -> dict[str, list[dict]]:
    """The direct pathway's PRIMARY calls, keyed by dandiset."""
    by_dandiset = defaultdict(list)
    for record in classifications:
        if record['classification'] == 'PRIMARY':
            by_dandiset[record['dandiset_id']].append(record)
    return by_dandiset


def fetch_dandiset_metadata(dandiset: str, version: str,
                            api_base: str = DANDI_API) -> dict:
    """The metadata DANDI holds for one version of a dandiset."""
    response = requests.get(f'{api_base}/dandisets/{dandiset}/versions/{version}/',
                            timeout=30)
    response.raise_for_status()
    return response.json()


def describe_dandiset(metadata: dict) -> dict:
    """What a reviewer matches a paper against: what the data is and how it was taken."""
    summary = metadata.get('assetsSummary') or {}
    names = {key: [item['name'] for item in summary.get(key) or []]
             for key in ('species', 'approach', 'measurementTechnique')}
    return {
        'description': metadata.get('description') or '',
        'species': names['species'],
        'approaches': names['approach'],
        'techniques': names['measurementTechnique'],
    }


def squash(title: str) -> str:
    """A title with case and punctuation set aside."""
    return NOT_A_WORD.sub(' ', title.lower()).strip()


def same_title(claimed: str, title: str) -> bool:
    """Whether the title a model gave a DOI is the title the DOI resolves to."""
    return SequenceMatcher(None, squash(claimed), squash(title)).ratio() >= SAME_TITLE


def build_cards(records: list[dict], direct: dict[str, list[dict]],
                dandisets: dict[str, dict],
                papers: dict[str, dict | None]) -> list[dict]:
    """
    One card per dandiset, its candidate papers ordered model's pick first.

    `dandisets` is each dandiset's description from describe_dandiset, and
    `papers` each candidate DOI's registrar record from paper_metadata.resolve.
    """
    cards = []
    for record in records:
        dandiset = record['dandiset_id']
        candidates: dict[str, dict] = {}

        def candidate(doi: str) -> dict:
            if doi.lower() not in candidates:
                paper = papers[doi.lower()]
                candidates[doi.lower()] = {
                    'doi': doi,
                    'title': paper['title'] if paper else '',
                    'citation': paper_metadata.citation(paper) if paper else '',
                    'resolves': paper is not None,
                    'sources': [],
                }
            return candidates[doi.lower()]

        for relation in record['paper_relations']:
            if relation['relation'] != 'llm_identified':
                continue
            pick = candidate(relation['doi'])
            pick['claimed_name'] = relation['name']
            pick['name_matches'] = (pick['resolves']
                                    and same_title(relation['name'], pick['title']))
            pick['sources'].append({'kind': 'llm_identified',
                                    'confidence': relation.get('llm_confidence'),
                                    'reasoning': relation.get('llm_reasoning', '')})
        for classification in direct.get(dandiset, []):
            candidate(classification['citing_doi'])['sources'].append({
                'kind': 'direct_primary',
                'confidence': classification['confidence'],
                'reasoning': classification['reasoning'],
                'quotes': [quote['quote']
                           for quote in classification['evidence_quotes']],
            })

        cards.append({
            'dandiset': dandiset,
            'dandiset_name': record['dandiset_name'],
            'dandiset_url': record['dandiset_url'],
            'contact_person': record.get('contact_person') or '',
            'created': (record.get('dandiset_created') or '')[:10],
            **dandisets[dandiset],
            'candidates': list(candidates.values()),
        })
    return sorted(cards, key=lambda card: card['dandiset'])


def candidate_dois(records: list[dict], direct: dict[str, list[dict]]) -> set[str]:
    """Every DOI a card will put forward, so all of them are resolved at once."""
    dois = set()
    for record in records:
        dandiset = record['dandiset_id']
        dois |= {relation['doi'] for relation in record['paper_relations']
                 if relation['relation'] == 'llm_identified'}
        dois |= {c['citing_doi'] for c in direct.get(dandiset, [])}
    return dois


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--results-file', type=Path, default=RESULTS_FILE,
                        help='The discovery corpus holding the models\' picks.')
    parser.add_argument('--direct-file', type=Path,
                        default=DIRECT_CLASSIFICATIONS_FILE,
                        help='The direct pathway\'s classifications.')
    parser.add_argument('-o', '--output', type=Path, default=CANDIDATES_FILE)
    args = parser.parse_args()

    records = llm_identified_dandisets(
        json.loads(args.results_file.read_text())['results'])
    if not records:
        raise SystemExit(
            f'{args.results_file} holds no model-picked papers. A corpus '
            f'rediscovered after review has had them replaced; build from '
            f'one discovered before it.')
    direct = direct_primaries(
        json.loads(args.direct_file.read_text())['classifications'])
    papers = paper_metadata.resolve(candidate_dois(records, direct),
                                    PAPER_METADATA_CACHE)

    dandisets = {}
    for i, record in enumerate(records, 1):
        print(f'Describing dandiset {i}/{len(records)}: {record["dandiset_id"]}')
        dandisets[record['dandiset_id']] = describe_dandiset(fetch_dandiset_metadata(
            record['dandiset_id'], record['dandiset_version']))

    cards = build_cards(records, direct, dandisets, papers)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({'dandisets': cards}, indent=2,
                                      ensure_ascii=False) + '\n')
    print(f'{len(cards)} dandisets, '
          f'{sum(len(card["candidates"]) for card in cards)} candidate papers '
          f'-> {args.output}')


if __name__ == '__main__':
    main()
