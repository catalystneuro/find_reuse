#!/usr/bin/env python3
"""
Batch-run full-text reuse classification over citing papers.

One API call per (paper, dandiset) pair, so a paper linked to several dandisets
is asked about each of them and the output lines up with the existing
(paper, dataset) classifications.

Results are cached one JSON file per (paper, dataset) pair, so an interrupted
run resumes without paying for the work already done. ERROR results are cached
too but marked, and `--retry-errors` re-runs only those.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import sys
import threading
import time
from collections import Counter
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import warnings
warnings.filterwarnings('ignore')

import requests
from tqdm import tqdm

from fetch_paper import PaperFetcher
from src.shared.classify_fulltext_reuse import (
    classify_paper_reuse, DEFAULT_MODEL, PROMPT_VERSION, MODE_CITING, MODE_DIRECT,
    VALID_REASONING_EFFORTS, DEFAULT_REASONING_EFFORT, DEFAULT_MAX_TOKENS,
)

REPO = Path(__file__).resolve().parents[2]

# Direct-mode discovery output covers several archives incidentally; this study
# is scoped to DANDI, so only its references are classified.
DIRECT_ARCHIVE = 'DANDI Archive'


def cache_path(cache_dir: Path, doi: str, dataset_id: str) -> Path:
    safe = f"{doi}__{dataset_id}".replace('/', '_').replace(':', '_').replace('\\', '_')
    return cache_dir / f"{safe}.json"


def select_with_full_text(work: list[dict], limit: int, paper_cache: str,
                          workers: int = 12) -> list[dict]:
    """
    Keep the items whose paper text we can actually retrieve.

    Parallel because this is network-bound, not CPU-bound: metadata-only cache
    entries now expire, so a serial pass refetches thousands of papers one at a
    time and spends over an hour before any classification starts.

    Order is preserved so that `--limit` selects the same items it always did,
    rather than whichever happened to resolve first.
    """
    local = threading.local()

    def fetcher_for_thread() -> PaperFetcher:
        if not hasattr(local, 'fetcher'):
            local.fetcher = PaperFetcher(use_cache=True, cache_dir=paper_cache)
        return local.fetcher

    def chars_of(doi):
        try:
            fetched = fetcher_for_thread().get_paper_text_detailed(doi)
        except Exception:
            return doi, 0
        if fetched['status'] != 'full_text':
            return doi, 0
        return doi, len(fetched['text'])

    # A paper citing several dandisets' primary papers appears in `work` once
    # per pair;
    # resolve its text once and share the answer across its pairs, since a
    # non-full-text entry past its metadata TTL refetches over the network.
    dois = list(dict.fromkeys(item['doi'] for item in work))
    chars_by_doi: dict[str, int] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        futures = [ex.submit(chars_of, doi) for doi in dois]
        for fut in tqdm(concurrent.futures.as_completed(futures), total=len(futures),
                        desc='Selecting papers with full text', file=sys.stderr):
            doi, chars = fut.result()
            chars_by_doi[doi] = chars

    keep = []
    for item in work:
        chars = chars_by_doi[item['doi']]
        if chars:
            item['text_chars'] = chars
            keep.append(item)
            if len(keep) >= limit:
                break
    return keep


def build_worklist(results_path: Path) -> list[dict]:
    """
    Build the worklist: one item per (citing paper, dandiset) pair.

    A paper citing several dandisets' primary papers is a separate question
    for each, because reusing one dandiset's data says nothing about its
    siblings'. Collapsing such a paper to a single pair attributes the
    classification to whichever dandiset happens to sort first and silently
    drops the rest.

    A dandiset can declare several papers, and the pair records which one it was
    built from, so that is the paper the prompt names. Naming the dandiset's
    first paper instead asks the model about a paper the citing work may never
    have cited, and it answers correctly to the wrong question.
    """
    data = json.loads(results_path.read_text())
    seen: set[tuple[str, str]] = set()
    work: list[dict] = []

    for ds in data['results']:
        dandiset_id = ds.get('dandiset_id', '')
        for paper in ds.get('citing_papers', []):
            doi = paper.get('doi')
            if not doi or (doi, dandiset_id) in seen:
                continue
            seen.add((doi, dandiset_id))
            work.append({
                'doi': doi,
                'title': paper.get('title', ''),
                'dandiset_id': dandiset_id,
                'dandiset_name': ds.get('dandiset_name', ''),
                'primary_paper_doi': paper['cited_paper_doi'],
            })

    return work


def build_direct_worklist(path: Path) -> list[dict]:
    """
    Worklist for the direct pathway, taken from the existing classifications.

    Each (paper, dataset) pair is its own question, because one paper can name
    several dataset identifiers and stand in a different relationship to each:
    primary for its own deposit, reuser of another.
    """
    data = json.loads(path.read_text())
    work = []

    if 'classifications' in data:
        # A previous classification run: re-judge the same pairs, carrying the
        # old label so the two methods can be compared.
        for entry in data['classifications']:
            doi = entry.get('citing_doi')
            if not doi:
                continue
            work.append({
                'doi': doi,
                'title': entry.get('citing_title', ''),
                'dandiset_id': entry.get('dandiset_id', ''),
                'dandiset_name': entry.get('dandiset_name', ''),
                'primary_paper_doi': entry.get('cited_doi') or '',
                'matched_patterns': entry.get('match_patterns'),
                'prior_classification': entry.get('classification'),
            })
    else:
        # Raw discovery output. One work item per (paper, dataset) pair, with
        # the strings that actually matched, so the model can recognize a
        # pattern-matching artifact rather than assuming the reference is real.
        for entry in data.get('results', []):
            doi = entry.get('doi')
            if not doi:
                continue
            for archive, info in (entry.get('archives') or {}).items():
                if archive != DIRECT_ARCHIVE:
                    continue
                matches = info.get('matches') or []
                for dataset_id in info.get('dataset_ids', []):
                    patterns = [m.get('matched_string') for m in matches
                                if m.get('id') == dataset_id and m.get('matched_string')]
                    work.append({
                        'doi': doi,
                        'title': entry.get('title', ''),
                        'dandiset_id': dataset_id,
                        'dandiset_name': '',
                        'primary_paper_doi': '',
                        'matched_patterns': patterns or None,
                        'prior_classification': None,
                    })

    return work


def group_by_paper(items: list[dict]) -> list[list[dict]]:
    """
    Group worklist items by citing DOI, in order of first appearance.

    Each group runs on a single worker, its pairs one after another. The prompt
    opens with the paper's full text, and providers cache prompts by prefix, so
    the second and later questions about a paper bill that text at the cached
    rate, about a tenth of the input price. That only happens when the repeat
    arrives after the first request has finished (a concurrent duplicate misses
    the cache, which is written on completion) and before the entry expires a
    few minutes later, which is what running the group sequentially on one
    worker guarantees.
    """
    groups: dict[str, list[dict]] = {}
    for item in items:
        groups.setdefault(item['doi'], []).append(item)
    return list(groups.values())


def openrouter_credit_remaining(model: str) -> Optional[float]:
    """
    Remaining credit on the OpenRouter key, or None if not applicable/unknown.

    Cheap to ask and worth asking: a key that is already spent turns a whole run
    into a single repeated 403, and finding that out after the fact costs more
    than the check.
    """
    if '/' not in model:
        return None
    from src.shared.classify_fulltext_reuse import _read_key
    key = _read_key('OPENROUTER_API_KEY')
    if not key:
        return None
    try:
        resp = requests.get('https://openrouter.ai/api/v1/key',
                            headers={'Authorization': f'Bearer {key}'}, timeout=20)
        if resp.status_code != 200:
            return None
        data = resp.json().get('data') or {}
        return data.get('limit_remaining')
    except Exception:
        return None


def load_cached_results(cache_dir: Path, skip_errors: bool = False,
                        skip_label: str = '') -> list[dict]:
    """Read every cached result, optionally excluding one label."""
    out = []
    for path in sorted(cache_dir.glob('*.json')):
        try:
            prior = json.loads(path.read_text())
        except Exception:
            continue
        if skip_errors and prior.get('classification') == 'ERROR':
            continue
        if skip_label and prior.get('classification') == skip_label:
            continue
        out.append(prior)
    return out


def primary_paper_index(results_path: Path) -> dict[tuple[str, str], str]:
    """
    Map (citing DOI, dandiset) to the paper that pair was built from.

    A cached result records which pair it answered but not which paper it was
    asked about, and a dandiset can declare several. Rebuilding a work item
    therefore has to go back to discovery for the answer.
    """
    try:
        data = json.loads(results_path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    index: dict[tuple[str, str], str] = {}
    for ds in data.get('results', []):
        for paper in ds.get('citing_papers') or []:
            if paper.get('doi') and paper.get('cited_paper_doi'):
                index[(paper['doi'].lower(), ds['dandiset_id'])] = paper['cited_paper_doi']
    return index


def build_retry_worklist(cache_dir: Path, label: str = 'ERROR',
                         primaries: Optional[dict] = None) -> list[dict]:
    """
    Rebuild work items straight from cached results carrying `label`.

    Every cached result carries the DOI and dandiset it was asked about, so a
    rerun can be reconstructed without touching the network. Defaults to ERROR,
    which is the retry case; passing another label reruns just those, which is
    what a prompt change affecting one boundary needs.

    `primaries` supplies the paper each pair was built from, from
    primary_paper_index. Without it the prompt names no primary paper at all,
    which asks a different and much vaguer question than the original run did,
    and the answers would not be comparable with the rows they replace.
    """
    work, missing = [], []
    for path in sorted(cache_dir.glob('*.json')):
        try:
            prior = json.loads(path.read_text())
        except Exception:
            continue
        if prior.get('classification') != label or not prior.get('citing_doi'):
            continue
        dandiset_id = prior.get('dandiset_id', '')
        primary = (primaries or {}).get(
            (prior['citing_doi'].lower(), dandiset_id), '')
        if primaries is not None and not primary:
            missing.append(prior['citing_doi'])
        work.append({
            'doi': prior['citing_doi'],
            'title': prior.get('title', ''),
            'dandiset_id': dandiset_id,
            'dandiset_name': '',
            'primary_paper_doi': primary,
            'matched_patterns': None,
            'prior_classification': prior.get('prior_classification'),
        })
    if missing:
        print(f"warning: {len(missing)} pairs have no primary paper in the corpus; "
              f"they will be asked without one (e.g. {missing[0]})",
              file=sys.stderr, flush=True)
    return work


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=[MODE_CITING, MODE_DIRECT],
                        default=MODE_CITING,
                        help="'citing' asks how a citing paper relates to a dataset; "
                             "'direct' asks whether a paper that names a dataset "
                             "identifier published it or reused it.")
    parser.add_argument('--limit', type=int, default=500)
    parser.add_argument('--workers', type=int, default=8)
    parser.add_argument('--model', default=DEFAULT_MODEL)
    parser.add_argument('--reasoning-effort', choices=sorted(VALID_REASONING_EFFORTS),
                        default=DEFAULT_REASONING_EFFORT,
                        help='how long the model thinks before answering. The paper '
                             'dominates the token bill, so raising this costs about a '
                             'tenth more per call.')
    parser.add_argument('--results-file',
                        default=str(REPO / 'output/all_dandiset_papers.json'))
    parser.add_argument('--paper-cache', default=str(REPO / '.paper_cache'))
    parser.add_argument('--cache-dir',
                        default=str(REPO / '.fulltext_classification_cache'))
    parser.add_argument('-o', '--output',
                        default=str(REPO / 'output/fulltext_classifications.json'))
    parser.add_argument('--min-credit', type=float, default=5.0,
                        help='Refuse to start if the OpenRouter key has less '
                             'than this much credit left (default 5.0).')
    parser.add_argument('--reclassify', metavar='LABEL',
                        help='rerun only cached results carrying this label, '
                             'carrying the rest forward unchanged. Use when a '
                             'prompt change moves one boundary and cannot change '
                             'the other labels.')
    parser.add_argument('--retry-errors', action='store_true',
                        help='Re-run pairs whose cached result was an ERROR')
    parser.add_argument('--max-tokens', type=int, default=DEFAULT_MAX_TOKENS,
                        help='Completion budget. This is a reasoning model, so '
                             'the budget covers thinking as well as the answer; '
                             'papers with ambiguous evidence can exhaust 8192 '
                             'and come back as truncated_response.')
    args = parser.parse_args()

    # Keep the two pathways in separate caches and outputs; they answer different
    # questions and their labels are not interchangeable.
    if args.mode == MODE_DIRECT:
        if args.cache_dir == str(REPO / '.fulltext_classification_cache'):
            args.cache_dir = str(REPO / '.fulltext_direct_cache')
        if args.output == str(REPO / 'output/fulltext_classifications.json'):
            args.output = str(REPO / 'output/fulltext_direct_classifications.json')
        if args.results_file == str(REPO / 'output/all_dandiset_papers.json'):
            args.results_file = str(REPO / 'output/direct_ref_classifications.json')

    cache_dir = Path(args.cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)

    fetcher = PaperFetcher(use_cache=True, cache_dir=args.paper_cache)

    # Results already on disk that this run is not re-running. In retry mode the
    # work list is only the failures, so without this the output file would be
    # rewritten with just those and the rest of the corpus would vanish from it.
    # The cache is the source of truth; the output file is a view of it.
    carried: list[dict] = []

    if args.reclassify:
        # A reworded prompt that only moves one boundary does not need the whole
        # corpus re-run. Rerunning just the affected label and carrying the rest
        # forward is only sound when the change cannot move the other labels;
        # the caller is asserting that by naming one.
        primaries = (primary_paper_index(Path(args.results_file))
                     if args.mode == MODE_CITING else {})
        work = build_retry_worklist(cache_dir, label=args.reclassify,
                                    primaries=primaries)
        carried = load_cached_results(cache_dir, skip_label=args.reclassify)
        print(f"{len(work)} cached {args.reclassify} results to reclassify, "
              f"{len(carried)} carried forward ({args.mode} mode)",
              file=sys.stderr, flush=True)
    elif args.retry_errors:
        # Retrying failures does not need the corpus rescanned. Selection calls
        # the fetcher for every paper, and metadata-only cache entries now
        # expire, so a rescan refetches roughly 1,400 papers over the network to
        # find a hundred cached errors. The errors already record what they were.
        primaries = (primary_paper_index(Path(args.results_file))
                     if args.mode == MODE_CITING else {})
        work = build_retry_worklist(cache_dir, primaries=primaries)
        carried = load_cached_results(cache_dir, skip_errors=True)
        print(f"{len(work)} cached errors to retry, {len(carried)} results carried "
              f"forward ({args.mode} mode)", file=sys.stderr, flush=True)
    else:
        builder = build_direct_worklist if args.mode == MODE_DIRECT else build_worklist
        work = select_with_full_text(builder(Path(args.results_file)),
                                     args.limit, args.paper_cache)
        print(f"{len(work)} items with full text selected ({args.mode} mode)",
              file=sys.stderr, flush=True)

    todo = []
    cached_results = []
    stale = 0
    for item in work:
        path = cache_path(cache_dir, item['doi'], item['dandiset_id'])
        if path.exists():
            try:
                prior = json.loads(path.read_text())
            except Exception:
                todo.append(item)
                continue
            # A cached answer to a different question is not an answer to this
            # one. Mixing prompt versions would silently produce a corpus where
            # some rows have same_lab and archive and others cannot.
            if (prior.get('prompt_version') != PROMPT_VERSION
                    or prior.get('model') != args.model):
                # A different model answering the same prompt is a different
                # answer, and the corpus must not mix them any more than it
                # mixes prompt versions.
                stale += 1
                todo.append(item)
            elif prior.get('classification') == 'ERROR' and args.retry_errors:
                todo.append(item)
            else:
                cached_results.append(prior)
            continue
        todo.append(item)

    print(f"{len(cached_results)} already classified, {len(todo)} to run"
          + (f" ({stale} stale from an older prompt version)" if stale else ""),
          file=sys.stderr, flush=True)

    remaining = openrouter_credit_remaining(args.model)
    if remaining is not None and remaining < args.min_credit:
        print(f"ABORT: OpenRouter key has ${remaining:.2f} remaining, below the "
              f"${args.min_credit:.2f} floor. Top it up or pass --min-credit 0 "
              "to proceed anyway.", file=sys.stderr, flush=True)
        raise SystemExit(2)
    if remaining is not None:
        print(f"OpenRouter credit remaining: ${remaining:.2f}",
              file=sys.stderr, flush=True)

    thread_local = threading.local()

    def get_fetcher():
        if not hasattr(thread_local, 'fetcher'):
            thread_local.fetcher = PaperFetcher(
                use_cache=True, cache_dir=args.paper_cache)
        return thread_local.fetcher

    def run_one(item):
        fetched = get_fetcher().get_paper_text_detailed(item['doi'])
        if fetched['status'] != 'full_text':
            # Should not happen given the worklist filter, but never guess.
            result = {
                'classification': 'ERROR', 'confidence': 0,
                'evidence_quotes': [], 'quote_warnings': [],
                'hallucinated_quote_count': 0,
                'error': f"no full text at classification time: {fetched['reason']}",
                'error_kind': 'no_full_text',
            }
        else:
            result = classify_paper_reuse(
                fetched['text'],
                dataset_id=item['dandiset_id'],
                dataset_name=item['dandiset_name'],
                primary_paper_doi=item['primary_paper_doi'],
                paper_doi=item['doi'],
                model=args.model,
                max_tokens=args.max_tokens,
                mode=args.mode,
                matched_patterns=item.get('matched_patterns'),
                reasoning_effort=args.reasoning_effort,
            )
        result['citing_doi'] = item['doi']
        result['dandiset_id'] = item['dandiset_id']
        result['title'] = item['title']
        if item.get('prior_classification'):
            result['prior_classification'] = item['prior_classification']

        path = cache_path(cache_dir, item['doi'], item['dandiset_id'])
        # An error must never replace a successful classification. A spent API
        # key once turned 10,559 good results into 10,559 copies of the same
        # 403, destroying a full corpus pass that had cost $47 to produce. A
        # stale good answer is worth more than a fresh failure.
        if result.get('classification') == 'ERROR' and path.exists():
            try:
                prior = json.loads(path.read_text())
            except Exception:
                prior = {}
            if prior.get('classification') not in (None, 'ERROR'):
                result['kept_prior_result'] = True
                return result
        path.write_text(json.dumps(result, indent=2))
        return result

    def run_paper(items):
        results = []
        for item in items:
            result = run_one(item)
            results.append(result)
            if result.get('fatal'):
                break
        return results

    fresh = []
    aborted = None
    t0 = time.time()
    if todo:
        # One future per paper, pairs within it sequential, so repeat questions
        # about a paper hit the provider's prompt cache (see group_by_paper).
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as ex:
            futures = [ex.submit(run_paper, group) for group in group_by_paper(todo)]
            pbar = tqdm(total=len(todo), desc='Classifying', file=sys.stderr)
            counts = Counter()
            for fut in concurrent.futures.as_completed(futures):
                try:
                    results = fut.result()
                except concurrent.futures.CancelledError:
                    continue
                except Exception as e:
                    print(f"  worker crashed: {type(e).__name__}: {e}",
                          file=sys.stderr, flush=True)
                    continue
                fresh.extend(results)
                pbar.update(len(results))
                for result in results:
                    counts[result['classification']] += 1
                pbar.set_postfix({k: v for k, v in counts.most_common(4)})

                fatal = next((r for r in results if r.get('fatal')), None)
                if fatal:
                    # Nothing after this can succeed: the credential is spent or
                    # rejected, so every remaining paper would fail identically.
                    aborted = fatal.get('error')
                    print(f"\n  FATAL: {aborted}\n  Stopping; {len(todo) - len(fresh)} "
                          "items were not attempted.", file=sys.stderr, flush=True)
                    for pending in futures:
                        pending.cancel()
                    break
            pbar.close()
    elapsed = time.time() - t0

    # `carried` is empty outside retry mode, where `cached_results` already
    # covers everything the work list skipped.
    all_results = carried + cached_results + fresh
    counts = Counter(r['classification'] for r in all_results)
    halluc = sum(r.get('hallucinated_quote_count', 0) for r in all_results)
    with_quotes = sum(1 for r in all_results if r.get('evidence_quotes'))
    tiers = Counter(q['match_type'] for r in all_results
                    for q in r.get('evidence_quotes', []))
    tokens_in = sum((r.get('usage') or {}).get('prompt_tokens', 0) for r in all_results)
    tokens_out = sum((r.get('usage') or {}).get('completion_tokens', 0) for r in all_results)
    tokens_cached = sum(
        (((r.get('usage') or {}).get('prompt_tokens_details') or {})
         .get('cached_tokens') or 0)
        for r in all_results)

    def result_cost(r):
        # OpenRouter reports what each request actually billed, cache reads and
        # writes included; flat per-token rates are the fallback for results
        # that predate this field or came from another provider.
        usage = r.get('usage') or {}
        if usage.get('cost') is not None:
            return usage['cost']
        return (usage.get('prompt_tokens', 0) * 0.14 / 1e6
                + usage.get('completion_tokens', 0) * 0.28 / 1e6)

    cost = sum(result_cost(r) for r in all_results)

    summary = {
        'pairs': len(all_results),
        'papers': len({r['citing_doi'] for r in all_results}),
        'newly_classified': len(fresh),
        'classification_counts': dict(counts),
        'papers_with_quotes': with_quotes,
        'quote_match_tiers': dict(tiers),
        'hallucinated_quotes': halluc,
        'prompt_tokens': tokens_in,
        'cached_prompt_tokens': tokens_cached,
        'completion_tokens': tokens_out,
        'estimated_cost_usd': round(cost, 2),
        'seconds': round(elapsed, 1),
        'model': args.model,
        'aborted': aborted,
    }

    Path(args.output).write_text(json.dumps(
        {'summary': summary, 'classifications': all_results}, indent=2))

    print('\n=== CLASSIFICATION COMPLETE ===', file=sys.stderr)
    print(json.dumps(summary, indent=2), file=sys.stderr, flush=True)


if __name__ == '__main__':
    main()
