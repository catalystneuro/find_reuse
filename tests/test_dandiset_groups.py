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


def write_corpus(path, papers):
    """A corpus in which each dandiset names the given primary papers."""
    path.write_text(json.dumps({'results': [
        {'dandiset_id': dandiset, 'paper_relations': [{'doi': doi} for doi in dois]}
        for dandiset, dois in papers.items()]}))
    return path


def test_dandisets_naming_one_paper_form_a_unit(tmp_path):
    corpus = write_corpus(tmp_path / 'corpus.json', {
        '000021': ['10.1/siegle'], '000022': ['10.1/SIEGLE'], '000003': ['10.1/other']})
    groups = write_groups(tmp_path / 'groups.json', {})
    assert G.units(corpus, groups) == {'000021': 'shared_paper_000021',
                                       '000022': 'shared_paper_000021'}


def test_a_shared_paper_extends_a_listed_group_and_keeps_its_name(tmp_path):
    corpus = write_corpus(tmp_path / 'corpus.json', {
        '000070': ['10.1/churchland'], '000128': ['10.1/churchland'], '000125': []})
    groups = write_groups(tmp_path / 'groups.json', {
        'MC_Maze': {'dandisets': ['000070', '000125'], 'reason': []}})
    assert G.units(corpus, groups) == {'000070': 'MC_Maze', '000125': 'MC_Maze',
                                       '000128': 'MC_Maze'}


def test_a_paper_joining_two_listed_groups_is_refused(tmp_path):
    corpus = write_corpus(tmp_path / 'corpus.json', {
        '000128': ['10.1/shared'], '000129': ['10.1/shared']})
    groups = write_groups(tmp_path / 'groups.json', {
        'MC_Maze': {'dandisets': ['000128'], 'reason': []},
        'MC_RTT': {'dandisets': ['000129'], 'reason': []}})
    with pytest.raises(SystemExit) as raised:
        G.units(corpus, groups)
    assert 'MC_Maze' in str(raised.value) and 'MC_RTT' in str(raised.value)


def test_related_dandisets_are_the_rest_of_the_unit(tmp_path):
    corpus = write_corpus(tmp_path / 'corpus.json', {
        '000021': ['10.1/siegle'], '000022': ['10.1/siegle']})
    groups = write_groups(tmp_path / 'groups.json', {})
    assert G.related_dandisets(corpus, groups) == {'000021': {'000022'}, '000022': {'000021'}}


def test_without_a_corpus_the_listed_groups_stand_alone(tmp_path, capsys):
    groups = write_groups(tmp_path / 'groups.json', {
        'MC_Maze': {'dandisets': ['000070', '000128'], 'reason': []}})
    assert G.units(tmp_path / 'absent.json', groups) == {'000070': 'MC_Maze', '000128': 'MC_Maze'}
    assert 'absent.json' in capsys.readouterr().err
