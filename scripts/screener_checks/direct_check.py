"""
Screener check on the direct pathway: papers that print a dataset identifier.

Every direct pair of an archive is put to the screener exactly as the production
run would put it (src.shared.screen_reuse.screen with direct=True, same cache),
and its P(REUSE) is set against the label the production classifier gave and,
where there is one, a reviewer's call.

    python scripts/screener_checks/direct_check.py DANDI
    python scripts/screener_checks/direct_check.py CRCNS
"""
import concurrent.futures, glob, json, sys
from collections import Counter
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from src.shared import screen_reuse as S
from src.shared.run_fulltext_classification import build_direct_worklist, cache_path
from paper_text_fetcher import PaperFetcher

REPO = Path(__file__).resolve().parents[2]
archive = {'DANDI': 'DANDI Archive'}.get(sys.argv[1], sys.argv[1])
model = sys.argv[2] if len(sys.argv) > 2 else S.DEFAULT_SCREENER
FILES = {'DANDI Archive': ('output/results_dandi_openalex.json', 'output/fulltext_direct_openalex.json'),
         'CRCNS': ('output/crcns/direct_search_20261108b.json', 'output/crcns/direct_fulltext.json')}
discovery, classified = FILES[archive]
labels = {(c['citing_doi'].lower(), c['dandiset_id']): c['classification']
          for c in json.loads((REPO / classified).read_text())['classifications']}
everything = build_direct_worklist(REPO / discovery, None, archive_name=archive)
work = [w for w in everything if (w['doi'].lower(), w['dandiset_id']) in labels]
print(f'{len(everything)} pairs in discovery, {len(labels)} classified, {len(work)} in both', file=sys.stderr)
calls = {}
for path in glob.glob(str(REPO / 'reuse_confirmation/*/*-reviews.json')):
    for doi, by in json.loads(Path(path).read_text())['reviews'].items():
        for dataset, review in by.items():
            calls[(doi.lower(), dataset)] = review.get('call')

cache_dir = S.screener_cache_dir(REPO, model, True, archive)
cache_dir.mkdir(parents=True, exist_ok=True)
fetcher = PaperFetcher(use_cache=True, cache_dir=str(REPO / '.paper_cache'))
session, ctx = S.new_session(), S.context_length(model)

def one(w):
    path = cache_path(cache_dir, w['doi'], w['dandiset_id'])
    if path.exists():
        return w, json.loads(path.read_text())
    fetched = fetcher.get_paper_text_detailed(w['doi'])
    if fetched.get('status') != 'full_text':
        return w, {'error': 'no full text'}
    for attempt in range(3):
        try:
            answer = S.screen(fetched['text'], model, session, ctx, direct=True, archive=archive,
                              dataset_id=w['dandiset_id'], matched_patterns=w.get('matched_patterns'),
                              title=w['title'], doi=w['doi'])
            path.write_text(json.dumps(answer, indent=1))
            return w, answer
        except Exception as e:
            error = {'error': f'{type(e).__name__}: {e}'[:120]}
    return w, error

with concurrent.futures.ThreadPoolExecutor(4) as ex:
    rows = list(ex.map(one, work))
key = lambda w: (w['doi'].lower(), w['dandiset_id'])
ok = [(w, a) for w, a in rows if 'p_reuse' in a]
print(f'{archive}: {len(rows)} pairs, {len(rows) - len(ok)} not answered',
      dict(Counter(a.get('error', '')[:40] for _, a in rows if 'error' in a)))
agree = sum(labels[key(w)] == a['choice'] for w, a in ok)
print(f'screener choice equals classifier label on {agree} of {len(ok)}')
groups = {'classifier REUSE': [a['p_reuse'] for w, a in ok if labels[key(w)] == 'REUSE'],
          'classifier PRIMARY': [a['p_reuse'] for w, a in ok if labels[key(w)] == 'PRIMARY'],
          'classifier NEITHER': [a['p_reuse'] for w, a in ok if labels[key(w)] == 'NEITHER'],
          'reviewer: reuse': [a['p_reuse'] for w, a in ok if calls.get(key(w)) in ('reuse', 'ambiguous_reuse')]}
print(f"{'cutoff':>7} " + ' '.join(f'{k:>22}' for k in groups) + f"{'sent to classifier':>22}")
for cut in (0.5, 0.3, 0.2, 0.1, 0.05, 0.02, 0.01):
    cell = lambda xs: (f'{sum(x >= cut for x in xs)}/{len(xs)} '
                       f'{sum(x >= cut for x in xs) / len(xs):.0%}') if xs else '-'
    print(f'{cut:>7} ' + ' '.join(f'{cell(xs):>22}' for xs in groups.values())
          + f"{cell([a['p_reuse'] for _, a in ok]):>22}")
print('classifier REUSE pairs under 0.1:',
      [(w['doi'], w['dandiset_id'], round(a['p_reuse'], 3), a['choice'], calls.get(key(w)))
       for w, a in ok if labels[key(w)] == 'REUSE' and a['p_reuse'] < 0.1])
