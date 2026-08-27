"""Tests for the reviewer registry and the filenames it decides."""

import json

import pytest

import src.review.reviewers as V


@pytest.fixture
def registry_file(tmp_path):
    path = tmp_path / 'reviewers.json'
    path.write_text(json.dumps([
        {'name': 'pauladkissonfloro', 'github': 'pauladkisson'},
        {'name': 'rly', 'github': 'rly'},
    ]))
    return path


class TestReviewerSlug:
    def test_names_a_file_that_survives_punctuation_and_case(self):
        assert V.reviewer_slug('Paul W. Adkisson') == 'paul-w-adkisson'

    def test_a_plain_name_is_left_alone(self):
        assert V.reviewer_slug('rly') == 'rly'


class TestSelectReviewers:
    def test_no_subset_means_every_registered_reviewer(self, registry_file):
        registry = V.load_reviewers(registry_file)
        assert [r['name'] for r in V.select_reviewers(registry, None)] == [
            'pauladkissonfloro', 'rly']

    def test_a_subset_keeps_registry_order_not_argument_order(self, registry_file):
        registry = V.load_reviewers(registry_file)
        chosen = V.select_reviewers(registry, 'rly,pauladkissonfloro')
        assert [r['name'] for r in chosen] == ['pauladkissonfloro', 'rly']

    def test_a_name_not_in_the_registry_stops_the_round(self, registry_file):
        registry = V.load_reviewers(registry_file)
        with pytest.raises(SystemExit) as excinfo:
            V.select_reviewers(registry, 'ryl')
        assert 'ryl' in str(excinfo.value)

    def test_the_refusal_names_the_reviewers_that_do_exist(self, registry_file):
        registry = V.load_reviewers(registry_file)
        with pytest.raises(SystemExit) as excinfo:
            V.select_reviewers(registry, 'ryl')
        assert 'pauladkissonfloro' in str(excinfo.value)
        assert 'rly' in str(excinfo.value)


class TestPaths:
    def test_an_assignment_is_named_for_its_reviewer_and_pathway(self, tmp_path):
        path = V.assignment_path('Paul W. Adkisson', 'indirect', tmp_path)
        assert path == tmp_path / 'paul-w-adkisson.indirect.json'

    def test_answers_are_named_for_the_reviewer_alone(self, tmp_path):
        assert V.answers_path('rly', tmp_path) == tmp_path / 'rly.json'
