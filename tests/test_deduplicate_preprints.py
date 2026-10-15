"""Tests for finding the published version of a preprint."""

import json

import src.direct_pipeline.deduplicate_preprints as D


class TestIsPreprintDoi:
    def test_knows_both_biorxiv_prefixes(self):
        assert D.is_preprint_doi('10.1101/2023.03.01.530616')
        assert D.is_preprint_doi('10.64898/2026.03.02.708943')

    def test_a_journal_article_is_not_a_preprint(self):
        assert not D.is_preprint_doi('10.1038/s41593-024-01626-2')


class TestPublishedVersions:
    def cached(self, tmp_path, monkeypatch, cache):
        path = tmp_path / 'preprint_doi_map.json'
        path.write_text(json.dumps(cache))
        monkeypatch.setattr(D, 'CACHE_FILE', path)
        return path

    def test_answers_from_the_cache_without_asking(self, tmp_path, monkeypatch):
        self.cached(tmp_path, monkeypatch, {
            '10.1101/2023.03.01.530616': '10.1038/s41593-024-01626-2',
            '10.1101/2024.01.01.000001': None})
        monkeypatch.setattr(D, 'resolve_preprint_doi', lambda *args: 1 / 0)

        found = D.published_versions({'10.1101/2023.03.01.530616',
                                      '10.1101/2024.01.01.000001',
                                      '10.1038/s41593-024-01626-2'})

        assert found == {'10.1101/2023.03.01.530616': '10.1038/s41593-024-01626-2'}

    def test_looks_up_a_preprint_once_and_keeps_the_answer(self, tmp_path, monkeypatch):
        path = self.cached(tmp_path, monkeypatch, {})
        asked = []

        def resolve(doi, session, cache):
            asked.append(doi)
            cache[doi] = '10.1/published'

        monkeypatch.setattr(D, 'resolve_preprint_doi', resolve)
        monkeypatch.setattr(D.time, 'sleep', lambda seconds: None)

        first = D.published_versions({'10.1101/2025.01.01.000002'})
        second = D.published_versions({'10.1101/2025.01.01.000002'})

        assert first == second == {'10.1101/2025.01.01.000002': '10.1/published'}
        assert asked == ['10.1101/2025.01.01.000002']
        assert json.loads(path.read_text()) == first
