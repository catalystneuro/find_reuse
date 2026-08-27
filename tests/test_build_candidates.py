"""Tests for the list of reuse pairs a person still has to check."""

import json

import pytest

import src.review.build_candidates as B


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


@pytest.fixture
def direct_results(tmp_path):
    path = tmp_path / 'results_dandi_openalex.json'
    path.write_text(json.dumps({'results': [
        {'doi': '10.1/Bare', 'title': 'The title discovery kept'},
    ]}))
    return path


class TestMergeByPair:
    def test_one_row_per_pair_keyed_on_doi_and_dandiset(self, four_dataset_input):
        merged = B.merge_by_pair([four_dataset_input])
        assert sorted(r['key'] for r in merged.values()) == [
            '10.1/citer\t000541', '10.1/citer\t000714',
            '10.1/citer\t000953', '10.1/citer\t000970',
        ]

    def test_each_pair_keeps_only_its_own_quote(self, four_dataset_input):
        merged = B.merge_by_pair([four_dataset_input])
        assert merged[('10.1/citer', '000714')]['quotes'] == [
            {'q': 'passage about 000714', 'tier': 'exact'}]

    def test_pairs_the_classifier_did_not_call_reuse_are_dropped(self, tmp_path):
        path = tmp_path / 'c.json'
        path.write_text(json.dumps({'classifications': [
            classification('10.1/a', '000541', 'kept'),
            classification('10.1/b', '000541', 'dropped', classification='MENTION'),
            classification('10.1/c', '000541', 'dropped', classification='NEITHER'),
        ]}))
        assert list(B.merge_by_pair([str(path)])) == [('10.1/a', '000541')]

    def test_the_fields_assignment_filters_on_survive_the_merge(self, tmp_path):
        path = tmp_path / 'c.json'
        path.write_text(json.dumps({'classifications': [
            classification('10.1/a', '000541', 'kept', confidence=7,
                           same_lab=False, reused_neurophysiology=True,
                           reused_dandi_hosted=True, source_archive='CRCNS',
                           reuse_type='BENCHMARK',
                           reused_modalities=['neurophysiology', 'behavior']),
        ]}))
        row = B.finalize(B.merge_by_pair([str(path)])[('10.1/a', '000541')])
        assert row['confidence'] == 7
        assert row['same_lab'] is False
        assert row['reused_neurophysiology'] is True
        assert row['reused_dandi_hosted'] is True
        assert row['archives'] == ['CRCNS']
        assert row['reuse_types'] == ['BENCHMARK']
        assert row['reused_modalities'] == ['neurophysiology', 'behavior']

    def test_a_pair_takes_the_highest_confidence_of_its_records(self, tmp_path):
        path = tmp_path / 'c.json'
        path.write_text(json.dumps({'classifications': [
            classification('10.1/a', '000541', 'one', confidence=4),
            classification('10.1/a', '000541', 'two', confidence=9, mode='direct'),
        ]}))
        row = B.merge_by_pair([str(path)])[('10.1/a', '000541')]
        assert row['confidence'] == 9

    def test_a_pair_collects_every_archive_its_records_named(self, tmp_path):
        path = tmp_path / 'c.json'
        path.write_text(json.dumps({'classifications': [
            classification('10.1/a', '000541', 'one', source_archive='CRCNS'),
            classification('10.1/a', '000541', 'two', source_archive='DANDI Archive',
                           mode='direct'),
        ]}))
        row = B.merge_by_pair([str(path)])[('10.1/a', '000541')]
        assert row['archives'] == ['CRCNS', 'DANDI Archive']


