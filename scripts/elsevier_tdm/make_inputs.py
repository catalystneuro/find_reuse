#!/usr/bin/env python3
"""
Write the inputs for run_elsevier_tdm.py: for each corpus, a copy holding only
the citing papers on Elsevier DOIs whose full text we could not retrieve.

    python scripts/elsevier_tdm/make_inputs.py
"""
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent / 'inputs'
CORPORA = {'dandi': REPO / 'output/all_dandiset_papers_refreshed.json',
           'crcns': REPO / 'output/crcns/rediscovery/refreshed.json'}
ELSEVIER = ('10.1016/',)


def main():
    OUT.mkdir(exist_ok=True)
    for name, path in CORPORA.items():
        data = json.loads(path.read_text())
        keep = []
        for r in data['results']:
            papers = [p for p in r.get('citing_papers') or []
                      if p['doi'].lower().startswith(ELSEVIER) and p.get('text_status') != 'full_text']
            if papers:
                keep.append(dict(r, citing_papers=papers))
        out = OUT / f'{name}_elsevier.json'
        out.write_text(json.dumps({'count': len(keep), 'results': keep}, indent=1))
        n = len({p['doi'] for r in keep for p in r['citing_papers']})
        print(f'{out.name}: {n} papers on {len(keep)} datasets')


if __name__ == '__main__':
    main()
