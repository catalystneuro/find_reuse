"""Tests for the primary-paper review page and the calls it records."""

import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

import src.review.run_primary_review as R
from src.review.run_review import TextCache

CARD = {
    'dandiset': '001414', 'dandiset_name': 'Blood flow </script> in mice',
    'dandiset_url': 'https://dandiarchive.org/dandiset/001414/draft',
    'contact_person': 'Doe, Jane', 'created': '2025-04-01',
    'description': 'Blood flow.', 'species': [], 'approaches': [], 'techniques': [],
    'candidates': [{
        'doi': '10.1/own-deposit', 'title': 'Astrocytic cAMP', 'citation': 'Doe, 2025',
        'resolves': True,
        'sources': [{'kind': 'direct_primary', 'confidence': 9, 'reasoning': 'Theirs.',
                     'quotes': ['available at DANDI 001414']}]}],
}


@pytest.fixture
def paper_cache(tmp_path):
    cache_dir = tmp_path / 'paper_cache'
    TextCache(cache_dir).put('10.1/own-deposit',
                             'The data are available at DANDI 001414 for all.',
                             'europe_pmc', True)
    return cache_dir


@pytest.fixture
def session(tmp_path, paper_cache):
    """A running review server, with the file its calls land in."""
    save_path = tmp_path / 'primary_paper_confirmation' / 'confirmed_primary_papers.json'
    handler = R.make_handler('<title>page</title>', save_path, paper_cache,
                             R.quotes_by_paper([CARD]))
    server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f'http://127.0.0.1:{server.server_address[1]}', save_path
    server.shutdown()
    server.server_close()


def post_save(url, payload):
    request = urllib.request.Request(
        f'{url}/save', data=json.dumps(payload).encode(),
        headers={'Content-Type': 'application/json'}, method='POST')
    with urllib.request.urlopen(request) as response:
        return response.status


def get(url):
    with urllib.request.urlopen(url) as response:
        return response.read().decode()


class TestPrimaryReviewServer:
    def test_load_is_empty_before_anything_is_called(self, session):
        url, _ = session

        assert json.loads(get(f'{url}/load')) == {'dandisets': {}}

    def test_save_writes_the_calls_by_dandiset(self, session):
        url, save_path = session

        post_save(url, {'dandisets': {'001414': {
            'calls': {'10.1/own-deposit': 'primary', '10.1/pick': 'not_primary'},
            'note': 'From the data availability statement.'}}})

        assert json.loads(save_path.read_text()) == {'dandisets': {'001414': {
            'calls': {'10.1/own-deposit': 'primary', '10.1/pick': 'not_primary'},
            'note': 'From the data availability statement.'}}}

    def test_load_returns_what_save_wrote(self, session):
        url, _ = session
        dandisets = {'001414': {'calls': {'10.1/own-deposit': 'primary'}}}

        post_save(url, {'dandisets': dandisets})

        assert json.loads(get(f'{url}/load')) == {'dandisets': dandisets}

    def test_serves_the_text_with_the_quote_marked(self, session):
        url, _ = session

        page = get(f'{url}/text?doi=10.1/own-deposit&dandiset=001414')

        assert '<mark>available at DANDI 001414</mark>' in page


class TestReviewedOnly:
    def test_drops_dandisets_nothing_was_said_about(self):
        assert R.reviewed_only({
            '000002': {'calls': {}, 'note': ''},
            '001414': {'calls': {'10.1/a': 'unsure'}, 'note': ''},
        }) == {'001414': {'calls': {'10.1/a': 'unsure'}}}

    def test_keeps_a_note_with_no_calls(self):
        assert R.reviewed_only({'000002': {'calls': {}, 'note': 'Search later.'}}) == {
            '000002': {'calls': {}, 'note': 'Search later.'}}

    def test_sorts_by_dandiset(self):
        reviewed = R.reviewed_only({'001414': {'calls': {'10.1/a': 'primary'}},
                                    '000002': {'calls': {'10.1/b': 'primary'}}})

        assert list(reviewed) == ['000002', '001414']


class TestBuild:
    def test_a_name_cannot_close_the_script(self):
        page = R.build([CARD])

        assert 'Blood flow <\\/script> in mice' in page
        assert page.count('</script>') == 1

    def test_offers_every_call(self):
        assert 'const CALLS = ["primary", "not_primary", "unsure"];' in R.build([CARD])
