"""Tests for dealing candidate pairs out to reviewers."""

import argparse
import json

import pytest

import src.review.assign_reviews as A


def pair(doi, dandiset='000541', pathway='indirect', **overrides):
    record = {
        'doi': doi, 'dandiset': dandiset, 'pathway': pathway,
        'confidence': 8, 'same_lab': False,
        'reused_neurophysiology': True, 'reused_dandi_hosted': True,
        'reused_modalities': ['neurophysiology'],
        'archives': ['DANDI Archive'], 'reuse_types': ['NOVEL_ANALYSIS'],
        'unverifiable_quotes': False,
    }
    record.update(overrides)
    return record


def filters(**overrides):
    """The flags with nothing selected, which is what keeps every pair."""
    defaults = {
        'pathway': None, 'dandi_hosted': None, 'neuro': None, 'modality': None,
        'archive': None, 'reuse_type': None, 'lab': 'any',
        'min_confidence': None, 'exclude_unverifiable_quotes': False,
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


@pytest.fixture
def reviewers():
    return [{'name': 'paul'}, {'name': 'rly'}]


@pytest.fixture
def pairs():
    """Six indirect pairs and two direct ones."""
    return ([pair(f'10.1/p{i}') for i in range(6)]
            + [pair(f'10.1/d{i}', '000714', pathway='direct') for i in range(2)])


class TestMatches:
    def test_nothing_selected_keeps_every_pair(self):
        assert A.matches(pair('10.1/a', '000541'), filters()) is True

    def test_pathway_keeps_only_that_queue(self):
        assert A.matches(pair('10.1/a', '000541'), filters(pathway='direct')) is False
        assert A.matches(pair('10.1/a', '000541', pathway='direct'),
                         filters(pathway='direct')) is True

    def test_dandi_hosted_selects_on_where_the_data_lived(self):
        hosted = pair('10.1/a', '000541', reused_dandi_hosted=True)
        elsewhere = pair('10.1/b', '000541', reused_dandi_hosted=False)
        assert A.matches(hosted, filters(dandi_hosted=True)) is True
        assert A.matches(elsewhere, filters(dandi_hosted=True)) is False
        assert A.matches(elsewhere, filters(dandi_hosted=False)) is True

    def test_neuro_selects_on_the_modality_that_was_reused(self):
        neuro = pair('10.1/a', '000541', reused_neurophysiology=True)
        other = pair('10.1/b', '000541', reused_neurophysiology=False)
        assert A.matches(neuro, filters(neuro=True)) is True
        assert A.matches(other, filters(neuro=True)) is False

    def test_modality_keeps_a_pair_reusing_any_of_the_named_ones(self):
        p = pair('10.1/a', '000541', reused_modalities=['behavior', 'transcriptomics'])
        assert A.matches(p, filters(modality=['transcriptomics'])) is True
        assert A.matches(p, filters(modality=['morphology'])) is False

    def test_archive_keeps_a_pair_sourced_from_any_of_the_named_ones(self):
        p = pair('10.1/a', '000541', archives=['CRCNS'])
        assert A.matches(p, filters(archive=['CRCNS', 'Zenodo'])) is True
        assert A.matches(p, filters(archive=['Zenodo'])) is False

    def test_an_archive_matches_however_it_was_spelled(self):
        # The classifier keeps an archive it does not recognise verbatim, so
        # CELLxGENE arrives under a dozen spellings.
        for recorded in ('CELLxGENE', 'cellxgene', 'CZ CELLxGENE Discover',
                         'Chan Zuckerberg CELLxGENE'):
            p = pair('10.1/a', '000541', archives=[recorded])
            assert A.matches(p, filters(archive=['CELLxGENE'])) is True

    def test_an_archive_matches_when_the_record_names_two(self):
        p = pair('10.1/a', '000541', archives=['DANDI Archive and GitHub'])
        assert A.matches(p, filters(archive=['DANDI Archive'])) is True
        assert A.matches(p, filters(archive=['GitHub'])) is True

    def test_a_loose_match_still_excludes_an_unrelated_archive(self):
        p = pair('10.1/a', '000541', archives=['CZ CELLxGENE Discover'])
        assert A.matches(p, filters(archive=['CRCNS'])) is False

    def test_reuse_type_keeps_a_pair_of_any_of_the_named_kinds(self):
        p = pair('10.1/a', '000541', reuse_types=['BENCHMARK'])
        assert A.matches(p, filters(reuse_type=['BENCHMARK'])) is True
        assert A.matches(p, filters(reuse_type=['TOOL_DEMO'])) is False

    def test_lab_different_keeps_a_pair_another_group_reused(self):
        assert A.matches(pair('10.1/a', '000541', same_lab=False),
                         filters(lab='different')) is True
        assert A.matches(pair('10.1/a', '000541', same_lab=True),
                         filters(lab='different')) is False

    def test_lab_same_keeps_a_pair_its_own_lab_reused(self):
        assert A.matches(pair('10.1/a', '000541', same_lab=True),
                         filters(lab='same')) is True
        assert A.matches(pair('10.1/a', '000541', same_lab=False),
                         filters(lab='same')) is False

    def test_a_mixed_pair_satisfies_either_side_of_the_lab_question(self):
        mixed = pair('10.1/a', '000541', same_lab='mixed')
        assert A.matches(mixed, filters(lab='same')) is True
        assert A.matches(mixed, filters(lab='different')) is True

    def test_a_pair_no_record_answered_satisfies_neither_side(self):
        unknown = pair('10.1/a', '000541', same_lab=None)
        assert A.matches(unknown, filters(lab='same')) is False
        assert A.matches(unknown, filters(lab='different')) is False

    def test_min_confidence_drops_the_less_certain(self):
        assert A.matches(pair('10.1/a', '000541', confidence=5),
                         filters(min_confidence=6)) is False
        assert A.matches(pair('10.1/a', '000541', confidence=6),
                         filters(min_confidence=6)) is True

    def test_unverifiable_pairs_can_be_left_out(self):
        p = pair('10.1/a', '000541', unverifiable_quotes=True)
        assert A.matches(p, filters()) is True
        assert A.matches(p, filters(exclude_unverifiable_quotes=True)) is False


class TestDeal:
    def test_every_pair_goes_to_exactly_one_reviewer(self, pairs, reviewers):
        assigned = A.deal(pairs, reviewers, {}, {}, None)
        dealt = [k for keys in assigned.values() for k in keys]
        assert sorted(dealt) == sorted((p['doi'], p['dandiset']) for p in pairs)
        assert len(dealt) == len(set(dealt))

    def test_each_queue_is_split_evenly(self, pairs, reviewers):
        assigned = A.deal(pairs, reviewers, {}, {}, None)
        assert len(assigned[('paul', 'indirect')]) == 3
        assert len(assigned[('rly', 'indirect')]) == 3
        assert len(assigned[('paul', 'direct')]) == 1
        assert len(assigned[('rly', 'direct')]) == 1

    def test_the_same_inputs_deal_the_same_way_every_time(self, pairs, reviewers):
        assert A.deal(pairs, reviewers, {}, {}, None) == \
               A.deal(pairs, reviewers, {}, {}, None)

    def test_a_tie_goes_to_the_reviewer_the_registry_lists_first(
            self, pairs, reviewers):
        assigned = A.deal(pairs, reviewers, {}, {}, None)
        assert assigned[('paul', 'indirect')][0] == ('10.1/p0', '000541')

    def test_a_pair_already_in_an_assignment_is_not_dealt_again(
            self, pairs, reviewers):
        placed = {('10.1/p0', '000541'): 'rly'}
        assigned = A.deal(pairs, reviewers, placed, {}, None)
        dealt = [k for keys in assigned.values() for k in keys]
        assert ('10.1/p0', '000541') not in dealt

    def test_what_someone_already_holds_counts_towards_their_share(
            self, pairs, reviewers):
        placed = {('10.1/p%d' % i, '000541'): 'rly' for i in range(4)}
        assigned = A.deal(pairs, reviewers, placed, {}, None)
        # rly holds four of the six already, so the last two go to paul.
        assert sorted(assigned[('paul', 'indirect')]) == [
            ('10.1/p4', '000541'), ('10.1/p5', '000541')]
        assert assigned[('rly', 'indirect')] == []

    def test_a_pair_someone_answered_is_not_dealt_to_anybody(self, pairs, reviewers):
        assigned = A.deal(pairs, reviewers, {}, {('10.1/p5', '000541'): 'rly'}, None)
        dealt = [k for keys in assigned.values() for k in keys]
        assert ('10.1/p5', '000541') not in dealt

    def test_an_answered_pair_does_not_land_in_the_answerers_assignment(
            self, pairs, reviewers):
        assigned = A.deal(pairs, reviewers, {}, {('10.1/p5', '000541'): 'rly'}, None)
        assert ('10.1/p5', '000541') not in assigned[('rly', 'indirect')]

    def test_limit_caps_how_many_new_pairs_are_dealt(self, pairs, reviewers):
        assigned = A.deal(pairs, reviewers, {}, {}, 3)
        dealt = [k for keys in assigned.values() for k in keys]
        assert len(dealt) == 3

    def test_limit_counts_only_pairs_that_were_actually_dealt(
            self, pairs, reviewers):
        answered = {('10.1/p0', '000541'): 'rly'}
        assigned = A.deal(pairs, reviewers, {}, answered, 1)
        dealt = [k for keys in assigned.values() for k in keys]
        assert dealt == [('10.1/p1', '000541')]


class TestWriteAssignment:
    def test_writes_the_reviewer_the_pathway_and_the_keys(self, tmp_path):
        A.write_assignment('rly', 'indirect', [('10.1/a', '000541')], 'STAMP', tmp_path)
        written = json.loads((tmp_path / 'rly.indirect.json').read_text())
        assert written['reviewer'] == 'rly'
        assert written['pathway'] == 'indirect'
        assert written['pairs'] == {'10.1/a': ['000541']}
        assert written['candidates_generated_at'] == 'STAMP'

    def test_holds_no_pair_records_only_their_keys(self, tmp_path):
        A.write_assignment('rly', 'indirect', [('10.1/a', '000541')], 'STAMP', tmp_path)
        written = json.loads((tmp_path / 'rly.indirect.json').read_text())
        assert set(written) == {'reviewer', 'pathway', 'assigned_at',
                                'candidates_generated_at', 'pairs'}

    def test_replaces_the_queue_rather_than_adding_to_it(self, tmp_path):
        A.write_assignment('rly', 'indirect', [('10.1/b', '000541')], 'STAMP', tmp_path)
        A.write_assignment('rly', 'indirect', [('10.1/a', '000541')], 'STAMP', tmp_path)
        written = json.loads((tmp_path / 'rly.indirect.json').read_text())
        assert written['pairs'] == {'10.1/a': ['000541']}

    def test_a_round_that_changes_nothing_leaves_the_file_untouched(self, tmp_path):
        A.write_assignment('rly', 'indirect', [('10.1/a', '000541')], 'STAMP', tmp_path)
        before = (tmp_path / 'rly.indirect.json').read_bytes()
        assert A.write_assignment(
            'rly', 'indirect', [('10.1/a', '000541')], 'LATER', tmp_path) is False
        assert (tmp_path / 'rly.indirect.json').read_bytes() == before

    def test_a_reviewer_with_nothing_to_read_gets_no_file(self, tmp_path):
        assert A.write_assignment('rly', 'direct', [], 'STAMP', tmp_path) is False
        assert not (tmp_path / 'rly.direct.json').exists()

    def test_a_finished_queue_is_emptied_rather_than_left_stale(self, tmp_path):
        A.write_assignment('rly', 'indirect', [('10.1/a', '000541')], 'STAMP', tmp_path)
        assert A.write_assignment('rly', 'indirect', [], 'STAMP', tmp_path) is True
        assert json.loads((tmp_path / 'rly.indirect.json').read_text())['pairs'] == {}


class TestQueueAfter:
    def test_a_pair_that_was_answered_leaves_the_queue(self, tmp_path):
        A.write_assignment('rly', 'indirect',
                           [('10.1/a', '000541'), ('10.1/b', '000541')], 'S', tmp_path)
        queue, done = A.queue_after('rly', 'indirect', [],
                                    {('10.1/a', '000541'): 'rly'}, tmp_path)
        assert queue == [('10.1/b', '000541')]
        assert done == 1

    def test_a_pair_that_was_never_read_stays_in_the_queue(self, tmp_path):
        A.write_assignment('rly', 'indirect', [('10.1/b', '000541')], 'S', tmp_path)
        queue, done = A.queue_after('rly', 'indirect', [], {}, tmp_path)
        assert queue == [('10.1/b', '000541')]
        assert done == 0

    def test_a_new_round_joins_what_was_left_over(self, tmp_path):
        A.write_assignment('rly', 'indirect',
                           [('10.1/a', '000541'), ('10.1/b', '000541')], 'S', tmp_path)
        queue, _ = A.queue_after('rly', 'indirect', [('10.1/c', '000541')],
                                 {('10.1/a', '000541'): 'rly'}, tmp_path)
        assert queue == [('10.1/b', '000541'), ('10.1/c', '000541')]

    def test_a_finished_round_leaves_an_empty_queue(self, tmp_path):
        A.write_assignment('rly', 'indirect', [('10.1/a', '000541')], 'S', tmp_path)
        queue, done = A.queue_after('rly', 'indirect', [],
                                    {('10.1/a', '000541'): 'rly'}, tmp_path)
        assert (queue, done) == ([], 1)

    def test_a_reviewer_with_no_queue_yet_just_takes_what_was_dealt(self, tmp_path):
        queue, done = A.queue_after('rly', 'indirect', [('10.1/a', '000541')],
                                    {}, tmp_path)
        assert (queue, done) == ([('10.1/a', '000541')], 0)


class TestReadingWhatIsHeld:
    def test_an_assignment_on_disk_holds_its_keys(self, tmp_path):
        A.write_assignment('rly', 'indirect', [('10.1/a', '000541')], 'S', tmp_path)
        assert A.assigned_to(tmp_path) == {('10.1/a', '000541'): 'rly'}

    def test_what_someone_has_answered_counts_towards_their_share(
            self, pairs, reviewers):
        answered = {('10.1/p%d' % i, '000541'): 'rly' for i in range(4)}
        assigned = A.deal(pairs, reviewers, {}, answered, None)
        # rly has already read four of the six, so the other two go to paul.
        assert sorted(assigned[('paul', 'indirect')]) == [
            ('10.1/p4', '000541'), ('10.1/p5', '000541')]
        assert assigned[('rly', 'indirect')] == []

    def test_every_reviewers_assignment_counts_not_just_this_rounds(self, tmp_path):
        A.write_assignment('rly', 'indirect', [('10.1/a', '000541')], 'S', tmp_path)
        A.write_assignment('paul', 'direct', [('10.1/b', '000714')], 'S', tmp_path)
        assert A.assigned_to(tmp_path) == {('10.1/a', '000541'): 'rly',
                                           ('10.1/b', '000714'): 'paul'}

    def test_an_answer_belongs_to_whoever_gave_it(self, tmp_path):
        (tmp_path / 'rly.json').write_text(json.dumps(
            {'reviewer': 'rly', 'reviews': {'10.1/a': {'000541': {'call': 'reuse'}}}}))
        assert A.answered_by([{'name': 'rly'}], tmp_path) == {('10.1/a', '000541'): 'rly'}

    def test_a_reviewer_who_has_answered_nothing_holds_nothing(self, tmp_path):
        assert A.answered_by([{'name': 'rly'}], tmp_path) == {}
