#!/usr/bin/env python3
"""
Load the articles from elsevier_texts.zip into the paper cache and mark them in
the corpora, so the next classification run picks them up.

    python scripts/elsevier_tdm/ingest_elsevier_zip.py path/to/elsevier_texts.zip
"""
import json
import sys
import zipfile
from pathlib import Path

from paper_text_fetcher.cache import TextCache

REPO = Path(__file__).resolve().parents[2]
CORPORA = [REPO / 'output/all_dandiset_papers_refreshed.json', REPO / 'output/crcns/rediscovery/refreshed.json']


def main():
    cache = TextCache(REPO / '.paper_cache')
    texts = {}
    with zipfile.ZipFile(sys.argv[1]) as z:
        for name in z.namelist():
            if name.startswith('texts/'):
                rec = json.loads(z.read(name))
                cache.put(rec['doi'], rec['text'], 'elsevier', True)
                texts[rec['doi'].lower()] = len(rec['text'])
    for path in CORPORA:
        data = json.loads(path.read_text())
        n = 0
        for r in data['results']:
            for p in r.get('citing_papers') or []:
                if p['doi'].lower() in texts and p.get('text_status') != 'full_text':
                    p.update(text_status='full_text', has_full_text=True, text_source='elsevier',
                             text_length=texts[p['doi'].lower()])
                    n += 1
        path.write_text(json.dumps(data, indent=2))
        print(f'{path.name}: {n} citing-paper records now have full text')
    print(f'{len(texts)} articles loaded into the paper cache')


if __name__ == '__main__':
    main()
