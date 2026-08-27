"""Tests for the reuse review worksheet and the answers it records."""

import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

import src.analysis.build_reuse_verification_page as B


def classification(citing_doi, dandiset_id, quote, **overrides):
    record = {
        'classification': 'REUSE',
        'confidence': 8,
        'citing_doi': citing_doi,
        'dandiset_id': dandiset_id,
        'title': 'One paper, several datasets',
        'reasoning': 'Reanalysed the deposited recordings.',
        'evidence_quotes': [{'quote': quote, 'match_type': 'exact'}],
        'source_quotes': [],
        'reused_neurophysiology': True,
        'reused_modalities': ['neurophysiology'],
        'same_lab': False,
        'source_archive': 'DANDI Archive',
    }
    record.update(overrides)
    return record


@pytest.fixture
def four_dataset_input(tmp_path):
    """One paper reusing four dandisets, each quoted differently."""
    path = tmp_path / 'fulltext_classifications.json'
    path.write_text(json.dumps({'classifications': [
        classification('10.1/citer', dandiset, f'passage about {dandiset}')
        for dandiset in ('000541', '000714', '000953', '000970')
    ]}))
    return str(path)


@pytest.fixture
def corpus(tmp_path):
    """A discovery corpus naming the paper each pair was built from."""
    path = tmp_path / 'all_dandiset_papers_refreshed.json'
    path.write_text(json.dumps({'results': [
        {
            'dandiset_id': '000541',
            'dandiset_name': 'Mouse motor cortex recordings',
            'paper_relations': [{'doi': '10.1/described-by',
                                 'name': 'The paper the data came from'}],
            'citing_papers': [{'doi': '10.1/CITER',
                               'cited_paper_doi': '10.1/described-by'}],
        },
        {
            'dandiset_id': '000714',
            'dandiset_name': 'Human intracortical dataset',
            'paper_relations': [
                {'doi': '10.1/published-in', 'name': 'Where it appeared',
                 'relation': 'dcite:IsPublishedIn'},
                {'doi': '10.1/declared', 'name': 'The paper describing 000714',
                 'relation': 'dcite:IsDescribedBy'},
            ],
            'citing_papers': [],
        },
        {
            'dandiset_id': '000953',
            'dandiset_name': 'Dataset that declares nothing',
            'paper_relations': [],
            'citing_papers': [],
        },
    ]}))
    return path


class TestMergeByPair:
    def test_one_row_per_pair_keyed_on_doi_and_dandiset(self, four_dataset_input):
        merged = B.merge_by_pair([four_dataset_input])

        assert sorted(merged) == [('10.1/citer', '000541'), ('10.1/citer', '000714'),
                                  ('10.1/citer', '000953'), ('10.1/citer', '000970')]
        assert merged[('10.1/citer', '000714')]['key'] == '10.1/citer\t000714'

    def test_each_pair_keeps_only_its_own_quote(self, four_dataset_input):
        merged = B.merge_by_pair([four_dataset_input])

        assert merged[('10.1/citer', '000541')]['quotes'] == [
            {'q': 'passage about 000541', 'tier': 'exact'}]
        assert merged[('10.1/citer', '000714')]['quotes'] == [
            {'q': 'passage about 000714', 'tier': 'exact'}]

    def test_pairs_the_classifier_did_not_call_reuse_are_dropped(self, tmp_path):
        path = tmp_path / 'classifications.json'
        path.write_text(json.dumps({'classifications': [
            classification('10.1/citer', '000541', 'kept'),
            classification('10.1/citer', '000714', 'dropped',
                           classification='MENTION'),
        ]}))

        assert sorted(B.merge_by_pair([str(path)])) == [('10.1/citer', '000541')]


