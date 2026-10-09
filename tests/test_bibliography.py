"""Tests for what a numbered citation in a quoted passage points at."""

import json

import pytest
import requests

import src.review.bibliography as B

REFERENCE_LIST = '''Methods

We reanalysed the recordings [2].

References

[1] O'Doherty JE, Cardoso MMB, Makin JG, Sabes PN. Nonhuman primate reaching with multichannel sensorimotor cortex electrophysiology. Zenodo. 2017.

[2] Churchland MM, Cunningham JP, Kaufman MT, et al. Neural population dynamics during reaching. Nature. 2012;487:51-56. CrossRef PubMed Google Scholar

[3] Siletti K, Hodge R, Mossi Albiach A, et al. Transcriptomic diversity of cell types across the adult human brain. Science. 2023;382:eadd7046.

Acknowledgements

We thank the original authors.
'''


class TestCitedNumbers:
    def test_reads_one_number(self):
        assert B.cited_numbers('downloaded from the CELLxGENE repository [28].') == [28]

    def test_reads_a_list_and_a_range(self):
        assert B.cited_numbers('as before [7, 9] and since [12-14]') == [7, 9, 12, 13, 14]

    def test_reads_numbers_set_off_from_their_brackets(self):
        assert B.cited_numbers('recordings [ 29 , 39 – 41 ] were used') == [29, 39, 40, 41]

    def test_names_a_number_once_however_often_it_is_cited(self):
        assert B.cited_numbers('the data [5] and the task [5, 6]') == [5, 6]

    def test_passes_over_brackets_that_cite_by_name_or_hold_a_year(self):
        assert B.cited_numbers('[Yu et al., 2016] released in [2019]') == []


class TestTextEntries:
    def test_reads_the_papers_own_list_by_number(self):
        entries = B.text_entries(REFERENCE_LIST)

        assert entries[1].startswith("O'Doherty JE, Cardoso MMB")
        assert entries[3].endswith('Science. 2023;382:eadd7046.')

    def test_drops_the_link_labels_a_rendered_list_trails(self):
        assert B.text_entries(REFERENCE_LIST)[2].endswith('Nature. 2012;487:51-56.')

    def test_numbered_headings_are_not_a_reference_list(self):
        text = ('1. Introduction\n\nReuse is hard to measure.\n\n'
                '2. Methods\n\nWe read every paper.\n\n'
                '3. Results\n\nMost reuse goes uncited.\n')

        assert B.text_entries(text) == {}

    def test_text_without_a_numbered_list_has_no_entries(self):
        assert B.text_entries('We reanalysed the recordings (Churchland 2012).') == {}


class TestTidy:
    def test_an_entry_is_one_line(self):
        assert B.tidy('Churchland MM,\n  Cunningham JP.\nNature.') == (
            'Churchland MM, Cunningham JP. Nature.')

    def test_a_long_entry_is_cut_short(self):
        entry = B.tidy('Author A, ' * 200)

        assert len(entry) <= B.MAX_ENTRY + 1
        assert entry.endswith('…')


class TestCrossrefEntry:
    def test_keeps_the_citation_the_publisher_wrote(self):
        entry = B.crossref_entry({
            'unstructured': 'Churchland MM, et al. Neural population dynamics '
                            'during reaching. Nature. 2012.',
            'DOI': '10.1038/nature11129'})

        assert entry == ('Churchland MM, et al. Neural population dynamics during '
                         'reaching. Nature. 2012. 10.1038/nature11129')

    def test_builds_one_from_what_the_publisher_itemized(self):
        entry = B.crossref_entry({
            'author': 'Marshel', 'year': '2011', 'journal-title': 'Neuron',
            'article-title': 'Functional specialization of seven mouse visual '
                             'cortical areas',
            'DOI': '10.1016/j.neuron.2011.12.004'})

        assert entry == ('Marshel. 2011. Functional specialization of seven mouse '
                         'visual cortical areas. Neuron 10.1016/j.neuron.2011.12.004')

    def test_does_not_repeat_a_doi_the_citation_already_gives(self):
        entry = B.crossref_entry({
            'unstructured': 'Siletti K. Science. doi:10.1126/science.add7046',
            'DOI': '10.1126/SCIENCE.ADD7046'})

        assert entry == 'Siletti K. Science. doi:10.1126/science.add7046'

    def test_a_reference_deposited_as_a_doi_alone_is_that_doi(self):
        assert B.crossref_entry({'DOI': '10.1038/nature11129'}) == '10.1038/nature11129'


