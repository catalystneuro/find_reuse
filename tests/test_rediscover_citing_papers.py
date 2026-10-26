"""Tests for choosing which citing papers rediscovery fetches in parallel."""

import importlib.util
import json
from pathlib import Path

import pytest
from paper_text_fetcher import TextCache

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'rediscover_citing_papers.py'
spec = importlib.util.spec_from_file_location('rediscover_citing_papers', SCRIPT)
rediscover = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rediscover)


@pytest.fixture
def cache(tmp_path):
    return TextCache(tmp_path)


def backdate(cache, doi, cached_at):
    path = cache.path_for(doi)
    entry = json.loads(path.read_text())
    entry['cached_at'] = cached_at
    path.write_text(json.dumps(entry))


class TestCached:
    def test_full_text_entry(self, cache):
        cache.put('10.1/full', 'body text', 'europe_pmc', has_full_text=True)
        assert rediscover.cached(cache, '10.1/full')

    def test_recent_metadata_only_entry(self, cache):
        cache.put('10.1/meta', 'abstract', 'crossref', has_full_text=False)
        assert rediscover.cached(cache, '10.1/meta')

    def test_expired_metadata_only_entry(self, cache):
        cache.put('10.1/meta', 'abstract', 'crossref', has_full_text=False)
        backdate(cache, '10.1/meta', '2020-01-01T00:00:00+00:00')
        assert not rediscover.cached(cache, '10.1/meta')

    def test_entry_without_text(self, cache):
        cache.put('10.1/empty', '', '', has_full_text=False)
        assert not rediscover.cached(cache, '10.1/empty')

    def test_no_entry(self, cache):
        assert not rediscover.cached(cache, '10.1/missing')
