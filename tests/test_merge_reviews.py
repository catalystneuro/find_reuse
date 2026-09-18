"""Tests for putting everybody's reviews back together."""

import json

import pytest

import src.review.merge_reviews as M


def candidate(doi, dandiset='000541', **overrides):
    record = {
        'doi': doi, 'dandiset': dandiset, 'pathway': 'indirect',
        'fetched_doi': doi,
        'title': 'Waveform-based classification of dentate spikes',
        'dandiset_name': 'Hippocampal recordings',
        'cited_doi': '10.1/cited', 'cited_title': 'The dataset paper',
        'cited_role': 'Cited', 'cited_source': 'dcite:IsDescribedBy',
        'shared_paper': None,
        'reasoning': 'The methods say the recordings were downloaded.',
        'quotes': [{'q': 'downloaded from the DANDI Archive', 'tier': 'exact'}],
        'same_lab': False, 'reused_neurophysiology': True,
        'reused_modalities': ['neurophysiology'],
        'archives': ['DANDI Archive'], 'reuse_types': ['NOVEL_ANALYSIS'],
        'dandi_reason': 'names DANDI Archive as the source',
    }
    record.update(overrides)
    return record


def write_reviews(base, username, reviews, added=None):
    """One reviewer's file, as the dashboard writes it."""
    path = base / username / f'{username}-reviews.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {'reviewer': username, 'reviews': reviews}
    if added:
        record['added'] = added
    path.write_text(json.dumps(record))


@pytest.fixture
def registry():
    return [{'username': 'paul', 'name': 'Paul Adkisson-Floro'},
            {'username': 'rly', 'name': 'Ryan Ly'}]


@pytest.fixture
def candidates():
    return [candidate('10.1/a'), candidate('10.1/b'), candidate('10.1/c')]


class TestCollectReviews:
    def test_keys_a_review_by_paper_and_dataset(self, registry, tmp_path):
        write_reviews(tmp_path, 'paul', {'10.1/a': {'000541': {'call': 'reuse'}}})
        assert M.collect_reviews(registry, tmp_path) == {
            ('10.1/a', '000541'): {'paul': {'call': 'reuse'}}}

    def test_a_pair_two_people_read_holds_both(self, registry, tmp_path):
        write_reviews(tmp_path, 'paul', {'10.1/a': {'000541': {'call': 'reuse'}}})
        write_reviews(tmp_path, 'rly', {'10.1/a': {'000541': {'call': 'mention'}}})
        assert M.collect_reviews(registry, tmp_path) == {
            ('10.1/a', '000541'): {'paul': {'call': 'reuse'},
                                   'rly': {'call': 'mention'}}}

    def test_reviewers_come_out_in_registry_order(self, tmp_path):
        write_reviews(tmp_path, 'paul', {'10.1/a': {'000541': {'call': 'reuse'}}})
        write_reviews(tmp_path, 'rly', {'10.1/a': {'000541': {'call': 'reuse'}}})
        registry = [{'username': 'rly'}, {'username': 'paul'}]
        collected = M.collect_reviews(registry, tmp_path)
        assert list(collected[('10.1/a', '000541')]) == ['rly', 'paul']

    def test_a_registered_reviewer_who_has_reviewed_nothing_is_no_trouble(
            self, registry, tmp_path):
        write_reviews(tmp_path, 'paul', {'10.1/a': {'000541': {'call': 'reuse'}}})
        assert list(M.collect_reviews(registry, tmp_path)) == [('10.1/a', '000541')]

    def test_reviews_from_an_unregistered_reviewer_are_refused(self, registry,
                                                               tmp_path):
        write_reviews(tmp_path, 'nobody', {'10.1/a': {'000541': {'call': 'reuse'}}})
        with pytest.raises(SystemExit) as raised:
            M.collect_reviews(registry, tmp_path)
        assert 'nobody' in str(raised.value)
        assert 'paul, rly' in str(raised.value)


