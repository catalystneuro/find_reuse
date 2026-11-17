#!/usr/bin/env python3
"""
Score an agent as the stage after the reuse classifier, against the reviewers.

The classifier reads one paper and answers once. A reviewer does more: follows
the data availability statement to the repository it names, looks at what the
dandiset holds, checks whether the benchmark the paper ran on is the one the
dandiset was made for. An agent with the paper, the dandiset's record and the
web can do the same, and this measures how often its call matches a reviewer's,
for each model that might do the job.

The sample is cut from pairs the production classifier called REUSE and a
reviewer has called, since those are the pairs the agent would be given. Calls
are scored at two levels:

  project    did the paper reuse the data of this dandiset or of a dandiset
             that shares its primary paper (see dandiset_groups)? This is the
             question the analysis needs answered.
  dandiset   did it reuse this dandiset in particular? Reviewers call a sibling
             the paper did not use Neither, so this is what they were asked.

Each agent runs as `claude -p` in a folder holding paper.txt and dataset.json,
with Read, Grep, WebSearch and WebFetch and nothing that writes or executes. It
is not shown the classifier's reasoning or the reviewer's call.

Usage:
    python -m src.review.eval_agent build            # freeze the sample, write the folders
    python -m src.review.eval_agent run --model claude-haiku-5-5
    python -m src.review.eval_agent report
"""

from __future__ import annotations

import argparse
import concurrent.futures
import glob
import json
import random
import re
import subprocess
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

import requests

from src.review.dandiset_groups import related_dandisets

REPO = Path(__file__).resolve().parents[2]
EVAL_DIR = REPO / 'model_eval'
SAMPLE = EVAL_DIR / 'agent_sample.json'
RESULTS = EVAL_DIR / 'agent_results.jsonl'
ROOT = REPO / '.agent_eval'
CORPUS = REPO / 'output/all_dandiset_papers_refreshed.json'

# How many pairs of each kind the sample holds. 'sibling' pairs are ones a
# reviewer called Neither because the paper reused a related dandiset instead:
# reuse at the project level, not at the dandiset level.
STRATA = {'reuse': 40, 'ambiguous': 15, 'sibling': 15, 'mention': 15, 'neither': 40}

# Neither calls that say nothing about whether the paper used the data.
NOT_ABOUT_USE = re.compile(r'preprint|duplicate|abstract[ /]|not full text|full text', re.I)