class TestFinalize:
    def test_a_pair_only_the_citing_pathway_found_is_indirect(self, both_pathway_input):
        merged = B.merge_by_pair([both_pathway_input])
        assert B.finalize(merged[('10.1/citer', '000541')])['pathway'] == 'indirect'

    def test_a_pair_only_the_direct_pathway_found_is_direct(self, both_pathway_input):
        merged = B.merge_by_pair([both_pathway_input])
        assert B.finalize(merged[('10.1/citer', '000714')])['pathway'] == 'direct'

    def test_a_pair_both_pathways_found_is_reviewed_once_in_direct(
            self, both_pathway_input):
        merged = B.merge_by_pair([both_pathway_input])
        assert B.finalize(merged[('10.1/citer', '000953')])['pathway'] == 'direct'

    def test_a_pair_both_pathways_found_keeps_both_their_quotes(
            self, both_pathway_input):
        merged = B.merge_by_pair([both_pathway_input])
        assert merged[('10.1/citer', '000953')]['quotes'] == [
            {'q': 'the citing passage', 'tier': 'exact'},
            {'q': 'the direct passage', 'tier': 'exact'},
        ]

    def test_agreeing_records_settle_same_lab(self, tmp_path):
        path = tmp_path / 'c.json'
        path.write_text(json.dumps({'classifications': [
            classification('10.1/a', '000541', 'one', same_lab=True),
            classification('10.1/a', '000541', 'two', same_lab=True, mode='direct'),
        ]}))
        row = B.finalize(B.merge_by_pair([str(path)])[('10.1/a', '000541')])
        assert row['same_lab'] is True

    def test_disagreeing_records_leave_same_lab_mixed(self, tmp_path):
        path = tmp_path / 'c.json'
        path.write_text(json.dumps({'classifications': [
            classification('10.1/a', '000541', 'one', same_lab=True),
            classification('10.1/a', '000541', 'two', same_lab=False, mode='direct'),
        ]}))
        row = B.finalize(B.merge_by_pair([str(path)])[('10.1/a', '000541')])
        assert row['same_lab'] == 'mixed'

    def test_a_pair_no_record_answered_leaves_same_lab_unset(self, four_dataset_input):
        merged = B.merge_by_pair([four_dataset_input])
        row = B.finalize(merged[('10.1/citer', '000541')])
        assert row['same_lab'] is None

    def test_a_pair_whose_every_quote_is_missing_is_unverifiable(self, tmp_path):
        path = tmp_path / 'c.json'
        path.write_text(json.dumps({'classifications': [
            classification('10.1/a', '000541', 'nowhere', evidence_quotes=[
                {'quote': 'nowhere', 'match_type': 'not_found'},
                {'quote': 'also nowhere', 'match_type': 'not_found'},
            ]),
        ]}))
        row = B.finalize(B.merge_by_pair([str(path)])[('10.1/a', '000541')])
        assert row['unverifiable_quotes'] is True

    def test_one_quote_found_in_the_paper_is_enough_to_verify_a_pair(self, tmp_path):
        path = tmp_path / 'c.json'
        path.write_text(json.dumps({'classifications': [
            classification('10.1/a', '000541', 'x', evidence_quotes=[
                {'quote': 'nowhere', 'match_type': 'not_found'},
                {'quote': 'right here', 'match_type': 'exact'},
            ]),
        ]}))
        row = B.finalize(B.merge_by_pair([str(path)])[('10.1/a', '000541')])
        assert row['unverifiable_quotes'] is False

    def test_a_pair_with_no_quotes_at_all_is_not_called_unverifiable(self, tmp_path):
        path = tmp_path / 'c.json'
        path.write_text(json.dumps({'classifications': [
            classification('10.1/a', '000541', 'x', evidence_quotes=[]),
        ]}))
        row = B.finalize(B.merge_by_pair([str(path)])[('10.1/a', '000541')])
        assert row['unverifiable_quotes'] is False


class TestAttachCitedPapers:
    def test_names_the_paper_the_pair_was_built_from(self, corpus):
        rows = [{'doi': '10.1/citer', 'dandiset': '000541'}]
        B.attach_cited_papers(rows, corpus)
        assert rows[0]['cited_doi'] == '10.1/described-by'
        assert rows[0]['cited_title'] == 'The paper the data came from'
        assert rows[0]['cited_role'] == 'Cited'

    def test_offers_the_declared_paper_when_the_pair_cited_none(self, corpus):
        rows = [{'doi': '10.1/stranger', 'dandiset': '000714'}]
        B.attach_cited_papers(rows, corpus)
        assert rows[0]['cited_doi'] == '10.1/declared'
        assert rows[0]['cited_title'] == 'The paper describing 000714'
        assert rows[0]['cited_role'] == 'Dataset paper'

    def test_leaves_the_paper_empty_when_the_dataset_declares_none(self, corpus):
        rows = [{'doi': '10.1/stranger', 'dandiset': '000953'}]
        B.attach_cited_papers(rows, corpus)
        assert rows[0]['cited_doi'] == ''
        assert rows[0]['cited_title'] == ''


class TestAttachDandisetNames:
    def test_names_the_dataset(self, corpus):
        rows = [{'dandiset': '000541'}, {'dandiset': '000714'}]
        B.attach_dandiset_names(rows, corpus)
        assert [r['dandiset_name'] for r in rows] == [
            'Mouse motor cortex recordings', 'Human intracortical dataset']

    def test_leaves_the_name_empty_for_a_dataset_the_corpus_lacks(self, corpus):
        rows = [{'dandiset': '999999'}]
        B.attach_dandiset_names(rows, corpus)
        assert rows[0]['dandiset_name'] == ''


class TestAttachMissingTitles:
    def test_titles_a_paper_the_classification_left_bare(self, direct_results):
        rows = [{'doi': '10.1/bare', 'title': ''}]
        B.attach_missing_titles(rows, direct_results)
        assert rows[0]['title'] == 'The title discovery kept'

    def test_leaves_a_title_the_classification_already_had(self, direct_results):
        rows = [{'doi': '10.1/bare', 'title': 'What the classifier recorded'}]
        B.attach_missing_titles(rows, direct_results)
        assert rows[0]['title'] == 'What the classifier recorded'


