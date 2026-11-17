#!/usr/bin/env python3
"""
Score the agent stage run once per paper, with escalation, against the reviewers.

eval_agent asks about one paper and one dandiset. Here the agent gets a paper
and every dandiset the classifier proposed for it, and gives a determination
for each, so it can say "this one, not that one" across datasets whether or not
they share a primary paper. The sample is reviewed papers with calls on more
than one dandiset, the case where that matters.

Each agent also says whether it wants a second opinion (`ask_senior`). Every
model is run on every paper, so an escalation rule (a junior model answers, a
senior one is asked when the junior flags the paper, is unsure, or rejects) is
scored afterwards from the answers on disk: `report` shows, for each rule, the
agreement of the combined answers, how often the senior is asked and the cost.

Scored at two levels, as in eval_agent:
  family     did the paper use the data of the dandiset's family (dandisets
             sharing a primary paper)?
  dandiset   did it use this dandiset in particular?

Usage:
    python -m src.review.eval_agent_paper build
    python -m src.review.eval_agent_paper run --model claude-haiku-5-5
    python -m src.review.eval_agent_paper report
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import random
import re
import subprocess
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

from src.review.dandiset_groups import units
from src.review.eval_agent import NOT_ABOUT_USE, dandiset_record, load_calls

REPO = Path(__file__).resolve().parents[2]
EVAL_DIR = REPO / 'model_eval'
SAMPLE = EVAL_DIR / 'agent_paper_sample.json'
ROOT = REPO / '.agent_eval' / 'per_paper'
CORPUS = REPO / 'output/all_dandiset_papers_refreshed.json'
CLASSIFIED = REPO / 'output/fulltext_classifications.json'
MODELS = ('claude-haiku-5-5', 'claude-sonnet-5-5', 'claude-opus-5-5')

PROMPT = """You are a curator checking which datasets a paper reused, for a study of how often shared neuroscience data are reused.

In this folder:
- `paper.txt`: the full text of the paper. It is long: use Grep to find the passages on data availability, methods, datasets, DANDI and the cited work, then Read around them.
- `datasets.json`: the DANDI Archive dandisets this paper was linked to. Each has its number, title, description, contributors, what it holds, the paper that published the data (`primary_papers`) and a `family`: dandisets with the same family come from the same paper or project.

The paper was linked to each dandiset because it cites the paper the data were published with. It may never name DANDI. The same recordings are often also on a lab's site or in another archive (CRCNS, the Allen Institute's portal or SDK, IBL/ONE, Zenodo, figshare, Dryad, G-Node, the Neural Latents Benchmark).

Reuse counts wherever the data came from. If the authors analyzed the recordings a dandiset holds, its answer is "yes" even when they got them from another archive, a lab's site, a benchmark's loader or the authors directly, and even when their copy is fuller or older than the one on DANDI, or only part of it. Never answer "no" because the data did not come from DANDI: "no" means the paper did not analyze these recordings. Report where the data came from in `source`; that is recorded separately.

Work as a careful human curator would. Read the paper first. Then, where the text leaves a question open, check: follow the paper's data availability links and the repositories or benchmarks it names (WebFetch), look at what a dandiset holds (https://dandiarchive.org/dandiset/<id> or https://api.dandiarchive.org/api/dandisets/<id>/versions/draft/), look at the paper's code repository if it names one, and search the web (WebSearch) to find out what a named dataset is. Do not spend effort on checks the paper's text already settles.

Give a determination for EVERY dandiset in datasets.json. Use means the authors actually obtained and analyzed the recordings. Citing the data's paper, comparing with its published results, reusing its methods, figures or parameters, or listing it in related work is not use.

Reply with ONLY a JSON object, no other text:

{
 "datasets": [
  {
   "dandiset": "000000",          // six-digit string, one entry per dandiset in datasets.json
   "used": "yes" | "no" | "family_unclear",
        // yes: the data in this dandiset were used.
        // no: they were not (the paper used another dataset, a different member of the family, or only cites the work).
        // family_unclear: the paper used this family's data, and you cannot tell whether this member was among the ones used.
   "verdict": "reuse" | "primary" | "mention",
        // reuse: they analyzed data someone else published, or that their own lab published earlier in another paper.
        // primary: this paper is the dataset's own publication. mention: not used.
   "source": "...",               // where the paper says it got these data: "DANDI", the archive or site it names, "the authors", "own", "not stated", or "" if not used
   "confidence": 0.0,             // your probability that "used" is right
   "evidence": ["..."]            // one or two short verbatim quotes from paper.txt; write any double quote inside a quote as a single quote
  }
 ],
 "other_dandisets_used": ["000000"],  // dandisets not in datasets.json that you found the paper used; [] if none
 "is_research_paper": true,       // false if the text is a peer review, an assessment, a correction or only an abstract
 "checks": ["..."],               // what you looked up outside the paper and what each showed; [] if none
 "ask_senior": false,             // true if a more experienced curator should look at this paper before your answer is relied on
 "ask_senior_reason": "",         // which determination you are unsure of and why; "" if ask_senior is false
 "notes": "..."                   // one or two sentences
}

