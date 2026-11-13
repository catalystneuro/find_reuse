"""Tests for reading which dandisets are counted as one dataset."""

import json

import pytest

import src.review.dandiset_groups as G


def write_groups(path, groups):
    path.write_text(json.dumps({'_comment': ['Why these are grouped.'], **groups}))
    return path


def test_maps_each_dandiset_to_its_group(tmp_path):
    path = write_groups(tmp_path / 'groups.json', {
        'MC_Maze': {'dandisets': ['000128', '000138'], 'reason': ['Variants.']},
        'IBL': {'dandisets': ['000409'], 'reason': ['Releases.']}})
    assert G.load_groups(path) == {'000128': 'MC_Maze', '000138': 'MC_Maze',
                                   '000409': 'IBL'}


def test_a_dandiset_in_two_groups_is_refused(tmp_path):
    path = write_groups(tmp_path / 'groups.json', {
        'MC_Maze': {'dandisets': ['000128', '000070'], 'reason': []},
        'Churchland': {'dandisets': ['000070'], 'reason': []}})
    with pytest.raises(SystemExit) as raised:
        G.load_groups(path)
    assert '000070' in str(raised.value)


def test_the_groups_in_the_repository_load():
    groups = G.load_groups()
    assert groups['000128'] == groups['000070'] == 'MC_Maze'
    assert groups['000129'] == groups['001055'] == 'MC_RTT'