PROMPT = """You are a curator checking one paper-dataset link for a study of how often shared neuroscience data are reused.

In this folder:
- `paper.txt`: the full text of the paper. It is long: use Grep to find the passages on data availability, methods, datasets, DANDI and the cited work, then Read around them.
- `dataset.json`: the DANDI Archive dandiset the paper was linked to (number, title, description, contributors, what it holds), the paper that published the data (`primary_papers`), and `related_dandisets`: other dandisets that come from the same paper or project.

The paper was linked to the dandiset because it cites the paper the data were published with. It may never name DANDI. The same recordings are often also on a lab's site or in another archive (CRCNS, the Allen Institute's portal or SDK, IBL/ONE, Zenodo, figshare, Dryad, G-Node, the Neural Latents Benchmark).

Reuse counts wherever the data came from. If the authors analyzed the recordings this dandiset holds, the answer is use even when they got them from another archive, a lab's site, a benchmark's loader or the authors directly, and even when their copy is fuller or older than the one on DANDI, or only part of it. Never answer that the data were not used because they did not come from DANDI. Report where they came from in `source`; that is recorded separately.

Work as a careful human curator would. Read the paper first. Then, where the text leaves the question open, check: follow the paper's data availability links and the repositories or benchmarks it names (WebFetch), look at what the dandiset and its related dandisets hold (https://dandiarchive.org/dandiset/<id> or https://api.dandiarchive.org/api/dandisets/<id>/versions/draft/), look at the paper's code repository if it names one, and search the web (WebSearch) if you need to find out what a named dataset is. Do not spend effort on checks the paper's text already settles.

Decide:

1. `uses_project_data`: did the authors actually obtain and analyze data from this project, meaning the recordings held in this dandiset or in one of its related dandisets, from DANDI or from another copy of the same data? Citing the data's paper, comparing with its published results, reusing its methods, figures or parameters, or listing it in related work is not use. true / false.
2. `verdict`: "reuse" (they analyzed data someone else published, or that their own lab published earlier in another paper); "primary" (this paper is the dataset's own publication); "mention" (it cites or discusses the work without analyzing the data); "not_a_paper" (the text is a peer review, an assessment, a correction, or only an abstract, so it cannot show reuse).
3. `dandisets_used`: of this dandiset and the related ones, the numbers whose data the paper used, as far as you can tell, each as a quoted six-digit string such as "000021". [] if none.
4. `this_dandiset`: "used" (the evidence shows the data in this particular dandiset were used), "not_used" (the paper used a related dandiset or another dataset, and the evidence shows it was not this one), "unclear" (the project's data were used but you cannot tell which dandiset), or "na" (the project's data were not used).
5. `source`: where the paper says it got the data ("DANDI", the archive or site it names, "the authors", "own", or "not stated").
6. `same_lab`: do the paper's authors overlap substantially with the dandiset's contributors? true / false / null.
7. `evidence`: a list of one or two short verbatim quotes from paper.txt that show the use, or that show there is none.
8. `checks`: the things you looked up outside the paper and what each showed, as short strings. [] if none.
9. `confidence`: your probability, 0 to 1, that `uses_project_data` is right.
10. `notes`: one or two sentences.

Reply with ONLY a JSON object with exactly those keys. No other text."""


def key(doi: str, dandiset: str) -> str:
    return f"{dandiset}__{re.sub(r'[^A-Za-z0-9._-]+', '_', doi.lower())}"


def load_calls() -> dict[tuple[str, str], dict]:
    calls = {}
    for path in sorted(glob.glob(str(REPO / 'reuse_confirmation/*/*-reviews.json'))):
        for doi, by_dataset in json.loads(Path(path).read_text())['reviews'].items():
            for dandiset, review in by_dataset.items():
                calls[(doi.lower(), dandiset)] = review
    return calls


def gold(calls: dict, related: dict[str, set[str]]) -> dict[tuple[str, str], dict]:
    """
    What a reviewer's call says at each level, for every reviewed pair.

    A Neither on a pair whose paper was called Reuse or Ambiguous Reuse for a
    related dandiset is the paper using the project's data from the sibling:
    project reuse, and not this dandiset.
    """
    by_paper: dict[str, dict[str, str]] = defaultdict(dict)
    for (doi, dandiset), review in calls.items():
        by_paper[doi][dandiset] = review.get('call')
    out = {}
    for (doi, dandiset), review in calls.items():
        call, note = review.get('call'), review.get('note') or ''
        sibling_reuse = any(by_paper[doi].get(d) in ('reuse', 'ambiguous_reuse')
                            for d in related.get(dandiset, ()))
        if call == 'reuse':
            kind, project, this = 'reuse', True, True
        elif call == 'ambiguous_reuse':
            kind, project, this = 'ambiguous', True, None
        elif call == 'neither' and sibling_reuse:
            kind, project, this = 'sibling', True, False
        elif call == 'mention':
            kind, project, this = 'mention', False, False
        elif call == 'neither' and not NOT_ABOUT_USE.search(note):
            kind, project, this = 'neither', False, False
        else:
            continue
        out[(doi, dandiset)] = {'kind': kind, 'project': project, 'this': this,
                                'review': call, 'note': note}
    return out