class TestSettledCall:
    def test_one_reviewer_settles_it(self):
        assert M.settled_call({'paul': {'call': 'reuse'}}) == 'reuse'

    def test_two_who_agree_settle_it(self):
        assert M.settled_call({'paul': {'call': 'mention'},
                               'rly': {'call': 'mention'}}) == 'mention'

    def test_two_who_disagree_settle_nothing(self):
        assert M.settled_call({'paul': {'call': 'reuse'},
                               'rly': {'call': 'mention'}}) is None

    def test_a_note_with_no_call_settles_nothing(self):
        """
        The dashboard writes a note as it is typed, so a reviewer who wrote one
        and moved on without answering leaves an entry holding no call at all.
        """
        assert M.settled_call({'rly': {'note': 'cannot tell from the text'}}) is None

    def test_a_reviewer_who_only_wrote_a_note_leaves_the_call_to_the_rest(self):
        assert M.settled_call({'paul': {'call': 'reuse'},
                               'rly': {'note': 'agreed, see figure 3'}}) == 'reuse'


class TestMerge:
    def test_a_reviewed_pair_carries_the_record_it_was_judged_on(self, candidates):
        pairs, _ = M.merge(candidates, {('10.1/a', '000541'):
                                        {'paul': {'call': 'reuse'}}})
        assert pairs == [{**candidate('10.1/a'), 'source': 'classifier',
                          'call': 'reuse', 'calls': {'paul': 'reuse'},
                          'notes': {}}]

    def test_a_candidate_nobody_read_is_left_out(self, candidates):
        pairs, _ = M.merge(candidates, {('10.1/b', '000541'):
                                        {'paul': {'call': 'reuse'}}})
        assert [pair['doi'] for pair in pairs] == ['10.1/b']

    def test_a_rejection_is_a_result_too(self, candidates):
        pairs, _ = M.merge(candidates, {('10.1/a', '000541'):
                                        {'paul': {'call': 'neither'}}})
        assert pairs[0]['call'] == 'neither'
        assert pairs[0]['calls'] == {'paul': 'neither'}

    def test_a_disagreement_settles_no_call_but_keeps_both(self, candidates):
        pairs, _ = M.merge(candidates, {('10.1/a', '000541'): {
            'paul': {'call': 'reuse'}, 'rly': {'call': 'mention'}}})
        assert pairs[0]['call'] is None
        assert pairs[0]['calls'] == {'paul': 'reuse', 'rly': 'mention'}

    def test_a_note_without_a_call_is_kept_and_votes_for_nothing(self, candidates):
        """
        A reviewer can write a note and move on without answering, and the pair
        still has to merge: their reading is worth keeping and their silence is
        not a call.
        """
        pairs, _ = M.merge(candidates, {('10.1/a', '000541'): {
            'paul': {'call': 'reuse'},
            'rly': {'note': 'I could not confirm this one myself.'}}})

        assert pairs[0]['call'] == 'reuse'
        assert pairs[0]['calls'] == {'paul': 'reuse'}
        assert pairs[0]['notes'] == {'rly': 'I could not confirm this one myself.'}

    def test_a_note_is_kept_under_the_reviewer_who_wrote_it(self, candidates):
        pairs, _ = M.merge(candidates, {('10.1/a', '000541'): {
            'paul': {'call': 'unsure', 'note': 'The methods are behind a wall.'},
            'rly': {'call': 'unsure'}}})
        assert pairs[0]['notes'] == {'paul': 'The methods are behind a wall.'}

    def test_a_review_of_a_pair_no_longer_a_candidate_comes_back_separately(
            self, candidates):
        pairs, orphaned = M.merge(candidates, {
            ('10.1/a', '000541'): {'paul': {'call': 'reuse'}},
            ('10.1/gone', '000541'): {'paul': {'call': 'reuse'}}})
        assert [pair['doi'] for pair in pairs] == ['10.1/a']
        assert orphaned == [('10.1/gone', '000541')]

    def test_pairs_come_out_sorted_by_paper_then_dataset(self):
        candidates = [candidate('10.1/b', '000002'), candidate('10.1/a'),
                      candidate('10.1/b', '000001')]
        pairs, _ = M.merge(candidates, {
            ('10.1/b', '000002'): {'paul': {'call': 'reuse'}},
            ('10.1/a', '000541'): {'paul': {'call': 'reuse'}},
            ('10.1/b', '000001'): {'paul': {'call': 'reuse'}}})
        assert [(pair['doi'], pair['dandiset']) for pair in pairs] == [
            ('10.1/a', '000541'), ('10.1/b', '000001'), ('10.1/b', '000002')]


