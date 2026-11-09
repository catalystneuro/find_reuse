#!/usr/bin/env python3
"""
Merge the decisions.json Yarik returns into both corpora's classification outputs.

    python scripts/elsevier_tdm/ingest_decisions.py decisions.json

Adds one classification per (paper, dataset) pair to output/fulltext_classifications.json
(DANDI) and output/crcns/fulltext_classifications.json, replacing any earlier
entry for the pair, and writes output/elsevier_text_facts.json with the
text-derived facts (length, DANDI identifiers named) that the analyses would
otherwise read from the paper cache. Each file is backed up as *.pre_elsevier first.
"""
import json
import shutil
import sys
from pathlib import Path

MAIN = Path.home() / 'dev/find_reuse_repo'
OUT = {'dandi': MAIN / 'output/fulltext_classifications.json',
       'crcns': MAIN / 'output/crcns/fulltext_classifications.json'}


def main():
    src = json.loads(Path(sys.argv[1]).read_text())
    facts = {}
    # Direct-pathway decisions go to the direct outputs: DANDI's direct classifications, and
    # a CRCNS direct file that data preparation picks up (output/crcns/direct_fulltext_2*.json).
    direct = [d for d in src['decisions'] if d.get('mode') == 'direct']
    for corpus, path in (('dandi', MAIN / 'output/fulltext_direct_openalex.json'),
                         ('crcns', MAIN / 'output/crcns/direct_fulltext_20261109_elsevier.json')):
        rows = [d for d in direct if d['corpus'] == corpus]
        data = json.loads(path.read_text()) if path.exists() else {'classifications': []}
        if path.exists() and not path.with_suffix('.json.pre_elsevier').exists():
            shutil.copy(path, path.with_suffix('.json.pre_elsevier'))
        new = {(d['doi'], d['dataset_id']) for d in rows}
        data['classifications'] = [c for c in data['classifications']
                                   if (c.get('citing_doi'), c.get('dandiset_id')) not in new] + [
            {**{k: v for k, v in d.items() if k not in ('corpus', 'dataset_id', 'doi')},
             'citing_doi': d['doi'], 'paper_doi': d['doi'], 'dandiset_id': d['dataset_id'],
             'text_source': 'elsevier_federated'} for d in rows]
        path.write_text(json.dumps(data, indent=2))
        print(f'{corpus}: {len(rows)} Elsevier direct pairs merged into {path.relative_to(MAIN)}')
    for corpus, path in OUT.items():
        rows = [d for d in src['decisions'] if d['corpus'] == corpus and d.get('mode', 'citing') != 'direct']
        data = json.loads(path.read_text())
        backup = path.with_suffix('.json.pre_elsevier')
        if not backup.exists():
            shutil.copy(path, backup)
        new = {(d['doi'], d['dataset_id']) for d in rows}
        kept = [c for c in data['classifications'] if (c.get('citing_doi'), c.get('dandiset_id')) not in new]
        for d in rows:
            kept.append({**{k: v for k, v in d.items() if k not in ('corpus', 'dataset_id', 'doi')},
                         'citing_doi': d['doi'], 'paper_doi': d['doi'], 'dandiset_id': d['dataset_id'],
                         'primary_paper_doi': d['primary_paper_doi'], 'text_source': 'elsevier_federated'})
            facts[d['doi']] = {k: d[k] for k in ('text_chars', 'text_names_dandiset_ids', 'text_names_dandi')}
        data['classifications'] = kept
        data.setdefault('summary', {})['elsevier_federated'] = {
            'pairs': len(rows), 'classifier_sha256': src['classifier_sha256']}
        path.write_text(json.dumps(data, indent=2))
        print(f'{corpus}: {len(rows)} Elsevier pairs merged into {path.relative_to(MAIN)}')
    (MAIN / 'output/elsevier_text_facts.json').write_text(json.dumps(
        {'fetch_status': src.get('fetch_status', {}), 'papers': facts}, indent=1))


if __name__ == '__main__':
    main()