def dandiset_record(dandiset: str, corpus: dict[str, dict], related: set[str]) -> dict:
    """What a curator sees on the dandiset's page, from the DANDI API."""
    meta = requests.get(
        f'https://api.dandiarchive.org/api/dandisets/{dandiset}/versions/draft/', timeout=60).json()
    summary = meta.get('assetsSummary') or {}
    names = lambda items: [i.get('name') for i in items or [] if i.get('name')]
    record = corpus.get(dandiset, {})
    return {
        'dandiset': dandiset,
        'url': f'https://dandiarchive.org/dandiset/{dandiset}',
        'title': meta.get('name'),
        'description': meta.get('description'),
        'contributors': names(meta.get('contributor'))[:40],
        'holds': {'files': summary.get('numberOfFiles'), 'subjects': summary.get('numberOfSubjects'),
                  'species': names(summary.get('species')), 'approaches': names(summary.get('approach')),
                  'techniques': names(summary.get('measurementTechnique')),
                  'data_standards': names(summary.get('dataStandard'))},
        'primary_papers': [{'doi': p.get('doi'), 'title': p.get('name'),
                            'published': p.get('publication_date')}
                           for p in record.get('paper_relations', [])],
        'other_related_resources': [{'relation': r.get('relation'), 'name': r.get('name'),
                                     'url': r.get('url') or r.get('identifier')}
                                    for r in meta.get('relatedResource') or []][:20],
        'related_dandisets': [{'dandiset': d, 'title': corpus.get(d, {}).get('dandiset_name')}
                              for d in sorted(related)],
    }


def cmd_build(args) -> None:
    from paper_text_fetcher import PaperFetcher

    if SAMPLE.exists() and not args.force:
        sys.exit(f'{SAMPLE} exists; --force replaces it and orphans earlier runs.')
    corpus = {d['dandiset_id']: d for d in json.loads(CORPUS.read_text())['results']}
    related = related_dandisets()
    frozen = {(p['doi'].lower(), p['dandiset_id']): p
              for p in json.loads((EVAL_DIR / 'eval_set.json').read_text())['pairs']
              if p['baseline'] == 'REUSE'}
    golds = {k: g for k, g in gold(load_calls(), related).items() if k in frozen}
    fetcher = PaperFetcher(use_cache=True, cache_dir=str(REPO / '.paper_cache'))

    rng = random.Random(args.seed)
    by_kind: dict[str, list] = defaultdict(list)
    for k in sorted(golds):
        by_kind[golds[k]['kind']].append(k)
    pairs, papers_used = [], set()
    for kind, n in STRATA.items():
        pool = by_kind[kind][:]
        rng.shuffle(pool)
        taken = 0
        for doi, dandiset in pool:
            if taken == n:
                break
            if doi in papers_used:           # one pair a paper, so no agent reads a paper twice
                continue
            fetched = fetcher.get_paper_text_detailed(frozen[(doi, dandiset)]['doi'])
            if fetched.get('status') != 'full_text':
                continue
            folder = ROOT / 'work' / key(doi, dandiset)
            folder.mkdir(parents=True, exist_ok=True)
            (folder / 'paper.txt').write_text(fetched['text'])
            (folder / 'dataset.json').write_text(json.dumps(
                dandiset_record(dandiset, corpus, related.get(dandiset, set())), indent=1))
            pairs.append({**{f: frozen[(doi, dandiset)][f] for f in
                             ('doi', 'title', 'dandiset_id', 'dandiset_name', 'cited_paper_doi')},
                          'key': key(doi, dandiset), **golds[(doi, dandiset)]})
            papers_used.add(doi)
            taken += 1
    SAMPLE.write_text(json.dumps({'seed': args.seed, 'counts': dict(Counter(p['kind'] for p in pairs)),
                                  'available': {k: len(v) for k, v in by_kind.items()},
                                  'pairs': pairs}, indent=1))
    print(json.dumps({'sampled': dict(Counter(p['kind'] for p in pairs)),
                      'available': {k: len(v) for k, v in by_kind.items()}}, indent=1))


