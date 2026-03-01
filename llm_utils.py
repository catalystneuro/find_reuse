#!/usr/bin/env python3
"""
llm_utils.py - Shared LLM utilities for OpenRouter API calls

Provides common functionality used by both classify_usage.py (direct dataset
mention classification) and classify_citing_papers.py (citing paper classification).
"""

import json
import os
import re
import sys
import time
from typing import Optional

import requests

# OpenRouter API
OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "google/gemini-3-flash-preview"


def get_api_key() -> str:
    """
    Get OpenRouter API key from environment.

    Raises ValueError if no API key is found.
    """
    api_key = os.environ.get('OPENROUTER_API_KEY')
    if not api_key:
        raise ValueError(
            "No API key found. Set OPENROUTER_API_KEY environment variable."
        )
    return api_key


def call_openrouter_api(
    prompt: str,
    api_key: str,
    model: str = DEFAULT_MODEL,
    max_retries: int = 3,
    max_tokens: int = 512,
    temperature: float = 0.1,
    timeout: int = 90,
    return_raw: bool = False,
    return_full_interaction: bool = False,
) -> dict | str | None:
    """
    Call OpenRouter API with retry logic.

    Args:
        prompt: The prompt to send
        api_key: OpenRouter API key
        model: Model identifier
        max_retries: Number of retry attempts
        max_tokens: Maximum tokens in response
        temperature: Sampling temperature
        timeout: Request timeout in seconds
        return_raw: If True, return raw response text instead of parsed JSON
        return_full_interaction: If True, return dict with 'result', 'prompt', 'raw_response'

    Returns:
        - If return_raw: Raw response text string, or None on failure
        - If return_full_interaction: Dict with 'result', 'prompt', 'raw_response'
        - Otherwise: Parsed JSON dict from LLM response
    """
    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {api_key}',
        'HTTP-Referer': 'https://github.com/catalystneuro/find_reuse',
    }

    data = {
        'model': model,
        'max_tokens': max_tokens,
        'temperature': temperature,
        'messages': [
            {'role': 'user', 'content': prompt}
        ]
    }

    last_error = None
    raw_response = None

    for attempt in range(max_retries):
        try:
            response = requests.post(
                OPENROUTER_API_URL,
                headers=headers,
                json=data,
                timeout=timeout,
            )

            if response.status_code == 429:
                wait = 2 ** (attempt + 1)
                print(f"  Rate limited, waiting {wait}s...", file=sys.stderr)
                time.sleep(wait)
                continue

            response.raise_for_status()
            raw_response = response.json()
            break

        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
            last_error = e
            if attempt < max_retries - 1:
                wait_time = 2 ** attempt
                time.sleep(wait_time)
            continue

        except requests.RequestException as e:
            if return_raw:
                print(f"  API error: {e}", file=sys.stderr)
                return None
            raise e
    else:
        # All retries failed
        if return_raw:
            print(f"  All {max_retries} attempts failed", file=sys.stderr)
            return None
        if last_error:
            raise last_error
        return None

    # Extract content from response
    choices = raw_response.get('choices', [])
    if not choices:
        if return_raw:
            return None
        parsed = {'classification': 'UNKNOWN', 'confidence': 1, 'reasoning': 'No choices in response'}
        if return_full_interaction:
            return {'result': parsed, 'prompt': prompt, 'raw_response': raw_response}
        return parsed

    # Check finish reason
    finish_reason = choices[0].get('finish_reason', '')
    if finish_reason == 'length':
        if return_raw:
            content = choices[0].get('message', {}).get('content', '')
            return content
        parsed = {'classification': 'UNKNOWN', 'confidence': 1, 'reasoning': 'Response ended prematurely'}
        if return_full_interaction:
            return {'result': parsed, 'prompt': prompt, 'raw_response': raw_response}
        return parsed

    content = choices[0].get('message', {}).get('content', '')

    if return_raw:
        return content

    # Parse JSON from response
    if not content or len(content.strip()) < 10:
        parsed = {'classification': 'UNKNOWN', 'confidence': 1, 'reasoning': 'Empty or truncated response'}
        if return_full_interaction:
            return {'result': parsed, 'prompt': prompt, 'raw_response': raw_response}
        return parsed

    parsed = parse_json_response(content)

    if return_full_interaction:
        return {'result': parsed, 'prompt': prompt, 'raw_response': raw_response}
    return parsed


def parse_json_response(
    response_text: str,
    valid_classifications: Optional[set[str]] = None,
    default_classification: str = 'UNKNOWN',
) -> dict:
    """
    Parse JSON from an LLM response with multiple fallback strategies.

    Handles bare JSON, markdown-wrapped JSON, and malformed responses.

    Args:
        response_text: Raw text response from LLM
        valid_classifications: Optional set of valid classification values for validation
        default_classification: Default classification on parse failure

    Returns:
        Dict with at least 'classification', 'confidence', 'reasoning' keys
    """
    if not response_text:
        return {
            'classification': default_classification,
            'confidence': 1,
            'reasoning': 'No response from LLM',
            'parse_error': True,
        }

    content = response_text.strip()

    # Strategy 1: Strip markdown code blocks if present
    if content.startswith('```'):
        lines = content.split('\n')
        start_idx = 1
        end_idx = len(lines)
        for i in range(len(lines) - 1, 0, -1):
            if lines[i].strip() == '```':
                end_idx = i
                break
        content = '\n'.join(lines[start_idx:end_idx]).strip()

    # Strategy 2: Try direct JSON parse
    try:
        result = json.loads(content)
        if isinstance(result, dict) and 'classification' in result:
            _validate_classification(result, valid_classifications)
            return result
    except json.JSONDecodeError:
        pass

    # Strategy 3: Try extracting JSON from markdown code block
    json_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', response_text, re.DOTALL)
    if json_match:
        try:
            result = json.loads(json_match.group(1))
            if isinstance(result, dict) and 'classification' in result:
                _validate_classification(result, valid_classifications)
                return result
        except json.JSONDecodeError:
            pass

    # Strategy 4: Find any JSON object with "classification" key (handles nested braces)
    json_match = re.search(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', response_text)
    if json_match:
        try:
            result = json.loads(json_match.group())
            if isinstance(result, dict) and 'classification' in result:
                _validate_classification(result, valid_classifications)
                return result
        except json.JSONDecodeError:
            pass

    # Strategy 5: Simpler JSON object search
    json_match = re.search(r'\{[^{}]*"classification"[^{}]*\}', response_text)
    if json_match:
        try:
            result = json.loads(json_match.group())
            _validate_classification(result, valid_classifications)
            return result
        except (json.JSONDecodeError, KeyError):
            pass

    # Strategy 6: Look for classification keyword in text
    if valid_classifications:
        for cls in valid_classifications:
            if cls in response_text.upper():
                return {
                    'classification': cls,
                    'confidence': 1,
                    'reasoning': 'Extracted from unstructured response',
                    'parse_error': True,
                }

    return {
        'classification': default_classification,
        'confidence': 1,
        'reasoning': f'Failed to parse: {response_text[:300]}',
        'parse_error': True,
    }


def _validate_classification(result: dict, valid_classifications: Optional[set[str]] = None):
    """Normalize and validate classification value in-place."""
    if 'classification' not in result:
        return
    classification = result['classification'].upper().replace(' ', '_')
    if valid_classifications and classification not in valid_classifications:
        result['parse_error'] = f'Invalid classification: {classification}'
        # Don't override - keep the raw value for debugging
    result['classification'] = classification
