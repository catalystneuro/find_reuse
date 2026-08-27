"""Tests for dealing candidate pairs out to reviewers."""

import argparse
import json

import pytest

import src.review.assign_reviews as A


def pair(doi, dandiset='000541', pathway='indirect', **overrides):
    record = {
        'doi': doi, 'dandiset': dandiset, 'pathway': pathway,
        'same_lab': False, 'reused_neurophysiology': True,
        'reused_modalities': ['neurophysiology'],
        'archives': ['DANDI Archive'], 'reuse_types': ['NOVEL_ANALYSIS'],
        'dandi_reason': 'names DANDI Archive as the source',
    }
    record.update(overrides)
    return record


def filters(**overrides):
    """The flags with nothing selected, which is what keeps every pair."""
    defaults = {
        'pathway': None, 'dandi_source': None, 'neuro': None,
        'modality': None, 'reuse_type': None, 'lab': 'any',
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


@pytest.fixture
def reviewers():
    return [{'username': 'paul'}, {'username': 'rly'}]


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


    def test_dandi_source_possible_keeps_a_pair_naming_no_archive(self):
        p = pair('10.1/a', '000541', archives=[], dandi_reason=None)
        assert A.matches(p, filters(dandi_source='possible')) is True

    def test_dandi_source_possible_keeps_a_pair_naming_dandi(self):
        p = pair('10.1/a', '000541', archives=['DANDI Archive'])
        assert A.matches(p, filters(dandi_source='possible')) is True

    def test_dandi_source_possible_drops_a_pair_naming_another_archive(self):
        p = pair('10.1/a', '000541', archives=['CRCNS'])
        assert A.matches(p, filters(dandi_source='possible')) is False

    def test_dandi_source_possible_keeps_a_pair_naming_dandi_among_others(self):
        # The classifier keeps an archive it does not recognise verbatim, so a
        # record can name two archives in one string.
        p = pair('10.1/a', '000541', archives=['DANDI Archive and GitHub'])
        assert A.matches(p, filters(dandi_source='possible')) is True

    def test_dandi_source_evidenced_keeps_only_what_says_so(self):
        said = pair('10.1/a', '000541',
                    dandi_reason='names DANDI Archive as the source')
        silent = pair('10.1/b', '000541', archives=[], dandi_reason=None)
        assert A.matches(said, filters(dandi_source='evidenced')) is True
        assert A.matches(silent, filters(dandi_source='evidenced')) is False

    def test_a_pair_naming_no_archive_is_possible_but_not_evidenced(self):
        p = pair('10.1/a', '000541', archives=[], dandi_reason=None)
        assert A.matches(p, filters(dandi_source='possible')) is True
        assert A.matches(p, filters(dandi_source='evidenced')) is False

    def test_neuro_selects_on_the_modality_that_was_reused(self):
        neuro = pair('10.1/a', '000541', reused_neurophysiology=True)
        other = pair('10.1/b', '000541', reused_neurophysiology=False)
        assert A.matches(neuro, filters(neuro=True)) is True
        assert A.matches(other, filters(neuro=True)) is False

    def test_modality_keeps_a_pair_reusing_any_of_the_named_ones(self):
        p = pair('10.1/a', '000541', reused_modalities=['behavior', 'transcriptomics'])
        assert A.matches(p, filters(modality=['transcriptomics'])) is True
        assert A.matches(p, filters(modality=['morphology'])) is False





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
        written = json.loads(A.assignment_path('rly', 'indirect', tmp_path).read_text())
        assert written['reviewer'] == 'rly'
        assert written['pathway'] == 'indirect'
        assert written['pairs'] == {'10.1/a': ['000541']}
        assert written['candidates_generated_at'] == 'STAMP'

    def test_holds_no_pair_records_only_their_keys(self, tmp_path):
        A.write_assignment('rly', 'indirect', [('10.1/a', '000541')], 'STAMP', tmp_path)
        written = json.loads(A.assignment_path('rly', 'indirect', tmp_path).read_text())
        assert set(written) == {'reviewer', 'pathway', 'assigned_at',
                                'candidates_generated_at', 'pairs'}

    def test_replaces_the_queue_rather_than_adding_to_it(self, tmp_path):
        A.write_assignment('rly', 'indirect', [('10.1/b', '000541')], 'STAMP', tmp_path)
        A.write_assignment('rly', 'indirect', [('10.1/a', '000541')], 'STAMP', tmp_path)
        written = json.loads(A.assignment_path('rly', 'indirect', tmp_path).read_text())
        assert written['pairs'] == {'10.1/a': ['000541']}

    def test_a_round_that_changes_nothing_leaves_the_file_untouched(self, tmp_path):
        A.write_assignment('rly', 'indirect', [('10.1/a', '000541')], 'STAMP', tmp_path)
        before = A.assignment_path('rly', 'indirect', tmp_path).read_bytes()
        assert A.write_assignment(
            'rly', 'indirect', [('10.1/a', '000541')], 'LATER', tmp_path) is False
        assert A.assignment_path('rly', 'indirect', tmp_path).read_bytes() == before

    def test_a_reviewer_with_nothing_to_read_gets_no_file(self, tmp_path):
        assert A.write_assignment('rly', 'direct', [], 'STAMP', tmp_path) is False
        assert not A.assignment_path('rly', 'direct', tmp_path).exists()

    def test_a_finished_queue_is_emptied_rather_than_left_stale(self, tmp_path):
        A.write_assignment('rly', 'indirect', [('10.1/a', '000541')], 'STAMP', tmp_path)
        assert A.write_assignment('rly', 'indirect', [], 'STAMP', tmp_path) is True
        assert json.loads(
            A.assignment_path('rly', 'indirect', tmp_path).read_text())['pairs'] == {}


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
        reviews = A.reviews_path('rly', tmp_path)
        reviews.parent.mkdir(parents=True, exist_ok=True)
        reviews.write_text(json.dumps(
            {'reviewer': 'rly', 'reviews': {'10.1/a': {'000541': {'call': 'reuse'}}}}))
        assert A.answered_by([{'username': 'rly'}], tmp_path) == {('10.1/a', '000541'): 'rly'}

    def test_a_reviewer_who_has_answered_nothing_holds_nothing(self, tmp_path):
        assert A.answered_by([{'username': 'rly'}], tmp_path) == {}