def parse_answer(text: str) -> dict:
    """
    The agent's JSON. Dandiset numbers written bare (000397) are put in quotes,
    and an answer whose last string was never closed still gives up its calls,
    which sit at the front.
    """
    body = text[text.index('{'):]
    body = re.sub(r'("dandisets_used"\s*:\s*\[)([^\]]*)\]',
                  lambda m: m.group(1) + ', '.join(
                      f'"{d}"' for d in re.findall(r'\d+', m.group(2))) + ']', body)
    try:
        answer = json.loads(body[:body.rindex('}') + 1])
    except ValueError:
        field = lambda name, pattern: re.search(rf'"{name}"\s*:\s*({pattern})', body)
        answer = {'uses_project_data': field('uses_project_data', 'true|false').group(1) == 'true',
                  'verdict': field('verdict', '"[a-z_]+"').group(1).strip('"'),
                  'this_dandiset': field('this_dandiset', '"[a-z_]+"').group(1).strip('"'),
                  'dandisets_used': re.findall(r'\d{6}', field('dandisets_used', r'\[[^\]]*\]').group(1)),
                  'confidence': float(field('confidence', r'[\d.]+').group(1)),
                  'checks': ['(answer was malformed; checks not read)']
                  if not re.search(r'"checks"\s*:\s*\[\s*\]', body) else [],
                  'malformed': True}
    answer['dandisets_used'] = [str(d).zfill(6) for d in answer.get('dandisets_used') or []]
    return answer


def run_one(pair: dict, model: str, out_dir: Path, budget: float, timeout: int) -> dict:
    path = out_dir / f"{pair['key']}.json"
    if path.exists():
        cached = json.loads(path.read_text())
        if 'error' not in cached:
            return cached
    started = time.time()
    row: dict = {'key': pair['key'], 'model': model}
    try:
        done = subprocess.run(
            ['claude', '-p', PROMPT, '--model', model, '--output-format', 'json',
             '--strict-mcp-config', '--max-budget-usd', str(budget),
             '--allowedTools', 'Read', 'Grep', 'WebSearch', 'WebFetch',
             '--disallowedTools', 'Bash', 'Write', 'Edit', 'NotebookEdit', 'Agent'],
            cwd=ROOT / 'work' / pair['key'], stdin=subprocess.DEVNULL,
            capture_output=True, text=True, timeout=timeout)
        raw = json.loads(done.stdout)
        (out_dir / f"{pair['key']}.raw.json").write_text(done.stdout)
        row.update(cost=raw.get('total_cost_usd'), turns=raw.get('num_turns'),
                   usage=raw.get('usage'))
        row.update(parse_answer(raw.get('result') or ''))
    except Exception as e:
        row['error'] = f'{type(e).__name__}: {str(e)[:300]}'
    row['seconds'] = round(time.time() - started)
    path.write_text(json.dumps(row, indent=1))
    return row


