"""Tests for the cards a primary-paper reviewer works through."""

import json

import pytest

import src.review.build_primary_candidates as B


@pytest.fixture
def corpus_records():
    """A dandiset whose paper a model picked, and one DANDI names a paper for."""
    return [
        {'dandiset_id': '001414', 'dandiset_name': 'Cerebral blood flow and cAMP',
         'dandiset_url': 'https://dandiarchive.org/dandiset/001414/draft',
         'dandiset_version': 'draft', 'contact_person': 'Doe, Jane',
         'dandiset_created': '2025-04-01T12:00:00Z',
         'paper_relations': [{
             'relation': 'llm_identified', 'doi': '10.1101/2024.11.25.625128',
             'name': 'Cerebral Blood Flow is Modulated by Astrocytic cAMP',
             'llm_confidence': 10, 'llm_reasoning': 'Title matches the dandiset.'}]},
        {'dandiset_id': '000003', 'dandiset_name': 'Declared',
         'dandiset_url': 'https://dandiarchive.org/dandiset/000003/draft',
         'dandiset_version': 'draft',
         'paper_relations': [{'relation': 'dcite:IsDescribedBy',
                              'doi': '10.1/declared', 'name': 'Declared paper'}]},
    ]


@pytest.fixture
def direct_classifications():
    return [
        {'classification': 'PRIMARY', 'dandiset_id': '001414',
         'citing_doi': '10.1/own-deposit', 'confidence': 9,
         'reasoning': 'The authors deposited these data.',
         'evidence_quotes': [{'quote': 'Data are available at DANDI 001414.'}]},
        {'classification': 'PRIMARY', 'dandiset_id': '001414',
         'citing_doi': '10.1/unregistered', 'confidence': 7,
         'reasoning': 'A deposit note.', 'evidence_quotes': []},
        {'classification': 'REUSE', 'dandiset_id': '001414',
         'citing_doi': '10.1/reuser', 'confidence': 8, 'reasoning': 'Reanalysed.',
         'evidence_quotes': []},
    ]


@pytest.fixture
def papers():
    """What each DOI resolves to; the model's pick resolves to another paper."""
    return {
        '10.1101/2024.11.25.625128': {
            'title': 'Old age variably impacts chimpanzee engagement in stone tool use',
            'authors': ['Howard-Spink'], 'year': 2024},
        '10.1/own-deposit': {'title': 'Astrocytic cAMP and blood flow',
                             'authors': ['Doe', 'Roe'], 'year': 2025},
        '10.1/unregistered': None,
    }


@pytest.fixture
def dandisets():
    return {'001414': {'description': 'Blood flow in mice.', 'species': ['House mouse'],
                       'approaches': [], 'techniques': ['two-photon microscopy']}}


class TestLlmIdentifiedDandisets:
    def test_keeps_only_the_dandisets_a_model_picked_for(self, corpus_records):
        records = B.llm_identified_dandisets(corpus_records)

        assert [r['dandiset_id'] for r in records] == ['001414']


class TestDirectPrimaries:
    def test_keeps_only_primary_calls_by_dandiset(self, direct_classifications):
        primaries = B.direct_primaries(direct_classifications)

        assert [c['citing_doi'] for c in primaries['001414']] == [
            '10.1/own-deposit', '10.1/unregistered']


class TestDescribeDandiset:
    def test_names_the_species_and_techniques(self):
        metadata = {'description': 'Blood flow in mice.', 'assetsSummary': {
            'species': [{'name': 'House mouse'}], 'approach': [],
            'measurementTechnique': [{'name': 'two-photon microscopy'}]}}

        assert B.describe_dandiset(metadata) == {
            'description': 'Blood flow in mice.', 'species': ['House mouse'],
            'approaches': [], 'techniques': ['two-photon microscopy']}


class TestSameTitle:
    def test_case_and_punctuation_do_not_count(self):
        assert B.same_title('Cortical State Dynamics: A Study',
                            'cortical state dynamics - a study')

    def test_a_different_atlas_is_a_different_title(self):
        assert not B.same_title(
            'Integrated multimodal cell atlas of twenty-five human brain areas',
            "Integrated multimodal cell atlas of Alzheimer's disease")


class TestBuildCards:
    @pytest.fixture
    def card(self, corpus_records, direct_classifications, papers, dandisets):
        records = B.llm_identified_dandisets(corpus_records)
        [card] = B.build_cards(records, B.direct_primaries(direct_classifications),
                               dandisets, papers)
        return card

    def test_describes_the_dandiset(self, card):
        assert {key: card[key] for key in (
            'dandiset', 'dandiset_name', 'contact_person', 'created', 'description',
            'species', 'techniques')} == {
            'dandiset': '001414', 'dandiset_name': 'Cerebral blood flow and cAMP',
            'contact_person': 'Doe, Jane', 'created': '2025-04-01',
            'description': 'Blood flow in mice.', 'species': ['House mouse'],
            'techniques': ['two-photon microscopy']}

    def test_puts_the_models_pick_first_then_the_other_candidates(self, card):
        assert [c['doi'] for c in card['candidates']] == [
            '10.1101/2024.11.25.625128', '10.1/own-deposit', '10.1/unregistered']

    def test_shows_the_models_name_beside_the_real_title(self, card):
        pick = card['candidates'][0]

        assert (pick['claimed_name'], pick['title'], pick['name_matches']) == (
            'Cerebral Blood Flow is Modulated by Astrocytic cAMP',
            'Old age variably impacts chimpanzee engagement in stone tool use',
            False)

    def test_carries_the_passages_naming_the_dandiset(self, card):
        assert card['candidates'][1]['sources'] == [{
            'kind': 'direct_primary', 'confidence': 9,
            'reasoning': 'The authors deposited these data.',
            'quotes': ['Data are available at DANDI 001414.']}]

    def test_a_doi_no_registrar_knows_has_no_title(self, card):
        found = card['candidates'][2]

        assert (found['title'], found['citation'], found['resolves']) == ('', '', False)

    def test_cites_a_resolved_paper(self, card):
        assert card['candidates'][1]['citation'] == 'Doe & Roe, 2025'


class TestCandidateDois:
    def test_names_every_paper_a_card_puts_forward(
            self, corpus_records, direct_classifications):
        records = B.llm_identified_dandisets(corpus_records)

        assert B.candidate_dois(records, B.direct_primaries(direct_classifications)) == {
            '10.1101/2024.11.25.625128', '10.1/own-deposit', '10.1/unregistered'}
