"""Tests for the review worksheet, its assignment, and the answers it records."""

import json
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import src.review.assign_reviews as A
import src.review.run_review as R


class TestLabels:
    def test_only_the_direct_queue_can_answer_primary(self):
        assert 'primary' in R.LABELS['direct']
        assert 'primary' not in R.LABELS['indirect']

    def test_only_the_indirect_queue_can_answer_mention(self):
        assert 'mention' in R.LABELS['indirect']
        assert 'mention' not in R.LABELS['direct']

    def test_the_page_carries_the_labels_of_every_pathway(self):
        row = {'doi': 'd', 'title': 't', 'dandiset': '000001',
               'dandiset_name': 'n', 'reasoning': 'r', 'quotes': []}

        page = R.build([row], 'Ada')

        assert ('const LABELS = {"direct": ["reuse", "ambiguous_reuse", '
                '"primary", "neither", "unsure"], "indirect": ["reuse", '
                '"ambiguous_reuse", "mention", "neither", "unsure"], '
                '"added": ["reuse"]}') in page

    def test_a_pair_is_offered_the_labels_of_its_own_pathway(self):
        row = {'doi': 'd', 'title': 't', 'dandiset': '000001',
               'dandiset_name': 'n', 'reasoning': 'r', 'quotes': []}

        assert 'LABELS[r.pathway]' in R.build([row], 'Ada')

    def test_either_queue_can_answer_ambiguous_reuse(self):
        assert 'ambiguous_reuse' in R.LABELS['direct']
        assert 'ambiguous_reuse' in R.LABELS['indirect']


PAPER_TEXT = ('Methods\n\nWe reanalysed the recordings of <i>Mus musculus</i> '
              'deposited by the original authors.\n')


@pytest.fixture
def paper_cache(tmp_path):
    """A text cache holding the paper as the classification run fetched it."""
    cache_dir = tmp_path / 'paper_cache'
    R.TextCache(cache_dir).put('10.1/citer', PAPER_TEXT, 'europe_pmc', True)
    return cache_dir


# Two dandisets and no more, so that an identifier the archive does not know is
# something the tests can ask about. 000128 is published under one name and
# drafted under another, which is how the two are told apart.
ARCHIVED = {
    '000128': {'most_recent_published_version': {'name': 'MC_Maze'},
               'draft_version': {'name': 'MC_Maze, in progress'}},
    '000999': {'most_recent_published_version': None,
               'draft_version': {'name': 'Never published'}},
}


@pytest.fixture
def dandi_api():
    """A stand-in for the DANDI API, so no test reaches the network."""

    class Archive(BaseHTTPRequestHandler):
        def do_GET(self):
            dandiset = ARCHIVED.get(self.path.strip('/').split('/')[-1])
            body = json.dumps(dandiset or {'detail': 'Not found.'}).encode()
            self.send_response(200 if dandiset else 404)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            """Quiet, the way the review server's own log is."""

    server = ThreadingHTTPServer(('127.0.0.1', 0), Archive)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f'http://127.0.0.1:{server.server_address[1]}'
    server.shutdown()
    server.server_close()


@pytest.fixture
def session(tmp_path, paper_cache, dandi_api):
    """A running review server, with the directory its answers land in."""
    reviews_dir = tmp_path / 'reuse_confirmation'
    handler = R.make_handler('<title>page</title>', 'Ada Lovelace',
                             reviews_dir / 'ada' / 'ada-reviews.json', paper_cache,
                             {('10.1/citer', '000541'): ['reanalysed the recordings']},
                             dandi_api)
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


def get_json(url):
    """A JSON answer and the status it came with, refusals included."""
    try:
        with urllib.request.urlopen(url) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as refusal:
        return refusal.code, json.loads(refusal.read())


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
        rows = [{'doi': '10.1/citer', 'fetched_doi': '10.1/citer',
                 'cited_doi': '10.1/never-fetched'},
                {'doi': '10.1/never-fetched', 'fetched_doi': '10.1/never-fetched',
                 'cited_doi': '10.1/citer'}]

        R.attach_paper_texts(rows, paper_cache)

        assert [(r['has_text'], r['cited_has_text']) for r in rows] == [
            (True, False), (False, True)]

    def test_a_direct_row_is_asked_only_about_its_own_paper(self, paper_cache):
        rows = [{'doi': '10.1/citer', 'fetched_doi': '10.1/citer'}]

        R.attach_paper_texts(rows, paper_cache)

        assert rows[0] == {'doi': '10.1/citer', 'fetched_doi': '10.1/citer',
                           'has_text': True}

    def test_finds_a_preprint_under_the_doi_it_was_fetched_under(self, paper_cache):
        R.TextCache(paper_cache).put('10.21203/rs.3.rs-8080516/v1',
                                     PAPER_TEXT, 'crossref+unpaywall', True)
        rows = [{'doi': '10.21203/rs.3.rs-8080516',
                 'fetched_doi': '10.21203/rs.3.rs-8080516/v1'}]

        R.attach_paper_texts(rows, paper_cache)

        assert rows[0]['has_text'] is True


