#!/usr/bin/env python3
"""
Decide whether a DOI scraped from a dandiset description actually describes it.

A dandiset that registers no relatedResource falls back to whatever DOIs appear
in its free-text description. That text is prose, and the DOIs in it are just as
likely to be background citations, method references, or a list of papers that
have *used* the data. Every citation of a dandiset's primary paper is treated as
a candidate reuse of that dandiset, so admitting the wrong DOI does not add a
little noise, it adds every paper that cites an unrelated work. Two such DOIs
were responsible for 835 spurious citing-paper links before they were caught.

The asymmetry decides the default. Wrongly admitting a DOI pollutes the corpus
with hundreds of papers; wrongly rejecting one loses coverage of a single
dandiset, visibly, in a field on the record. So anything short of a confident
"this describes the dataset" is excluded, and the reason is recorded either way.

Usage:
    python -m src.indirect_pipeline.validate_description_dois --report
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Optional

import requests

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from src.shared.classify_fulltext_reuse import (  # noqa: E402
    DEFAULT_MODEL, DEFAULT_PROVIDER, _read_key,
)

CACHE_DIR = REPO / '.description_doi_cache'

INCLUDE = 'describes'
VALID_VERDICTS = frozenset({INCLUDE, 'background', 'unclear'})

PROMPT = """A dataset repository entry lists a DOI that was scraped from its free-text description. Decide whether that DOI is the paper DESCRIBING this dataset, or a work cited for some other reason.

This matters because every paper citing a dataset's describing paper is treated as a candidate reuse of that dataset. Admitting a background citation pulls in every paper that cites an unrelated work.

DATASET NAME: {name}

DESCRIPTION:
{description}

DOI IN QUESTION: {doi}

Choose one:
- "describes": the description presents this DOI as the publication reporting THIS dataset. Phrasing such as "data from", "as published in", "this dataset accompanies", "associated with", or a description that reads as that paper's abstract.
- "background": the DOI is cited for context, to credit a method, to support a claim, or as related work. Also choose this when the description lists papers that USED the dataset, which is the opposite of a describing paper.
- "unclear": the description does not say enough to tell.

