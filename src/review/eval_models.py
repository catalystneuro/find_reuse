#!/usr/bin/env python3
"""
Score a model as the reuse classifier against what reviewers decided.

New models arrive every week or so, and the question for each is the same: would
it make a better classifier than the one the corpus was built with? Answering it
from the full corpus costs a full run. This answers it from a frozen sample of
pairs whose answer is known, in minutes and for a few dollars.

The eval set is built once from the reviewers' calls and frozen in
model_eval/eval_set.json, with each pair's corpus record, so it does not drift
as the corpus is rediscovered. It holds four kinds of pair:

  reuse        a reviewer confirmed the paper reused the dataset
  not_reuse    a reviewer called it neither, mention or primary
  ambiguous    a reviewer called it ambiguous reuse (reported, not scored)
  negative     the production classifier called it MENTION or NEITHER and no
               reviewer has seen it

Reviewers only see pairs the production classifier called REUSE, so the
reviewed pairs measure how much confirmed reuse a model keeps and how many of
the production classifier's false positives it avoids. They cannot show reuse
the production classifier missed. The negatives are the only window onto that:
a model that calls one REUSE has found either a miss or a false positive, and
only a reviewer can say which. Those pairs are listed so one can.

Usage:
    python -m src.review.eval_models build
    python -m src.review.eval_models run --model anthropic/claude-haiku-5.5 --effort high
    python -m src.review.eval_models run --model ... --tier full
    python -m src.review.eval_models score --from-output output/fulltext_classifications.json \\
        --label "gpt-5.6-luna max (production)"
    python -m src.review.eval_models report
"""

from __future__ import annotations

import argparse
import datetime
import glob
import json
import random
import re
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

import requests

from src.shared.classify_fulltext_reuse import VALID_REASONING_EFFORTS
from src.shared.run_fulltext_classification import cache_path

REPO = Path(__file__).resolve().parents[2]
EVAL_DIR = REPO / 'model_eval'
EVAL_SET = EVAL_DIR / 'eval_set.json'
RESULTS = EVAL_DIR / 'results.jsonl'
CACHE_ROOT = REPO / '.model_eval_cache'

# Reviewer calls, folded into what a classifier should have said.
GOLD = {'reuse': 'reuse', 'ambiguous_reuse': 'ambiguous',
        'neither': 'not_reuse', 'mention': 'not_reuse', 'primary': 'not_reuse'}

# The quick tier is what a new model gets first. Sized so a model priced like
# the current ones costs about a dollar, and stratified so each number below
# rests on enough pairs to move only when the model does.
QUICK = {'reuse': 80, 'not_reuse': 60, 'ambiguous': 20, 'negative': 40}
NEGATIVES = {'MENTION': 150, 'NEITHER': 50}

# A paper averages about 35,000 input tokens and a reasoning model writes a
# couple of thousand back; used only to refuse a run that would cost too much.
EST_INPUT_TOKENS = 35_000
EST_OUTPUT_TOKENS = 2_500


def load_reviews(pattern: str) -> dict[tuple[str, str], str]:
    """Every reviewer call, keyed by (citing DOI, dandiset)."""
    calls = {}
    for path in sorted(glob.glob(pattern)):
        for doi, by_dataset in json.loads(Path(path).read_text())['reviews'].items():
            for dandiset, review in by_dataset.items():
                if review.get('call') in GOLD:
                    calls[(doi.lower(), dandiset)] = review['call']
    return calls


