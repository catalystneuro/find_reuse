"""Tests for the reviewer registry and the filenames it decides."""

import json

import pytest

import src.review.reviewers as V


@pytest.fixture
def registry_file(tmp_path):
    path = tmp_path / 'reviewers.json'
    path.write_text(json.dumps([
        {'username': 'pauladkisson', 'name': 'Paul Adkisson-Floro'},
        {'username': 'rly', 'name': 'Ryan Ly'},
    ]))
    return path


class TestLoadReviewers:
    def test_keeps_the_order_the_file_lists(self, registry_file):
        assert [r['username'] for r in V.load_reviewers(registry_file)] == [
            'pauladkisson', 'rly']

    def test_a_username_that_could_not_name_a_file_is_refused(self, tmp_path):
        path = tmp_path / 'reviewers.json'
        path.write_text(json.dumps([{'username': 'Paul W. Adkisson',
                                     'name': 'Paul Adkisson-Floro'}]))
        with pytest.raises(SystemExit) as excinfo:
            V.load_reviewers(path)
        assert 'Paul W. Adkisson' in str(excinfo.value)

    def test_a_github_handle_needs_no_thought(self, tmp_path):
        path = tmp_path / 'reviewers.json'
        path.write_text(json.dumps([{'username': 'paul-adkisson_1', 'name': 'P'}]))
        assert V.load_reviewers(path)[0]['username'] == 'paul-adkisson_1'

    def test_a_username_that_would_escape_its_directory_is_refused(self, tmp_path):
        path = tmp_path / 'reviewers.json'
        path.write_text(json.dumps([{'username': '../elsewhere', 'name': 'P'}]))
        with pytest.raises(SystemExit):
            V.load_reviewers(path)


class TestSelectReviewers:
    def test_no_subset_means_every_registered_reviewer(self, registry_file):
        registry = V.load_reviewers(registry_file)
        assert [r['username'] for r in V.select_reviewers(registry, None)] == [
            'pauladkisson', 'rly']

    def test_a_subset_keeps_registry_order_not_argument_order(self, registry_file):
        registry = V.load_reviewers(registry_file)
        chosen = V.select_reviewers(registry, 'rly,pauladkisson')
        assert [r['username'] for r in chosen] == ['pauladkisson', 'rly']

    def test_a_username_is_what_selects_not_the_persons_name(self, registry_file):
        registry = V.load_reviewers(registry_file)
        with pytest.raises(SystemExit):
            V.select_reviewers(registry, 'Ryan Ly')

    def test_a_name_not_in_the_registry_stops_the_round(self, registry_file):
        registry = V.load_reviewers(registry_file)
        with pytest.raises(SystemExit) as excinfo:
            V.select_reviewers(registry, 'ryl')
        assert 'ryl' in str(excinfo.value)

    def test_the_refusal_names_the_reviewers_that_do_exist(self, registry_file):
        registry = V.load_reviewers(registry_file)
        with pytest.raises(SystemExit) as excinfo:
            V.select_reviewers(registry, 'ryl')
        assert 'pauladkisson' in str(excinfo.value)
        assert 'rly' in str(excinfo.value)


class TestPaths:
    def test_a_reviewer_gets_a_directory_named_for_them(self, tmp_path):
        assert V.reviewer_dir('rly', tmp_path) == tmp_path / 'rly'

    def test_an_assignment_sits_under_its_reviewer_and_names_its_pathway(
            self, tmp_path):
        assert V.assignment_path('pauladkisson', 'indirect', tmp_path) == (
            tmp_path / 'pauladkisson' / 'assignment-indirect.json')

    def test_reviews_sit_under_their_reviewer(self, tmp_path):
        assert V.reviews_path('rly', tmp_path) == tmp_path / 'rly' / 'reviews.json'

    def test_the_username_is_used_as_written(self, tmp_path):
        assert V.reviews_path('Paul-Adkisson', tmp_path) == (
            tmp_path / 'Paul-Adkisson' / 'reviews.json')

    def test_every_assignment_is_found_whosever_it_is(self, tmp_path):
        for username, pathway in (('rly', 'indirect'), ('rly', 'direct'),
                                  ('pauladkisson', 'indirect')):
            path = V.assignment_path(username, pathway, tmp_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('{}')
        V.reviews_path('rly', tmp_path).write_text('{}')
        assert [p.relative_to(tmp_path).as_posix()
                for p in V.assignment_paths(tmp_path)] == [
            'pauladkisson/assignment-indirect.json',
            'rly/assignment-direct.json',
            'rly/assignment-indirect.json',
        ]