Set ask_senior to true when you could not settle a determination: the evidence conflicts, the text does not say where the data came from and your checks did not resolve it, you cannot tell family members apart and it seems resolvable with more work, or you are guessing. Do not set it for determinations the text makes plain."""


def paper_key(doi: str) -> str:
    return re.sub(r'[^A-Za-z0-9._-]+', '_', doi.lower())


def gold(calls: dict, unit: dict[str, str]) -> dict[str, dict[str, dict]]:
    """Reviewers' calls by paper and dandiset, with what each says at each level."""
    by_paper: dict[str, dict[str, dict]] = defaultdict(dict)
    for (doi, dandiset), review in calls.items():
        call, note = review.get('call'), review.get('note') or ''
        if call == 'reuse':
            this = True
        elif call in ('mention', 'primary') or (call == 'neither' and not NOT_ABOUT_USE.search(note)):
            this = False
        elif call == 'ambiguous_reuse':
            this = None
        else:
            continue
        by_paper[doi][dandiset] = {'review': call, 'note': note, 'this': this}
    for doi, rows in by_paper.items():
        for dandiset, row in rows.items():
            family = unit.get(dandiset, dandiset)
            row['family'] = family
            row['family_used'] = any(r['review'] in ('reuse', 'ambiguous_reuse')
                                     for d, r in rows.items() if unit.get(d, d) == family)
    return by_paper


def cmd_build(args) -> None:
    from paper_text_fetcher import PaperFetcher

    if SAMPLE.exists() and not args.force:
        sys.exit(f'{SAMPLE} exists; --force replaces it and orphans earlier runs.')
    corpus = {d['dandiset_id']: d for d in json.loads(CORPUS.read_text())['results']}
    unit = units()
    members: dict[str, set[str]] = defaultdict(set)
    for dandiset, name in unit.items():
        members[name].add(dandiset)
    proposed: dict[str, set[str]] = defaultdict(set)
    titles = {}
    for c in json.loads(CLASSIFIED.read_text())['classifications']:
        if c['classification'] == 'REUSE':
            proposed[c['citing_doi'].lower()].add(c['dandiset_id'])
            titles[c['citing_doi'].lower()] = (c['citing_doi'], c.get('title', ''))
    golds = gold(load_calls(), unit)
    # Papers with usable calls on two or more dandisets the classifier proposed.
    pool = sorted(doi for doi, rows in golds.items()
                  if doi in proposed and len(set(rows) & proposed[doi]) >= 2)
    random.Random(args.seed).shuffle(pool)
    fetcher = PaperFetcher(use_cache=True, cache_dir=str(REPO / '.paper_cache'))

    papers = []
    for doi in pool:
        if len(papers) == args.papers:
            break
        fetched = fetcher.get_paper_text_detailed(titles[doi][0])
        if fetched.get('status') != 'full_text':
            continue
        dandisets = sorted(proposed[doi] | set(golds[doi]))
        if any(d not in corpus for d in dandisets):
            continue
        folder = ROOT / 'work' / paper_key(doi)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / 'paper.txt').write_text(fetched['text'])
        records = []
        for d in dandisets:
            record = dandiset_record(d, corpus, set())
            record.pop('related_dandisets')
            record['family'] = unit.get(d, d)
            record['family_members_not_listed_here'] = sorted(
                members.get(unit.get(d, ''), set()) - set(dandisets))
            records.append(record)
        (folder / 'datasets.json').write_text(json.dumps(records, indent=1))
        papers.append({'doi': titles[doi][0], 'title': titles[doi][1], 'key': paper_key(doi),
                       'dandisets': dandisets, 'gold': golds[doi]})
    SAMPLE.write_text(json.dumps({'seed': args.seed, 'pool': len(pool), 'papers': papers}, indent=1))
    sizes = Counter(len(p['dandisets']) for p in papers)
    print(json.dumps({'pool': len(pool), 'papers': len(papers),
                      'reviewed_calls': dict(Counter(g['review'] for p in papers for g in p['gold'].values())),
                      'dandisets_per_paper': dict(sorted(sizes.items()))}, indent=1))


