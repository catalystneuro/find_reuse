"""Tests for clearing cached classifications whose pair now cites another paper."""

import pytest

import src.review.clear_stale_classifications as C
from src.shared.run_fulltext_classification import cache_path


def corpus(*pairs):
    """A corpus holding (citing DOI, dandiset, cited DOI) pairs."""
    records = {}
    for citing, dandiset, cited in pairs:
        records.setdefault(dandiset, {'dandiset_id': dandiset, 'citing_papers': []})
        records[dandiset]['citing_papers'].append(
            {'doi': citing, 'cited_paper_doi': cited})
    return list(records.values())


@pytest.fixture
def old():
    return corpus(('10.1/both', '000768', '10.1/wrong-pick'),
                  ('10.1/same', '000768', '10.1/declared'),
                  ('10.1/gone', '000768', '10.1/wrong-pick'))


@pytest.fixture
def new():
    return corpus(('10.1/both', '000768', '10.1/right'),
                  ('10.1/same', '000768', '10.1/DECLARED'),
                  ('10.1/new', '000768', '10.1/right'))


class TestChangedPairs:
    def test_finds_only_pairs_in_both_that_cite_another_paper(self, old, new):
        assert C.changed_pairs(old, new) == [('10.1/both', '000768')]


class TestDeleteCached:
    def test_deletes_the_changed_pairs_cache_entries_only(self, tmp_path):
        for doi in ('10.1/both', '10.1/same'):
            cache_path(tmp_path, doi, '000768').write_text('{}')

        deleted = C.delete_cached([('10.1/both', '000768'), ('10.1/uncached', '000768')],
                                  tmp_path)

        assert [path.name for path in deleted] == ['10.1_both__000768.json']
        assert sorted(path.name for path in tmp_path.iterdir()) == [
            '10.1_same__000768.json']