def build_eval_set(corpus: dict, classifications: list[dict], calls: dict,
                   seed: int = 20261022) -> dict:
    """
    Freeze the pairs a model is scored on.

    A pair needs a corpus record to be asked about, so reviewed pairs whose
    dandiset has since changed primary paper are left out rather than asked
    about a paper the reviewer never saw.
    """
    records = {}
    for dataset in corpus['results']:
        for paper in dataset.get('citing_papers', []):
            if paper.get('doi') and paper.get('cited_paper_doi'):
                records[(paper['doi'].lower(), dataset['dandiset_id'])] = {
                    'doi': paper['doi'], 'title': paper.get('title', ''),
                    'dandiset_id': dataset['dandiset_id'],
                    'dandiset_name': dataset.get('dandiset_name', ''),
                    'cited_paper_doi': paper['cited_paper_doi'],
                }
    baseline = {(c['citing_doi'].lower(), c['dandiset_id']): c['classification']
                for c in classifications}

    pairs = []
    for key, call in sorted(calls.items()):
        if key in records and baseline.get(key, 'ERROR') != 'ERROR':
            pairs.append({**records[key], 'kind': GOLD[call], 'review': call,
                          'baseline': baseline[key]})

    rng = random.Random(seed)
    for label, n in NEGATIVES.items():
        pool = sorted(k for k, v in baseline.items()
                      if v == label and k in records and k not in calls)
        for key in rng.sample(pool, min(n, len(pool))):
            pairs.append({**records[key], 'kind': 'negative', 'review': None,
                          'baseline': label})

    by_kind: dict[str, list[dict]] = {}
    for pair in pairs:
        by_kind.setdefault(pair['kind'], []).append(pair)
    for kind, n in QUICK.items():
        for pair in rng.sample(by_kind.get(kind, []), min(n, len(by_kind.get(kind, [])))):
            pair['quick'] = True
    for pair in pairs:
        pair.setdefault('quick', False)

    return {'version': 1, 'built': datetime.date.today().isoformat(), 'seed': seed,
            'counts': dict(Counter(p['kind'] for p in pairs)),
            'quick_counts': dict(Counter(p['kind'] for p in pairs if p['quick'])),
            'pairs': pairs}


def tier_pairs(eval_set: dict, tier: str) -> list[dict]:
    return [p for p in eval_set['pairs'] if tier == 'full' or p['quick']]


def as_corpus(pairs: list[dict]) -> dict:
    """The pairs in the shape run_fulltext_classification reads a corpus in."""
    datasets: dict[str, dict] = {}
    for p in pairs:
        dataset = datasets.setdefault(p['dandiset_id'], {
            'dandiset_id': p['dandiset_id'], 'dandiset_name': p['dandiset_name'],
            'citing_papers': []})
        dataset['citing_papers'].append({'doi': p['doi'], 'title': p['title'],
                                         'cited_paper_doi': p['cited_paper_doi']})
    return {'count': len(datasets), 'results': list(datasets.values())}


def score(pairs: list[dict], results: dict[tuple[str, str], dict],
          weights: dict[str, int] | None = None) -> dict:
    """
    How a model's answers compare with the reviewers' and the baseline's.

    A pair the model failed on counts against it: an ERROR in production is a
    pair nobody reviews, which is the same loss as calling it MENTION.

    Precision is taken from recall and the false-positive rate, weighted by how
    many reviewed pairs of each kind the whole set holds. The quick tier
    over-samples rejected pairs to measure false positives, so its raw precision
    would understate the model's and not be comparable with the full tier's.
    """
    def label(p):
        r = results.get((p['doi'].lower(), p['dandiset_id']))
        return r['classification'] if r else 'MISSING'

    kinds = {k: [p for p in pairs if p['kind'] == k]
             for k in ('reuse', 'not_reuse', 'ambiguous', 'negative')}
    said = {k: [label(p) for p in v] for k, v in kinds.items()}

    def frac(labels, value='REUSE'):
        return round(sum(x == value for x in labels) / len(labels), 3) if labels else None

    recall, fpr = frac(said['reuse']), frac(said['not_reuse'])
    w = weights or {k: len(kinds[k]) for k in ('reuse', 'not_reuse')}
    kept = (recall or 0) * w.get('reuse', 0)
    wrong = (fpr or 0) * w.get('not_reuse', 0)
    answered = [results[k] for k in ((p['doi'].lower(), p['dandiset_id']) for p in pairs)
                if k in results]
    usage = [r.get('usage') or {} for r in answered]
    quotes = sum(len(r.get('evidence_quotes') or []) for r in answered)
    costs = [u['cost'] for u in usage if u.get('cost') is not None]

    return {
        'pairs': len(pairs),
        'reuse_recall': recall,
        'false_positive_rate': fpr,
        'precision': round(kept / (kept + wrong), 3) if kept + wrong else None,
        'ambiguous_called_reuse': frac(said['ambiguous']),
        'negatives_called_reuse': frac(said['negative']),
        'error_rate': round(sum(label(p) in ('ERROR', 'MISSING') for p in pairs)
                            / len(pairs), 3) if pairs else None,
        'unverified_quote_rate': round(sum(r.get('hallucinated_quote_count') or 0
                                           for r in answered) / quotes, 3) if quotes else None,
        'cost_per_pair': round(sum(costs) / len(costs), 5) if costs else None,
        'input_tokens_per_pair': round(sum(u.get('prompt_tokens') or 0 for u in usage)
                                       / len(usage)) if usage else None,
        'output_tokens_per_pair': round(sum(u.get('completion_tokens') or 0 for u in usage)
                                        / len(usage)) if usage else None,
        'negatives_flipped_to_reuse': sorted(
            f"{p['doi']} {p['dandiset_id']}" for p, x in zip(kinds['negative'], said['negative'])
            if x == 'REUSE'),
    }