class TestQuotesByPair:
    def test_keyed_by_the_doi_the_card_asks_for_the_text_by(self):
        rows = [{'doi': '10.21203/rs.3.rs-8080516', 'dandiset': '000776',
                 'fetched_doi': '10.21203/rs.3.rs-8080516/v1',
                 'quotes': [{'q': 'the passage', 'tier': 'exact'}]}]

        assert R.quotes_by_pair(rows) == {
            ('10.21203/rs.3.rs-8080516/v1', '000776'): ['the passage']}


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


class TestAllPairs:
    @pytest.fixture
    def candidates(self, tmp_path):
        path = tmp_path / 'reuse_candidates.json'
        path.write_text(json.dumps({'generated_at': 'STAMP', 'pairs': [
            {'doi': '10.1/b', 'dandiset': '000541', 'pathway': 'indirect',
             'title': 'The second paper'},
            {'doi': '10.1/a', 'dandiset': '000714', 'pathway': 'direct',
             'title': 'The direct paper'},
            {'doi': '10.1/a', 'dandiset': '000541', 'pathway': 'indirect',
             'title': 'The first paper'},
        ]}))
        return path

    def test_holds_every_candidate_of_both_pathways(self, candidates):
        rows = R.all_pairs(candidates)
        assert [r['title'] for r in rows] == [
            'The first paper', 'The direct paper', 'The second paper']

    def test_orders_the_pairs_by_paper_then_dataset(self, candidates):
        rows = R.all_pairs(candidates)
        assert [(r['doi'], r['dandiset']) for r in rows] == [
            ('10.1/a', '000541'), ('10.1/a', '000714'), ('10.1/b', '000541')]

    def test_a_papers_two_pathways_stand_together(self, candidates):
        rows = R.all_pairs(candidates)
        assert [r['pathway'] for r in rows] == ['indirect', 'direct', 'indirect']


class TestReadAssignment:
    @pytest.fixture
    def assignment(self, tmp_path):
        path = tmp_path / 'rly-assignment-indirect.json'
        path.write_text(json.dumps({
            'reviewer': 'rly', 'pathway': 'indirect',
            'pairs': {'10.1/b': ['000541'], '10.1/a': ['000541', '000714']},
        }))
        return path

    def test_reads_whose_queue_it_is(self, assignment):
        _, reviewer = R.read_assignment(assignment)
        assert reviewer == 'rly'

    def test_flattens_the_pairs_it_names(self, assignment):
        pairs, _ = R.read_assignment(assignment)
        assert sorted(pairs) == [('10.1/a', '000541'), ('10.1/a', '000714'),
                                 ('10.1/b', '000541')]


class TestAssignmentPairs:
    """A session covers both pathways, so it opens both of a reviewer's queues."""

    @pytest.fixture
    def base(self, tmp_path):
        queues = {
            ('rly', 'indirect'): {'10.1/b': ['000541']},
            ('rly', 'direct'): {'10.1/a': ['000714']},
            ('ada', 'indirect'): {'10.1/c': ['000128']},
        }
        for (reviewer, pathway), pairs in queues.items():
            path = tmp_path / reviewer / f'{reviewer}-assignment-{pathway}.json'
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(
                {'reviewer': reviewer, 'pathway': pathway, 'pairs': pairs}))
        return tmp_path

    def test_opens_both_of_a_reviewers_queues(self, base):
        assert sorted(R.assignment_pairs('rly', base)) == [
            ('10.1/a', '000714'), ('10.1/b', '000541')]

    def test_a_reviewer_dealt_only_one_queue_gets_that_one(self, base):
        assert R.assignment_pairs('ada', base) == [('10.1/c', '000128')]

    def test_a_reviewer_dealt_nothing_has_no_assignment(self, base):
        assert R.assignment_pairs('grace', base) is None

    def test_refuses_a_queue_that_says_it_is_somebody_elses(self, base):
        path = base / 'rly' / 'rly-assignment-direct.json'
        path.write_text(json.dumps(
            {'reviewer': 'ada', 'pathway': 'direct', 'pairs': {'10.1/c': ['000128']}}))

        with pytest.raises(SystemExit):
            R.assignment_pairs('rly', base)


