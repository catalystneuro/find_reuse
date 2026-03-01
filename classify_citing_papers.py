#!/usr/bin/env python3
"""
classify_citing_papers.py - Classify citing papers as data reuse or paper mention

This script takes papers that cite a dataset's primary paper and uses an LLM
to classify whether each citing paper actually REUSED THE DATA from the dataset,
or just cited the primary paper for background/context.

This addresses the key insight that most secondary data use papers cite the
primary paper associated with the dataset, NOT the dataset itself.

Classification schema (two orthogonal decisions):
1. Citation type (classification + confidence 1-10):
   - REUSE: The citing paper downloaded/accessed and reused the dataset
   - MENTION: Cites the paper as prior work / background but does not use the data
   - NEITHER: Not a real reference to the dataset (parsing mistake, irrelevant citation)
2. Same lab (same_lab bool + same_lab_confidence 1-10, only for REUSE):
   - true: Authors overlap with the original dataset creators
   - false: Authors are from a different lab/group

Input modes:
1. Results file from dandi_primary_papers.py --citations --fetch-text
2. Pre-extracted citation_contexts.json from extract_citation_contexts.py

Usage:
    python classify_citing_papers.py --results-file output/dandi_all_results.json
    python classify_citing_papers.py --contexts-file output/citation_contexts.json
    python classify_citing_papers.py --results-file output/dandi_all_results.json --max-papers 10
    python classify_citing_papers.py --results-file output/dandi_all_results.json --model google/gemini-2.5-flash
"""

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests
from tqdm import tqdm

from citation_context import find_citation_contexts, find_citation_in_cached_paper
from find_reuse import ArchiveFinder
from llm_utils import get_api_key, call_openrouter_api, parse_json_response, DEFAULT_MODEL

# Classification cache
CLASSIFICATION_CACHE_DIR = Path(__file__).parent / '.classification_cache'

# Valid classification values
VALID_CLASSIFICATIONS = {'REUSE', 'MENTION', 'NEITHER'}


def build_classification_prompt(
    contexts: list[dict],
    dandiset_id: str,
    dandiset_name: str,
    cited_doi: str,
    citing_doi: str,
    fallback_text: Optional[str] = None,
) -> str:
    """
    Build an LLM prompt for classifying a citing paper's relationship to a dataset.

    Args:
        contexts: List of citation context dicts from citation_context.py
        dandiset_id: DANDI dataset identifier
        dandiset_name: Name of the DANDI dataset
        cited_doi: DOI of the primary paper being cited
        citing_doi: DOI of the citing paper
        fallback_text: If no contexts found, first N chars of paper text

    Returns:
        Prompt string for the LLM
    """
    prompt = f"""You are classifying how a scientific paper relates to a DANDI neuroscience dataset. The citing paper references the primary paper associated with the dataset. Your job is to determine whether the citing paper actually REUSED THE DATA from that dataset, or just cited the paper as prior work.

DANDI DATASET: {dandiset_id} - {dandiset_name}
PRIMARY PAPER DOI (the paper that originally published the dataset): {cited_doi}
CITING PAPER DOI (the paper we are classifying): {citing_doi}

"""

    if contexts:
        prompt += f"The following are {len(contexts)} text excerpt(s) from the citing paper where the primary paper is referenced:\n\n"
        for i, ctx in enumerate(contexts, 1):
            context_text = ctx.get('context', '')
            method = ctx.get('method', 'unknown')
            prompt += f"--- Excerpt {i} (detected via {method}) ---\n{context_text}\n\n"
    elif fallback_text:
        prompt += "No specific citation contexts were found. Here is the beginning of the paper text:\n\n"
        prompt += f"--- Paper text (first portion) ---\n{fallback_text}\n\n"
    else:
        prompt += "No text was available for this paper.\n\n"

    prompt += """Based on the text above, make TWO separate decisions:

DECISION 1 - Did this paper reuse the DATA from the dataset?
- REUSE: The citing paper downloaded/accessed and reused the actual DATA (recordings, images, behavioral traces, etc.) for their own analysis. Look for phrases like "we used data from", "we downloaded", "we analyzed recordings from", "data were obtained from", or explicit mentions of using the DANDI archive.
- MENTION: The paper cites the primary paper as prior work, background, or for comparison, but does NOT actually use the underlying data. The citation is for referencing findings, methods, or context.
- NEITHER: The citation is not meaningfully related to the dataset — e.g., a parsing mistake, an unrelated reference, or the context is too ambiguous to determine any relationship.

IMPORTANT RULES:
1. Using or adapting analytical SOFTWARE, code, algorithms, or methods from the primary paper is NOT data reuse. Only classify as REUSE if the actual recorded data (e.g., neural recordings, imaging data, behavioral data) was downloaded and reanalyzed. If a paper only uses software tools, analysis pipelines, or methodological approaches from the primary paper, that is MENTION.
2. The citing paper must have reused data from THIS SPECIFIC dataset (described by the primary paper DOI above). If the paper reuses data from a DIFFERENT dataset but merely cites this primary paper for context, methodology, or as a reference, that is MENTION, not REUSE. Be precise about which dataset's data was actually used.

DECISION 2 - If REUSE, is it the same lab?
Check whether the citing paper's author list shares names with the primary paper's authors. If the same group reused or extended their own data, same_lab is true. If a different group used it, same_lab is false.

Respond ONLY with a JSON object (no markdown, no explanation outside the JSON):
{"classification": "REUSE|MENTION|NEITHER", "confidence": <1-10>, "same_lab": <true|false>, "same_lab_confidence": <1-10>, "reasoning": "Brief 1-2 sentence explanation"}

Only include same_lab and same_lab_confidence when classification is REUSE.
Confidence scale: 1 = pure guess, 5 = uncertain but leaning, 8 = fairly confident, 10 = certain."""

    return prompt


