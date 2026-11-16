"""
Screener check on CRCNS citing pairs, put to the screener as production would
(src.shared.screen_reuse.screen, production cache): a sample of the pairs the
classifier called REUSE and of the pairs it called MENTION or NEITHER.

    python scripts/screener_checks/crcns_citing_check.py
"""
import concurrent.futures, json, random, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from src.review.paper_metadata import citation, resolve
from src.shared import screen_reuse as S
from src.shared.run_fulltext_classification import cache_path
from paper_text_fetcher import PaperFetcher

REPO = Path(__file__).resolve().parents[2]
model = S.DEFAULT_SCREENER
corpus = json.loads((REPO / sys.argv[1]).read_text())
labels = {(c['citing_doi'].lower(), c['dandiset_id']): c['classification']
          for c in json.loads((REPO / sys.argv[2]).read_text())['classifications']
          if c.get('text_source') != 'elsevier_federated'}
records = {}
for d in corpus['results']:
    for p in d.get('citing_papers', []):
        key = ((p.get('doi') or '').lower(), d['dandiset_id'])
        if key in labels and p.get('cited_paper_doi') and d.get('dandiset_name'):
            records[key] = {'doi': p['doi'], 'title': p.get('title', ''), 'dandiset_id': d['dandiset_id'],
                            'dandiset_name': d['dandiset_name'], 'cited': p['cited_paper_doi']}
rng = random.Random(20261116)
pos = sorted(k for k in records if labels[k] == 'REUSE')
neg = sorted(k for k in records if labels[k] in ('MENTION', 'NEITHER'))
sample = rng.sample(pos, min(400, len(pos))) + rng.sample(neg, min(1000, len(neg)))
print(len(pos), 'REUSE and', len(neg), 'other pairs with a record; sampled', len(sample), file=sys.stderr)
meta = resolve({records[k]['cited'] for k in sample}, REPO / '.paper_metadata_cache.json')
cache_dir = S.screener_cache_dir(REPO, model)
cache_dir.mkdir(parents=True, exist_ok=True)
fetcher = PaperFetcher(use_cache=True, cache_dir=str(REPO / '.paper_cache'))
session, ctx = S.new_session(), S.context_length(model)

def one(k):
    r = records[k]
    path = cache_path(cache_dir, r['doi'], r['dandiset_id'])
    if path.exists():
        return k, json.loads(path.read_text())
    fetched = fetcher.get_paper_text_detailed(r['doi'])
    if fetched.get('status') != 'full_text':
        return k, None
    primary = meta.get(r['cited'].lower())
    for attempt in range(3):
        try:
            a = S.screen(fetched['text'], model, session, ctx, dandiset_id=r['dandiset_id'],
                         dandiset_name=r['dandiset_name'],
                         published_in=(f"{citation(primary)}, \"{primary['title']}\"" if primary else r['cited']),
                         title=r['title'], doi=r['doi'])
            path.write_text(json.dumps(a, indent=1))
            return k, a
        except Exception:
            a = None
    return k, a

with concurrent.futures.ThreadPoolExecutor(4) as ex:
    rows = [(k, a) for k, a in ex.map(one, sample) if a]
print(f'{len(rows)} of {len(sample)} answered')
print(f"{'cutoff':>7} {'classifier REUSE kept':>24} {'MENTION/NEITHER sent':>24}")
for cut in (0.5, 0.3, 0.2, 0.1, 0.05, 0.02):
    a = [x['p_reuse'] >= cut for k, x in rows if labels[k] == 'REUSE']
    b = [x['p_reuse'] >= cut for k, x in rows if labels[k] != 'REUSE']
    print(f"{cut:>7} {f'{sum(a)}/{len(a)} {sum(a)/len(a):.1%}':>24} {f'{sum(b)}/{len(b)} {sum(b)/len(b):.1%}':>24}")
tokens = [x['usage'].get('prompt_tokens', 0) for _, x in rows]
print('input tokens per pair', round(sum(tokens) / len(tokens)))