def row(doi='10.1/a', dandiset='000541', pathway='indirect'):
    """A pair as the page receives it, with only the fields the page reads."""
    return {'doi': doi, 'dandiset': dandiset, 'pathway': pathway, 'title': 't',
            'dandiset_name': 'n', 'reasoning': 'r', 'quotes': []}


class TestCardReachesThePaper:
    """
    The citing paper is opened and read by the DOI its text was fetched under.

    `doi` is collapsed onto the work a preprint's versions share, and for
    Research Square nothing is minted under that collapsed form, so a card
    built on it links to a 404 and asks the cache for text it will not find.
    """

    def test_the_card_names_the_paper_by_the_doi_it_was_fetched_under(self):
        page = R.build([row()], 'rly')
        assert "paperPanel('Citing Paper', r.fetched_doi" in page

    def test_the_raw_text_link_asks_for_that_same_doi(self):
        page = R.build([row()], 'rly')
        assert 'textLink(r.fetched_doi, r.dandiset)' in page

    def test_the_overview_shows_that_same_doi(self):
        page = R.build([row()], 'rly')
        assert '<span class="doitext">${esc(r.fetched_doi)}</span>' in page


class TestScope:
    def test_without_an_assignment_the_page_has_no_whose_filter(self):
        page = R.build([row()], 'rly')
        assert 'const MINE = null' in page
        assert 'data-value="mine"' not in page

    def test_an_assignment_adds_the_filter_without_removing_a_pair(self):
        rows = [row('10.1/a'), row('10.1/b')]
        page = R.build(rows, 'rly', [('10.1/a', '000541')])
        assert 'data-control="scope" data-value="mine"' in page
        assert 'data-control="scope" data-value="candidates"' in page
        # Both pairs are still aboard; the filter narrows what is shown.
        assert '10.1/b' in page

    def test_an_empty_list_says_which_control_emptied_it(self):
        page = R.build([row()], 'rly', [('10.1/a', '000541')])
        assert r'No ${pathway}pairs are assigned to you \u2014 try All Candidates.' in page
        assert 'Nothing called ${callWords(controls.filter)}' in page

    def test_the_assigned_pairs_reach_the_page_as_a_lookup(self):
        page = R.build([row()], 'rly', [('10.1/a', '000541')])
        assert 'const MINE = new Set(["10.1/a\\t000541"])' in page


class TestOpeningPathway:
    """Both pathways are always aboard, and the session opens on both."""

    def test_the_page_opens_on_both(self):
        page = R.build([row()], 'rly')
        assert 'data-control="pathway" data-value="all" aria-pressed="true"' in page
        assert 'data-value="direct" aria-pressed="false"' in page

    def test_both_pathways_are_aboard(self):
        rows = [row('10.1/a', pathway='indirect'),
                row('10.1/b', pathway='direct')]

        page = R.build(rows, 'rly')

        assert '10.1/a' in page and '10.1/b' in page

    def test_a_pair_carries_its_own_pathway_to_the_page(self):
        rows = [row('10.1/a', pathway='indirect'),
                row('10.1/b', pathway='direct')]

        page = R.build(rows, 'rly')

        assert '"pathway": "indirect"' in page
        assert '"pathway": "direct"' in page


