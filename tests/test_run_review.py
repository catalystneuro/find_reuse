"""Tests for the review worksheet, its assignment, and the answers it records."""

import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

import src.review.run_review as R


class TestLabels:
    def test_only_the_direct_queue_can_answer_primary(self):
        assert 'primary' in R.LABELS['direct']
        assert 'primary' not in R.LABELS['indirect']

    def test_only_the_indirect_queue_can_answer_mention(self):
        assert 'mention' in R.LABELS['indirect']
        assert 'mention' not in R.LABELS['direct']

    def test_the_page_offers_the_labels_of_its_own_mode(self):
        row = {'key': 'k', 'doi': 'd', 'title': 't', 'dandiset': '000001',
               'dandiset_name': 'n', 'reasoning': 'r', 'quotes': []}

        assert '"primary"' in R.build([row], 'Ada', 'direct')
        assert '"mention"' not in R.build([row], 'Ada', 'direct')


class TestLoadAssignment:
    @pytest.fixture
    def candidates(self, tmp_path):
        path = tmp_path / 'reuse_candidates.json'
        path.write_text(json.dumps({'generated_at': 'STAMP', 'pairs': [
            {'key': '10.1/b\t000541', 'doi': '10.1/b', 'dandiset': '000541',
             'pathway': 'indirect', 'title': 'The second paper'},
            {'key': '10.1/a\t000714', 'doi': '10.1/a', 'dandiset': '000714',
             'pathway': 'direct', 'title': 'The first paper'},
            {'key': '10.1/a\t000541', 'doi': '10.1/a', 'dandiset': '000541',
             'pathway': 'indirect', 'title': 'The first paper'},
        ]}))
        return path

    @pytest.fixture
    def assignment(self, tmp_path):
        path = tmp_path / 'rly.indirect.json'
        path.write_text(json.dumps({
            'reviewer': 'rly', 'pathway': 'indirect',
            'keys': ['10.1/b\t000541', '10.1/a\t000541'],
        }))
        return path

    def test_reads_the_reviewer_and_the_pathway_off_the_assignment(
            self, assignment, candidates):
        _, reviewer, pathway = R.load_assignment(assignment, candidates)
        assert (reviewer, pathway) == ('rly', 'indirect')

    def test_returns_the_records_of_the_pairs_it_names(self, assignment, candidates):
        rows, _, _ = R.load_assignment(assignment, candidates)
        assert [r['title'] for r in rows] == ['The first paper', 'The second paper']

    def test_leaves_out_a_pair_the_assignment_does_not_name(
            self, assignment, candidates):
        rows, _, _ = R.load_assignment(assignment, candidates)
        assert '10.1/a\t000714' not in [r['key'] for r in rows]

    def test_orders_the_pairs_by_paper_then_dataset(self, assignment, candidates):
        rows, _, _ = R.load_assignment(assignment, candidates)
        assert [r['key'] for r in rows] == ['10.1/a\t000541', '10.1/b\t000541']

    def test_shows_a_pair_answered_before_it_was_ever_assigned(
            self, assignment, candidates, tmp_path):
        (tmp_path / 'rly.json').write_text(json.dumps(
            {'reviewer': 'rly', 'calls': {'10.1/c\t000541': 'reuse'}, 'notes': {}}))
        candidates.write_text(json.dumps({'pairs': [
            *json.loads(candidates.read_text())['pairs'],
            {'key': '10.1/c\t000541', 'doi': '10.1/c', 'dandiset': '000541',
             'pathway': 'indirect', 'title': 'Answered but never assigned'},
        ]}))
        rows, _, _ = R.load_assignment(assignment, candidates, tmp_path)
        assert '10.1/c\t000541' in [r['key'] for r in rows]

    def test_leaves_out_an_answer_belonging_to_the_other_queue(
            self, assignment, candidates, tmp_path):
        (tmp_path / 'rly.json').write_text(json.dumps(
            {'reviewer': 'rly', 'calls': {'10.1/a\t000714': 'reuse'}, 'notes': {}}))
        rows, _, _ = R.load_assignment(assignment, candidates, tmp_path)
        assert '10.1/a\t000714' not in [r['key'] for r in rows]

    def test_leaves_out_an_answer_the_candidate_list_no_longer_describes(
            self, assignment, candidates, tmp_path):
        (tmp_path / 'rly.json').write_text(json.dumps(
            {'reviewer': 'rly', 'calls': {'10.1/dropped\t000541': 'reuse'},
             'notes': {}}))
        rows, _, _ = R.load_assignment(assignment, candidates, tmp_path)
        assert '10.1/dropped\t000541' not in [r['key'] for r in rows]

    def test_counts_an_answered_pair_once_when_it_is_also_assigned(
            self, assignment, candidates, tmp_path):
        (tmp_path / 'rly.json').write_text(json.dumps(
            {'reviewer': 'rly', 'calls': {'10.1/a\t000541': 'reuse'}, 'notes': {}}))
        rows, _, _ = R.load_assignment(assignment, candidates, tmp_path)
        assert [r['key'] for r in rows].count('10.1/a\t000541') == 1

    def test_an_assigned_pair_the_candidate_list_lacks_stops_the_session(
            self, tmp_path, candidates):
        path = tmp_path / 'rly.indirect.json'
        path.write_text(json.dumps({'reviewer': 'rly', 'pathway': 'indirect',
                                    'keys': ['10.1/gone\t000541']}))
        with pytest.raises(SystemExit) as excinfo:
            R.load_assignment(path, candidates)
        # Shown escaped, since a raw tab in a terminal message is invisible.
        assert '10.1/gone' in str(excinfo.value)


@pytest.fixture
def session(tmp_path):
    """A running review server, with the directory its answers land in."""
    reviews_dir = tmp_path / 'reviews'
    handler = R.make_handler('<title>page</title>', 'Ada Lovelace',
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
