#!/usr/bin/env python3
"""
Classify the Elsevier articles where they were fetched, and share only the decisions.

The article text never leaves this machine except in the request to the language
model. What comes back to us, in decisions.json, is per (paper, dataset) pair:
the label and the model's structured answers, evidence quotes cut to Elsevier's
200-character snippet limit, the model's reasoning with any long verbatim run of
the article redacted, and a few facts computed from the text (its length, and
whether it names a DANDI identifier). No article text is written to decisions.json.

    export OPENROUTER_API_KEY=...      # the key Ben sent
    python classify_local.py --dry-run # counts pairs and checks the texts are there
    python classify_local.py           # about 2,400 pairs; safe to rerun

Needs Python 3 and `requests`. Reads elsevier_texts/ (written by fetch_elsevier.py)
and jobs.json; caches each answer in decisions_cache/ so a rerun skips finished pairs.
"""
import argparse
import concurrent.futures
import hashlib
import json
import re
import sys
from pathlib import Path
from urllib.parse import quote

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from classify_fulltext_reuse import PROMPT_VERSION, DEFAULT_MODEL, classify_paper_reuse  # noqa: E402

SNIPPET = 200        # Elsevier TDM output limit for text snippets, in characters
RUN = 120            # a reasoning passage this long, copied verbatim, is redacted
DATASET_ID_RE = re.compile(
    r'10\.48324/dandi\.\d{6}|dandiarchive\.org/dandiset/\d{6}|DANDI\s*:\s*\d{6}'
    r'|DANDI\s+(?:dataset|dandiset|archive)?\s*(?:ID\s*)?#?\s*0\d{5}\b', re.IGNORECASE)
DANDI_NAME_RE = re.compile(r'\bDANDI\b|dandiarchive')
KEEP = ('classification', 'confidence', 'hallucinated_quote_count', 'same_lab', 'same_lab_confidence',
        'source_archive', 'reused_modalities', 'reuse_type', 'reuse_type_other',
        'reused_neurophysiology', 'reused_dandi_hosted', 'mode', 'prompt_version', 'input_chars',
        'truncation', 'usage', 'model', 'provider', 'error', 'error_kind')


def snippet(rec):
    q = rec.get('quote') or ''
    return {'quote': q[:SNIPPET], 'clipped': len(q) > SNIPPET, 'chars': rec.get('chars'),
            'match_type': rec.get('match_type'), 'verbatim': rec.get('verbatim'), 'offset': rec.get('offset')}


def redact(reasoning, text):
    """Replace any RUN-character stretch of the reasoning found verbatim in the article."""
    if not reasoning:
        return reasoning, 0
    flat = ' '.join(text.split())
    out, i, n = [], 0, 0
    r = reasoning
    while i < len(r):
        if i + RUN <= len(r) and ' '.join(r[i:i + RUN].split()) in flat:
            j = i + RUN
            while j < len(r) and ' '.join(r[i:j + 1].split()) in flat:
                j += 1
            out.append('[…]')
            i, n = j, n + 1
        else:
            out.append(r[i])
            i += 1
    return ''.join(out), n


def shareable(job, result, text):
    named = sorted({m for hit in DATASET_ID_RE.finditer(text) for m in re.findall(r'0\d{5}', hit.group(0))})
    reasoning, redactions = redact(result.get('reasoning'), text)
    return {**{k: job[k] for k in ('corpus', 'doi', 'dataset_id', 'primary_paper_doi')},
            **{k: result.get(k) for k in KEEP},
            'evidence_quotes': [snippet(q) for q in result.get('evidence_quotes') or []],
            'source_quotes': [snippet(q) for q in result.get('source_quotes') or []],
            'reasoning': reasoning, 'reasoning_redactions': redactions,
            'text_chars': len(text), 'text_names_dandiset_ids': named,
            'text_names_dandi': bool(DANDI_NAME_RE.search(text))}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('--texts', default=str(HERE / 'elsevier_texts'))
    ap.add_argument('--jobs', default=str(HERE / 'jobs.json'))
    ap.add_argument('--out', default=str(HERE / 'decisions.json'))
    ap.add_argument('--workers', type=int, default=16)
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()
    texts, cache = Path(args.texts), HERE / 'decisions_cache'
    cache.mkdir(exist_ok=True)
    jobs = json.loads(Path(args.jobs).read_text())
    text_of = lambda doi: texts / f'{quote(doi, safe="")}.json'  # noqa: E731
    have = [j for j in jobs if text_of(j['doi']).exists()]
    cached = lambda j: cache / (hashlib.sha1(f"{j['corpus']}|{j['doi']}|{j['dataset_id']}".encode()).hexdigest() + '.json')  # noqa: E731
    todo = [j for j in have if not cached(j).exists()]
    print(f'{len(jobs)} pairs; {len(have)} have fetched text; {len(have) - len(todo)} already classified; '
          f'{len(todo)} to go (about ${0.009 * len(todo):.0f})')
    if args.dry_run:
        return

    def run(job):
        text = json.loads(text_of(job['doi']).read_text())['text']
        result = classify_paper_reuse(text, dataset_id=job['dataset_id'], dataset_name=job['dataset_name'],
                                      primary_paper_doi=job['primary_paper_doi'], paper_doi=job['doi'])
        if result.get('fatal') or result.get('error_kind') == 'no_api_key':
            raise SystemExit(f"Stopping: {result.get('error')}")
        if result.get('classification') != 'ERROR':
            cached(job).write_text(json.dumps(shareable(job, result, text)))
        return result.get('classification')

    done = 0
    with concurrent.futures.ThreadPoolExecutor(args.workers) as pool:
        for label in pool.map(run, todo):
            done += 1
            if done % 50 == 0 or done == len(todo):
                print(f'  {done}/{len(todo)}', flush=True)

    decisions = [json.loads(cached(j).read_text()) for j in have if cached(j).exists()]
    status = {}
    log = texts / 'fetch_log.json'
    if log.exists():
        status = json.loads(log.read_text())
    Path(args.out).write_text(json.dumps({
        'prompt_version': PROMPT_VERSION, 'model': DEFAULT_MODEL,
        'classifier_sha256': hashlib.sha256((HERE / 'classify_fulltext_reuse.py').read_bytes()).hexdigest(),
        'fetch_status': status, 'decisions': decisions}, indent=1))
    print(f'Wrote {args.out}: {len(decisions)} decisions. Share this file; keep elsevier_texts/ here.')


if __name__ == '__main__':
    main()