## call_llm_api and parse_llm_response are now provided by llm_utils
## (call_openrouter_api with return_raw=True, and parse_json_response)


def get_cache_path(citing_doi: str, cited_doi: str) -> Path:
    """Get the classification cache file path for a DOI pair."""
    safe_citing = citing_doi.replace('/', '_')
    safe_cited = cited_doi.replace('/', '_')
    return CLASSIFICATION_CACHE_DIR / f"{safe_citing}__{safe_cited}.json"


def get_cached_classification(citing_doi: str, cited_doi: str) -> Optional[dict]:
    """Load a cached classification result if it exists."""
    cache_path = get_cache_path(citing_doi, cited_doi)
    if cache_path.exists():
        try:
            with open(cache_path) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return None


def cache_classification(citing_doi: str, cited_doi: str, result: dict):
    """Save a classification result to cache."""
    CLASSIFICATION_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = get_cache_path(citing_doi, cited_doi)
    result['cached_at'] = datetime.now(timezone.utc).isoformat()
    with open(cache_path, 'w') as f:
        json.dump(result, f, indent=2)


def classify_single_paper(
    citing_doi: str,
    cited_doi: str,
    dandiset_id: str,
    dandiset_name: str,
    cache_dir: Path,
    api_key: str,
    model: str,
    context_chars: int = 500,
    use_cache: bool = True,
    session: Optional[requests.Session] = None,
    archive_finder: Optional[ArchiveFinder] = None,
) -> dict:
    """
    Classify a single citing paper's relationship to a dataset.

    Args:
        citing_doi: DOI of the citing paper
        cited_doi: DOI of the cited primary paper
        dandiset_id: DANDI dataset ID
        dandiset_name: Name of the DANDI dataset
        cache_dir: Directory containing cached paper texts
        api_key: OpenRouter API key
        model: Model to use
        context_chars: Characters of context around citations
        use_cache: Whether to use classification cache
        session: Optional requests session for metadata lookups
        archive_finder: Optional ArchiveFinder instance for fetching missing paper text

    Returns:
        Classification result dict
    """
    # Check classification cache
    if use_cache:
        cached = get_cached_classification(citing_doi, cited_doi)
        if cached:
            cached['from_cache'] = True
            # Always update dandiset_name from current data (may have been
            # missing when originally cached)
            if dandiset_name and not cached.get('dandiset_name'):
                cached['dandiset_name'] = dandiset_name
            return cached

    result = {
        'citing_doi': citing_doi,
        'cited_doi': cited_doi,
        'dandiset_id': dandiset_id,
        'dandiset_name': dandiset_name,
        'from_cache': False,
    }

    # Load the citing paper text from cache
    paper_cache_file = cache_dir / f"{citing_doi.replace('/', '_')}.json"
    if not paper_cache_file.exists() and archive_finder is not None:
        # Attempt to fetch paper text
        try:
            text, source, from_text_cache = archive_finder.get_paper_text(citing_doi)
            if text and len(text) >= 200:
                print(f"  Fetched text for {citing_doi} ({len(text)} chars from {source})", file=sys.stderr)
            else:
                print(f"  Could not fetch text for {citing_doi}", file=sys.stderr)
        except Exception as e:
            print(f"  Error fetching text for {citing_doi}: {e}", file=sys.stderr)

    if not paper_cache_file.exists():
        result['classification'] = 'NEITHER'
        result['confidence'] = 1
        result['reasoning'] = 'Paper text not available'
        result['error'] = 'no_paper_text'
        if use_cache:
            cache_classification(citing_doi, cited_doi, result)
        return result

    try:
        with open(paper_cache_file) as f:
            paper_data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        result['classification'] = 'NEITHER'
        result['confidence'] = 1
        result['reasoning'] = f'Error reading paper cache: {e}'
        result['error'] = 'cache_read_error'
        return result

    text = paper_data.get('text', '')
    if not text or len(text) < 200:
        result['classification'] = 'NEITHER'
        result['confidence'] = 1
        result['reasoning'] = 'Paper text too short or empty'
        result['error'] = 'insufficient_text'
        if use_cache:
            cache_classification(citing_doi, cited_doi, result)
        return result

    result['text_length'] = len(text)
    result['text_source'] = paper_data.get('source', '')

    # Extract citation contexts
    contexts = find_citation_contexts(
        text, cited_doi,
        context_chars=context_chars,
        session=session,
        exclude_reference_section=True,
    )

    result['num_contexts'] = len(contexts)

    # Build prompt
    fallback_text = None
    if not contexts:
        # No specific citation context found - send beginning of paper
        fallback_text = text[:8000]

    prompt = build_classification_prompt(
        contexts=contexts,
        dandiset_id=dandiset_id,
        dandiset_name=dandiset_name,
        cited_doi=cited_doi,
        citing_doi=citing_doi,
        fallback_text=fallback_text,
    )

    # Call LLM
    response = call_openrouter_api(
        prompt, api_key, model,
        return_raw=True, max_tokens=300, timeout=60,
    )

    # Parse response
    classification = parse_json_response(
        response,
        valid_classifications=VALID_CLASSIFICATIONS,
        default_classification='NEITHER',
    )
    result.update(classification)

    # Include context excerpts in output (full text - viewer handles display)
    if contexts:
        result['context_excerpts'] = []
        for ctx in contexts[:5]:  # Max 5 excerpts
            excerpt_data = {
                'text': ctx.get('context', ''),
                'method': ctx.get('method', ''),
            }
            # Store highlight offset (position of citation within excerpt)
            if 'citation_position' in ctx and 'start' in ctx:
                excerpt_data['highlight_offset'] = ctx['citation_position'] - ctx['start']
            # Store reference info for viewer highlighting
            if ctx.get('reference_number'):
                excerpt_data['reference_number'] = ctx['reference_number']
            if ctx.get('authors'):
                excerpt_data['authors'] = ctx['authors']
            if ctx.get('year'):
                excerpt_data['year'] = ctx['year']
            result['context_excerpts'].append(excerpt_data)

    # Cache result
    if use_cache:
        cache_classification(citing_doi, cited_doi, result)

    return result