def parse(text: str, dandisets: list[str]) -> dict:
    body = text[text.index('{'):text.rindex('}') + 1]
    body = re.sub(r'^\s*//.*$', '', body, flags=re.M)
    answer = json.loads(body)
    rows = {}
    for row in answer['datasets']:
        rows[str(row['dandiset']).zfill(6)] = row
    missing = [d for d in dandisets if d not in rows]
    if missing:
        raise ValueError(f'no determination for {missing}')
    answer['datasets'] = rows
    return answer


def run_one(paper: dict, model: str, out_dir: Path, budget: float, timeout: int) -> dict:
    path = out_dir / f"{paper['key']}.json"
    if path.exists():
        cached = json.loads(path.read_text())
        if 'error' not in cached:
            return cached
    started, row = time.time(), {'key': paper['key'], 'model': model, 'cost': 0.0}
    for attempt in range(2):      # an answer that does not parse is asked for once more
        try:
            done = subprocess.run(
                ['claude', '-p', PROMPT, '--model', model, '--output-format', 'json',
                 '--strict-mcp-config', '--max-budget-usd', str(budget),
                 '--allowedTools', 'Read', 'Grep', 'WebSearch', 'WebFetch',
                 '--disallowedTools', 'Bash', 'Write', 'Edit', 'NotebookEdit', 'Agent'],
                cwd=ROOT / 'work' / paper['key'], stdin=subprocess.DEVNULL,
                capture_output=True, text=True, timeout=timeout)
            raw = json.loads(done.stdout)
            (out_dir / f"{paper['key']}.raw{attempt}.json").write_text(done.stdout)
            row['cost'] += raw.get('total_cost_usd') or 0
            row['turns'] = raw.get('num_turns')
            row.update(parse(raw.get('result') or '', paper['dandisets']))
            row.pop('error', None)
            break
        except Exception as e:
            row['error'] = f'{type(e).__name__}: {str(e)[:300]}'
    row['seconds'] = round(time.time() - started)
    path.write_text(json.dumps(row, indent=1))
    return row


def cmd_run(args) -> None:
    papers = json.loads(SAMPLE.read_text())['papers'][:args.limit]
    out_dir = ROOT / 'out' / args.model
    out_dir.mkdir(parents=True, exist_ok=True)
    with concurrent.futures.ThreadPoolExecutor(args.workers) as ex:
        jobs = [ex.submit(run_one, p, args.model, out_dir, args.budget, args.timeout) for p in papers]
        for i, job in enumerate(concurrent.futures.as_completed(jobs), 1):
            row = job.result()
            said = Counter(d.get('used') for d in (row.get('datasets') or {}).values())
            print(f"{i}/{len(papers)} {row['key']} {dict(said) or row.get('error')} "
                  f"ask_senior={row.get('ask_senior')} ${row['cost']:.2f} {row['seconds']}s",
                  file=sys.stderr, flush=True)


def load(model: str, papers: list[dict]) -> dict[str, dict]:
    out = {}
    for p in papers:
        path = ROOT / 'out' / model / f"{p['key']}.json"
        if path.exists():
            row = json.loads(path.read_text())
            if 'datasets' in row and 'error' not in row:
                out[p['key']] = row
    return out