class TestCollectAdded:
    """The pairs reviewers put on the list themselves, gathered off their files."""

    def test_reads_the_pairs_and_the_names_they_carry(self, tmp_path):
        write_reviews(tmp_path, 'rly', {'10.1/a': {'000128': {'call': 'reuse'}}},
                      added={'10.1/a': {'000128': {'dandiset_name': 'MC_Maze'}}})

        assert M.collect_added(tmp_path) == {('10.1/a', '000128'): 'MC_Maze'}

    def test_gathers_what_every_reviewer_added(self, tmp_path):
        write_reviews(tmp_path, 'paul', {}, added={
            '10.1/a': {'000128': {'dandiset_name': 'MC_Maze'}}})
        write_reviews(tmp_path, 'rly', {}, added={
            '10.1/b': {'000139': {'dandiset_name': 'MC_Maze_Medium'}}})

        assert M.collect_added(tmp_path) == {
            ('10.1/a', '000128'): 'MC_Maze',
            ('10.1/b', '000139'): 'MC_Maze_Medium'}

    def test_a_reviewer_who_added_nothing_is_no_trouble(self, tmp_path):
        write_reviews(tmp_path, 'rly', {'10.1/a': {'000541': {'call': 'reuse'}}})

        assert M.collect_added(tmp_path) == {}


class TestMergingAddedPairs:
    """
    A pair a reviewer found by reading the paper. It is ground truth like any
    other, and has no classifier record to carry.
    """

    def merged(self, candidates, name='MC_Maze', call='reuse'):
        pairs, orphaned = M.merge(
            candidates,
            {('10.1/a', '000128'): {'rly': {'call': call}}},
            {('10.1/a', '000128'): name})
        return pairs, orphaned

    def test_an_added_pair_is_merged_rather_than_orphaned(self, candidates):
        pairs, orphaned = self.merged(candidates)

        assert orphaned == []
        assert [(p['doi'], p['dandiset']) for p in pairs] == [('10.1/a', '000128')]

    def test_it_says_a_reviewer_found_it(self, candidates):
        pairs, _ = self.merged(candidates)
        assert pairs[0]['source'] == 'reviewer'

    def test_it_carries_the_name_and_the_pathway(self, candidates):
        pairs, _ = self.merged(candidates)
        assert pairs[0]['dandiset_name'] == 'MC_Maze'
        assert pairs[0]['pathway'] == 'added'

    def test_it_takes_the_paper_from_a_pair_already_built_on_it(self, candidates):
        """
        The paper is one the pipeline reached, so a reviewer opening the record
        should not be left with a bare DOI where every other pair has a title.
        """
        pairs, _ = self.merged(candidates)
        assert pairs[0]['title'] == candidate('10.1/a')['title']

    def test_it_holds_the_same_fields_as_a_pair_the_classifier_proposed(
            self, candidates):
        """
        Reading a field should not depend on which kind of record it came off,
        so a field added to the candidate list cannot quietly go missing here.
        """
        pairs, _ = M.merge(
            candidates,
            {('10.1/a', '000128'): {'rly': {'call': 'reuse'}},
             ('10.1/b', '000541'): {'rly': {'call': 'reuse'}}},
            {('10.1/a', '000128'): 'MC_Maze'})
        found, proposed = pairs[0], pairs[1]

        assert found['source'] == 'reviewer' and proposed['source'] == 'classifier'
        assert set(found) == set(proposed)

    def test_the_fields_only_a_classifier_answers_come_back_empty(self, candidates):
        pairs, _ = self.merged(candidates)
        assert pairs[0]['reasoning'] is None
        assert pairs[0]['quotes'] == []

    def test_a_pair_in_neither_list_is_still_orphaned(self, candidates):
        pairs, orphaned = M.merge(
            candidates, {('10.1/gone', '000999'): {'rly': {'call': 'reuse'}}}, {})

        assert pairs == []
        assert orphaned == [('10.1/gone', '000999')]


