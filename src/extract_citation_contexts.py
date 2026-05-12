#!/usr/bin/env python3
"""
extract_citation_contexts.py - Extract citation contexts for LLM classification

This script iterates over all citing papers, extracts the text segments around
citations of DANDI-related papers, and prepares them for LLM classification
to determine if the citation represents data reuse or just a paper mention.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

import requests
from tqdm import tqdm

from src.citation_context import (
    find_citation_contexts,
    find_citation_in_cached_paper,
    get_context_text,
    get_paper_metadata,
)


def extract_all_citation_contexts(
    results_file: Path,
    cache_dir: Path,
    context_chars: int = 500,
    show_progress: bool = True,
    max_papers: Optional[int] = None,
) -> tuple[list[dict], list[dict], dict]:
    """
    Extract citation contexts from all cached papers.

    Returns a tuple of:
    - pair_records: one record per (citing, cited) pair whose text loaded successfully.
        Each record carries citing-paper metadata, text length/source, and a list of
        per-citation context dicts (may be empty if no occurrences were detected).
        These records are the input contract for the downstream classification stage.
    - failed_pairs: pairs where text access failed (missing cache, insufficient main
        text, or unhandled exception). Excluded from pair_records so they cannot
        propagate into classification.
    - stats: counts by outcome.
    """
    with open(results_file) as f:
        results_data = json.load(f)

    # Create a session for API calls
    session = requests.Session()
    session.headers.update({
        'User-Agent': 'CitationContextExtractor/1.0 (mailto:ben.dichter@catalystneuro.com)'
    })

    # Build alternate DOI map (preprint↔published) for better citation finding
    from src.openalex import get_alternate_doi
    alt_doi_session = requests.Session()
    alt_doi_session.headers.update({
        'User-Agent': 'CitationContextExtractor/1.0 (mailto:ben.dichter@catalystneuro.com)'
    })

    # Collect all cited DOIs and look up alternates
    all_cited_dois = set()
    for result in results_data['results']:
        for citing in result.get('citing_papers', []):
            cited_doi = citing.get('cited_paper_doi')
            if cited_doi:
                all_cited_dois.add(cited_doi)

    alt_doi_map = {}
    for cited_doi in all_cited_dois:
        alt = get_alternate_doi(alt_doi_session, cited_doi)
        if alt:
            alt_doi_map[cited_doi] = alt

    if alt_doi_map:
        print(f"Found {len(alt_doi_map)} alternate DOIs for citation context search", file=sys.stderr)

    citation_pairs = []
    failed_pairs = []
    for result in results_data['results']:
        dandiset_id = result['dandiset_id']
        dandiset_name = result.get('dandiset_name', '')
        dandiset_description = result.get('dandiset_description', '')

        for citing in result.get('citing_papers', []):
            citing_doi = citing.get('doi')
            cited_doi = citing.get('cited_paper_doi')

            if not citing_doi or not cited_doi:
                failed_pairs.append({
                    'citing_doi': citing_doi or '',
                    'cited_doi': cited_doi or '',
                    'dandiset_id': dandiset_id,
                    'reason': 'missing_doi',
                })
                continue

            if 'text_cached' in citing and not citing['text_cached']:
                stage2_error = citing.get('text_error', 'unknown')
                failed_pairs.append({
                    'citing_doi': citing_doi,
                    'cited_doi': cited_doi,
                    'dandiset_id': dandiset_id,
                    'reason': f'stage2_fetch_failed: {stage2_error}',
                })
                continue

            cache_file = cache_dir / f"{citing_doi.replace('/', '_')}.json"
            if not cache_file.exists():
                if citing.get('text_cached'):
                    reason = 'cache_file_missing_after_fetch'
                else:
                    reason = 'cache_file_missing_no_stage2_record'
                failed_pairs.append({
                    'citing_doi': citing_doi,
                    'cited_doi': cited_doi,
                    'dandiset_id': dandiset_id,
                    'reason': reason,
                })
                continue

            citation_pairs.append({
                'citing_doi': citing_doi,
                'citing_title': citing.get('title', ''),
                'citing_journal': citing.get('journal', ''),
                'citing_date': citing.get('publication_date', ''),
                'cited_doi': cited_doi,
                'alt_cited_doi': alt_doi_map.get(cited_doi),
                'dandiset_id': dandiset_id,
                'dandiset_name': dandiset_name,
                'dandiset_description': dandiset_description,
                'cache_file': cache_file,
            })

    if max_papers:
        citation_pairs = citation_pairs[:max_papers]

    pair_records = []
    stats = {
        'input_pairs': len(citation_pairs) + len(failed_pairs),
        'pre_extraction_failures': len(failed_pairs),
        'attempted_extractions': len(citation_pairs),
        'with_citations': 0,
        'no_citations_found': 0,
        'low_quality_text': 0,
        'extraction_exceptions': 0,
    }

    pbar = tqdm(citation_pairs, desc="Extracting citation contexts", disable=not show_progress)

    for pair in pbar:
        pbar.set_postfix({'doi': pair['citing_doi'][:30]})

        try:
            result = find_citation_in_cached_paper(
                pair['cache_file'],
                pair['cited_doi'],
                context_chars=context_chars,
                session=session
            )

            if (result.get('num_citations', 0) == 0 and not result.get('error')
                    and pair.get('alt_cited_doi')):
                alt_result = find_citation_in_cached_paper(
                    pair['cache_file'],
                    pair['alt_cited_doi'],
                    context_chars=context_chars,
                    session=session
                )
                if alt_result.get('num_citations', 0) > 0:
                    result = alt_result

            if result.get('error'):
                if 'Insufficient' in result.get('error', ''):
                    stats['low_quality_text'] += 1
                else:
                    stats['extraction_exceptions'] += 1
                failed_pairs.append({
                    'citing_doi': pair['citing_doi'],
                    'cited_doi': pair['cited_doi'],
                    'dandiset_id': pair['dandiset_id'],
                    'reason': result['error'],
                })
                continue

            if result['num_citations'] == 0:
                stats['no_citations_found'] += 1
            else:
                stats['with_citations'] += 1

            contexts = []
            for citation in result['citations']:
                context_entry = {
                    'method': citation.get('method', ''),
                    'citation_position': citation.get('citation_position', 0),
                    'start': citation.get('start', 0),
                    'end': citation.get('end', 0),
                }
                for optional_field in ('reference_number', 'authors', 'year', 'title'):
                    if optional_field in citation:
                        context_entry[optional_field] = citation[optional_field]
                contexts.append(context_entry)

            pair_records.append({
                'citing_doi': pair['citing_doi'],
                'cited_doi': pair['cited_doi'],
                'dandiset_id': pair['dandiset_id'],
                'dandiset_name': pair['dandiset_name'],
                'dandiset_description': pair['dandiset_description'],
                'citing_title': pair['citing_title'],
                'citing_journal': pair['citing_journal'],
                'citing_date': pair['citing_date'],
                'text_length': result.get('text_length', 0),
                'text_source': result.get('source', ''),
                'main_text_length': result.get('main_text_length', 0),
                'contexts': contexts,
            })

        except Exception as exception:
            stats['extraction_exceptions'] += 1
            failed_pairs.append({
                'citing_doi': pair['citing_doi'],
                'cited_doi': pair['cited_doi'],
                'dandiset_id': pair['dandiset_id'],
                'reason': f'exception: {exception}',
            })
            if show_progress:
                tqdm.write(f"Error processing {pair['citing_doi']}: {exception}")

    return pair_records, failed_pairs, stats


def format_for_llm(occurrence: dict, cache_dir: Path) -> str:
    """
    Format a single citation occurrence for LLM classification.

    `occurrence` is a flattened row produced by `flatten_pair_records`: it carries
    the citing/cited DOIs, the dandiset ID, and the context's start/end offsets
    so the excerpt text can be extracted from cache.
    """
    context_text = get_context_text(
        occurrence['citing_doi'],
        occurrence['start'],
        occurrence['end'],
        cache_dir,
    )

    prompt = f"""Analyze this citation context to determine if it represents DATA REUSE or just a PAPER MENTION.