def model_slug(model: str, effort: str) -> str:
    return re.sub(r'[^A-Za-z0-9._-]+', '_', f'{model}__{effort}')


def openrouter_price(model: str) -> tuple[float, float] | None:
    """Live (input, output) price per token, or None if OpenRouter does not list it."""
    try:
        listing = requests.get('https://openrouter.ai/api/v1/models', timeout=30).json()['data']
    except Exception:
        return None
    for entry in listing:
        if entry['id'] == model:
            pricing = entry.get('pricing') or {}
            return float(pricing.get('prompt', 0)), float(pricing.get('completion', 0))
    return None


def read_cached(pairs: list[dict], cache_dir: Path) -> dict[tuple[str, str], dict]:
    out = {}
    for p in pairs:
        path = cache_path(cache_dir, p['doi'], p['dandiset_id'])
        if path.exists():
            out[(p['doi'].lower(), p['dandiset_id'])] = json.loads(path.read_text())
    return out


def record(row: dict) -> None:
    with RESULTS.open('a') as f:
        f.write(json.dumps(row) + '\n')


def cmd_build(args) -> None:
    if EVAL_SET.exists() and not args.force:
        sys.exit(f'{EVAL_SET} exists; pass --force to replace it. Scores from different '
                 'versions are not comparable.')
    eval_set = build_eval_set(json.loads(Path(args.corpus).read_text()),
                              json.loads(Path(args.classifications).read_text())['classifications'],
                              load_reviews(args.reviews))
    EVAL_DIR.mkdir(exist_ok=True)
    EVAL_SET.write_text(json.dumps(eval_set, indent=1) + '\n')
    print(f"{len(eval_set['pairs'])} pairs {eval_set['counts']}; "
          f"quick tier {eval_set['quick_counts']}")


def cmd_run(args) -> None:
    eval_set = json.loads(EVAL_SET.read_text())
    pairs = tier_pairs(eval_set, args.tier)
    cache_dir = CACHE_ROOT / model_slug(args.model, args.effort)
    todo = [p for p in pairs if not cache_path(cache_dir, p['doi'], p['dandiset_id']).exists()]

    price = openrouter_price(args.model)
    if price is None:
        print(f'OpenRouter does not list {args.model}; cannot estimate its cost.', file=sys.stderr)
    else:
        estimate = len(todo) * (EST_INPUT_TOKENS * price[0] + EST_OUTPUT_TOKENS * price[1])
        print(f'{len(todo)} of {len(pairs)} pairs to classify, about ${estimate:.2f}',
              file=sys.stderr)
        if estimate > args.max_cost:
            sys.exit(f'Estimated ${estimate:.2f} exceeds --max-cost {args.max_cost}. '
                     'Try --tier quick, or raise --max-cost.')

    if todo:
        with tempfile.TemporaryDirectory() as tmp:
            corpus = Path(tmp) / 'corpus.json'
            corpus.write_text(json.dumps(as_corpus(pairs)))
            subprocess.run([sys.executable, '-m', 'src.shared.run_fulltext_classification',
                            '--model', args.model, '--reasoning-effort', args.effort,
                            '--results-file', str(corpus), '--paper-cache', args.paper_cache,
                            '--cache-dir', str(cache_dir), '-o', str(Path(tmp) / 'out.json'),
                            '--limit', str(len(pairs) + 1), '--workers', str(args.workers),
                            '--max-tokens', str(args.max_tokens)],
                           cwd=REPO, check=True)

    finish(args.label or f'{args.model} {args.effort}', args.tier, pairs,
           read_cached(pairs, cache_dir), eval_set,
           model=args.model, effort=args.effort)


