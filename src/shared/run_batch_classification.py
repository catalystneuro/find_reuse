#!/usr/bin/env python
"""
Full-corpus classification through OpenAI's Batch API.

Builds the same (paper, dandiset) pair worklist as run_fulltext_classification,
skips pairs already cached at the current prompt version, and submits the rest
as OpenAI batches, which bill at half the interactive rate. Completed responses
are replayed through classify_paper_reuse with a stubbed HTTP session, so
parsing, quote verification, and the cache-file format are identical to
interactive runs, and the two pathways can share one cache directory.

Requires OPENAI_API_KEY in the environment or .env; batches are created on the
OpenAI account directly rather than through OpenRouter.

Every step is resumable. `build` writes chunk JSONL files and the custom_id to
item map to disk once; `submit` uploads and creates a batch per chunk, with
retries, recording progress in the manifest after every call, and skips chunks
already submitted; `collect` ingests completed batches into the per-pair cache
and marks them; `status` prints one line per chunk. Rerunning any command
after a crash or a quota deferral continues where it stopped.

Usage:
  python -m src.shared.run_batch_classification build
  python -m src.shared.run_batch_classification submit
  python -m src.shared.run_batch_classification collect
  python -m src.shared.run_batch_classification status
"""
import argparse
import io
import json
import sys
import time
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import requests

from fetch_paper import PaperFetcher
from src.shared import classify_fulltext_reuse as C
from src.shared.run_fulltext_classification import build_worklist, group_by_paper

REPO = Path(__file__).resolve().parents[2]

# The OpenAI API knows the model by its bare name; the corpus records the
# OpenRouter slug so batch and interactive rows compare as the same model.
MODEL_OPENAI = 'gpt-5.6-luna'
MODEL_RECORDED = 'openai/gpt-5.6-luna'

# Small enough that a mid-upload connection drop is cheap to retry; OpenAI's
# hard caps are 200 MB and 50,000 requests per file.
MAX_FILE_BYTES = 50 * 1024 * 1024


def openai_headers() -> dict:
    key = C._read_key('OPENAI_API_KEY')
    if not key:
        raise SystemExit('OPENAI_API_KEY is not set in the environment or .env')
    return {'Authorization': f'Bearer {key}'}


def load_manifest(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text())
    return {'chunks': {}}


def save_manifest(path: Path, m: dict):
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(m, indent=2))
    tmp.replace(path)


def cache_path(cache_dir: Path, doi: str, dandiset_id: str) -> Path:
    return cache_dir / f"{doi.replace('/', '_')}__{dandiset_id}.json"


def needs_run(cache_dir: Path, item: dict) -> bool:
    p = cache_path(cache_dir, item['doi'], item['dandiset_id'])
    if not p.exists():
        return True
    try:
        prior = json.loads(p.read_text())
    except Exception:
        return True
    if (prior.get('prompt_version') != C.PROMPT_VERSION
            or prior.get('model') != MODEL_RECORDED):
        return True
    return prior.get('classification') == 'ERROR'


def fetch_text(fetcher, doi):
    try:
        f = fetcher.get_paper_text_detailed(doi)
    except Exception:
        return None
    return f['text'] if f['status'] == 'full_text' else None


def chunk_groups(groups: list[list[str]], max_bytes: int) -> list[list[str]]:
    """
    Pack per-paper groups of request lines into chunks of at most max_bytes.

    A group is never split across chunks: a paper's pairs must land in the
    same batch file for its repeated full text to have any chance of being
    served from the provider's prompt cache.
    """
    chunks, chunk, chunk_bytes = [], [], 0
    for lines in groups:
        group_bytes = sum(len(l) + 1 for l in lines)
        if chunk and chunk_bytes + group_bytes > max_bytes:
            chunks.append(chunk)
            chunk, chunk_bytes = [], 0
        chunk.extend(lines)
        chunk_bytes += group_bytes
    if chunk:
        chunks.append(chunk)
    return chunks