def score(papers: list[dict], answers: dict[str, dict]) -> dict:
    """Agreement of one set of per-paper answers with the reviewers."""
    fam_right = fam_n = fam_reuse_kept = fam_reuse_n = fam_false_passed = fam_false_n = 0
    ds_right = ds_n = ds_unclear = 0
    by_size: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for p in papers:
        answer = answers.get(p['key'])
        if not answer:
            continue
        said = {d: r.get('used') for d, r in answer['datasets'].items()
                if r.get('verdict') != 'primary' or r.get('used') == 'no'}
        families: dict[str, bool] = {}
        for d, g in p['gold'].items():
            families[g['family']] = g['family_used']
        for family, used in families.items():
            agent = any(said.get(d) in ('yes', 'family_unclear') for d, g in p['gold'].items()
                        if g['family'] == family)
            fam_n += 1
            fam_right += agent == used
            if used:
                fam_reuse_n += 1
                fam_reuse_kept += agent
            else:
                fam_false_n += 1
                fam_false_passed += agent
        size = '2-3' if len(p['dandisets']) <= 3 else '4-6' if len(p['dandisets']) <= 6 else '7+'
        for d, g in p['gold'].items():
            if g['this'] is None:
                continue
            ds_n += 1
            if said.get(d) == 'family_unclear':
                ds_unclear += 1
                continue
            right = (said.get(d) == 'yes') == g['this']
            ds_right += right
            by_size[size][0] += right
            by_size[size][1] += 1
    frac = lambda a, b: f'{a}/{b} ({a / b:.0%})' if b else '-'
    return {'papers': sum(p['key'] in answers for p in papers),
            'family_agreement': frac(fam_right, fam_n),
            'family_reuse_kept': frac(fam_reuse_kept, fam_reuse_n),
            'family_non_reuse_passed': frac(fam_false_passed, fam_false_n),
            'dandiset_agreement': frac(ds_right, ds_n),
            'dandiset_left_unclear': ds_unclear,
            'dandiset_agreement_by_candidates': {k: frac(*v) for k, v in sorted(by_size.items())}}


# When the junior model's answer on a paper is passed to the senior model.
RULES = {
    'never': lambda a: False,
    'junior asks (ask_senior)': lambda a: bool(a.get('ask_senior')),
    'asks, or any confidence under 0.8': lambda a: bool(a.get('ask_senior')) or any(
        (r.get('confidence') or 0) < 0.8 for r in a['datasets'].values()),
    'asks, or any family left unclear': lambda a: bool(a.get('ask_senior')) or any(
        r.get('used') == 'family_unclear' for r in a['datasets'].values()),
    'asks, low confidence, unclear, or rejects everything': lambda a: (
        bool(a.get('ask_senior'))
        or any((r.get('confidence') or 0) < 0.8 or r.get('used') == 'family_unclear'
               for r in a['datasets'].values())
        or all(r.get('used') == 'no' for r in a['datasets'].values())),
    'always': lambda a: True,
}


def cmd_report(args) -> None:
    papers = json.loads(SAMPLE.read_text())['papers']
    answers = {m: load(m, papers) for m in MODELS}
    done = [p for p in papers if all(p['key'] in answers[m] for m in MODELS)]
    print(f'{len(done)} of {len(papers)} papers answered by all three models\n')
    for m in MODELS:
        rows = [answers[m][p['key']] for p in done]
        print(json.dumps({'model': m, **score(done, answers[m]),
                          'asks_senior': f"{sum(bool(r.get('ask_senior')) for r in rows)}/{len(rows)}",
                          'cost_per_paper': round(sum(r['cost'] for r in rows) / max(1, len(rows)), 3),
                          'seconds_per_paper': round(sum(r['seconds'] for r in rows) / max(1, len(rows)))},
                         indent=1))
    for junior, senior in (('claude-haiku-5-5', 'claude-opus-5-5'),
                           ('claude-haiku-5-5', 'claude-sonnet-5-5'),
                           ('claude-sonnet-5-5', 'claude-opus-5-5')):
        print(f'\n{junior} answers, {senior} is asked when:')
        for name, rule in RULES.items():
            combined, asked, cost = {}, 0, 0.0
            for p in done:
                first = answers[junior][p['key']]
                cost += first['cost']
                if rule(first):
                    asked += 1
                    cost += answers[senior][p['key']]['cost']
                    combined[p['key']] = answers[senior][p['key']]
                else:
                    combined[p['key']] = first
            s = score(done, combined)
            print(f"  {name:<52} asked {asked:>2}/{len(done)}  family {s['family_agreement']:<14} "
                  f"dandiset {s['dandiset_agreement']:<14} unclear {s['dandiset_left_unclear']:<2} "
                  f"${cost / max(1, len(done)):.3f}/paper")


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='cmd', required=True)
    b = sub.add_parser('build')
    b.add_argument('--seed', type=int, default=20261116)
    b.add_argument('--papers', type=int, default=60)
    b.add_argument('--force', action='store_true')
    r = sub.add_parser('run')
    r.add_argument('--model', required=True)
    r.add_argument('--workers', type=int, default=3)
    r.add_argument('--limit', type=int, default=None)
    r.add_argument('--budget', type=float, default=4.0, help='dollar cap per paper')
    r.add_argument('--timeout', type=int, default=1800)
    sub.add_parser('report')
    args = parser.parse_args()
    {'build': cmd_build, 'run': cmd_run, 'report': cmd_report}[args.cmd](args)


if __name__ == '__main__':
    main()