class TestOverview:
    """The second pass: the pairs laid out in groups rather than one at a time."""

    def test_the_page_offers_both_views(self):
        page = R.build([row()], 'rly')
        assert 'data-view="worksheet"' in page
        assert 'data-view="overview"' in page

    def test_every_label_a_pair_can_be_given_can_be_filtered_to(self):
        page = R.build([row()], 'rly')

        for label in ['reuse', 'mention', 'primary', 'neither', 'unsure']:
            assert f'data-control="filter" data-value="{label}"' in page

    def test_the_review_state_filters_share_the_group_with_the_labels(self):
        page = R.build([row()], 'rly')

        for state in ['all', 'todo', 'done']:
            assert f'data-control="filter" data-value="{state}"' in page

    def test_a_label_chip_says_which_pathways_can_produce_it(self):
        page = R.build([row()], 'rly')
        assert ('data-value="primary" aria-pressed="false" '
                'data-pathways="direct"') in page
        assert ('data-value="reuse" aria-pressed="false" '
                'data-pathways="direct indirect added"') in page

    def test_the_pairs_can_be_gathered_from_either_end_of_a_pair(self):
        page = R.build([row()], 'rly')
        assert 'data-control="grouping" data-value="dandiset"' in page
        assert 'data-control="grouping" data-value="paper"' in page
        assert 'data-control="grouping" data-value="none"' in page

    def test_the_grouping_says_which_end_of_the_pair_the_paper_is(self):
        assert '>By Citing Paper</button>' in R.build([row()], 'rly')

    def test_a_group_can_be_folded_shut(self):
        page = R.build([row()], 'rly')
        assert 'id="foldall"' in page
        assert 'shut.has(shutKey(group.id))' in page

    def test_a_call_can_be_taken_back(self):
        page = R.build([row()], 'rly')
        assert 'id="undo"' in page
        assert "undoStack.push({row: r, call: callFor(r), view, index})" in page

    def test_nothing_answered_yet_is_nothing_to_take_back(self):
        assert '<button class="btn" id="undo" disabled>' in R.build([row()], 'rly')

    def test_the_page_offers_a_search(self):
        page = R.build([row()], 'rly')
        assert 'id="search"' in page


class TestCitedPaperOrigin:
    """The chip is drawn in the browser, so what the page can be held to is
    that the field reaches it and that it carries the wording to draw."""

    def row(self, **overrides):
        record = {'doi': '10.1/citer', 'dandiset': '000541', 'title': 't',
                  'dandiset_name': 'n', 'reasoning': 'r', 'quotes': [],
                  'cited_doi': '10.1/guessed', 'cited_title': 'What a model picked',
                  'cited_role': 'Cited', 'cited_source': 'llm_identified'}
        record.update(overrides)
        return record

    def test_the_page_carries_how_each_dataset_named_its_paper(self):
        page = R.build([self.row()], 'rly')
        assert '"cited_source": "llm_identified"' in page

    def test_every_origin_a_pair_can_carry_has_wording_on_the_page(self):
        page = R.build([self.row()], 'rly')
        labels = page.split('const originLabel')[1].split('}[o]')[0]
        for origin in A.PAPER_LINKS:
            assert origin in labels

    def test_a_candidate_list_built_before_the_field_existed_still_loads(self):
        page = R.build([{'doi': '10.1/citer', 'dandiset': '000541', 'title': 't',
                         'dandiset_name': 'n', 'reasoning': 'r', 'quotes': []}],
                       'rly')
        assert 'cited_source' not in page.split('const REVIEWER')[0]


class TestSharedPaper:
    """Like the origin chip, the flag is drawn in the browser: what the page can
    be held to is that the siblings reach it and that it carries the wording."""

    def row(self, **overrides):
        record = {'doi': '10.1/citer', 'dandiset': '000128', 'title': 't',
                  'dandiset_name': 'MC_Maze', 'reasoning': 'r', 'quotes': [],
                  'pathway': 'indirect',
                  'cited_doi': '10.1/nature11129', 'cited_title': 'Reaching',
                  'cited_role': 'Cited', 'cited_source': 'dcite:IsDescribedBy',
                  'shared_paper': {
                      'doi': '10.1/nature11129', 'title': 'Reaching',
                      'dandisets': [{'dandiset': '000070',
                                     'dandiset_name': 'Neural population dynamics',
                                     'relation': 'dcite:IsDescribedBy'}]}}
        record.update(overrides)
        return record

    def test_the_page_carries_the_datasets_sharing_the_paper(self):
        page = R.build([self.row()], 'rly')
        assert '"dandiset": "000070"' in page
        assert 'Neural population dynamics' in page

    def test_the_page_carries_the_wording_the_flag_is_drawn_with(self):
        page = R.build([self.row()], 'rly')
        assert 'shared with ' in page
        assert 'Dandisets Sharing This Paper' in page

    def test_the_direct_queue_flags_it_too(self):
        page = R.build([self.row(pathway='direct', cited_doi='', cited_title='',
                                 cited_role='', cited_source='')], 'rly')
        assert '"dandiset": "000070"' in page

    def test_a_candidate_list_built_before_the_field_existed_still_loads(self):
        page = R.build([{'doi': '10.1/citer', 'dandiset': '000541', 'title': 't',
                         'dandiset_name': 'n', 'reasoning': 'r', 'quotes': []}],
                       'rly')
        assert 'shared_paper' not in page.split('const REVIEWER')[0]


