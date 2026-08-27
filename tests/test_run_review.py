"""Tests for the reuse review worksheet and the answers it records."""

import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

import src.review.run_review as R


def classification(citing_doi, dandiset_id, quote, **overrides):
    record = {
        'classification': 'REUSE',
        'mode': 'citing',
        'citing_doi': citing_doi,
        'dandiset_id': dandiset_id,
        'title': 'One paper, several datasets',
        'reasoning': 'Reanalysed the deposited recordings.',
        'evidence_quotes': [{'quote': quote, 'match_type': 'exact'}],
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
def both_pathway_input(tmp_path):
    """One pair found only by citing, one only by direct, one by both."""
    path = tmp_path / 'classifications.json'
    path.write_text(json.dumps({'classifications': [
        classification('10.1/citer', '000541', 'the citing passage'),
        classification('10.1/citer', '000714', 'the direct passage', mode='direct'),
        classification('10.1/citer', '000953', 'the citing passage'),
        classification('10.1/citer', '000953', 'the direct passage', mode='direct'),
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
        merged = R.merge_by_pair([four_dataset_input])

        assert sorted(merged) == [('10.1/citer', '000541'), ('10.1/citer', '000714'),
                                  ('10.1/citer', '000953'), ('10.1/citer', '000970')]
        row = merged[('10.1/citer', '000714')]
        assert (row['doi'], row['dandiset']) == ('10.1/citer', '000714')

    def test_each_pair_keeps_only_its_own_quote(self, four_dataset_input):
        merged = R.merge_by_pair([four_dataset_input])

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

        assert sorted(R.merge_by_pair([str(path)])) == [('10.1/citer', '000541')]


class TestAttachCitedPapers:
    def test_names_the_paper_the_pair_was_built_from(self, corpus):
        rows = [{'doi': '10.1/citer', 'dandiset': '000541'}]

        R.attach_cited_papers(rows, corpus)

        assert rows[0]['cited_doi'] == '10.1/described-by'
        assert rows[0]['cited_title'] == 'The paper the data came from'
        assert rows[0]['cited_role'] == 'Cited'

    def test_offers_the_declared_paper_when_the_pair_cited_none(self, corpus):
        rows = [{'doi': '10.1/citer', 'dandiset': '000714'}]

        R.attach_cited_papers(rows, corpus)

        assert rows[0]['cited_doi'] == '10.1/declared'
        assert rows[0]['cited_title'] == 'The paper describing 000714'
        assert rows[0]['cited_role'] == 'Dataset paper'

    def test_leaves_the_paper_empty_when_the_dataset_declares_none(self, corpus):
        rows = [{'doi': '10.1/citer', 'dandiset': '000953'}]

        R.attach_cited_papers(rows, corpus)

        assert rows[0]['cited_doi'] == ''
        assert rows[0]['cited_title'] == ''


class TestAttachDandisetNames:
    def test_names_the_dataset(self, corpus):
        rows = [{'dandiset': '000541'}, {'dandiset': '000714'}]

        R.attach_dandiset_names(rows, corpus)

        assert [r['dandiset_name'] for r in rows] == [
            'Mouse motor cortex recordings', 'Human intracortical dataset']

    def test_leaves_the_name_empty_for_a_dataset_the_corpus_lacks(self, corpus):
        rows = [{'dandiset': '999999'}]

        R.attach_dandiset_names(rows, corpus)

        assert rows[0]['dandiset_name'] == ''


class TestRowContents:
    def test_an_indirect_row_carries_the_cited_paper(self, four_dataset_input, corpus):
        rows = R.queue_for(R.merge_by_pair([four_dataset_input]), 'indirect')
        R.attach_dandiset_names(rows, corpus)
        R.attach_cited_papers(rows, corpus)

        assert set(rows[0]) == {'doi', 'title', 'cited_doi', 'cited_title',
                                'cited_role', 'dandiset', 'dandiset_name',
                                'reasoning', 'quotes'}

    def test_a_direct_row_carries_no_cited_paper(self, both_pathway_input, corpus):
        rows = R.queue_for(R.merge_by_pair([both_pathway_input]), 'direct')
        R.attach_dandiset_names(rows, corpus)

        assert set(rows[0]) == {'doi', 'title', 'dandiset', 'dandiset_name',
                                'reasoning', 'quotes'}


class TestQueueFor:
    def test_a_pair_only_the_citing_pathway_found_goes_to_indirect(
            self, both_pathway_input):
        merged = R.merge_by_pair([both_pathway_input])

        assert [r['dandiset'] for r in R.queue_for(merged, 'indirect')] == ['000541']

    def test_a_pair_only_the_direct_pathway_found_goes_to_direct(
            self, both_pathway_input):
        merged = R.merge_by_pair([both_pathway_input])

        assert '000714' in [r['dandiset'] for r in R.queue_for(merged, 'direct')]

    def test_a_pair_both_pathways_found_is_reviewed_once_in_direct(
            self, both_pathway_input):
        merged = R.merge_by_pair([both_pathway_input])

        assert '000953' in [r['dandiset'] for r in R.queue_for(merged, 'direct')]
        assert '000953' not in [r['dandiset'] for r in R.queue_for(merged, 'indirect')]

    def test_the_two_queues_partition_the_pairs(self, both_pathway_input):
        merged = R.merge_by_pair([both_pathway_input])
        direct = {(r['doi'], r['dandiset']) for r in R.queue_for(merged, 'direct')}
        indirect = {(r['doi'], r['dandiset']) for r in R.queue_for(merged, 'indirect')}

        assert not direct & indirect
        assert len(direct | indirect) == len(merged)

    def test_a_pair_both_pathways_found_keeps_both_their_quotes(self, both_pathway_input):
        merged = R.merge_by_pair([both_pathway_input])

        both = next(r for r in R.queue_for(merged, 'direct') if r['dandiset'] == '000953')
        assert both['quotes'] == [{'q': 'the citing passage', 'tier': 'exact'},
                                  {'q': 'the direct passage', 'tier': 'exact'}]


class TestAttachMissingTitles:
    @pytest.fixture
    def direct_results(self, tmp_path):
        path = tmp_path / 'results_dandi_openalex.json'
        path.write_text(json.dumps({'results': [
            {'doi': '10.1/UNTITLED', 'title': 'The title discovery kept'},
        ]}))
        return path

    def test_titles_a_paper_the_classification_left_bare(self, direct_results):
        rows = [{'doi': '10.1/untitled', 'title': ''}]

        R.attach_missing_titles(rows, direct_results)

        assert rows[0]['title'] == 'The title discovery kept'

    def test_leaves_a_title_the_classification_already_had(self, direct_results):
        rows = [{'doi': '10.1/untitled', 'title': 'The title it came with'}]

        R.attach_missing_titles(rows, direct_results)

        assert rows[0]['title'] == 'The title it came with'


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
    reviews_dir = tmp_path / 'reviews'
    handler = R.make_handler('<title>page</title>', 'Ada Lovelace',
                             reviews_dir / 'ada-lovelace.json', paper_cache,
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

        written = json.loads((reviews_dir / 'ada-lovelace.json').read_text())
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

        written = json.loads((reviews_dir / 'ada-lovelace.json').read_text())
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


class TestReviewerSlug:
    def test_names_a_file_that_survives_punctuation_and_case(self):
        assert R.reviewer_slug('Paul Adkisson-Floro') == 'paul-adkisson-floro'