def cmd_score(args) -> None:
    eval_set = json.loads(EVAL_SET.read_text())
    pairs = tier_pairs(eval_set, args.tier)
    results = {(c['citing_doi'].lower(), c['dandiset_id']): c
               for c in json.loads(Path(args.from_output).read_text())['classifications']}
    finish(args.label, args.tier, pairs, results, eval_set)


def finish(label: str, tier: str, pairs: list[dict], results: dict, eval_set: dict,
           **extra) -> None:
    metrics = score(pairs, results, weights=eval_set['counts'])
    row = {'label': label, 'tier': tier, 'eval_version': eval_set['version'],
           'date': datetime.date.today().isoformat(), **extra, **metrics}
    record(row)
    flipped = row.pop('negatives_flipped_to_reuse')
    print(json.dumps(row, indent=1))
    if flipped:
        print('Negatives this model calls REUSE (a miss or a false positive; ask a reviewer):')
        for pair in flipped:
            print('  ' + pair)


def cmd_report(args) -> None:
    rows = [json.loads(line) for line in RESULTS.read_text().splitlines() if line.strip()]
    rows = [r for r in rows if r['tier'] == args.tier]
    latest = {}
    for r in rows:
        latest[(r['label'], r['eval_version'])] = r
    cols = [('reuse_recall', 'recall'), ('false_positive_rate', 'FP rate'),
            ('precision', 'precision'), ('negatives_called_reuse', 'neg->REUSE'),
            ('error_rate', 'errors'), ('unverified_quote_rate', 'bad quotes'),
            ('cost_per_pair', '$/pair')]
    print(f"{'model':<40} v " + ' '.join(f'{name:>10}' for _, name in cols))
    for r in sorted(latest.values(), key=lambda r: -(r['reuse_recall'] or 0)):
        cells = ' '.join(f"{'' if r.get(k) is None else r[k]:>10}" for k, _ in cols)
        print(f"{r['label'][:40]:<40} {r['eval_version']} {cells}")


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='cmd', required=True)

    b = sub.add_parser('build', help='freeze the eval set from the current reviews')
    b.add_argument('--corpus', default=str(REPO / 'output/all_dandiset_papers_refreshed.json'))
    b.add_argument('--classifications', default=str(REPO / 'output/fulltext_classifications.json'))
    b.add_argument('--reviews', default=str(REPO / 'reuse_confirmation/*/*-reviews.json'))
    b.add_argument('--force', action='store_true')

    r = sub.add_parser('run', help='classify the eval set with a model and score it')
    r.add_argument('--model', required=True, help='OpenRouter model id')
    r.add_argument('--effort', default='high', choices=sorted(VALID_REASONING_EFFORTS))
    r.add_argument('--tier', default='quick', choices=['quick', 'full'])
    r.add_argument('--label', help='name in the results table (default: model and effort)')
    r.add_argument('--max-cost', type=float, default=10.0,
                   help='refuse to start above this estimated cost in dollars')
    r.add_argument('--workers', type=int, default=48)
    r.add_argument('--max-tokens', type=int, default=32768)
    r.add_argument('--paper-cache', default=str(REPO / '.paper_cache'))

    s = sub.add_parser('score', help='score classifications that already exist')
    s.add_argument('--from-output', required=True)
    s.add_argument('--label', required=True)
    s.add_argument('--tier', default='quick', choices=['quick', 'full'])

    p = sub.add_parser('report', help='the results table, latest row per model')
    p.add_argument('--tier', default='quick', choices=['quick', 'full'])

    args = parser.parse_args()
    {'build': cmd_build, 'run': cmd_run, 'score': cmd_score, 'report': cmd_report}[args.cmd](args)


if __name__ == '__main__':
    main()