class TestRoundPairs:
    """
    Which pairs a round reached, which is what marks the rest as pairs nobody
    was asked about. Rounds are cut with filters, so most of the candidate list
    was never dealt out, and those pairs stand in a paper's datasets looking
    exactly like the ones somebody owes an answer on.
    """

    @pytest.fixture
    def base(self, tmp_path):
        for reviewer, pathway, pairs in [
                ('rly', 'indirect', {'10.1/a': ['000541']}),
                ('rly', 'direct', {'10.1/a': ['000714']}),
                ('ada', 'indirect', {'10.1/c': ['000128']})]:
            path = tmp_path / reviewer / f'{reviewer}-assignment-{pathway}.json'
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(
                {'reviewer': reviewer, 'pathway': pathway, 'pairs': pairs}))
        return tmp_path

    def test_gathers_the_queues_of_every_reviewer(self, base):
        assert R.round_pairs(base) == {
            ('10.1/a', '000541'), ('10.1/a', '000714'), ('10.1/c', '000128')}

    def test_a_pair_already_answered_is_one_the_round_reached(self, base):
        """
        Reviewing a pair takes it out of the queue it came from, so the queues
        alone would call an answered pair one nobody was asked about.
        """
        path = base / 'ada' / 'ada-reviews.json'
        path.write_text(json.dumps({'reviewer': 'ada', 'reviews': {
            '10.1/b': {'000999': {'call': 'mention'}}}}))

        assert ('10.1/b', '000999') in R.round_pairs(base)

    def test_a_tree_with_no_queues_and_no_reviews_says_nothing(self, tmp_path):
        """The page reads None as unmarked, where an empty set marks every pair."""
        assert R.round_pairs(tmp_path) is None


class TestPairsNobodyWasAsked:
    def test_the_pairs_a_round_reached_go_to_the_page(self):
        page = R.build([row()], 'rly', dealt={('10.1/a', '000541')})
        assert 'const DEALT = new Set(["10.1/a\\t000541"])' in page

    def test_a_session_with_no_queues_marks_nothing(self):
        """
        Saying nothing is the safe answer: a session that cannot see the queues
        would otherwise put the mark on every pair there is.
        """
        assert 'const DEALT = null' in R.build([row()], 'rly')


class TestDandisetId:
    """What the add box takes, which is an identifier and nothing else."""

    def test_takes_the_identifier_as_it_is_written(self):
        assert R.dandiset_id('000128') == '000128'

    def test_drops_the_space_around_a_pasted_identifier(self):
        assert R.dandiset_id('  000128  ') == '000128'

    @pytest.mark.parametrize('typed', [
        '128',                                        # leading zeros dropped
        '0001289',                                    # a digit too many
        '10.48324/dandi.000128/0.220113.0400',        # the dandiset's own DOI
        'https://dandiarchive.org/dandiset/000128',   # its page
        '10.1002/acn3.70285',                         # a paper's DOI
        'MC_Maze',
        '',
    ])
    def test_what_is_not_six_digits_names_no_dandiset(self, typed):
        """
        Six digits read out of a longer string are a guess at what was meant,
        and a wrong guess can name a real dandiset and be added with nothing
        looking amiss.
        """
        assert R.dandiset_id(typed) == ''


class TestFetchDandiset:
    """
    What DANDI calls a dataset, which is what a call is made against. Six digits
    are not something a reviewer can check a paper's claim about.
    """

    def test_names_a_dandiset_by_its_published_version(self, dandi_api):
        assert R.fetch_dandiset('000128', dandi_api) == {
            'dandiset': '000128', 'name': 'MC_Maze'}

    def test_falls_back_to_the_draft_where_nothing_is_published(self, dandi_api):
        assert R.fetch_dandiset('000999', dandi_api) == {
            'dandiset': '000999', 'name': 'Never published'}

    def test_an_identifier_the_archive_does_not_know_is_an_error(self, dandi_api):
        found = R.fetch_dandiset('000001', dandi_api)
        assert 'name' not in found
        assert '000001' in found['error']


