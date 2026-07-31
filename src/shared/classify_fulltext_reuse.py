#!/usr/bin/env python3
"""
classify_fulltext_reuse.py - Classify data reuse from a paper's full text.

The citation-context pipeline extracts small windows of text around each
citation and asks the model to judge from those. This module takes the opposite
approach: it hands the model the entire paper and asks it to find the evidence
itself, then requires it to quote the passage it judged from.

Requiring a quote is the point. A label on its own cannot be checked, whereas a
quote either appears in the paper or it does not, and a quote that does not
appear is a fabrication the caller can detect and act on.

Two rules govern the output:

  1. A failure is never a classification. Any transport error, malformed
     response, or unrecognized label produces ERROR. It never produces REUSE,
     and it never produces a quiet MENTION that would read as a real judgement.

  2. Every quote is checked against the paper text before it is returned.

Requires DEEPSEEK_API_KEY in the environment or in the project .env file.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import unicodedata
from typing import Any, Optional

import requests

DEEPSEEK_API_URL = "https://api.deepseek.com/chat/completions"
DEFAULT_MODEL = "deepseek-v4-flash"

VALID_CLASSIFICATIONS = frozenset({'REUSE', 'MENTION', 'NEITHER'})

# The model is a reasoning model, so completion tokens cover thinking as well as
# the answer. Too small a budget truncates mid-JSON and yields an ERROR.
DEFAULT_MAX_TOKENS = 8192

# Verified against the live API at 800K characters (~136K tokens). Real papers
# in this corpus top out near 140K characters, so truncation should not trigger;
# it exists so that an unusually large input degrades visibly rather than by
# being silently rejected.
DEFAULT_MAX_INPUT_CHARS = 600_000


class ClassificationError(Exception):
    """Raised only by helpers; the public entry point returns ERROR instead."""


# --------------------------------------------------------------------------- #
# API key
# --------------------------------------------------------------------------- #

def get_deepseek_api_key() -> Optional[str]:
    """Return the DeepSeek API key from the environment or the project .env."""
    api_key = os.environ.get('DEEPSEEK_API_KEY')
    if api_key:
        return api_key

    env_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), '..', '..', '.env')
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, _, value = line.partition('=')
                    if key.strip() == 'DEEPSEEK_API_KEY':
                        return value.strip().strip('"').strip("'")
    return None


# --------------------------------------------------------------------------- #
# Quote verification
# --------------------------------------------------------------------------- #

# Characters that text extractors and language models disagree about. The model
# routinely straightens curly quotes and dashes when transcribing, so a strict
# substring test reports hallucinations that did not happen.
_CHAR_FOLDING = {
    '‘': "'", '’': "'", '‚': "'", '‛': "'",
    '“': '"', '”': '"', '„': '"', '‟': '"',
    '‐': '-', '‑': '-', '‒': '-', '–': '-',
    '—': '-', '―': '-', '−': '-',
    ' ': ' ', ' ': ' ', ' ': ' ', ' ': ' ',
    '​': '', '‌': '', '‍': '', '﻿': '',
    '…': '...',
}


def _fold(text: str) -> str:
    """Fold characters that extraction and transcription disagree about."""
    text = unicodedata.normalize('NFKC', text)
    return ''.join(_CHAR_FOLDING.get(ch, ch) for ch in text)


def _collapse_space(text: str) -> str:
    return re.sub(r'\s+', ' ', text).strip()


def _strip_space(text: str) -> str:
    return re.sub(r'\s+', '', text)


def verify_quote(quote: str, paper_text: str) -> dict:
    """
    Locate `quote` in `paper_text`, tolerating benign transcription differences.

    Progressively looser matching, recording which tier succeeded so the caller
    can tell a byte-perfect quote from one that merely survived normalization:

      exact                - present verbatim
      normalized           - present after folding quotes, dashes, and spaces
      case_insensitive     - present apart from capitalization
      spacing_insensitive  - present ignoring whitespace entirely
      not_found            - absent, meaning the model fabricated it

    Every tier preserves the order of non-whitespace characters, so a quote that
    matches is genuinely present rather than merely built from the same words.

    The case-insensitive tier is not pedantry. A model quoting from the middle
    of a sentence routinely drops the lead-in and capitalizes the new first
    word: the paper reads "Notably, a recent study also reported..." and the
    quote comes back as "A recent study also reported...". Reporting that as a
    possible fabrication trains the reader to ignore the warning, which costs
    more than the small looseness of ignoring case.
    """
    result = {
        'quote': quote,
        'chars': len(quote or ''),
        'match_type': 'not_found',
        'verbatim': False,
        'offset': None,
    }
    if not quote or not paper_text:
        return result

    offset = paper_text.find(quote)
    if offset != -1:
        result.update(match_type='exact', verbatim=True, offset=offset)
        return result

    folded_paper = _collapse_space(_fold(paper_text))
    folded_quote = _collapse_space(_fold(quote))
    offset = folded_paper.find(folded_quote)
    if offset != -1:
        result.update(match_type='normalized', offset=offset)
        return result

    offset = folded_paper.lower().find(folded_quote.lower())
    if offset != -1:
        result.update(match_type='case_insensitive', offset=offset)
        return result

    stripped_paper = _strip_space(_fold(paper_text)).lower()
    stripped_quote = _strip_space(_fold(quote)).lower()
    if stripped_quote and stripped_quote in stripped_paper:
        result.update(match_type='spacing_insensitive',
                      offset=stripped_paper.find(stripped_quote))
    return result


# --------------------------------------------------------------------------- #
# Prompt
# --------------------------------------------------------------------------- #

def build_prompt(
    paper_text: str,
    dataset_id: str = '',
    dataset_name: str = '',
    dataset_description: str = '',
    primary_paper_doi: str = '',
) -> str:
    """Build the classification prompt around the full paper text."""
    if dataset_id or dataset_name:
        target = "THE DATASET DESCRIBED BELOW"
        described = "THE DATASET IN QUESTION\n"
        if dataset_id:
            described += f"Identifier: {dataset_id}\n"
        if dataset_name:
            described += f"Name: {dataset_name}\n"
        if dataset_description:
            described += f"Description: {dataset_description[:2000]}\n"
        if primary_paper_doi:
            described += f"Primary paper DOI: {primary_paper_doi}\n"
    else:
        target = "ANY EXTERNALLY-PUBLISHED DATASET THAT THE AUTHORS DID NOT COLLECT"
        described = ""

    return f"""You are reading the full text of a scientific paper to determine whether its authors REUSED DATA from {target}.

