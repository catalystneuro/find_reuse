"""Tests for what a DOI is, according to the agency that registered it."""

import json

import pytest

import src.review.paper_metadata as M


@pytest.fixture
def metadata_cache(tmp_path):
    path = tmp_path / 'paper_metadata_cache.json'
    path.write_text(json.dumps({
        '10.1038/nature11129': {'title': 'Neural population dynamics during reaching',
                                'authors': ['Churchland', 'Cunningham'], 'year': 2012},
        '10.1/unregistered': None,
    }))
    return path


class TestSummarize:
    def test_keeps_the_title_surnames_and_year(self):
        record = M.summarize({
            'title': 'Neural population dynamics during reaching',
            'author': [{'given': 'Mark M.', 'family': 'Churchland'},
                       {'given': 'John P.', 'family': 'Cunningham'}],
            'issued': {'date-parts': [[2012, 6, 3]]},
        })
        assert record == {'title': 'Neural population dynamics during reaching',
                          'authors': ['Churchland', 'Cunningham'], 'year': 2012}

    def test_names_a_consortium_by_its_name(self):
        record = M.summarize({
            'title': 'Standardized and reproducible measurement of decision-making in mice',
            'author': [{'name': 'The International Brain Laboratory'},
                       {'given': 'Valeria', 'family': 'Aguillon-Rodriguez'}],
            'issued': {'date-parts': [[2020, 1, 17]]},
        })
        assert record['authors'] == ['The International Brain Laboratory',
                                     'Aguillon-Rodriguez']

    def test_drops_markup_and_line_breaks_from_the_title(self):
        record = M.summarize({'title': 'Recordings <i>in vivo</i>\n   from mouse V1',
                              'author': [], 'issued': {'date-parts': [[2021]]}})
        assert record['title'] == 'Recordings in vivo from mouse V1'


class TestCitation:
    def test_one_author(self):
        assert M.citation({'authors': ['Makin'], 'year': 2018}) == 'Makin, 2018'

    def test_two_authors(self):
        assert M.citation({'authors': ['Chowdhury', 'Glaser'],
                           'year': 2020}) == 'Chowdhury & Glaser, 2020'

    def test_three_or_more_authors(self):
        assert M.citation({'authors': ['Churchland', 'Cunningham', 'Kaufman'],
                           'year': 2012}) == 'Churchland et al., 2012'

    def test_no_authors(self):
        assert M.citation({'authors': [], 'year': 2020}) == '2020'

    def test_no_year(self):
        assert M.citation({'authors': ['Makin'], 'year': None}) == 'Makin'


class TestResolve:
    def test_answers_a_cached_doi_whatever_its_casing(self, metadata_cache):
        papers = M.resolve({'10.1038/NATURE11129'}, metadata_cache)
        assert papers['10.1038/nature11129'] == {
            'title': 'Neural population dynamics during reaching',
            'authors': ['Churchland', 'Cunningham'], 'year': 2012}

    def test_answers_a_doi_no_agency_knows_with_none(self, metadata_cache):
        papers = M.resolve({'10.1/unregistered'}, metadata_cache)
        assert papers['10.1/unregistered'] is None