class TestAddingADandiset:
    """
    The lookup behind the box that puts a dataset the pipeline missed onto a
    paper. A typo has to read as a typo rather than as a dataset with no name.
    """

    def test_names_the_dandiset_the_reviewer_typed(self, session):
        url, _ = session

        assert get_json(f'{url}/dandiset?id=000128') == (
            200, {'dandiset': '000128', 'name': 'MC_Maze'})

    def test_refuses_something_that_is_not_an_identifier(self, session):
        url, _ = session

        status, body = get_json(f'{url}/dandiset?id=MC_Maze')

        assert status == 400
        assert 'six digits' in body['error']

    def test_says_so_for_an_identifier_dandi_does_not_know(self, session):
        url, _ = session

        status, body = get_json(f'{url}/dandiset?id=000001')

        assert status == 404
        assert '000001' in body['error']


class TestAddedPairs:
    """
    The pairs a reviewer found by reading the paper, which no classifier
    proposed. They are as irreplaceable as the reviews, so they are kept beside
    them in the one file a session writes.
    """

    def test_save_keeps_the_pairs_the_reviewer_added(self, session):
        url, reviews_dir = session

        post_save(url, {
            'reviewer': 'Ada Lovelace',
            'reviews': {'10.1/citer': {'000128': {'call': 'reuse'}}},
            'added': {'10.1/citer': {'000128': {'dandiset_name': 'MC_Maze'}}}})

        written = json.loads((reviews_dir / 'ada' / 'ada-reviews.json').read_text())
        assert written['added'] == {
            '10.1/citer': {'000128': {'dandiset_name': 'MC_Maze'}}}

    def test_a_reviewer_who_added_nothing_gets_a_file_that_says_nothing(
            self, session):
        url, reviews_dir = session

        post_save(url, {'reviewer': 'Ada Lovelace', 'added': {},
                        'reviews': {'10.1/citer': {'000541': {'call': 'reuse'}}}})

        written = json.loads((reviews_dir / 'ada' / 'ada-reviews.json').read_text())
        assert 'added' not in written

    def test_load_returns_the_added_pairs_alongside_the_reviews(self, session):
        url, _ = session
        added = {'10.1/citer': {'000128': {'dandiset_name': 'MC_Maze'}}}

        post_save(url, {'reviewer': 'Ada Lovelace', 'reviews': {}, 'added': added})

        with urllib.request.urlopen(f'{url}/load') as response:
            assert json.loads(response.read())['added'] == added


class TestAddedPathway:
    """
    A pair the reviewer put on the list. Adding one says the paper reused that
    dataset, so reuse is the call it carries and the only one it is offered:
    the box is there to catch reuse, and the other calls are a separate job.
    """

    def test_the_worksheet_can_take_an_added_pair_off_the_list(self):
        """
        The call stands as long as the pair does, so removing the pair is the
        only way back from a mistyped identifier, and the worksheet is where a
        reviewer is when they read the pair and see it is wrong.
        """
        assert '\\u00d7 Remove Pair' in R.build([row()], 'rly')

    def test_the_session_can_be_narrowed_to_the_pairs_you_added(self):
        assert 'data-control="pathway" data-value="added"' in R.build([row()], 'rly')

    def test_no_round_is_ever_dealt_on_the_added_pathway(self, tmp_path):
        """
        Queues are cut from what the classifier proposed, and an added pair is
        on the list because a person put it there.
        """
        path = tmp_path / 'rly' / 'rly-assignment-added.json'
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(
            {'reviewer': 'rly', 'pathway': 'added', 'pairs': {'10.1/a': ['000128']}}))

        assert R.assignment_pairs('rly', tmp_path) is None

    def test_the_worksheet_offers_the_way_into_the_papers_datasets(self):
        assert 'id="addcited"' in R.build([row()], 'rly')

    def test_an_added_pair_can_be_taken_back_from_its_row(self):
        assert 'title="Remove this pair"' in R.build([row()], 'rly')


class TestNotesInTheList:
    def test_a_note_is_written_where_it_is_read(self):
        r"""
        The overview is where a second pass happens, and a call made there that
        needs saying why should not send the reviewer to another screen to say
        it.
        """
        page = R.build([row()], 'rly')
        assert 'placeholder="Note \\u2014 optional"' in page