{described}
CLASSIFICATIONS
- REUSE: The authors obtained and analyzed the actual DATA (recordings, images, behavioral traces, scans, spike trains, etc.) that they did not collect themselves. Downloading, re-analyzing, re-processing, training on, or benchmarking against the data all count.
- MENTION: The paper cites or discusses the work as background, prior findings, methodology, or comparison, but the authors did not obtain and analyze the data itself.
- NEITHER: The paper has no meaningful relationship to it at all, or the apparent reference is a parsing artifact.

HOW TO DECIDE
Data reuse is the rare case. Most references to other work are background mentions. Default to MENTION and output REUSE only when the text shows the authors actually handled the data.

Evidence of REUSE typically appears in Methods, Data Availability, Results, or Acknowledgements, and reads like: "data were downloaded from", "we analyzed the publicly available dataset", "obtained from the X archive", "we used the dataset of [author]", "accession number", or a description of re-processing someone else's recordings.

A review, perspective, commentary, or editorial article does NOT reuse data, even when it describes datasets in detail. Classify those as MENTION.

EVIDENCE QUOTES — THIS IS THE CRITICAL REQUIREMENT
You MUST quote the exact passage(s) from the paper that drove your judgement.
- Copy the text CHARACTER FOR CHARACTER from the paper above. Do not paraphrase, summarize, correct, abridge, or join separated sentences.
- Each quote should be one or two complete sentences: long enough to stand on its own, short enough to be precise.
- Give 1 to 3 quotes. For REUSE, quote the passage showing the authors obtained the data. For MENTION, quote the passage showing the citation is background rather than reuse. For NEITHER, quote whatever passage is most relevant, or use an empty list if genuinely nothing is relevant.
- If you cannot find a supporting passage, return an empty list rather than inventing one. A fabricated quote is far worse than no quote.

