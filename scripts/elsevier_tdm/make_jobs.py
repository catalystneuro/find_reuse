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
                jobs.append({'corpus': corpus, 'doi': p['doi'], 'title': p.get('title', ''),
                             'dataset_id': ds['dandiset_id'], 'dataset_name': ds.get('dandiset_name', ''),
                             'primary_paper_doi': p['cited_paper_doi']})
    (HERE / 'jobs.json').write_text(json.dumps(jobs, indent=1))
    (HERE / 'dois.txt').write_text('\n'.join(sorted({j['doi'] for j in jobs})) + '\n')
    print(f'{len(jobs)} pairs on {len({j["doi"] for j in jobs})} papers')


if __name__ == '__main__':
    main()
