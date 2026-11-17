"""
Screener check on pairs outside the eval set: every pair the production
classifier called REUSE that the eval set does not hold, and a random sample of
its MENTION and NEITHER pairs. Shows what a cutoff would have kept and dropped
in production, where the eval set's reuse pairs are all reviewer-confirmed.

    python scripts/screener_checks/corpus_check.py perplexity/pplx-decider-v1.3:free
"""
import concurrent.futures, json, os, random, sys
from collections import Counter
from pathlib import Path
import requests
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from src.review.eval_decision_model import classify, context_length
from src.review.eval_models import CACHE_ROOT, model_slug
from src.review.paper_metadata import resolve
from src.shared.run_fulltext_classification import cache_path
from paper_text_fetcher import PaperFetcher

REPO = Path(__file__).resolve().parents[2]
model = sys.argv[1]
n_neg = int(sys.argv[2]) if len(sys.argv) > 2 else 1500
corpus = json.loads((REPO / 'output/all_dandiset_papers_refreshed.json').read_text())
records = {}
for d in corpus['results']:
    for p in d.get('citing_papers', []):
        if p.get('doi') and p.get('cited_paper_doi') and p.get('text_status') == 'full_text':
            records[(p['doi'].lower(), d['dandiset_id'])] = {
                'doi': p['doi'], 'title': p.get('title', ''), 'dandiset_id': d['dandiset_id'],
                'dandiset_name': d.get('dandiset_name', ''), 'cited_paper_doi': p['cited_paper_doi']}
held = {(p['doi'].lower(), p['dandiset_id'])
        for p in json.loads((REPO / 'model_eval/eval_set.json').read_text())['pairs']}
labels = {(c['citing_doi'].lower(), c['dandiset_id']): c['classification']
          for c in json.loads((REPO / 'output/fulltext_classifications.json').read_text())['classifications']}
rng = random.Random(20261116)
pos = sorted(k for k, v in labels.items() if v == 'REUSE' and k in records and k not in held)
neg = sorted(k for k, v in labels.items() if v in ('MENTION', 'NEITHER') and k in records and k not in held)
neg = rng.sample(neg, min(n_neg, len(neg)))
pairs = [{**records[k], 'baseline': labels[k]} for k in pos + neg]
print(len(pos), 'REUSE,', len(neg), 'MENTION/NEITHER', file=sys.stderr)

cache_dir = CACHE_ROOT / model_slug(model, 'full')
metadata = resolve({p['cited_paper_doi'] for p in pairs}, REPO / '.paper_metadata_cache.json')
fetcher = PaperFetcher(use_cache=True, cache_dir=str(REPO / '.paper_cache'))
session = requests.Session()
session.headers['Authorization'] = f"Bearer {os.environ['OPENROUTER_API_KEY']}"
ctx = context_length(model)

def one(pair):
    path = cache_path(cache_dir, pair['doi'], pair['dandiset_id'])
    if path.exists():
        cached = json.loads(path.read_text())
        if cached['classification'] != 'ERROR':
            return pair, cached
    fetched = fetcher.get_paper_text_detailed(pair['doi'])
    text = fetched['text'] if fetched.get('status') == 'full_text' else None
    row = classify(pair, text, metadata.get(pair['cited_paper_doi'].lower()), model, session, ctx)
    path.write_text(json.dumps(row, indent=1))
    return pair, row

rows = []
with concurrent.futures.ThreadPoolExecutor(6) as ex:
    for i, (pair, row) in enumerate(ex.map(one, pairs), 1):
        rows.append((pair, row))
        if i % 100 == 0:
            print(f'{i}/{len(pairs)}', file=sys.stderr, flush=True)

errors = Counter((r.get('error') or '')[:60] for _, r in rows if r['classification'] == 'ERROR')
print('errors:', dict(errors))
ok = [(p, r) for p, r in rows if r['classification'] != 'ERROR']
p_reuse = lambda r: r['probabilities'].get('REUSE', 0)
print(f"{'cutoff':>7} {'REUSE pairs kept':>20} {'MENTION/NEITHER sent':>24}")
for cut in (0.5, 0.3, 0.2, 0.1, 0.05, 0.02, 0.01):
    a = [p_reuse(r) >= cut for p, r in ok if p['baseline'] == 'REUSE']
    b = [p_reuse(r) >= cut for p, r in ok if p['baseline'] != 'REUSE']
    print(f"{cut:>7} {sum(a):>8}/{len(a)} {sum(a)/len(a):>6.1%} {sum(b):>12}/{len(b)} {sum(b)/len(b):>6.1%}")
json.dump([{'doi': p['doi'], 'dandiset_id': p['dandiset_id'], 'baseline': p['baseline'],
            'p_reuse': p_reuse(r), 'reused': r.get('reused')} for p, r in ok],
          open(REPO / '.model_eval_cache/screener_corpus_check_rows.json', 'w'))
