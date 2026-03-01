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

from citation_context import (
    find_citation_contexts,
    find_citation_in_cached_paper,
    get_paper_metadata,
)


def get_context_text(
    citing_doi: str,
    context_start: int,
    context_end: int,
    cache_dir: Path = Path('/Volumes/microsd64/data/'),
) -> str:
    """
    Extract context text from a cached paper file on-the-fly.

    Args:
        citing_doi: DOI of the citing paper
        context_start: Start position in the text
        context_end: End position in the text
        cache_dir: Directory containing cached paper files

    Returns:
        The context text string
    """
    cache_file = cache_dir / f"{citing_doi.replace('/', '_')}.json"
    if not cache_file.exists():
        return ""

    with open(cache_file) as f:
        data = json.load(f)

    text = data.get('text', '')
    return text[context_start:context_end]


def extract_all_citation_contexts(
    results_file: Path,
    cache_dir: Path,
    context_chars: int = 500,
    show_progress: bool = True,
    max_papers: Optional[int] = None,
) -> list[dict]:
    """
    Extract citation contexts from all cached papers.

    Args:
        results_file: Path to the dandi_all_results.json file
        cache_dir: Directory containing cached paper text files
        context_chars: Number of characters to extract around each citation
        show_progress: Whether to show progress bar
        max_papers: Maximum number of citing papers to process (None for all)

    Returns:
        List of dicts with citation context information
    """
    with open(results_file) as f:
        results_data = json.load(f)

    # Create a session for API calls
    session = requests.Session()
    session.headers.update({
        'User-Agent': 'CitationContextExtractor/1.0 (mailto:ben.dichter@catalystneuro.com)'
    })

    # Collect all citing paper -> cited paper relationships
    citation_pairs = []
    for result in results_data['results']:
        dandiset_id = result['dandiset_id']
        dandiset_name = result.get('dandiset_name', '')

        for citing in result.get('citing_papers', []):
            citing_doi = citing.get('doi')
            cited_doi = citing.get('cited_paper_doi')

            if not citing_doi or not cited_doi:
                continue

            cache_file = cache_dir / f"{citing_doi.replace('/', '_')}.json"
            if not cache_file.exists():
                continue

            citation_pairs.append({
                'citing_doi': citing_doi,
                'citing_title': citing.get('title', ''),
                'citing_journal': citing.get('journal', ''),
                'citing_date': citing.get('publication_date', ''),
                'cited_doi': cited_doi,
                'dandiset_id': dandiset_id,
                'dandiset_name': dandiset_name,
                'cache_file': cache_file,
            })

    if max_papers:
        citation_pairs = citation_pairs[:max_papers]

    # Extract contexts for each pair
    all_contexts = []
    stats = {
        'total_pairs': len(citation_pairs),
        'successful': 0,
        'no_citations_found': 0,
        'low_quality_text': 0,
        'errors': 0,
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

            if result.get('error'):
                if 'Insufficient' in result.get('error', ''):
                    stats['low_quality_text'] += 1
                else:
                    stats['errors'] += 1
                continue

            if result['num_citations'] == 0:
                stats['no_citations_found'] += 1
                continue

            stats['successful'] += 1

            # Add each citation context as a separate entry
            for i, citation in enumerate(result['citations']):
                context_entry = {
                    'citing_doi': pair['citing_doi'],
                    'cited_doi': pair['cited_doi'],
                    'dandiset_id': pair['dandiset_id'],
                    'citation_index': i,
                    'total_citations_in_paper': result['num_citations'],
                    'detection_method': citation.get('method', ''),
                    # Just store positions - text can be extracted on-the-fly from cached files
                    'citation_position': citation.get('citation_position', 0),
                    'context_start': citation.get('start', 0),
                    'context_end': citation.get('end', 0),
                }

                all_contexts.append(context_entry)

        except Exception as e:
            stats['errors'] += 1
            if show_progress:
                tqdm.write(f"Error processing {pair['citing_doi']}: {e}")

    return all_contexts, stats


def format_for_llm(context: dict, cache_dir: Path = Path('/Volumes/microsd64/data/')) -> str:
    """
    Format a citation context for LLM classification.

    Args:
        context: Citation context dict with position info
        cache_dir: Directory containing cached paper files

    Returns a prompt string that can be sent to an LLM.
    """
    # Extract context text on-the-fly
    context_text = get_context_text(
        context['citing_doi'],
        context['context_start'],
        context['context_end'],
        cache_dir
    )

    prompt = f"""Analyze this citation context to determine if it represents DATA REUSE or just a PAPER MENTION.

CITATION CONTEXT:
"{context_text}"

METADATA:
- Cited paper DOI: {context['cited_doi']}
- Associated DANDI dataset: {context['dandiset_id']}

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
        default=Path('/Volumes/microsd64/data/'),
        help='Directory containing cached paper text files'
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

    # Extract contexts
    contexts, stats = extract_all_citation_contexts(
        results_file=args.results_file,
        cache_dir=args.cache_dir,
        context_chars=args.context_chars,
        show_progress=not args.quiet,
        max_papers=args.max_papers,
    )

    # Print stats
    print(f"\nExtraction Statistics:", file=sys.stderr)
    print(f"  Total citation pairs: {stats['total_pairs']}", file=sys.stderr)
    print(f"  Successful extractions: {stats['successful']}", file=sys.stderr)
    print(f"  No citations found: {stats['no_citations_found']}", file=sys.stderr)
    print(f"  Low quality text: {stats['low_quality_text']}", file=sys.stderr)
    print(f"  Errors: {stats['errors']}", file=sys.stderr)
    print(f"  Total contexts extracted: {len(contexts)}", file=sys.stderr)

    # Format output
    if args.format == 'json':
        output = {
            'stats': stats,
            'contexts': contexts,
        }
        output_str = json.dumps(output, indent=2)
    elif args.format == 'jsonl':
        output_str = '\n'.join(json.dumps(ctx) for ctx in contexts)
    elif args.format == 'prompts':
        prompts = [format_for_llm(ctx, args.cache_dir) for ctx in contexts]
        output = {
            'stats': stats,
            'prompts': [
                {
                    'citing_doi': ctx['citing_doi'],
                    'cited_doi': ctx['cited_doi'],
                    'dandiset_id': ctx['dandiset_id'],
                    'prompt': prompt,
                }
                for ctx, prompt in zip(contexts, prompts)
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
