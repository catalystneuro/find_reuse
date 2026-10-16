"""Tests for sending the OpenAlex API key and for refusing to truncate on a refused request."""

import pytest
import requests
from requests.adapters import HTTPAdapter

import src.indirect_pipeline.openalex as O


class FakeResponse:
    def __init__(self, status_code, body=None):
        self.status_code = status_code
        self.body = body or {}

    def json(self):
        return self.body


class FakeSession:
    """Answers every request with the next response in line."""

    def __init__(self, *responses):
        self.responses = list(responses)

    def get(self, url, **kwargs):
        return self.responses.pop(0)


@pytest.fixture
def key(monkeypatch, tmp_path):
    monkeypatch.setenv('OPENALEX_API_KEY', 'test-key')
    monkeypatch.setattr(O, 'REPO', tmp_path)


class TestApiKey:
    def test_read_from_the_environment(self, key):
        assert O.get_openalex_api_key() == 'test-key'

    def test_read_from_the_repositorys_env_file(self, monkeypatch, tmp_path):
        monkeypatch.delenv('OPENALEX_API_KEY', raising=False)
        monkeypatch.setattr(O, 'REPO', tmp_path)
        (tmp_path / '.env').write_text('OTHER=1\nOPENALEX_API_KEY="from-file"\n')

        assert O.get_openalex_api_key() == 'from-file'

    def test_a_missing_key_is_an_error(self, monkeypatch, tmp_path):
        monkeypatch.delenv('OPENALEX_API_KEY', raising=False)
        monkeypatch.setattr(O, 'REPO', tmp_path)

        with pytest.raises(O.OpenAlexError, match='OPENALEX_API_KEY'):
            O.get_openalex_api_key()


class TestKeyAdapter:
    def sent_urls(self, monkeypatch, session, urls):
        sent = []

        def send(adapter, request, **kwargs):
            sent.append(request.url)
            response = requests.Response()
            response.status_code = 200
            return response

        monkeypatch.setattr(HTTPAdapter, 'send', send)
        for url in urls:
            session.get(url)
        return sent

    def test_the_key_goes_to_openalex_and_nowhere_else(self, key, monkeypatch):
        session = O._make_openalex_session()

        sent = self.sent_urls(monkeypatch, session, [
            'https://api.openalex.org/works?filter=cites:W1&per_page=200',
            'https://api.crossref.org/works/10.1/x',
            'https://api.semanticscholar.org/graph/v1/paper/DOI:10.1/x',
        ])

        assert sent[0] == ('https://api.openalex.org/works?filter=cites:W1'
                           '&per_page=200&api_key=test-key')
        assert 'api_key' not in sent[1] and 'api_key' not in sent[2]

    def test_the_direct_search_sends_the_key_too(self, key, monkeypatch):
        from src.direct_pipeline.find_reuse import ArchiveFinder

        finder = ArchiveFinder(use_cache=False)

        assert isinstance(finder.session.get_adapter('https://api.openalex.org/works'),
                          O.OpenAlexKeyAdapter)
        assert not isinstance(finder.session.get_adapter('https://www.ebi.ac.uk/'),
                              O.OpenAlexKeyAdapter)


class TestRefusedRequests:
    def test_a_refused_page_of_citing_papers_is_an_error(self):
        session = FakeSession(
            FakeResponse(200, {'results': [{'doi': 'https://doi.org/10.1/a'}],
                               'meta': {'next_cursor': 'next'}}),
            FakeResponse(401))

        with pytest.raises(O.OpenAlexError, match='HTTP 401'):
            O.get_citing_papers(session, 'W1', '2020-01-01', max_results=999)

    def test_a_refused_lookup_of_the_cited_paper_is_an_error(self):
        with pytest.raises(O.OpenAlexError, match='HTTP 401'):
            O.get_openalex_paper_data(FakeSession(FakeResponse(401)), '10.1/a')

    def test_a_paper_openalex_does_not_know_has_no_data(self):
        assert O.get_openalex_paper_data(FakeSession(FakeResponse(404)), '10.1/a') is None