def request_line(custom_id: str, prompt: str, max_tokens: int, effort: str) -> str:
    # No temperature: OpenAI's reasoning models accept only the default, and
    # max_tokens is spelled max_completion_tokens on chat completions. The
    # effort scale also differs: OpenRouter's 'max' is OpenAI's 'xhigh', and
    # sending 'max' fails every request in the batch with a 400.
    return json.dumps({
        'custom_id': custom_id,
        'method': 'POST',
        'url': '/v1/chat/completions',
        'body': {
            'model': MODEL_OPENAI,
            'messages': [{'role': 'user', 'content': prompt}],
            'max_completion_tokens': max_tokens,
            'response_format': {'type': 'json_object'},
            'reasoning_effort': 'xhigh' if effort == 'max' else effort,
        },
    })


def build(args):
    chunk_dir = Path(args.chunk_dir)
    if list(chunk_dir.glob('chunk_*.jsonl')):
        print(f'chunk files already exist; delete {chunk_dir}/ to rebuild',
              file=sys.stderr)
        return
    chunk_dir.mkdir(parents=True, exist_ok=True)
    cache_dir = Path(args.cache_dir)
    work = [i for i in build_worklist(Path(args.results_file))
            if needs_run(cache_dir, i)]
    print(f'{len(work)} pairs need classification', file=sys.stderr, flush=True)
    fetcher = PaperFetcher(use_cache=True, cache_dir=args.paper_cache)

    items, groups = {}, []
    for group in group_by_paper(work):
        text = fetch_text(fetcher, group[0]['doi'])
        if not text:
            continue
        sent_text, _ = C._truncate(text, C.DEFAULT_MAX_INPUT_CHARS)
        lines = []
        for item in group:
            prompt = C.build_prompt(sent_text, item['dandiset_id'],
                                    item['dandiset_name'], '',
                                    item['primary_paper_doi'], mode=C.MODE_CITING)
            cid = f"{item['doi'].replace('/', '_')}__{item['dandiset_id']}"
            items[cid] = item
            lines.append(request_line(cid, prompt, args.max_tokens,
                                      args.reasoning_effort))
        groups.append(lines)

    chunks = chunk_groups(groups, MAX_FILE_BYTES)
    for n, lines in enumerate(chunks):
        (chunk_dir / f'chunk_{n:03d}.jsonl').write_text('\n'.join(lines))
    (chunk_dir / 'items.json').write_text(json.dumps(items, indent=2))
    print(f'{len(items)} requests in {len(chunks)} chunk file(s)',
          file=sys.stderr, flush=True)


def post_with_retries(what, attempts=6, prepare=None, **kwargs):
    """POST with backoff. `prepare` builds per-attempt kwargs; file streams
    must be rebuilt each try, since a failed attempt leaves them consumed."""
    for attempt in range(attempts):
        try:
            r = requests.post(**{**kwargs, **(prepare() if prepare else {})})
            if r.status_code < 500:
                return r
            print(f'  {what}: HTTP {r.status_code}, retrying',
                  file=sys.stderr, flush=True)
        except requests.RequestException as e:
            print(f'  {what}: {type(e).__name__}, retrying',
                  file=sys.stderr, flush=True)
        time.sleep(min(2 ** attempt * 5, 120))
    raise RuntimeError(f'{what} failed after {attempts} attempts')


def submit(args):
    build(args)
    headers = openai_headers()
    chunk_dir, manifest_path = Path(args.chunk_dir), Path(args.manifest)
    manifest = load_manifest(manifest_path)
    for path in sorted(chunk_dir.glob('chunk_*.jsonl')):
        entry = manifest['chunks'].setdefault(path.name, {})
        if entry.get('batch_id'):
            continue
        if not entry.get('file_id'):
            data = path.read_bytes()
            up = post_with_retries(
                f'upload {path.name}',
                prepare=lambda: {'files': {
                    'file': (path.name, io.BytesIO(data), 'application/jsonl')}},
                url='https://api.openai.com/v1/files', headers=headers,
                data={'purpose': 'batch'}, timeout=1800)
            if up.status_code != 200:
                print(f'{path.name}: upload rejected: {up.text[:200]}',
                      file=sys.stderr, flush=True)
                continue
            entry['file_id'] = up.json()['id']
            entry['requests'] = sum(1 for _ in path.open())
            save_manifest(manifest_path, manifest)
        b = post_with_retries(
            f'create batch for {path.name}',
            url='https://api.openai.com/v1/batches', headers=headers,
            json={'input_file_id': entry['file_id'],
                  'endpoint': '/v1/chat/completions',
                  'completion_window': '24h'},
            timeout=300)
        bj = b.json()
        if b.status_code != 200 or bj.get('error'):
            # Usually the enqueued-token quota; rerun submit later to retry.
            print(f'{path.name}: deferred: {json.dumps(bj)[:200]}',
                  file=sys.stderr, flush=True)
        else:
            entry['batch_id'] = bj['id']
            print(f"{path.name}: {bj['id']} ({entry['requests']} requests)",
                  file=sys.stderr, flush=True)
        save_manifest(manifest_path, manifest)


