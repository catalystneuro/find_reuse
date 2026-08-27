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
        row = {'doi': 'd', 'title': 't', 'dandiset': '000001',
               'dandiset_name': 'n', 'reasoning': 'r', 'quotes': []}

        assert '"primary"' in R.build([row], 'Ada', 'direct')
        assert '"mention"' not in R.build([row], 'Ada', 'direct')


PAPER_TEXT = ('Methods\n\nWe reanalysed the recordings of <i>Mus musculus</i> '
              'deposited by the original authors.\n')


@pytest.fixture
def paper_cache(tmp_path):
    """A text cache holding the paper as the classification run fetched it."""
    cache_dir = tmp_path / 'paper_cache'
    R.TextCache(cache_dir).put('10.1/citer', PAPER_TEXT, 'europe_pmc', True)
    return cache_dir


@pytest.fixture
def session(tmp_path, paper_cache):
    """A running review server, with the directory its answers land in."""
    reviews_dir = tmp_path / 'reuse_confirmation'
    handler = R.make_handler('<title>page</title>', 'Ada Lovelace',
                             reviews_dir / 'ada' / 'ada-reviews.json', paper_cache,
                             {('10.1/citer', '000541'): ['reanalysed the recordings']})
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
    def test_save_writes_only_the_reviewer_and_the_answers(self, session):
        url, reviews_dir = session

        post_save(url, {'reviewer': 'Ada Lovelace',
                        'reviews': {'10.1002/acn3.70285': {'000768': {'call': 'reuse'}}},
                        'schema': 3, 'saved_at': '2026-08-26T23:43:00.442Z',
                        'corpus': {'models': ['openai/gpt-5.6-luna']},
                        'labels': ['reuse', 'mention']})

        written = json.loads((reviews_dir / 'ada' / 'ada-reviews.json').read_text())
        assert written == {
            'reviewer': 'Ada Lovelace',
            'reviews': {'10.1002/acn3.70285': {'000768': {'call': 'reuse'}}}}

    def test_a_paper_holds_one_record_per_dataset_it_was_reviewed_against(
            self, session):
        url, reviews_dir = session

        post_save(url, {'reviewer': 'Ada Lovelace', 'reviews': {'10.1/citer': {
            '000541': {'call': 'reuse'},
            '000714': {'call': 'mention', 'note': 'Cited for the method.'},
        }}})

        written = json.loads((reviews_dir / 'ada' / 'ada-reviews.json').read_text())
        assert written['reviews']['10.1/citer'] == {
            '000541': {'call': 'reuse'},
            '000714': {'call': 'mention', 'note': 'Cited for the method.'}}

    def test_load_returns_an_empty_session_before_anything_is_answered(self, session):
        url, _ = session

        with urllib.request.urlopen(f'{url}/load') as response:
            assert json.loads(response.read()) == {
                'reviewer': 'Ada Lovelace', 'reviews': {}}

    def test_load_returns_what_save_wrote(self, session):
        url, _ = session
        reviews = {'10.1/citer': {'000541': {
            'call': 'mention', 'note': 'Cited for the method, not the data.'}}}

        post_save(url, {'reviewer': 'Ada Lovelace', 'reviews': reviews})

        with urllib.request.urlopen(f'{url}/load') as response:
            assert json.loads(response.read()) == {
                'reviewer': 'Ada Lovelace', 'reviews': reviews}

    def test_serves_the_page_at_the_root(self, session):
        url, _ = session

        with urllib.request.urlopen(f'{url}/') as response:
            assert response.read() == b'<title>page</title>'


class TestMarkQuotes:
    def test_marks_the_passage_where_it_stands_in_the_text(self):
        marked = R.mark_quotes('The paper says we reused it, plainly.',
                               ['we reused it'])

        assert marked == 'The paper says <mark>we reused it</mark>, plainly.'

    def test_leaves_a_quote_that_is_not_there_verbatim_unmarked(self):
        marked = R.mark_quotes('The paper says we reused it.', ['We  reused it'])

        assert marked == 'The paper says we reused it.'

    def test_marks_every_quoted_passage(self):
        marked = R.mark_quotes('first here, second there', ['first', 'second'])

        assert marked == '<mark>first</mark> here, <mark>second</mark> there'

    def test_escapes_the_paper_around_the_marks(self):
        marked = R.mark_quotes('in <i>Mus musculus</i> we reused it', ['we reused it'])

        assert marked == ('in &lt;i&gt;Mus musculus&lt;/i&gt; '
                          '<mark>we reused it</mark>')


class TestAttachPaperTexts:
    def test_says_which_papers_the_fetched_text_is_on_hand_for(self, paper_cache):
        rows = [{'doi': '10.1/citer', 'cited_doi': '10.1/never-fetched'},
                {'doi': '10.1/never-fetched', 'cited_doi': '10.1/citer'}]

        R.attach_paper_texts(rows, paper_cache)

        assert [(r['has_text'], r['cited_has_text']) for r in rows] == [
            (True, False), (False, True)]

    def test_a_direct_row_is_asked_only_about_its_own_paper(self, paper_cache):
        rows = [{'doi': '10.1/citer'}]

        R.attach_paper_texts(rows, paper_cache)

        assert rows[0] == {'doi': '10.1/citer', 'has_text': True}