Answer in English. Return ONLY this JSON, with exactly these keys:
{{"verdict": "describes", "confidence": 7, "reason": "one short sentence quoting the relevant phrasing"}}"""


def cache_path(dandiset_id: str, doi: str) -> Path:
    safe = f"{dandiset_id}__{doi}".replace('/', '_').replace(':', '_').replace('\\', '_')
    return CACHE_DIR / f"{safe}.json"


def _parse(content: str) -> Optional[dict]:
    """
    Read the model's verdict, tolerating a mangled key but not a missing answer.

    The model occasionally emits the right value under the wrong key, so a
    verdict is recovered from any field holding one of the valid values. It also
    occasionally answers in another language, which is why the prompt asks for
    English and why an unrecognised answer is rejected rather than guessed at.
    """
    text = re.sub(r'^```(?:json)?\s*|\s*```$', '', (content or '').strip())
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if not match:
            return None
        try:
            parsed = json.loads(match.group())
        except json.JSONDecodeError:
            return None
    if not isinstance(parsed, dict):
        return None

    verdict = parsed.get('verdict')
    if not (isinstance(verdict, str) and verdict.strip().lower() in VALID_VERDICTS):
        verdict = next(
            (v.strip().lower() for k, v in parsed.items()
             if isinstance(v, str) and v.strip().lower() in VALID_VERDICTS),
            None)
        if verdict is None:
            return None
    parsed['verdict'] = verdict.strip().lower() if isinstance(verdict, str) else verdict
    return parsed


def validate_doi(dandiset_id: str, name: str, description: str, doi: str,
                 api_key: Optional[str] = None, model: str = DEFAULT_MODEL,
                 use_cache: bool = True, timeout: int = 180) -> dict:
    """
    Return {'verdict', 'confidence', 'reason', 'include'} for one scraped DOI.

    A failure yields verdict 'error' and include False. That is the safe
    direction here, and unlike a classification it is recorded rather than
    silently dropped, so a run can report what it could not decide.
    """
    path = cache_path(dandiset_id, doi)
    if use_cache and path.exists():
        try:
            return json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            pass

    key = api_key or _read_key('OPENROUTER_API_KEY')
    if not key:
        return {'verdict': 'error', 'confidence': 0, 'include': False,
                'reason': 'no OPENROUTER_API_KEY available', 'doi': doi,
                'dandiset_id': dandiset_id}

    payload = {
        'model': model,
        'messages': [{'role': 'user', 'content': PROMPT.format(
            name=name, description=(description or '')[:4000], doi=doi)}],
        'max_tokens': 4000,
        'temperature': 0.1,
        'response_format': {'type': 'json_object'},
    }
    if '/' in model and DEFAULT_PROVIDER:
        payload['provider'] = {'order': [DEFAULT_PROVIDER], 'allow_fallbacks': False}

    try:
        resp = requests.post('https://openrouter.ai/api/v1/chat/completions',
                             headers={'Authorization': f'Bearer {key}',
                                      'Content-Type': 'application/json'},
                             json=payload, timeout=timeout)
        if resp.status_code != 200:
            raise RuntimeError(f'HTTP {resp.status_code}: {resp.text[:200]}')
        parsed = _parse((resp.json()['choices'][0]['message'] or {}).get('content'))
        if parsed is None:
            raise RuntimeError('model output could not be read as a verdict')
    except Exception as e:
        result = {'verdict': 'error', 'confidence': 0, 'include': False,
                  'reason': f'{type(e).__name__}: {e}', 'doi': doi,
                  'dandiset_id': dandiset_id}
        return result

    result = {
        'verdict': parsed['verdict'],
        'confidence': parsed.get('confidence'),
        'reason': parsed.get('reason'),
        'include': parsed['verdict'] == INCLUDE,
        'doi': doi,
        'dandiset_id': dandiset_id,
        'model': model,
    }
    if use_cache:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        try:
            path.write_text(json.dumps(result, indent=2))
        except OSError:
            pass
    return result


def filter_description_resources(dandiset_id: str, name: str, description: str,
                                 resources: list[dict], **kwargs) -> list[dict]:
    """
    Drop description-scraped resources the model does not judge as describing.

    Resources from a registered relatedResource are left alone: DANDI's own
    metadata is a deliberate statement by the depositor, and second-guessing it
    is a different problem from reading prose.
    """
    kept = []
    for resource in resources:
        if resource.get('source') != 'description':
            kept.append(resource)
            continue
        verdict = validate_doi(dandiset_id, name, description,
                               resource.get('doi') or '', **kwargs)
        resource = {**resource,
                    'llm_verdict': verdict['verdict'],
                    'llm_confidence': verdict.get('confidence'),
                    'llm_reasoning': verdict.get('reason')}
        if verdict['include']:
            kept.append(resource)
    return kept


def main():
    import argparse
    from collections import Counter

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--results-file',
                        default=str(REPO / 'output/all_dandiset_papers_refreshed.json'))
    parser.add_argument('--report', action='store_true',
                        help='Report what would change without writing anything.')
    args = parser.parse_args()

    data = json.loads(Path(args.results_file).read_text())
    session = requests.Session()
    verdicts, dropped_papers = Counter(), 0

    for result in data['results']:
        desc_res = [r for r in (result.get('paper_relations') or [])
                    if r.get('source') == 'description']
        if not desc_res:
            continue
        ds = result['dandiset_id']
        try:
            info = session.get(
                f'https://api.dandiarchive.org/api/dandisets/{ds}/versions/draft/info/',
                timeout=30).json()
            description = info.get('metadata', {}).get('description') or ''
        except Exception:
            description = ''
        for resource in desc_res:
            v = validate_doi(ds, result.get('dandiset_name', ''), description,
                             resource.get('doi') or '')
            verdicts[v['verdict']] += 1
            if not v['include']:
                dropped_papers += len(result.get('citing_papers') or [])
                print(f"  DROP {ds} {resource.get('doi')}: {v['verdict']} "
                      f"({v.get('reason') or ''})", file=sys.stderr)

    print(f"\nverdicts: {dict(verdicts)}", file=sys.stderr)
    print(f"citing papers behind dropped DOIs: {dropped_papers}", file=sys.stderr)


if __name__ == '__main__':
    main()