class TestAttachCitedPapers:
    def test_names_the_paper_the_pair_was_built_from(self, corpus):
        rows = [{'doi': '10.1/citer', 'dandiset': '000541'}]

        B.attach_cited_papers(rows, corpus)

        assert rows[0]['cited_doi'] == '10.1/described-by'
        assert rows[0]['cited_title'] == 'The paper the data came from'
        assert rows[0]['cited_role'] == 'Cited'
        assert rows[0]['dandiset_name'] == 'Mouse motor cortex recordings'

    def test_offers_the_declared_paper_when_the_pair_cited_none(self, corpus):
        rows = [{'doi': '10.1/citer', 'dandiset': '000714'}]

        B.attach_cited_papers(rows, corpus)

        assert rows[0]['cited_doi'] == '10.1/declared'
        assert rows[0]['cited_title'] == 'The paper describing 000714'
        assert rows[0]['cited_role'] == 'Dataset paper'
        assert rows[0]['dandiset_name'] == 'Human intracortical dataset'

    def test_leaves_the_paper_empty_when_the_dataset_declares_none(self, corpus):
        rows = [{'doi': '10.1/citer', 'dandiset': '000953'}]

        B.attach_cited_papers(rows, corpus)

        assert rows[0]['cited_doi'] == ''
        assert rows[0]['cited_title'] == ''
        assert rows[0]['dandiset_name'] == 'Dataset that declares nothing'


class TestDisplayRows:
    def test_carries_only_what_the_page_asks_about(self, four_dataset_input, corpus):
        rows = [B.finalize(r) for r in B.merge_by_pair([four_dataset_input]).values()]
        B.attach_cited_papers(rows, corpus)

        shown = B.display_rows(rows)[0]

        assert set(shown) == {'key', 'doi', 'title', 'cited_doi', 'cited_title',
                              'cited_role', 'dandiset', 'dandiset_name',
                              'confidence', 'reasoning', 'quotes'}


@pytest.fixture
def session(tmp_path):
    """A running review server, with the directory its answers land in."""
    reviews_dir = tmp_path / 'reviews'
    handler = B.make_handler('<title>page</title>', 'Ada Lovelace',
                             reviews_dir / 'ada-lovelace.json')
    server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f'http://127.0.0.1:{server.server_address[1]}', reviews_dir
    server.shutdown()
    server.server_close()


def post_save(url, payload):
    request = urllib.request.Request(
        f'{url}/save', data=json.dumps(payload).encode(),
        headers={'Content-Type': 'application/json'}, method='POST')
    with urllib.request.urlopen(request) as response:
        return response.status


class TestReviewServer:
    def test_save_writes_only_the_reviewer_the_calls_and_the_notes(self, session):
        url, reviews_dir = session

        post_save(url, {'reviewer': 'Ada Lovelace',
                        'calls': {'10.1002/acn3.70285\t000768': 'reuse'},
                        'notes': {},
                        'schema': 3, 'saved_at': '2026-08-26T23:43:00.442Z',
                        'corpus': {'models': ['openai/gpt-5.6-luna']},
                        'labels': ['reuse', 'mention']})

        written = json.loads((reviews_dir / 'ada-lovelace.json').read_text())
        assert written == {'reviewer': 'Ada Lovelace',
                           'calls': {'10.1002/acn3.70285\t000768': 'reuse'},
                           'notes': {}}

    def test_load_returns_an_empty_session_before_anything_is_answered(self, session):
        url, _ = session

        with urllib.request.urlopen(f'{url}/load') as response:
            assert json.loads(response.read()) == {
                'reviewer': 'Ada Lovelace', 'calls': {}, 'notes': {}}

    def test_load_returns_what_save_wrote(self, session):
        url, _ = session
        calls = {'10.1/citer\t000541': 'mention'}
        notes = {'10.1/citer\t000541': 'Cited for the method, not the data.'}

        post_save(url, {'reviewer': 'Ada Lovelace', 'calls': calls, 'notes': notes})

        with urllib.request.urlopen(f'{url}/load') as response:
            assert json.loads(response.read()) == {
                'reviewer': 'Ada Lovelace', 'calls': calls, 'notes': notes}

    def test_serves_the_page_at_the_root(self, session):
        url, _ = session

        with urllib.request.urlopen(f'{url}/') as response:
            assert response.read() == b'<title>page</title>'


class TestReviewerSlug:
    def test_names_a_file_that_survives_punctuation_and_case(self):
        assert B.reviewer_slug('Paul Adkisson-Floro') == 'paul-adkisson-floro'
