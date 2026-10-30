"""Tests for leaving dandisets that hold no data out of every stage that mines them."""

import json
import sys

import pytest
import requests

import src.direct_pipeline.find_missing_papers as F
import src.direct_pipeline.merge_paper_sources as M
import src.indirect_pipeline.dandi_primary_papers as P
import src.shared.dandisets as D
import src.shared.run_fulltext_classification as R


def response(status: int, body) -> requests.Response:
    """A real Response, so that raise_for_status behaves as it does against DANDI."""
    answer = requests.Response()
    answer.status_code = status
    answer._content = json.dumps(body).encode()
    answer.url = 'https://api.dandiarchive.org/api/'
    return answer


class Listing:
    """A session that answers each request with the next page in line."""

    def __init__(self, *pages):
        self.pages = list(pages)

    def get(self, url, **kwargs):
        return self.pages.pop(0)


def dandiset(identifier: str, draft=(0, 0), published=None) -> dict:
    """A listing record with the given (asset_count, size) per version."""
    record = {'identifier': identifier, 'created': '2024-01-01T00:00:00Z',
              'draft_version': {'asset_count': draft[0], 'size': draft[1],
                                'name': f'Dandiset {identifier}',
                                'modified': '2024-02-01T00:00:00Z'},
              'most_recent_published_version': None}
    if published:
        record['most_recent_published_version'] = {
            'version': '0.240101.0000', 'asset_count': published[0],
            'size': published[1], 'name': f'Dandiset {identifier}'}
    return record


def page(records, has_next=False) -> requests.Response:
    return response(200, {'count': 4, 'results': records,
                          'next': 'https://api.dandiarchive.org/api/dandisets/?page=2'
                                  if has_next else None})


class TestDandisetsWithData:
    def test_every_page_is_read_and_only_dandisets_holding_data_are_kept(
            self, monkeypatch):
        monkeypatch.setattr(D.time, 'sleep', lambda seconds: None)
        session = Listing(
            page([dandiset('000125'),
                  dandiset('000070', published=(10, 4096))], has_next=True),
            page([dandiset('001468', draft=(3, 2048)),
                  dandiset('001286')]))

        assert D.dandisets_with_data(session) == {'000070', '001468'}

    def test_a_listing_dandi_fails_to_give_raises(self):
        with pytest.raises(requests.HTTPError):
            D.dandisets_with_data(Listing(response(503, {})))


class TestPrimaryPaperEnumeration:
    def test_an_empty_dandiset_is_not_mined_even_when_its_last_scan_is_cached(
            self, monkeypatch):
        empty, full = dandiset('999901'), dandiset('999902', draft=(5, 1024))
        monkeypatch.setattr(P, 'get_all_dandisets',
                            lambda session, show_progress: [empty, full])
        monkeypatch.setattr(P, 'get_dandiset_version_metadata',
                            lambda session, dandiset_id, version: {
                                'relatedResource': [{
                                    'relation': 'dcite:IsDescribedBy',
                                    'identifier': 'doi:10.1/paper',
                                    'url': 'https://doi.org/10.1/paper'}]})
        unchanged = {'dandiset_id': '999901',
                     'draft_modified': empty['draft_version']['modified'],
                     'paper_relations': [{'doi': '10.1/paper'}]}

        results = P.find_dandisets_with_primary_papers(
            show_progress=False, rate_limit=0, previous_results=[unchanged])

        assert [r['dandiset_id'] for r in results] == ['999902']


class TestMissingPaperSearch:
    def test_a_model_is_never_asked_about_an_empty_dandiset(self):
        session = Listing(page([dandiset('001463'),
                                dandiset('001641', published=(70, 1 << 30))]))

        assert [d['id'] for d in F.get_all_dandiset_ids(session)] == ['001641']


class TestMergePaperSources:
    def test_a_model_picked_paper_for_an_empty_dandiset_is_not_mined(
            self, monkeypatch, tmp_path):
        (tmp_path / 'output').mkdir()
        (tmp_path / 'output/dandi_primary_papers_results.json').write_text(
            json.dumps({'results': []}))
        pick = {'found': True, 'confidence': 8, 'doi_validated': True,
                'doi': '10.1/picked', 'title': 'A picked paper'}
        (tmp_path / '.missing_paper_cache.json').write_text(
            json.dumps({'001463': pick, '001641': pick}))
        records = {'001463': dandiset('001463'),
                   '001641': dandiset('001641', published=(70, 1 << 30))}

        class Session:
            headers = {}

            def get(self, url, **kwargs):
                return response(200, records[url.rstrip('/').rsplit('/', 1)[-1]])

        monkeypatch.setattr(M.requests, 'Session', Session)
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(sys, 'argv', ['merge_paper_sources', '-o', 'merged.json'])

        M.main()

        merged = json.loads((tmp_path / 'merged.json').read_text())
        assert [r['dandiset_id'] for r in merged['results']] == ['001641']


class TestDirectWorklist:
    @pytest.mark.parametrize('discovery', [
        {'classifications': [
            {'citing_doi': '10.1038/s41597-024-03775-2', 'dandiset_id': '123456'},
            {'citing_doi': '10.1038/s41597-024-03775-2', 'dandiset_id': '000026'}]},
        {'results': [
            {'doi': '10.1038/s41597-024-03775-2', 'title': 'HELM',
             'archives': {'DANDI Archive': {'dataset_ids': ['123456', '000026'],
                                            'matches': []}}}]},
    ], ids=['previous classifications', 'raw discovery'])
    def test_only_dandisets_holding_data_are_asked_about(self, tmp_path, discovery):
        path = tmp_path / 'direct.json'
        path.write_text(json.dumps(discovery))

        work = R.build_direct_worklist(path, with_data={'000026'})

        assert [item['dandiset_id'] for item in work] == ['000026']

    def test_a_direct_run_asks_dandi_which_dandisets_hold_data(
            self, monkeypatch, tmp_path):
        path = tmp_path / 'direct.json'
        path.write_text(json.dumps({'results': [
            {'doi': '10.1038/s41597-024-03775-2', 'title': 'HELM',
             'archives': {'DANDI Archive': {'dataset_ids': ['123456', '000026'],
                                            'matches': []}}}]}))
        selected = []

        def select(work, limit, paper_cache):
            selected.extend(work)
            raise SystemExit

        monkeypatch.setattr(R, 'dandisets_with_data', lambda: {'000026'})
        monkeypatch.setattr(R, 'select_with_full_text', select)
        monkeypatch.setattr(sys, 'argv', [
            'run_fulltext_classification', '--mode', 'direct',
            '--results-file', str(path), '--cache-dir', str(tmp_path / 'cache'),
            '--paper-cache', str(tmp_path / 'papers'),
            '-o', str(tmp_path / 'out.json')])

        with pytest.raises(SystemExit):
            R.main()

        assert [item['dandiset_id'] for item in selected] == ['000026']