def score(pairs: list[dict], results: dict[str, dict]) -> dict:
    """Agreement with the reviewers, at the project and the dandiset level."""
    rows = [(p, results.get(p['key'], {})) for p in pairs]
    ok = [(p, r) for p, r in rows if 'uses_project_data' in r]

    def rate(items, test):
        return (round(sum(test(r) for _, r in items) / len(items), 3), len(items)) if items else (None, 0)

    yes = lambda r: r['uses_project_data'] is True and r.get('verdict') != 'primary'
    positives = [(p, r) for p, r in ok if p['project']]
    negatives = [(p, r) for p, r in ok if not p['project']]
    kept, n_pos = rate(positives, yes)
    wrong, n_neg = rate(negatives, yes)
    by_kind = {k: rate([(p, r) for p, r in ok if p['kind'] == k], yes) for k in STRATA}
    # Dandiset level: of project reuse both agree on, does the agent place it as the reviewer did?
    placed = [(p, r) for p, r in positives if yes(r) and p['this'] is not None]
    right = sum((r.get('this_dandiset') == 'used') == p['this'] if r.get('this_dandiset') != 'unclear'
                else False for p, r in placed)
    unclear = sum(r.get('this_dandiset') == 'unclear' for _, r in placed)
    agree = sum(yes(r) == p['project'] for p, r in ok)
    costs = [r['cost'] for _, r in rows if r.get('cost') is not None]
    return {
        'pairs': len(pairs), 'answered': len(ok),
        'agreement': round(agree / len(ok), 3) if ok else None,
        'reuse_kept': kept, 'reuse_n': n_pos,
        'non_reuse_passed': wrong, 'non_reuse_n': n_neg,
        'called_reuse_by_kind': {k: f'{v[0]} of {v[1]}' for k, v in by_kind.items()},
        'dandiset_placed_as_reviewer': f'{right} of {len(placed)} ({unclear} left unclear)',
        'cost_per_pair': round(sum(costs) / len(costs), 3) if costs else None,
        'seconds_per_pair': round(sum(r.get('seconds', 0) for _, r in rows) / max(1, len(rows))),
        'turns_per_pair': round(sum(r.get('turns') or 0 for _, r in rows) / max(1, len(rows)), 1),
        'used_the_web': round(sum(bool(r.get('checks')) for _, r in ok) / len(ok), 3) if ok else None,
        'errors': sum('error' in r for _, r in rows),
    }


def cmd_run(args) -> None:
    pairs = json.loads(SAMPLE.read_text())['pairs'][:args.limit]
    out_dir = ROOT / 'out' / args.model
    out_dir.mkdir(parents=True, exist_ok=True)
    results = {}
    with concurrent.futures.ThreadPoolExecutor(args.workers) as ex:
        jobs = [ex.submit(run_one, p, args.model, out_dir, args.budget, args.timeout) for p in pairs]
        for i, job in enumerate(concurrent.futures.as_completed(jobs), 1):
            row = job.result()
            results[row['key']] = row
            print(f"{i}/{len(pairs)} {row['key']} {row.get('verdict', row.get('error'))} "
                  f"${row.get('cost') or 0:.2f} {row['seconds']}s", file=sys.stderr, flush=True)
    metrics = {'model': args.model, 'date': time.strftime('%Y-%m-%d'), **score(pairs, results)}
    with RESULTS.open('a') as f:
        f.write(json.dumps(metrics) + '\n')
    print(json.dumps(metrics, indent=1))


def cmd_report(args) -> None:
    pairs = json.loads(SAMPLE.read_text())['pairs']
    for out_dir in sorted((ROOT / 'out').iterdir()):
        results = {}
        for path in out_dir.glob('*.json'):
            if path.name.endswith('.raw.json'):
                continue
            row = json.loads(path.read_text())
            raw = path.with_name(path.stem + '.raw.json')
            if 'error' in row and raw.exists():      # answered, but the answer did not parse then
                try:
                    row.update(parse_answer(json.loads(raw.read_text()).get('result') or ''))
                    del row['error']
                    path.write_text(json.dumps(row, indent=1))
                except Exception:
                    pass
            results[path.stem] = row
        done = [p for p in pairs if p['key'] in results]
        print(json.dumps({'model': out_dir.name, **score(done, results)}, indent=1))


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='cmd', required=True)
    b = sub.add_parser('build')
    b.add_argument('--seed', type=int, default=20261116)
    b.add_argument('--force', action='store_true')
    r = sub.add_parser('run')
    r.add_argument('--model', required=True, help='claude-haiku-5-5, claude-sonnet-5-5, claude-opus-5-5')
    r.add_argument('--workers', type=int, default=4)
    r.add_argument('--limit', type=int, default=None)
    r.add_argument('--budget', type=float, default=3.0, help='dollar cap per pair')
    r.add_argument('--timeout', type=int, default=1500)
    sub.add_parser('report')
    args = parser.parse_args()
    {'build': cmd_build, 'run': cmd_run, 'report': cmd_report}[args.cmd](args)


if __name__ == '__main__':
    main()
