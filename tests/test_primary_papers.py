"""Tests for how confirmed primary papers replace a model's picks in the corpus."""

import json

import pytest

import src.review.primary_papers as P


def record(dandiset, *relations):
    return {'dandiset_id': dandiset, 'dandiset_name': f'dataset {dandiset}',
            'paper_relations': list(relations), 'citing_papers': []}


def relation(doi, kind):
    return {'relation': kind, 'doi': doi, 'name': f'paper {doi}'}


@pytest.fixture
def corpus():
    """One dandiset DANDI names a paper for, and two a model picked a paper for."""
    return [
        record('000001', relation('10.1/declared', 'dcite:IsDescribedBy')),
        record('000002', relation('10.1/wrong-pick', 'llm_identified')),
        record('000003', relation('10.1/unreviewed-pick', 'llm_identified')),
    ]


class TestApplyConfirmedPrimaryPapers:
    def test_a_declared_paper_is_left_as_it_is(self, corpus):
        corrected = P.apply_confirmed_primary_papers(corpus, {})

        assert corrected[0] == corpus[0]

    def test_an_unreviewed_pick_drops_its_dandiset(self, corpus):
        corrected = P.apply_confirmed_primary_papers(corpus, {})

        assert [r['dandiset_id'] for r in corrected] == ['000001']

    def test_a_pick_is_replaced_by_the_confirmed_papers(self, corpus):
        confirmed = {'000002': {'calls': {'10.1/wrong-pick': 'not_primary',
                                          '10.1/right': 'primary',
                                          '10.1/also-right': 'primary'}}}

        corrected = P.apply_confirmed_primary_papers(corpus, confirmed)

        assert corrected[1]['paper_relations'] == [
            {'relation': 'confirmed', 'url': 'https://doi.org/10.1/right',
             'name': None, 'identifier': '10.1/right', 'resource_type': None,
             'doi': '10.1/right', 'source': 'review'},
            {'relation': 'confirmed', 'url': 'https://doi.org/10.1/also-right',
             'name': None, 'identifier': '10.1/also-right', 'resource_type': None,
             'doi': '10.1/also-right', 'source': 'review'},
        ]

    def test_a_confirmed_pick_is_kept_as_confirmed(self, corpus):
        confirmed = {'000002': {'calls': {'10.1/wrong-pick': 'primary'}}}

        corrected = P.apply_confirmed_primary_papers(corpus, confirmed)

        assert [(r['relation'], r['doi']) for r in corrected[1]['paper_relations']] == [
            ('confirmed', '10.1/wrong-pick')]

    def test_a_dandiset_with_no_paper_called_primary_is_dropped(self, corpus):
        confirmed = {'000002': {'calls': {'10.1/wrong-pick': 'not_primary'}},
                     '000003': {'calls': {'10.1/unreviewed-pick': 'unsure'}}}

        corrected = P.apply_confirmed_primary_papers(corpus, confirmed)

        assert [r['dandiset_id'] for r in corrected] == ['000001']

    def test_a_confirmed_paper_already_declared_is_not_added_twice(self, corpus):
        confirmed = {'000001': {'calls': {'10.1/DECLARED': 'primary'}}}

        corrected = P.apply_confirmed_primary_papers(corpus, confirmed)

        assert corrected[0]['paper_relations'] == corpus[0]['paper_relations']

    def test_the_corpus_passed_in_is_not_changed(self, corpus):
        before = json.dumps(corpus)

        P.apply_confirmed_primary_papers(
            corpus, {'000002': {'calls': {'10.1/right': 'primary'}}})

        assert json.dumps(corpus) == before


class TestLoadConfirmed:
    def test_reads_the_calls_by_dandiset(self, tmp_path):
        path = tmp_path / 'confirmed_primary_papers.json'
        path.write_text(json.dumps({'dandisets': {
            '000002': {'calls': {'10.1/right': 'primary'}, 'note': 'From the DAS.'}}}))

        assert P.load_confirmed(path) == {
            '000002': {'calls': {'10.1/right': 'primary'}, 'note': 'From the DAS.'}}