class TestConfirmed:
    @pytest.fixture
    def pairs(self, candidates):
        """One pair read once, one read twice, one read twice and disputed."""
        merged, _ = M.merge(candidates, {
            ('10.1/a', '000541'): {'paul': {'call': 'reuse'}},
            ('10.1/b', '000541'): {'paul': {'call': 'reuse'},
                                   'rly': {'call': 'reuse'}},
            ('10.1/c', '000541'): {'paul': {'call': 'reuse'},
                                   'rly': {'call': 'mention'}}})
        return merged

    def test_one_reviewer_is_enough_by_default(self, pairs):
        assert [pair['doi'] for pair in M.confirmed(pairs, 1)] == [
            '10.1/a', '10.1/b', '10.1/c']

    def test_two_reviewers_needs_two_who_said_reuse(self, pairs):
        assert [pair['doi'] for pair in M.confirmed(pairs, 2)] == ['10.1/b']

    def test_a_pair_nobody_called_reuse_is_never_confirmed(self, candidates):
        pairs, _ = M.merge(candidates, {('10.1/a', '000541'):
                                        {'paul': {'call': 'mention'}}})
        assert M.confirmed(pairs, 1) == []


class TestTally:
    def test_counts_pairs_by_what_they_came_out(self, candidates):
        pairs, _ = M.merge(candidates, {
            ('10.1/a', '000541'): {'paul': {'call': 'reuse'}},
            ('10.1/b', '000541'): {'paul': {'call': 'reuse'}},
            ('10.1/c', '000541'): {'paul': {'call': 'mention'}}})
        assert M.tally(pairs) == {'mention': 1, 'reuse': 2}

    def test_a_disputed_pair_is_its_own_outcome(self, candidates):
        pairs, _ = M.merge(candidates, {('10.1/a', '000541'): {
            'paul': {'call': 'reuse'}, 'rly': {'call': 'neither'}}})
        assert M.tally(pairs) == {'disputed': 1}

    def test_a_pair_a_reviewer_found_is_left_out(self, candidates):
        """
        This is how often the classifier was right about what it proposed, and
        it never proposed an added pair, so counting one would credit it with a
        call it did not make and raise its precision for work it did not do.
        """
        pairs, _ = M.merge(
            candidates,
            {('10.1/a', '000541'): {'rly': {'call': 'reuse'}},
             ('10.1/a', '000128'): {'rly': {'call': 'reuse'}}},
            {('10.1/a', '000128'): 'MC_Maze'})

        assert len(pairs) == 2
        assert M.tally(pairs) == {'reuse': 1}


class TestWriting:
    def test_a_merge_that_says_the_same_thing_leaves_the_file_alone(
            self, candidates, tmp_path):
        pairs, _ = M.merge(candidates, {('10.1/a', '000541'):
                                        {'paul': {'call': 'reuse'}}})
        out = tmp_path / 'all_reviews.json'
        body = {'reviewed': len(pairs), 'pairs': pairs}
        assert M.write_stamped(out, body) is True
        before = out.read_bytes()
        assert M.write_stamped(out, body) is False
        assert out.read_bytes() == before

    def test_a_merged_pair_survives_the_round_trip_through_json(
            self, candidates, tmp_path):
        pairs, _ = M.merge(candidates, {('10.1/a', '000541'): {
            'paul': {'call': 'reuse', 'note': 'Figure 3 is built on it.'}}})
        out = tmp_path / 'all_reviews.json'
        M.write_stamped(out, {'pairs': pairs})
        assert json.loads(out.read_text())['pairs'] == [
            {**candidate('10.1/a'), 'source': 'classifier', 'call': 'reuse',
             'calls': {'paul': 'reuse'},
             'notes': {'paul': 'Figure 3 is built on it.'}}]