CITATION CONTEXT:
"{context_text}"

METADATA:
- Cited paper DOI: {occurrence['cited_doi']}
- Associated DANDI dataset: {occurrence['dandiset_id']}

DATA REUSE means the citing paper:
- Downloaded and analyzed data from the DANDI dataset
- Used the dataset for their own analysis/experiments
- Re-analyzed or reproduced results using the data
- Built upon the dataset in a substantive way

PAPER MENTION means the citing paper:
- Only cites the paper for background/context
- Mentions the methodology without using the data
- References the findings without using the underlying data
- Compares their results to the cited paper's findings

Based on the context, classify this as:
1. DATA_REUSE - Clear evidence of using the actual dataset
2. LIKELY_DATA_REUSE - Strong indicators of data use but not definitive
3. UNCLEAR - Cannot determine from context alone
4. LIKELY_PAPER_MENTION - Appears to be citing for context/comparison
5. PAPER_MENTION - Clearly just referencing the paper, not the data

Respond with just the classification and a brief (1-2 sentence) explanation."""

    return prompt


def flatten_pair_records(pair_records: list[dict]) -> list[dict]:
    """Flatten pair_records to one row per detected citation occurrence.

    Used by --format jsonl and --format prompts to preserve their per-occurrence
    output shape while the canonical in-memory representation is per-pair.
    """
    rows = []
    for record in pair_records:
        for index, context in enumerate(record['contexts']):
            row = {
                'citing_doi': record['citing_doi'],
                'cited_doi': record['cited_doi'],
                'dandiset_id': record['dandiset_id'],
                'citation_index': index,
                'total_citations_in_paper': len(record['contexts']),
                'method': context.get('method', ''),
                'citation_position': context.get('citation_position', 0),
                'start': context.get('start', 0),
                'end': context.get('end', 0),
            }
            for optional_field in ('reference_number', 'authors', 'year', 'title'):
                if optional_field in context:
                    row[optional_field] = context[optional_field]
            rows.append(row)
    return rows


def main():
    parser = argparse.ArgumentParser(
        description='Extract citation contexts for LLM classification'
    )
    parser.add_argument(
        '--results-file',
        type=Path,
        default=Path('/Volumes/microsd64/data/dandi_all_results.json'),
        help='Path to dandi_all_results.json'
    )
    parser.add_argument(
        '--cache-dir',
        type=Path,
        default=Path('.paper_cache'),
        help='Directory containing cached paper text files (default: .paper_cache)'
    )
    parser.add_argument(
        '--output',
        '-o',
        type=Path,
        help='Output file path (default: stdout as JSON)'
    )
    parser.add_argument(
        '--context-chars',
        type=int,
        default=500,
        help='Number of characters to extract around each citation'
    )
    parser.add_argument(
        '--max-papers',
        type=int,
        help='Maximum number of papers to process'
    )
    parser.add_argument(
        '--format',
        choices=['json', 'jsonl', 'prompts'],
        default='json',
        help='Output format: json (full), jsonl (one per line), prompts (LLM-ready)'
    )
    parser.add_argument(
        '--quiet',
        action='store_true',
        help='Suppress progress output'
    )

    args = parser.parse_args()

    pair_records, failed_pairs, stats = extract_all_citation_contexts(
        results_file=args.results_file,
        cache_dir=args.cache_dir,
        context_chars=args.context_chars,
        show_progress=not args.quiet,
        max_papers=args.max_papers,
    )

    total_contexts = sum(len(record['contexts']) for record in pair_records)

    print(f"\nExtraction Statistics:", file=sys.stderr)
    print(f"  Input pairs (from datasets.json): {stats['input_pairs']}", file=sys.stderr)
    print(f"    Pre-extraction failures (missing DOI / Stage 2 fetch failed / cache missing): {stats['pre_extraction_failures']}", file=sys.stderr)
    print(f"    Attempted extractions: {stats['attempted_extractions']}", file=sys.stderr)
    print(f"      With citations found: {stats['with_citations']}", file=sys.stderr)
    print(f"      No citations found (text fine, DOI not detected): {stats['no_citations_found']}", file=sys.stderr)
    print(f"      Low quality text (only refs/metadata): {stats['low_quality_text']}", file=sys.stderr)
    print(f"      Extraction exceptions: {stats['extraction_exceptions']}", file=sys.stderr)
    print(f"  Pairs eligible for classification: {len(pair_records)}", file=sys.stderr)
    print(f"  Failed pairs (excluded from classification): {len(failed_pairs)}", file=sys.stderr)
    print(f"  Total contexts extracted: {total_contexts}", file=sys.stderr)

    if args.format == 'json':
        output = {
            'stats': stats,
            'pairs': pair_records,
            'failed_pairs': failed_pairs,
        }
        output_str = json.dumps(output, indent=2)
    elif args.format == 'jsonl':
        rows = flatten_pair_records(pair_records)
        output_str = '\n'.join(json.dumps(row) for row in rows)
    elif args.format == 'prompts':
        rows = flatten_pair_records(pair_records)
        prompts = [format_for_llm(row, args.cache_dir) for row in rows]
        output = {
            'stats': stats,
            'prompts': [
                {
                    'citing_doi': row['citing_doi'],
                    'cited_doi': row['cited_doi'],
                    'dandiset_id': row['dandiset_id'],
                    'prompt': prompt,
                }
                for row, prompt in zip(rows, prompts)
            ]
        }
        output_str = json.dumps(output, indent=2)

    # Write output
    if args.output:
        with open(args.output, 'w') as f:
            f.write(output_str)
        print(f"\nOutput written to {args.output}", file=sys.stderr)
    else:
        print(output_str)


if __name__ == '__main__':
    main()