def load_citation_pairs_from_results(results_file: Path) -> list[dict]:
    """
    Load citing paper pairs from dandi_primary_papers.py --fetch-text output.

    Args:
        results_file: Path to the results JSON

    Returns:
        List of dicts with citing_doi, cited_doi, dandiset_id, dandiset_name
    """
    with open(results_file) as f:
        data = json.load(f)

    pairs = []
    seen = set()

    for result in data.get('results', []):
        dandiset_id = result['dandiset_id']
        dandiset_name = result.get('dandiset_name', '')

        for citing in result.get('citing_papers', []):
            citing_doi = citing.get('doi')
            cited_doi = citing.get('cited_paper_doi')

            if not citing_doi or not cited_doi:
                continue

            key = (citing_doi, cited_doi)
            if key in seen:
                continue
            seen.add(key)

            pairs.append({
                'citing_doi': citing_doi,
                'cited_doi': cited_doi,
                'dandiset_id': dandiset_id,
                'dandiset_name': dandiset_name,
                'citing_title': citing.get('title', ''),
                'citing_journal': citing.get('journal', ''),
                'citing_date': citing.get('publication_date', ''),
            })

    return pairs


def load_citation_pairs_from_contexts(contexts_file: Path) -> list[dict]:
    """
    Load citing paper pairs from citation_contexts.json.

    Groups by (citing_doi, cited_doi) to get unique pairs.

    Args:
        contexts_file: Path to citation_contexts.json

    Returns:
        List of dicts with citing_doi, cited_doi, dandiset_id
    """
    with open(contexts_file) as f:
        data = json.load(f)

    pairs = []
    seen = set()

    for ctx in data.get('contexts', []):
        citing_doi = ctx.get('citing_doi')
        cited_doi = ctx.get('cited_doi')
        dandiset_id = ctx.get('dandiset_id', '')

        if not citing_doi or not cited_doi:
            continue

        key = (citing_doi, cited_doi)
        if key in seen:
            continue
        seen.add(key)

        pairs.append({
            'citing_doi': citing_doi,
            'cited_doi': cited_doi,
            'dandiset_id': dandiset_id,
            'dandiset_name': '',  # Not available in contexts file
        })

    return pairs


