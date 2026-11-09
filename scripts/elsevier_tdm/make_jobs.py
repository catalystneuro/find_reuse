#!/usr/bin/env python3
"""
Write jobs.json: every (Elsevier paper, dataset) pair Yarik's machine should
classify, in the form classify_local.py reads. Run here, not at Dartmouth.

    python scripts/elsevier_tdm/make_jobs.py
"""
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
MAIN = Path.home() / 'dev/find_reuse_repo'      # the corpora live in the main checkout
CORPORA = {'dandi': MAIN / 'output/all_dandiset_papers_refreshed.json',
           'crcns': MAIN / 'output/crcns/rediscovery/refreshed.json'}


def main():
    jobs, seen = [], set()
    for corpus, path in CORPORA.items():
        for ds in json.loads(path.read_text())['results']:
            for p in ds.get('citing_papers') or []:
                key = (corpus, p['doi'], ds['dandiset_id'])
                if (not p['doi'].lower().startswith('10.1016/') or p.get('text_status') == 'full_text'
                        or key in seen):
                    continue
                seen.add(key)
                jobs.append({'corpus': corpus, 'mode': 'citing', 'doi': p['doi'], 'title': p.get('title', ''),
                             'dataset_id': ds['dandiset_id'], 'dataset_name': ds.get('dandiset_name', ''),
                             'primary_paper_doi': p['cited_paper_doi']})
    # Direct pathway: Elsevier papers the full-text searches found naming a dataset, but
    # whose text we could not read, so they were never classified.
    searches = {'dandi': (MAIN / 'output/results_dandi_openalex.json', MAIN / 'output/fulltext_direct_openalex.json',
                          'DANDI Archive'),
                'crcns': (sorted((MAIN / 'output/crcns').glob('direct_search_2*.json'))[-1],
                          sorted((MAIN / 'output/crcns').glob('direct_fulltext_2*.json'))[-1], 'CRCNS')}
    for corpus, (search, cls, archive) in searches.items():
        done = {r['citing_doi'].lower() for r in json.loads(cls.read_text())['classifications']}
        for r in json.loads(search.read_text())['results']:
            doi = (r.get('doi') or '')
            info = (r.get('archives') or {}).get(archive)
            if not info or not doi.lower().startswith('10.1016/') or doi.lower() in done:
                continue
            for did in info.get('dataset_ids', []):
                key = (corpus + '-direct', doi, did)
                if key in seen:
                    continue
                seen.add(key)
                jobs.append({'corpus': corpus, 'mode': 'direct', 'doi': doi, 'title': r.get('title', ''),
                             'dataset_id': did, 'dataset_name': '', 'primary_paper_doi': '',
                             'matched_patterns': [m.get('matched_string') for m in info.get('matches') or []
                                                  if m.get('id') == did and m.get('matched_string')] or None})
    (HERE / 'jobs.json').write_text(json.dumps(jobs, indent=1))
    (HERE / 'dois.txt').write_text('\n'.join(sorted({j['doi'] for j in jobs})) + '\n')
    print(f'{len(jobs)} pairs on {len({j["doi"] for j in jobs})} papers')


if __name__ == '__main__':
    main()