class TestBuildCandidates:
    def test_an_indirect_pair_carries_the_cited_paper(
            self, four_dataset_input, corpus, direct_results):
        pairs = B.build_candidates([four_dataset_input], corpus, direct_results)
        pair = next(p for p in pairs if p['dandiset'] == '000541')
        assert pair['pathway'] == 'indirect'
        assert pair['cited_doi'] == '10.1/described-by'
        assert pair['cited_title'] == 'The paper the data came from'

    def test_a_direct_pair_carries_no_cited_paper(
            self, both_pathway_input, corpus, direct_results):
        pairs = B.build_candidates([both_pathway_input], corpus, direct_results)
        pair = next(p for p in pairs if p['dandiset'] == '000714')
        assert pair['pathway'] == 'direct'
        assert (pair['cited_doi'], pair['cited_title'], pair['cited_role']) == ('', '', '')

    def test_pairs_come_out_sorted_so_a_rerun_diffs_cleanly(
            self, four_dataset_input, corpus, direct_results):
        pairs = B.build_candidates([four_dataset_input], corpus, direct_results)
        assert [p['key'] for p in pairs] == [
            '10.1/citer\t000541', '10.1/citer\t000714',
            '10.1/citer\t000953', '10.1/citer\t000970',
        ]

    def test_no_bookkeeping_from_the_merge_survives_into_a_pair(
            self, four_dataset_input, corpus, direct_results):
        pairs = B.build_candidates([four_dataset_input], corpus, direct_results)
        assert 'pathways' not in pairs[0]
        assert 'same_lab_values' not in pairs[0]


class TestWriteCandidates:
    def test_a_rebuild_that_says_the_same_thing_leaves_the_file_alone(
            self, tmp_path, four_dataset_input):
        out = tmp_path / 'reuse_candidates.json'
        assert B.write_candidates([], [four_dataset_input], out) is True
        before = out.read_bytes()
        assert B.write_candidates([], [four_dataset_input], out) is False
        assert out.read_bytes() == before

    def test_a_rebuild_that_found_new_pairs_rewrites(self, tmp_path,
                                                     four_dataset_input):
        out = tmp_path / 'reuse_candidates.json'
        B.write_candidates([], [four_dataset_input], out)
        assert B.write_candidates(
            [{'key': '10.1/new\t000541'}], [four_dataset_input], out) is True
        assert json.loads(out.read_text())['pairs'] == [{'key': '10.1/new\t000541'}]


    def test_records_what_each_input_held(self, tmp_path, four_dataset_input):
        out = tmp_path / 'reuse_candidates.json'
        B.write_candidates([], [four_dataset_input], out)
        stamp = json.loads(out.read_text())['inputs'][0]
        assert stamp['classifications'] == 4
        assert stamp['reuse'] == 4
        assert len(stamp['sha256']) == 64

    def test_records_the_model_and_the_question_it_was_asked(self, tmp_path):
        path = tmp_path / 'c.json'
        path.write_text(json.dumps({'classifications': [
            classification('10.1/a', '000541', 'kept',
                           model='openai/gpt-5.6-luna', prompt_version=5),
        ]}))
        out = tmp_path / 'reuse_candidates.json'
        B.write_candidates([], [str(path)], out)
        stamp = json.loads(out.read_text())['inputs'][0]
        assert stamp['models'] == ['openai/gpt-5.6-luna']
        assert stamp['prompt_versions'] == [5]

    def test_records_every_label_the_run_reached(self, tmp_path):
        path = tmp_path / 'c.json'
        path.write_text(json.dumps({'classifications': [
            classification('10.1/a', '000541', 'x'),
            classification('10.1/b', '000541', 'x', classification='MENTION'),
            classification('10.1/c', '000541', 'x', classification='NEITHER'),
        ]}))
        out = tmp_path / 'reuse_candidates.json'
        B.write_candidates([], [str(path)], out)
        assert json.loads(out.read_text())['inputs'][0]['labels'] == [
            'MENTION', 'NEITHER', 'REUSE']

    def test_the_labels_tell_the_two_pathways_apart(self, tmp_path):
        path = tmp_path / 'direct.json'
        path.write_text(json.dumps({'classifications': [
            classification('10.1/a', '000541', 'x', mode='direct'),
            classification('10.1/b', '000541', 'x', mode='direct',
                           classification='PRIMARY'),
        ]}))
        out = tmp_path / 'reuse_candidates.json'
        B.write_candidates([], [str(path)], out)
        labels = json.loads(out.read_text())['inputs'][0]['labels']
        assert labels == ['PRIMARY', 'REUSE']
        assert 'MENTION' not in labels

    def test_a_field_no_record_answered_is_left_out_not_nulled(self, tmp_path):
        path = tmp_path / 'c.json'
        path.write_text(json.dumps({'classifications': [
            classification('10.1/a', '000541', 'x', model=None),
        ]}))
        out = tmp_path / 'reuse_candidates.json'
        B.write_candidates([], [str(path)], out)
        assert json.loads(out.read_text())['inputs'][0]['models'] == []

    def test_counts_only_the_reuse_records_as_reuse(self, tmp_path):
        path = tmp_path / 'c.json'
        path.write_text(json.dumps({'classifications': [
            classification('10.1/a', '000541', 'kept'),
            classification('10.1/b', '000541', 'no', classification='MENTION'),
        ]}))
        out = tmp_path / 'reuse_candidates.json'
        B.write_candidates([], [str(path)], out)
        stamp = json.loads(out.read_text())['inputs'][0]
        assert (stamp['classifications'], stamp['reuse']) == (2, 1)