def replay_result(row: dict, item: dict, text: str, effort: str) -> dict:
    """
    Feed one stored batch response through classify_paper_reuse.

    The classifier's HTTP session is stubbed to return the batch row's body,
    so the response goes through exactly the parsing, quote verification, and
    validation an interactive call gets, and the result dict is
    indistinguishable from one apart from the batch marker.
    """
    body = ((row.get('response') or {}).get('body')) or {}
    fake = mock.Mock()
    fake.status_code = (row.get('response') or {}).get('status_code', 200)
    fake.json.return_value = body
    fake.text = json.dumps(body)
    session = mock.Mock()
    session.post.return_value = fake
    with mock.patch.object(C, '_session', return_value=session):
        result = C.classify_paper_reuse(
            text or '', dataset_id=item['dandiset_id'],
            dataset_name=item['dandiset_name'],
            primary_paper_doi=item['primary_paper_doi'],
            paper_doi=item['doi'], model=MODEL_RECORDED,
            reasoning_effort=effort, max_retries=1,
        )
    result['citing_doi'] = item['doi']
    result['dandiset_id'] = item['dandiset_id']
    result['title'] = item.get('title', '')
    result['batch'] = True
    return result


def collect(args):
    headers = openai_headers()
    chunk_dir, manifest_path = Path(args.chunk_dir), Path(args.manifest)
    cache_dir = Path(args.cache_dir)
    manifest = load_manifest(manifest_path)
    items = json.loads((chunk_dir / 'items.json').read_text())
    fetcher = PaperFetcher(use_cache=True, cache_dir=args.paper_cache)
    for name, entry in sorted(manifest['chunks'].items()):
        bid = entry.get('batch_id')
        if not bid or entry.get('ingested'):
            continue
        b = requests.get(f'https://api.openai.com/v1/batches/{bid}',
                         headers=headers, timeout=120).json()
        print(f"{name}: {b.get('status')} {b.get('request_counts')}",
              file=sys.stderr, flush=True)
        if b.get('status') != 'completed':
            continue
        n_ok = n_err = cached_tok = prompt_tok = 0
        for fid in (b.get('output_file_id'), b.get('error_file_id')):
            if not fid:
                continue
            content = requests.get(
                f'https://api.openai.com/v1/files/{fid}/content',
                headers=headers, timeout=1800).text
            for line in content.splitlines():
                row = json.loads(line)
                item = items[row['custom_id']]
                text = fetch_text(fetcher, item['doi'])
                result = replay_result(row, item, text, args.reasoning_effort)
                usage = result.get('usage') or {}
                prompt_tok += usage.get('prompt_tokens', 0)
                cached_tok += (usage.get('prompt_tokens_details') or {}) \
                    .get('cached_tokens', 0)
                p = cache_path(cache_dir, result['citing_doi'],
                               result['dandiset_id'])
                # An error must never replace a successful classification.
                if result.get('classification') == 'ERROR' and p.exists():
                    n_err += 1
                    continue
                p.write_text(json.dumps(result, indent=2))
                if result.get('classification') == 'ERROR':
                    n_err += 1
                else:
                    n_ok += 1
        entry['ingested'] = True
        entry['ok'], entry['errors'] = n_ok, n_err
        entry['prompt_tokens'], entry['cached_tokens'] = prompt_tok, cached_tok
        save_manifest(manifest_path, manifest)
        print(f'  ingested: {n_ok} ok, {n_err} errors; '
              f'{cached_tok}/{prompt_tok} prompt tokens cached',
              file=sys.stderr, flush=True)