def fetch_dandiset_names(pairs: list[dict]) -> None:
    """
    Populate dandiset_name for pairs where it's missing, using the DANDI API.

    Modifies pairs in-place. Caches results so each dandiset is only fetched once.
    """
    # Collect unique dandiset IDs that need names
    need_names = set()
    for pair in pairs:
        if not pair.get('dandiset_name') and pair.get('dandiset_id'):
            need_names.add(pair['dandiset_id'])

    if not need_names:
        return

    print(f"Fetching names for {len(need_names)} dandisets from DANDI API...", file=sys.stderr)
    name_cache = {}

    for dandiset_id in need_names:
        try:
            resp = requests.get(
                f'https://api.dandiarchive.org/api/dandisets/{dandiset_id}/',
                timeout=10,
            )
            if resp.status_code == 200:
                data = resp.json()
                # Name is in the most_recent_published_version or draft_version
                version = data.get('most_recent_published_version') or data.get('draft_version') or {}
                name = version.get('name', '')
                name_cache[dandiset_id] = name
            else:
                name_cache[dandiset_id] = ''
        except Exception:
            name_cache[dandiset_id] = ''
        time.sleep(0.2)  # Be polite to the API

    # Apply names
    for pair in pairs:
        if not pair.get('dandiset_name') and pair.get('dandiset_id'):
            pair['dandiset_name'] = name_cache.get(pair['dandiset_id'], '')


def classify_all_papers(
    pairs: list[dict],
    cache_dir: Path,
    api_key: str,
    model: str = DEFAULT_MODEL,
    max_papers: Optional[int] = None,
    rate_limit: float = 0.5,
    context_chars: int = 500,
    use_cache: bool = True,
    show_progress: bool = True,
    fetch_text: bool = False,
) -> dict:
    """
    Classify all citing papers.

    Args:
        pairs: List of citation pairs from load_citation_pairs_*
        cache_dir: Directory containing cached paper texts
        api_key: OpenRouter API key
        model: Model to use
        max_papers: Maximum papers to process (None for all)
        rate_limit: Seconds between API calls
        context_chars: Characters of context around citations
        use_cache: Whether to use classification cache
        show_progress: Whether to show progress bar
        fetch_text: Whether to fetch paper text for papers not in cache

    Returns:
        Dict with metadata and classifications list
    """
    if max_papers:
        pairs = pairs[:max_papers]

    # Populate missing dandiset names
    fetch_dandiset_names(pairs)

    session = requests.Session()
    session.headers.update({
        'User-Agent': 'CitingPaperClassifier/1.0 (mailto:ben.dichter@catalystneuro.com)'
    })

    # Create ArchiveFinder for fetching missing paper text
    archive_finder = None
    if fetch_text:
        archive_finder = ArchiveFinder(
            verbose=False,
            use_cache=True,
            follow_references=False,
            cache_dir=cache_dir,
        )
        print("Text fetching enabled - will attempt to fetch missing papers", file=sys.stderr)

    classifications = []
    stats = {
        'total_pairs': len(pairs),
        'from_cache': 0,
        'api_calls': 0,
        'errors': 0,
        'by_classification': {},
    }

    pbar = tqdm(pairs, desc="Classifying papers", disable=not show_progress)

    for pair in pbar:
        pbar.set_postfix({
            'doi': pair['citing_doi'][:30],
            'cache': stats['from_cache'],
            'api': stats['api_calls'],
        })

        result = classify_single_paper(
            citing_doi=pair['citing_doi'],
            cited_doi=pair['cited_doi'],
            dandiset_id=pair['dandiset_id'],
            dandiset_name=pair['dandiset_name'],
            cache_dir=cache_dir,
            api_key=api_key,
            model=model,
            context_chars=context_chars,
            use_cache=use_cache,
            session=session,
            archive_finder=archive_finder,
        )

        # Add metadata from pair
        result['citing_title'] = pair.get('citing_title', '')
        result['citing_journal'] = pair.get('citing_journal', '')
        result['citing_date'] = pair.get('citing_date', '')

        classifications.append(result)

        # Track stats
        cls = result.get('classification', 'NEITHER')
        stats['by_classification'][cls] = stats['by_classification'].get(cls, 0) + 1

        if result.get('from_cache'):
            stats['from_cache'] += 1
        elif result.get('error'):
            stats['errors'] += 1
        else:
            stats['api_calls'] += 1
            # Rate limit only for actual API calls
            time.sleep(rate_limit)

    output = {
        'metadata': {
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'model': model,
            'context_chars': context_chars,
            'total_pairs': stats['total_pairs'],
            'api_calls': stats['api_calls'],
            'from_cache': stats['from_cache'],
            'errors': stats['errors'],
            'classification_counts': stats['by_classification'],
        },
        'classifications': classifications,
    }

    return output