class FakeResponse:
    def __init__(self, status_code=200, message=None):
        self.status_code = status_code
        self.message = message or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f'{self.status_code} Client Error')

    def json(self):
        return {'message': self.message}


class FakeSession:
    """Crossref, answering for the works it was given and for no others."""

    def __init__(self, works):
        self.works = works
        self.asked = []
        self.headers = {}

    def get(self, url, timeout):
        doi = requests.utils.unquote(url.removeprefix(B.CROSSREF_WORKS))
        self.asked.append(doi)
        work = self.works.get(doi)
        if isinstance(work, int):
            return FakeResponse(work)
        return FakeResponse(200, work) if work else FakeResponse(404)


class TestFetch:
    def test_lists_the_references_in_the_order_deposited(self):
        session = FakeSession({'10.1/published': {
            'type': 'journal-article',
            'reference': [{'unstructured': 'First A. 2001.'},
                          {'unstructured': 'Second B. 2002.'}]}})

        assert B.fetch(session, '10.1/published') == ['First A. 2001.',
                                                      'Second B. 2002.']

    def test_a_preprint_has_no_list_to_go_by(self):
        session = FakeSession({'10.1101/2024.01.01.573831': {
            'type': 'posted-content',
            'reference': [{'unstructured': 'First A. 2001.'}]}})

        assert B.fetch(session, '10.1101/2024.01.01.573831') == []

    def test_an_elife_article_is_not_asked_about(self):
        session = FakeSession({})

        assert B.fetch(session, '10.7554/elife.93060') == []
        assert session.asked == []

    def test_a_paper_crossref_does_not_know_has_no_list(self):
        assert B.fetch(FakeSession({}), '10.1/unregistered') == []

    def test_a_paper_that_deposited_no_references_has_no_list(self):
        session = FakeSession({'10.1/bare': {'type': 'journal-article'}})

        assert B.fetch(session, '10.1/bare') == []


@pytest.fixture
def crossref(monkeypatch):
    """Stand a fake Crossref in for the network, and take the waiting out."""
    session = FakeSession({
        '10.1/published': {'type': 'journal-article',
                           'reference': [{'unstructured': 'First A. 2001.'}]},
        '10.1/refused': 429,
    })
    monkeypatch.setattr(B.requests, 'Session', lambda: session)
    monkeypatch.setattr(B, 'CROSSREF_INTERVAL', 0)
    return session


class TestResolve:
    def test_fetches_a_list_once_and_keeps_it(self, crossref, tmp_path):
        cache_path = tmp_path / 'references.json'

        first = B.resolve({'10.1/published'}, cache_path)
        second = B.resolve({'10.1/published'}, cache_path)

        assert first == second == {'10.1/published': ['First A. 2001.']}
        assert crossref.asked == ['10.1/published']
        assert json.loads(cache_path.read_text()) == first

    def test_a_paper_crossref_refuses_is_asked_about_again(self, crossref, tmp_path):
        cache_path = tmp_path / 'references.json'

        assert B.resolve({'10.1/refused'}, cache_path) == {}
        B.resolve({'10.1/refused'}, cache_path)

        assert crossref.asked == ['10.1/refused', '10.1/refused']

    def test_asks_nothing_when_no_paper_needs_a_list(self, crossref, tmp_path):
        cache_path = tmp_path / 'references.json'

        assert B.resolve(set(), cache_path) == {}
        assert not cache_path.exists()


class TestLookup:
    def test_the_papers_own_list_answers_before_crossref(self):
        found = B.lookup([2], {2: 'Churchland MM. 2012.'}, ['Other A.', 'Other B.'])

        assert found == [{'n': 2, 'entry': 'Churchland MM. 2012.', 'source': 'text'}]

    def test_crossref_answers_for_what_the_text_does_not(self):
        found = B.lookup([1, 2], {1: 'First A. 2001.'}, ['Other A.', 'Second B. 2002.'])

        assert found == [
            {'n': 1, 'entry': 'First A. 2001.', 'source': 'text'},
            {'n': 2, 'entry': 'Second B. 2002.', 'source': 'crossref'}]

    def test_a_number_neither_list_reaches_is_left_out(self):
        assert B.lookup([0, 9], {}, ['First A. 2001.']) == []