def pump(args):
    """
    Keep the enqueued-token quota full: recreate quota-failed batches.

    A batch can be accepted at creation and still fail validation later with
    token_limit_exceeded once the enqueued-token quota is counted (40M tokens
    per model for this org, roughly three chunks). Clear those entries so
    their already-uploaded files get a fresh batch, and submit only a few per
    pass, since creations beyond the quota would just fail again.
    """
    headers = openai_headers()
    manifest_path = Path(args.manifest)
    manifest = load_manifest(manifest_path)
    for name, entry in sorted(manifest['chunks'].items()):
        bid = entry.get('batch_id')
        if not bid or entry.get('ingested'):
            continue
        b = requests.get(f'https://api.openai.com/v1/batches/{bid}',
                         headers=headers, timeout=120).json()
        if b.get('status') == 'failed':
            errs = json.dumps(b.get('errors') or {})
            if 'token_limit_exceeded' in errs:
                entry['batch_id'] = None
                save_manifest(manifest_path, manifest)
            elif 'Cannot find file' in errs:
                # Validation raced the upload: a batch created right after its
                # file was uploaded can fail to see it. Recreate from the file
                # when it still exists; re-upload when it is truly gone.
                f = requests.get(
                    f"https://api.openai.com/v1/files/{entry['file_id']}",
                    headers=headers, timeout=60)
                entry['batch_id'] = None
                if f.status_code != 200:
                    entry['file_id'] = None
                save_manifest(manifest_path, manifest)
            else:
                print(f'{name}: failed for another reason: {errs[:200]}',
                      file=sys.stderr, flush=True)
    submitted = 0
    for name, entry in sorted(manifest['chunks'].items()):
        if entry.get('batch_id') or not entry.get('file_id'):
            continue
        if submitted >= args.pump_size:
            break
        submitted += 1
        b = post_with_retries(
            f'create batch for {name}',
            url='https://api.openai.com/v1/batches', headers=headers,
            json={'input_file_id': entry['file_id'],
                  'endpoint': '/v1/chat/completions',
                  'completion_window': '24h'},
            timeout=300)
        bj = b.json()
        if b.status_code != 200 or bj.get('error'):
            if 'token_limit' in json.dumps(bj):
                break
            print(f'{name}: refused: {json.dumps(bj)[:200]}',
                  file=sys.stderr, flush=True)
        else:
            entry['batch_id'] = bj['id']
            print(f"{name}: resubmitted as {bj['id']}",
                  file=sys.stderr, flush=True)
        save_manifest(manifest_path, manifest)


def status(args):
    headers = openai_headers()
    manifest = load_manifest(Path(args.manifest))
    for name, entry in sorted(manifest['chunks'].items()):
        bid = entry.get('batch_id')
        if not bid:
            state = ('uploaded, batch deferred' if entry.get('file_id')
                     else 'not uploaded')
            print(f'{name}: {state}')
            continue
        if entry.get('ingested'):
            print(f"{name}: ingested ({entry.get('ok')} ok, "
                  f"{entry.get('errors')} errors)")
            continue
        b = requests.get(f'https://api.openai.com/v1/batches/{bid}',
                         headers=headers, timeout=120).json()
        print(f"{name}: {b.get('status')} {b.get('request_counts')}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',
                        choices=['build', 'submit', 'collect', 'pump', 'status'])
    parser.add_argument('--results-file',
                        default=str(REPO / 'output/all_dandiset_papers.json'))
    parser.add_argument('--paper-cache', default=str(REPO / '.paper_cache'))
    parser.add_argument('--cache-dir',
                        default=str(REPO / '.fulltext_classification_cache'))
    parser.add_argument('--chunk-dir', default=str(REPO / '.batch_chunks'))
    parser.add_argument('--manifest',
                        default=str(REPO / '.batch_chunks/manifest.json'))
    parser.add_argument('--reasoning-effort',
                        choices=sorted(C.VALID_REASONING_EFFORTS),
                        default=C.DEFAULT_REASONING_EFFORT)
    parser.add_argument('--max-tokens', type=int, default=C.DEFAULT_MAX_TOKENS)
    parser.add_argument('--pump-size', type=int, default=4,
                        help='batches to (re)create per pump pass; keep near '
                             'what the enqueued-token quota holds at once.')
    args = parser.parse_args()
    {'build': build, 'submit': submit, 'collect': collect,
     'pump': pump, 'status': status}[args.command](args)


if __name__ == '__main__':
    main()