OUTPUT
Return ONLY a JSON object, no markdown fences and no commentary:
{{
  "classification": "REUSE" | "MENTION" | "NEITHER",
  "confidence": <integer 1-10>,
  "evidence_quotes": ["<exact quote>", ...],
  "source_archive": "<archive or source the data came from, or null>",
  "reasoning": "<2-4 sentences explaining the judgement>"
}}

FULL TEXT OF THE PAPER
{paper_text}
"""


# --------------------------------------------------------------------------- #
# Result helpers
# --------------------------------------------------------------------------- #

def _error_result(kind: str, message: str, **extra) -> dict:
    """
    Build an ERROR result.

    ERROR is a distinct outcome from every real classification. It must never be
    collapsed into REUSE, and it must never be collapsed into MENTION either,
    since a silent MENTION would be indistinguishable from a genuine negative
    and would quietly bias the corpus.
    """
    result = {
        'classification': 'ERROR',
        'confidence': 0,
        'evidence_quotes': [],
        'quote_warnings': [],
        'hallucinated_quote_count': 0,
        'source_archive': None,
        'reasoning': None,
        'error': message,
        'error_kind': kind,
        'usage': None,
        'truncation': None,
    }
    result.update(extra)
    return result


def parse_strict(content: str) -> dict:
    """
    Parse the model's JSON response, rejecting anything unrecognized.

    Deliberately strict. The shared `parse_json_response` falls back to scanning
    for a label keyword anywhere in the text, which would turn an error page
    containing the word "REUSE" into a REUSE verdict. Here, a response that is
    not well-formed JSON carrying a valid label is an error.

    Raises ClassificationError when the response cannot be trusted.
    """
    if not content or not content.strip():
        raise ClassificationError('empty response')

    text = content.strip()
    if text.startswith('```'):
        text = re.sub(r'^```(?:json)?\s*', '', text)
        text = re.sub(r'\s*```$', '', text).strip()

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        # One narrow rescue: a single JSON object embedded in prose. Anything
        # looser risks inventing a verdict out of surrounding text.
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if not match:
            raise ClassificationError(f'response is not JSON: {text[:200]}')
        try:
            parsed = json.loads(match.group())
        except json.JSONDecodeError:
            raise ClassificationError(f'response is not JSON: {text[:200]}')

    if not isinstance(parsed, dict):
        raise ClassificationError(f'response is not a JSON object: {text[:200]}')

    raw_label = parsed.get('classification')
    if not isinstance(raw_label, str):
        raise ClassificationError(f'missing classification field: {text[:200]}')

    label = raw_label.strip().upper().replace(' ', '_')
    if label not in VALID_CLASSIFICATIONS:
        raise ClassificationError(f'unrecognized classification {label!r}')
    parsed['classification'] = label
    return parsed


def _truncate(paper_text: str, max_chars: int) -> tuple[str, Optional[dict]]:
    """
    Trim an oversized paper, keeping the head and the tail.

    Data availability statements sit at the end of a paper, so a head-only trim
    would remove the passages most likely to prove reuse.
    """
    if len(paper_text) <= max_chars:
        return paper_text, None

    head = int(max_chars * 0.6)
    tail = max_chars - head
    marker = '\n\n[... MIDDLE OF PAPER OMITTED FOR LENGTH ...]\n\n'
    trimmed = paper_text[:head] + marker + paper_text[-tail:]
    info = {
        'truncated': True,
        'original_chars': len(paper_text),
        'kept_chars': len(trimmed),
        'dropped_chars': len(paper_text) - max_chars,
        'strategy': 'head_tail',
    }
    print(f"  WARNING: paper is {len(paper_text):,} chars, truncating to "
          f"{max_chars:,} (head+tail); {info['dropped_chars']:,} chars dropped",
          file=sys.stderr, flush=True)
    return trimmed, info


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #

def classify_paper_reuse(
    paper_text: str,
    dataset_id: str = '',
    dataset_name: str = '',
    dataset_description: str = '',
    primary_paper_doi: str = '',
    paper_doi: str = '',
    api_key: Optional[str] = None,
    model: str = DEFAULT_MODEL,
    max_input_chars: int = DEFAULT_MAX_INPUT_CHARS,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    temperature: float = 0.1,
    timeout: int = 300,
    max_retries: int = 3,
) -> dict:
    """
    Classify data reuse by sending the whole paper to DeepSeek.

    Args:
        paper_text: Full text of the paper. Must be the article body; an
            abstract cannot support this judgement, since reuse is described in
            Methods and Data Availability.
        dataset_id / dataset_name / dataset_description / primary_paper_doi:
            Optional. When supplied, the question is whether this paper reused
            that specific dataset. When omitted, it is whether the paper reused
            any dataset its authors did not collect.
        paper_doi: Recorded on the result for traceability only.

    Returns a dict with:
        classification            REUSE | MENTION | NEITHER | ERROR
        confidence                1-10, or 0 for ERROR
        evidence_quotes           list of verified quote records
        quote_warnings            human-readable notes about unverifiable quotes
        hallucinated_quote_count  quotes not found in the paper at all
        source_archive, reasoning, usage, truncation, error, error_kind
    """
    if not paper_text or not paper_text.strip():
        return _error_result('empty_input', 'No paper text supplied',
                             paper_doi=paper_doi)

    key = api_key or get_deepseek_api_key()
    if not key:
        return _error_result(
            'no_api_key',
            'No DeepSeek API key. Set DEEPSEEK_API_KEY in the environment or .env.',
            paper_doi=paper_doi)

    sent_text, truncation = _truncate(paper_text, max_input_chars)
    prompt = build_prompt(sent_text, dataset_id, dataset_name,
                          dataset_description, primary_paper_doi)

    payload = {
        'model': model,
        'messages': [{'role': 'user', 'content': prompt}],
        'max_tokens': max_tokens,
        'temperature': temperature,
        'response_format': {'type': 'json_object'},
    }
    headers = {
        'Authorization': f'Bearer {key}',
        'Content-Type': 'application/json',
    }

    raw = None
    last_error = None
    for attempt in range(max_retries):
        try:
            response = requests.post(DEEPSEEK_API_URL, headers=headers,
                                     json=payload, timeout=timeout)
            if response.status_code == 429 or response.status_code >= 500:
                last_error = f'HTTP {response.status_code}'
                if attempt < max_retries - 1:
                    time.sleep(2 ** (attempt + 1))
                    continue
                return _error_result('http_error',
                                     f'{last_error} after {max_retries} attempts',
                                     paper_doi=paper_doi, truncation=truncation)
            if response.status_code != 200:
                body = response.text[:300]
                return _error_result('http_error',
                                     f'HTTP {response.status_code}: {body}',
                                     paper_doi=paper_doi, truncation=truncation)
            raw = response.json()
            break
        except (requests.Timeout, requests.ConnectionError) as e:
            last_error = f'{type(e).__name__}: {e}'
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
                continue
            return _error_result('network_error', last_error,
                                 paper_doi=paper_doi, truncation=truncation)
        except requests.RequestException as e:
            return _error_result('request_error', f'{type(e).__name__}: {e}',
                                 paper_doi=paper_doi, truncation=truncation)
        except Exception as e:  # includes malformed JSON in the envelope
            return _error_result('unexpected_error', f'{type(e).__name__}: {e}',
                                 paper_doi=paper_doi, truncation=truncation)

    if raw is None:
        return _error_result('no_response',
                             last_error or 'No response from API',
                             paper_doi=paper_doi, truncation=truncation)

    if isinstance(raw, dict) and raw.get('error'):
        return _error_result('api_error', str(raw['error'])[:300],
                             paper_doi=paper_doi, truncation=truncation)

    choices = raw.get('choices') or []
    if not choices:
        return _error_result('no_choices', 'Response contained no choices',
                             paper_doi=paper_doi, truncation=truncation)

    finish_reason = choices[0].get('finish_reason')
    content = (choices[0].get('message') or {}).get('content') or ''
    usage = raw.get('usage')

    if finish_reason == 'length':
        return _error_result(
            'truncated_response',
            f'Model output hit the {max_tokens}-token cap before finishing; '
            'raise max_tokens',
            paper_doi=paper_doi, usage=usage, truncation=truncation)

    try:
        parsed = parse_strict(content)
    except ClassificationError as e:
        return _error_result('parse_error', str(e), paper_doi=paper_doi,
                             usage=usage, truncation=truncation)

    # Verify quotes against the text the model was actually shown.
    verified, warnings, hallucinated = [], [], 0
    quotes = parsed.get('evidence_quotes') or []
    if isinstance(quotes, str):
        quotes = [quotes]
    if not isinstance(quotes, list):
        warnings.append(f'evidence_quotes was {type(quotes).__name__}, not a list')
        quotes = []

    for quote in quotes:
        if not isinstance(quote, str):
            warnings.append(f'ignored non-string quote of type {type(quote).__name__}')
            continue
        record = verify_quote(quote, sent_text)
        verified.append(record)
        if record['match_type'] == 'not_found':
            hallucinated += 1
            warnings.append(
                f'quote not found in paper (possible fabrication): '
                f'{quote[:120]!r}')
        elif record['match_type'] != 'exact':
            warnings.append(
                f"quote matched only after {record['match_type']} normalization: "
                f'{quote[:80]!r}')

    confidence = parsed.get('confidence')
    if not isinstance(confidence, int) or not 1 <= confidence <= 10:
        warnings.append(f'confidence {confidence!r} out of range, recorded as-is')

    return {
        'classification': parsed['classification'],
        'confidence': confidence,
        'evidence_quotes': verified,
        'quote_warnings': warnings,
        'hallucinated_quote_count': hallucinated,
        'source_archive': parsed.get('source_archive'),
        'reasoning': parsed.get('reasoning'),
        'paper_doi': paper_doi,
        'input_chars': len(sent_text),
        'truncation': truncation,
        'usage': usage,
        'model': model,
        'error': None,
        'error_kind': None,
    }


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def main():
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--doi', help='Fetch this paper and classify it')
    source.add_argument('--text-file', help='Classify text from a local file')
    parser.add_argument('--dataset-id', default='')
    parser.add_argument('--dataset-name', default='')
    parser.add_argument('--model', default=DEFAULT_MODEL)
    parser.add_argument('--cache-dir', default='.paper_cache')
    parser.add_argument('--max-tokens', type=int, default=DEFAULT_MAX_TOKENS)
    args = parser.parse_args()

    if args.text_file:
        paper_text = open(args.text_file).read()
        paper_doi = args.text_file
    else:
        sys.path.insert(0, os.path.join(
            os.path.dirname(os.path.abspath(__file__)), '..', '..'))
        from fetch_paper import PaperFetcher

        fetcher = PaperFetcher(use_cache=True, cache_dir=args.cache_dir)
        fetched = fetcher.get_paper_text_detailed(args.doi)
        if fetched['status'] != 'full_text':
            print(json.dumps(_error_result(
                'no_full_text',
                f"Cannot classify {args.doi}: {fetched['reason']}",
                paper_doi=args.doi), indent=2))
            return 1
        paper_text = fetched['text']
        paper_doi = args.doi
        print(f"Fetched {len(paper_text):,} chars from {fetched['source']}",
              file=sys.stderr)

    result = classify_paper_reuse(
        paper_text,
        dataset_id=args.dataset_id,
        dataset_name=args.dataset_name,
        paper_doi=paper_doi,
        model=args.model,
        max_tokens=args.max_tokens,
    )
    print(json.dumps(result, indent=2))
    return 0 if result['classification'] != 'ERROR' else 1


if __name__ == '__main__':
    sys.exit(main())
