"""Tests for the review worksheet, its assignment, and the answers it records."""

import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer

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

    def test_the_page_carries_the_labels_of_both_pathways(self):
        row = {'doi': 'd', 'title': 't', 'dandiset': '000001',
               'dandiset_name': 'n', 'reasoning': 'r', 'quotes': []}

        page = R.build([row], 'Ada')

        assert ('const LABELS = {"direct": ["reuse", "ambiguous_reuse", '
                '"primary", "neither", "unsure"], "indirect": ["reuse", '
                '"ambiguous_reuse", "mention", "neither", "unsure"]}') in page

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

    def test_every_pair_offers_the_doi_its_text_was_fetched_under(self, tmp_path):
        path = tmp_path / 'reuse_candidates.json'
        path.write_text(json.dumps({'generated_at': 'STAMP', 'pairs': [
            {'doi': '10.21203/rs.3.rs-8080516', 'dandiset': '000776',
             'pathway': 'indirect',
             'fetched_doi': '10.21203/rs.3.rs-8080516/v1'},
            {'doi': '10.1/unversioned', 'dandiset': '000541',
             'pathway': 'indirect'},
        ]}))

        assert [r['fetched_doi'] for r in R.all_pairs(path)] == [
            '10.1/unversioned', '10.21203/rs.3.rs-8080516/v1']


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


class TestScope:
    def test_without_an_assignment_the_page_has_no_whose_filter(self):
        page = R.build([row()], 'rly')
        assert 'const MINE = null' in page
        assert 'data-value="mine"' not in page

    def test_an_assignment_adds_the_filter_without_removing_a_pair(self):
        rows = [row('10.1/a'), row('10.1/b')]
        page = R.build(rows, 'rly', [('10.1/a', '000541')])
        assert 'data-control="scope" data-value="mine"' in page
        assert 'data-control="scope" data-value="everyone"' in page
        # Both pairs are still aboard; the filter narrows what is shown.
        assert '10.1/b' in page

    def test_an_empty_list_says_which_control_emptied_it(self):
        page = R.build([row()], 'rly', [('10.1/a', '000541')])
        assert r'No ${pathway}pairs are assigned to you \u2014 try Everyone.' in page
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
                'data-pathways="direct indirect"') in page

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