def main():
    parser = argparse.ArgumentParser(
        description='Classify citing papers as data reuse or paper mention using LLM'
    )

    # Input options (at least one required)
    input_group = parser.add_mutually_exclusive_group(required=True)
    input_group.add_argument(
        '--results-file',
        type=Path,
        help='Path to dandi_primary_papers.py output JSON (with --fetch-text citing_papers)'
    )
    input_group.add_argument(
        '--contexts-file',
        type=Path,
        help='Path to citation_contexts.json from extract_citation_contexts.py'
    )

    parser.add_argument(
        '--cache-dir',
        type=Path,
        default=Path(__file__).parent / '.paper_cache',
        help='Directory containing cached paper text files (default: .paper_cache)'
    )
    parser.add_argument(
        '-o', '--output',
        type=Path,
        help='Output file path (default: stdout)'
    )
    parser.add_argument(
        '--model',
        default=DEFAULT_MODEL,
        help=f'OpenRouter model to use (default: {DEFAULT_MODEL})'
    )
    parser.add_argument(
        '--max-papers',
        type=int,
        help='Maximum number of papers to classify'
    )
    parser.add_argument(
        '--rate-limit',
        type=float,
        default=0.5,
        help='Seconds between API calls (default: 0.5)'
    )
    parser.add_argument(
        '--context-chars',
        type=int,
        default=500,
        help='Characters of context around each citation (default: 500)'
    )
    parser.add_argument(
        '--no-cache',
        action='store_true',
        help='Disable classification caching'
    )
    parser.add_argument(
        '--clear-cache',
        action='store_true',
        help='Clear classification cache before running'
    )
    parser.add_argument(
        '--fetch-text',
        action='store_true',
        help='Attempt to fetch paper text for papers not already in cache'
    )
    parser.add_argument(
        '--quiet',
        action='store_true',
        help='Suppress progress output'
    )

    args = parser.parse_args()

    # Get API key
    try:
        api_key = get_api_key()
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    # Clear cache if requested
    if args.clear_cache and CLASSIFICATION_CACHE_DIR.exists():
        import shutil
        shutil.rmtree(CLASSIFICATION_CACHE_DIR)
        print("Classification cache cleared", file=sys.stderr)

    # Load citation pairs
    if args.results_file:
        pairs = load_citation_pairs_from_results(args.results_file)
        if not pairs:
            print(
                "No citing papers found in results file. "
                "Did you run dandi_primary_papers.py with --fetch-text?",
                file=sys.stderr
            )
            sys.exit(1)
    else:
        pairs = load_citation_pairs_from_contexts(args.contexts_file)

    print(f"Found {len(pairs)} unique citing paper pairs", file=sys.stderr)

    # Run classification
    output = classify_all_papers(
        pairs=pairs,
        cache_dir=args.cache_dir,
        api_key=api_key,
        model=args.model,
        max_papers=args.max_papers,
        rate_limit=args.rate_limit,
        context_chars=args.context_chars,
        use_cache=not args.no_cache,
        show_progress=not args.quiet,
        fetch_text=args.fetch_text,
    )

    # Print summary
    counts = output['metadata']['classification_counts']
    print(f"\nClassification Summary:", file=sys.stderr)
    for cls in ['REUSE', 'MENTION', 'NEITHER']:
        count = counts.get(cls, 0)
        print(f"  {cls}: {count}", file=sys.stderr)
    print(f"  Errors: {output['metadata']['errors']}", file=sys.stderr)
    print(f"  From cache: {output['metadata']['from_cache']}", file=sys.stderr)
    print(f"  API calls: {output['metadata']['api_calls']}", file=sys.stderr)

    # Write output
    output_str = json.dumps(output, indent=2)
    if args.output:
        with open(args.output, 'w') as f:
            f.write(output_str)
        print(f"\nResults written to {args.output}", file=sys.stderr)
    else:
        print(output_str)


if __name__ == '__main__':
    main()
