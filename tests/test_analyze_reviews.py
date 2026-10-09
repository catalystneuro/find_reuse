"""Tests for sorting the classifier's rejected REUSE calls by cause."""

import json

import src.review.analyze_reviews as A


def row(doi='10.1/citer', dandiset='000128', siblings=(), **fields):
    """A candidate pair with only the fields the analysis reads."""
    shared = {'dandisets': [{'dandiset': d} for d in siblings]} if siblings else None
    return {'doi': doi, 'dandiset': dandiset, 'dandiset_name': 'n',
            'pathway': 'indirect', 'shared_paper': shared, **fields}


class TestLoadReviews:
    def test_keeps_an_added_pair_apart_from_the_calls(self, tmp_path):
        path = tmp_path / 'rly-reviews.json'
        path.write_text(json.dumps({
            'reviewer': 'rly',
            'reviews': {'10.1/citer': {'000128': {'call': 'neither', 'note': 'why'},
                                       '000070': {'call': 'reuse', 'note': 'found'}}},
            'added': {'10.1/citer': {'000070': {'dandiset_name': 'n'}}}}))

        calls, added = A.load_reviews([path])

        assert dict(calls) == {('10.1/citer', '000128'): {
            'rly': {'call': 'neither', 'note': 'why'}}}
        assert added == {('10.1/citer', '000070'): 'found'}


class TestSettled:
    def test_reviewers_who_agree_settle_the_call(self):
        call, notes = A.settled({'a': {'call': 'reuse'},
                                 'b': {'call': 'reuse', 'note': 'clear'}})

        assert call == 'reuse'
        assert notes.strip() == 'clear'

    def test_reviewers_who_disagree_settle_nothing(self):
        assert A.settled({'a': {'call': 'reuse'}, 'b': {'call': 'neither'}})[0] is None


class TestCause:
    def test_an_elife_review_doi_is_not_a_paper(self):
        assert A.cause(row(doi='10.7554/elife.89421.1.sa2'), '', set()) == 'not a paper'

    def test_a_preprint_duplicate_is_named_by_the_note(self):
        note = 'This is a preprint duplicate of the following DOI: 10.1038/x'

        assert A.cause(row(), note, set()) == 'preprint of a paper also listed'

    def test_a_sibling_the_reviewers_accepted_is_the_reused_dandiset(self):
        pair = row(dandiset='000138', siblings=['000128', '000138'])

        assert A.cause(pair, '', {'000128'}) == 'reuse of a dandiset sharing the paper'

    def test_a_sibling_the_note_names_is_the_reused_dandiset(self):
        pair = row(dandiset='000035', siblings=['000008', '000035'])
        note = 'Shared with Dandiset 000008, which I am attributing the reuse to.'

        assert A.cause(pair, note, set()) == 'reuse of a dandiset sharing the paper'

    def test_a_dandiset_named_from_outside_the_group_is_another_dandiset(self):
        note = 'DANDI:000409 is cited as the source, not DANDI:000149.'

        assert A.cause(row(dandiset='000149'), note, set()) == 'reuse of another dandiset'

    def test_a_named_repository_is_data_taken_from_elsewhere(self):
        note = 'Data accessed from Zenodo; the dandiset holds one session of it.'

        assert A.cause(row(), note, set()) == 'data taken from elsewhere'

    def test_a_background_citation_is_cited_without_reuse(self):
        note = 'The cited paper is only a background mention in the introduction.'

        assert A.cause(row(), note, set()) == 'cited without reuse'

    def test_a_depositing_paper_is_the_source(self):
        note = 'This is a primary paper for the FALCON benchmark.'

        assert A.cause(row(), note, set()) == 'citing paper is the source'

    def test_naming_another_dandisets_primary_paper_is_not_being_the_source(self):
        note = 'Ref 45 does correspond to the primary paper of Dandiset 000447.'

        assert A.cause(row(dandiset='000447'), note, set()) != 'citing paper is the source'

    def test_an_unexplained_rejection_beside_an_accepted_pair(self):
        assert A.cause(row(), '', {'000044'}) == 'reuse of another dandiset'

    def test_an_unexplained_rejection_alone_has_no_rule(self):
        assert A.cause(row(), '', set()) == 'no rule'


class TestAnalyze:
    def test_reports_the_confirmed_share_and_the_causes(self):
        candidates = [row(dandiset='000128', siblings=['000128', '000138']),
                      row(dandiset='000138', siblings=['000128', '000138']),
                      row(doi='10.7554/elife.1.sa1', dandiset='000017')]
        calls = {('10.1/citer', '000128'): {'rly': {'call': 'reuse'}},
                 ('10.1/citer', '000138'): {'rly': {'call': 'neither'}},
                 ('10.7554/elife.1.sa1', '000017'): {'rly': {'call': 'neither'}}}

        report = A.analyze(candidates, calls, {})

        assert '| all | 3 | 1 | 0 | 2 | 0 | 0 | 0 | 33% |' in report
        assert '| reuse of a dandiset sharing the paper | 1 | 1 |' in report
        assert '| not a paper | 1 | 1 |' in report
        assert '1 of 3 candidate pairs are on a DOI that is not a paper.' in report