class TestServedFullText:
    def test_serves_the_text_the_classification_was_made_from(self, session):
        url, _ = session

        with urllib.request.urlopen(f'{url}/text?doi=10.1%2Fciter') as response:
            page = response.read().decode()

        assert 'We reanalysed the recordings' in page
        assert 'europe_pmc' in page

    def test_marks_the_passage_this_pair_was_quoted_on(self, session):
        url, _ = session

        with urllib.request.urlopen(
                f'{url}/text?doi=10.1%2Fciter&dandiset=000541') as response:
            page = response.read().decode()

        assert '<mark>reanalysed the recordings</mark>' in page

    def test_marks_nothing_for_a_pair_with_no_quote_of_its_own(self, session):
        url, _ = session

        with urllib.request.urlopen(
                f'{url}/text?doi=10.1%2Fciter&dandiset=000714') as response:
            page = response.read().decode()

        assert '<mark>' not in page

    def test_says_so_for_a_paper_the_cache_never_held(self, session):
        url, _ = session

        with urllib.request.urlopen(f'{url}/text?doi=10.1%2Funfetched') as response:
            page = response.read().decode()

        assert 'No text for this paper is in the cache.' in page


class TestLoadAssignment:
    @pytest.fixture
    def candidates(self, tmp_path):
        path = tmp_path / 'reuse_candidates.json'
        path.write_text(json.dumps({'generated_at': 'STAMP', 'pairs': [
            {'doi': '10.1/b', 'dandiset': '000541', 'pathway': 'indirect',
             'title': 'The second paper'},
            {'doi': '10.1/a', 'dandiset': '000714', 'pathway': 'direct',
             'title': 'The first paper'},
            {'doi': '10.1/a', 'dandiset': '000541', 'pathway': 'indirect',
             'title': 'The first paper'},
        ]}))
        return path

    @pytest.fixture
    def assignment(self, tmp_path):
        path = tmp_path / 'rly.indirect.json'
        path.write_text(json.dumps({
            'reviewer': 'rly', 'pathway': 'indirect',
            'pairs': {'10.1/b': ['000541'], '10.1/a': ['000541']},
        }))
        return path

    def answered(self, tmp_path, reviews):
        path = tmp_path / 'rly' / 'rly-reviews.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({'reviewer': 'rly', 'reviews': reviews}))

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
        assert ('10.1/a', '000714') not in [(r['doi'], r['dandiset']) for r in rows]

    def test_orders_the_pairs_by_paper_then_dataset(self, assignment, candidates):
        rows, _, _ = R.load_assignment(assignment, candidates)
        assert [(r['doi'], r['dandiset']) for r in rows] == [
            ('10.1/a', '000541'), ('10.1/b', '000541')]

    def test_shows_a_pair_answered_before_it_was_ever_assigned(
            self, assignment, candidates, tmp_path):
        self.answered(tmp_path, {'10.1/c': {'000541': {'call': 'reuse'}}})
        candidates.write_text(json.dumps({'pairs': [
            *json.loads(candidates.read_text())['pairs'],
            {'doi': '10.1/c', 'dandiset': '000541', 'pathway': 'indirect',
             'title': 'Answered but never assigned'},
        ]}))
        rows, _, _ = R.load_assignment(assignment, candidates, tmp_path)
        assert ('10.1/c', '000541') in [(r['doi'], r['dandiset']) for r in rows]

    def test_leaves_out_an_answer_belonging_to_the_other_queue(
            self, assignment, candidates, tmp_path):
        self.answered(tmp_path, {'10.1/a': {'000714': {'call': 'reuse'}}})
        rows, _, _ = R.load_assignment(assignment, candidates, tmp_path)
        assert ('10.1/a', '000714') not in [(r['doi'], r['dandiset']) for r in rows]

    def test_leaves_out_an_answer_the_candidate_list_no_longer_describes(
            self, assignment, candidates, tmp_path):
        self.answered(tmp_path, {'10.1/dropped': {'000541': {'call': 'reuse'}}})
        rows, _, _ = R.load_assignment(assignment, candidates, tmp_path)
        assert '10.1/dropped' not in [r['doi'] for r in rows]

    def test_counts_an_answered_pair_once_when_it_is_also_assigned(
            self, assignment, candidates, tmp_path):
        self.answered(tmp_path, {'10.1/a': {'000541': {'call': 'reuse'}}})
        rows, _, _ = R.load_assignment(assignment, candidates, tmp_path)
        assert [(r['doi'], r['dandiset']) for r in rows].count(
            ('10.1/a', '000541')) == 1

    def test_an_assigned_pair_the_candidate_list_lacks_stops_the_session(
            self, tmp_path, candidates):
        path = tmp_path / 'rly.indirect.json'
        path.write_text(json.dumps({'reviewer': 'rly', 'pathway': 'indirect',
                                    'pairs': {'10.1/gone': ['000541']}}))
        with pytest.raises(SystemExit) as excinfo:
            R.load_assignment(path, candidates)
        assert '10.1/gone' in str(excinfo.value)
