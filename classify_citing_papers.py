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

Input:
    citation_contexts.json produced by extract_citation_contexts.py. Each pair record
    in that file already carries pre-extracted citation contexts and text metadata,
    so this script does no text parsing of its own. Pairs that failed text access in
    the extraction stage are absent from `pairs` (they live in `failed_pairs`) and
    therefore cannot propagate into classification.

Usage:
    python classify_citing_papers.py --contexts-file output/citation_contexts.json
    python classify_citing_papers.py --contexts-file output/citation_contexts.json --max-papers 10
    python classify_citing_papers.py --contexts-file output/citation_contexts.json --model google/gemini-3-flash-preview
"""

import argparse
import concurrent.futures
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from tqdm import tqdm

from citation_context import get_context_text, get_paper_text_prefix
from llm_utils import get_api_key, call_openrouter_api, parse_json_response, DEFAULT_MODEL

# Classification cache
CLASSIFICATION_CACHE_DIR = Path(__file__).parent / '.classification_cache'

# Valid classification values
VALID_CLASSIFICATIONS = {'REUSE', 'MENTION', 'NEITHER'}

# DOI patterns for non-research documents (peer review, editorial comments, etc.)
NON_RESEARCH_DOI_PATTERNS = [
    re.compile(r'\.sa\d+'),       # eLife sub-articles (peer review)
    re.compile(r'/peer-review/'),  # explicit peer review paths
]


def build_classification_prompt(
    contexts: list[dict],
    dandiset_id: str,
    dandiset_name: str,
    cited_doi: str,
    citing_doi: str,
    fallback_text: Optional[str] = None,
    dandiset_description: str = '',
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
        dandiset_description: Optional dataset description from the archive

    Returns:
        Prompt string for the LLM
    """
    prompt = f"""You are classifying how a scientific paper relates to a DANDI neuroscience dataset. The citing paper references the primary paper associated with the dataset. Your job is to determine whether the citing paper actually REUSED THE DATA from that dataset, or just cited the paper as prior work.

DANDI DATASET: {dandiset_id} - {dandiset_name}
PRIMARY PAPER DOI (the paper that originally published the dataset): {cited_doi}
CITING PAPER DOI (the paper we are classifying): {citing_doi}

"""

    if dandiset_description:
        truncated = dandiset_description[:2000]
        ellipsis = '…' if len(dandiset_description) > 2000 else ''
        prompt += f"DATASET DESCRIPTION (from the archive): {truncated}{ellipsis}\n\n"

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

    prompt += """Based on the text above, make THREE separate decisions:

DECISION 1 - Did this paper reuse the DATA from the dataset?
- REUSE: The citing paper downloaded/accessed and reused the actual DATA (recordings, images, behavioral traces, etc.) for their own analysis. Look for phrases like "we used data from", "we downloaded", "we analyzed recordings from", "data were obtained from", or explicit mentions of using the DANDI archive.
- MENTION: The paper cites the primary paper as prior work, background, or for comparison, but does NOT actually use the underlying data. The citation is for referencing findings, methods, or context.
- NEITHER: The citation does not fit REUSE or MENTION. Use NEITHER for one of the following cases, and state which subtype in your reasoning:
    * low_quality_text — the provided text is unusable for classification (e.g., only references/metadata, severely truncated, or otherwise garbage). This is a pipeline backstop; the extraction step should have caught it, but call it out if you see it.
    * parsing_mistake — the matched citation is not actually a citation of the cited paper (e.g., a stray DOI in a reference list footer, a coincidental string match).
    * ambiguous — the text is real but too unclear to determine any relationship between the citing paper and the dataset.

IMPORTANT RULES:
1. Using or adapting analytical SOFTWARE, code, algorithms, or methods from the primary paper is NOT data reuse. Only classify as REUSE if the actual recorded data (e.g., neural recordings, imaging data, behavioral data) was downloaded and reanalyzed. If a paper only uses software tools, analysis pipelines, or methodological approaches from the primary paper, that is MENTION.
2. The citing paper must have reused data from THIS SPECIFIC dataset (described by the primary paper DOI above). If the paper reuses data from a DIFFERENT dataset but merely cites this primary paper for context, methodology, or as a reference, that is MENTION, not REUSE. Be precise about which dataset's data was actually used.
3. A citation used only as background or to establish a fact (e.g., "X cells are found in region Y [ref]") is MENTION, not REUSE — even if the topic closely matches the dataset name. Pay attention to HOW the reference is cited: parenthetical citations in lists like (31, 32, 33) supporting a general statement are almost never data reuse.
4. If the paper ran SIMULATIONS inspired by or parameterized from a different data source, and only cites the primary paper as background context, that is MENTION. Simulating a cell type described in the primary paper is not the same as reusing the primary paper's recorded data.
5. If the citing paper describes collecting its OWN new recordings or data and merely cites the primary paper for comparison, context, or analytical approach, that is MENTION, not REUSE. The citing paper must have downloaded and used data FROM the primary paper's dataset, not just performed a similar experiment.
6. Using a figure, numerical value, or summary statistic from a published paper purely for comparison is NOT data reuse. However, downloading open data to use as input for simulations or models IS data reuse.
7. If only an abstract or very short text is available, do NOT classify as REUSE unless the abstract explicitly states data was downloaded or reused from this specific dataset. Prefer MENTION when evidence is ambiguous. If the provided text appears to be a fragment, abstract only, or is missing methods and data sections, note this in your reasoning and use a confidence score ≤5 rather than high-confidence NEITHER.
8. If the citing paper references the primary paper only for its experimental protocol, methodology, electrode placement procedure, surgical technique, or analytical pipeline — and does not explicitly state that data files or recordings were downloaded — that is MENTION, not REUSE. The citation must be about the DATA, not the METHOD.
9. If you classify as REUSE, your reasoning must reference a specific phrase or sentence from the provided text that supports this conclusion. Do not describe dataset-specific details (unit counts, electrode counts, epoch counts, subject numbers, recording durations) that do not appear in the provided text. If no such grounding is present, do not fabricate it.
10. Shared authorship between the citing paper and the primary paper is NOT evidence of data reuse on its own. Determine DECISION 1 (classification) purely from the text evidence, independently of DECISION 2 (same_lab). A same-lab paper may simply be citing its own prior work as background context.
11. If you use a numerical signature (unit count, electrode count, subject count, epoch count, recording duration, etc.) from the citing paper as evidence of data reuse, the EXACT same number must also explicitly appear in the DATASET DESCRIPTION block above (or be stated in the citation context excerpts as a quoted property of the cited paper). Do NOT assume that a number reported in the citing paper refers to the cited dataset just because it sounds plausible — citing papers commonly report counts from their own analyses (subsets, exclusions, re-segmentations) that do not equal the dataset's published counts. If the same number does not appear on both sides, a numerical-match argument for REUSE is unsubstantiated and the classification should be MENTION (or NEITHER:ambiguous if otherwise unclear).

DECISION 2 - If REUSE, is it the same lab?
Check whether the citing paper's author list shares names with the primary paper's authors. If the same group reused or extended their own data, same_lab is true. If a different group used it, same_lab is false.

DECISION 3 - If REUSE, which data archive was used to access the data?
Look for explicit mentions of how the data was accessed. Common archives include:
- DANDI Archive (dandiarchive.org, DANDI)
- CRCNS (crcns.org, Collaborative Research in Computational Neuroscience)
- Dryad (datadryad.org)
- Figshare (figshare.com)
- EBRAINS (ebrains.eu, Human Brain Project)
- OpenNeuro (openneuro.org)
- Zenodo (zenodo.org)
- OSF (osf.io, Open Science Framework)
- GIN (gin.g-node.org, G-Node)
- Allen Institute (brain-map.org, Allen Brain Observatory)
- IBL (International Brain Laboratory data portal)
- INDI (International Neuroimaging Data-sharing Initiative)
- GitHub / institutional repository / lab website
If the text explicitly states where the data was obtained from, set source_archive to that name. If the text does not clearly indicate which archive or repository was used, set source_archive to "unclear".

Respond ONLY with a JSON object (no markdown, no explanation outside the JSON):
{"classification": "REUSE|MENTION|NEITHER", "confidence": <1-10>, "same_lab": <true|false>, "same_lab_confidence": <1-10>, "source_archive": "<archive name or unclear>", "reasoning": "Brief 1-2 sentence explanation"}

Only include same_lab, same_lab_confidence, and source_archive when classification is REUSE.
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
    pair_record: dict,
    cache_dir: Path,
    api_key: str,
    model: str,
    use_cache: bool = True,
) -> dict:
    """Classify a single citing paper using a pair_record from Stage 3.

    `pair_record` is one entry from citation_contexts.json's `pairs` list. It already
    contains the citing-paper metadata, text length/source, and pre-extracted
    contexts. This function only resolves context excerpt text from cache and calls
    the LLM — it never re-parses the paper.
    """
    citing_doi = pair_record['citing_doi']
    cited_doi = pair_record['cited_doi']
    dandiset_id = pair_record['dandiset_id']
    dandiset_name = pair_record.get('dandiset_name', '')
    dandiset_description = pair_record.get('dandiset_description', '')
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

    # Pre-filter non-research DOIs (peer review documents, etc.)
    for pattern in NON_RESEARCH_DOI_PATTERNS:
        if pattern.search(citing_doi):
            result = {
                'citing_doi': citing_doi,
                'cited_doi': cited_doi,
                'dandiset_id': dandiset_id,
                'dandiset_name': dandiset_name,
                'from_cache': False,
                'classification': 'NEITHER',
                'confidence': 10,
                'reasoning': 'DOI pattern indicates peer review or non-research document',
                'error': 'non_research_doi',
            }
            if use_cache:
                cache_classification(citing_doi, cited_doi, result)
            return result

    result = {
        'citing_doi': citing_doi,
        'cited_doi': cited_doi,
        'dandiset_id': dandiset_id,
        'dandiset_name': dandiset_name,
        'from_cache': False,
        'text_length': pair_record.get('text_length', 0),
        'text_source': pair_record.get('text_source', ''),
    }

    raw_contexts = pair_record.get('contexts', [])
    contexts_with_text = []
    for raw in raw_contexts:
        excerpt = get_context_text(citing_doi, raw['start'], raw['end'], cache_dir)
        contexts_with_text.append({**raw, 'context': excerpt})

    result['num_contexts'] = len(contexts_with_text)

    fallback_text = None
    if not contexts_with_text:
        fallback_text = get_paper_text_prefix(citing_doi, cache_dir, max_chars=8000)

    prompt = build_classification_prompt(
        contexts=contexts_with_text,
        dandiset_id=dandiset_id,
        dandiset_name=dandiset_name,
        cited_doi=cited_doi,
        citing_doi=citing_doi,
        fallback_text=fallback_text,
        dandiset_description=dandiset_description,
    )

    response = call_openrouter_api(
        prompt, api_key, model,
        return_raw=True, max_tokens=300, timeout=60,
    )

    classification = parse_json_response(
        response,
        valid_classifications=VALID_CLASSIFICATIONS,
        default_classification='NEITHER',
    )
    result.update(classification)

    if result.get('classification') == 'REUSE' and result.get('text_length', 0) < 15000:
        result['confidence'] = min(result.get('confidence', 1), 5)
        result['low_text_warning'] = True

    result['high_confidence_reuse'] = (
        result.get('classification') == 'REUSE'
        and result.get('confidence', 0) >= 7
    )

    if contexts_with_text:
        result['context_excerpts'] = []
        for context in contexts_with_text[:5]:
            excerpt_data = {
                'text': context['context'],
                'method': context.get('method', ''),
            }
            if 'citation_position' in context and 'start' in context:
                excerpt_data['highlight_offset'] = context['citation_position'] - context['start']
            for optional_field in ('reference_number', 'authors', 'year'):
                if context.get(optional_field):
                    excerpt_data[optional_field] = context[optional_field]
            result['context_excerpts'].append(excerpt_data)

    if use_cache:
        cache_classification(citing_doi, cited_doi, result)

    return result


def load_pair_records_from_contexts(contexts_file: Path) -> list[dict]:
    """Load pair_records from citation_contexts.json (Stage 3 output).

    Each record carries pre-extracted contexts plus citing-paper metadata.
    Pairs that failed text access during extraction are absent — they live in
    `failed_pairs` in the same file and are intentionally not surfaced here.
    """
    with open(contexts_file) as f:
        data = json.load(f)
    return data.get('pairs', [])


def classify_all_papers(
    pair_records: list[dict],
    cache_dir: Path,
    api_key: str,
    model: str = DEFAULT_MODEL,
    max_papers: Optional[int] = None,
    use_cache: bool = True,
    show_progress: bool = True,
    workers: int = 10,
) -> dict:
    """Classify all pair_records produced by extract_citation_contexts.py."""
    if max_papers:
        pair_records = pair_records[:max_papers]

    classifications = []
    stats = {
        'total_pairs': len(pair_records),
        'from_cache': 0,
        'api_calls': 0,
        'errors': 0,
        'by_classification': {},
    }

    def _classify_one(record):
        result = classify_single_paper(
            pair_record=record,
            cache_dir=cache_dir,
            api_key=api_key,
            model=model,
            use_cache=use_cache,
        )
        result['citing_title'] = record.get('citing_title', '')
        result['citing_journal'] = record.get('citing_journal', '')
        result['citing_date'] = record.get('citing_date', '')
        return result

    print(f"Classifying {len(pair_records)} papers with {workers} workers...", file=sys.stderr)

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(_classify_one, record): record for record in pair_records}
        pbar = tqdm(total=len(pair_records), desc="Classifying papers", disable=not show_progress)

        for future in concurrent.futures.as_completed(futures):
            try:
                result = future.result()
            except Exception as exception:
                record = futures[future]
                result = {
                    'citing_doi': record['citing_doi'],
                    'cited_doi': record['cited_doi'],
                    'dandiset_id': record['dandiset_id'],
                    'classification': 'NEITHER',
                    'confidence': 1,
                    'reasoning': f'Worker error: {exception}',
                    'error': 'worker_exception',
                    'citing_title': record.get('citing_title', ''),
                    'citing_journal': record.get('citing_journal', ''),
                    'citing_date': record.get('citing_date', ''),
                }

            classifications.append(result)

            classification = result.get('classification', 'NEITHER')
            stats['by_classification'][classification] = stats['by_classification'].get(classification, 0) + 1

            if result.get('from_cache'):
                stats['from_cache'] += 1
            elif result.get('error'):
                stats['errors'] += 1
            else:
                stats['api_calls'] += 1

            pbar.update(1)
            pbar.set_postfix({'cache': stats['from_cache'], 'api': stats['api_calls']})

        pbar.close()

    output = {
        'metadata': {
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'model': model,
            'total_pairs': stats['total_pairs'],
            'api_calls': stats['api_calls'],
            'from_cache': stats['from_cache'],
            'errors': stats['errors'],
            'classification_counts': stats['by_classification'],
            'workers': workers,
        },
        'classifications': classifications,
    }

    return output


def main():
    parser = argparse.ArgumentParser(
        description='Classify citing papers as data reuse or paper mention using LLM'
    )

    parser.add_argument(
        '--contexts-file',
        type=Path,
        required=True,
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
        '--workers',
        type=int,
        default=10,
        help='Number of parallel workers for API calls (default: 10)'
    )
    parser.add_argument(
        '--quiet',
        action='store_true',
        help='Suppress progress output'
    )

    args = parser.parse_args()

    try:
        api_key = get_api_key()
    except ValueError as exception:
        print(f"Error: {exception}", file=sys.stderr)
        sys.exit(1)

    if args.clear_cache and CLASSIFICATION_CACHE_DIR.exists():
        import shutil
        shutil.rmtree(CLASSIFICATION_CACHE_DIR)
        print("Classification cache cleared", file=sys.stderr)

    pair_records = load_pair_records_from_contexts(args.contexts_file)
    if not pair_records:
        print(
            f"No pair_records found in {args.contexts_file}. "
            "Did you run extract_citation_contexts.py first?",
            file=sys.stderr,
        )
        sys.exit(1)

    print(f"Loaded {len(pair_records)} pair_records from {args.contexts_file}", file=sys.stderr)

    output = classify_all_papers(
        pair_records=pair_records,
        cache_dir=args.cache_dir,
        api_key=api_key,
        model=args.model,
        max_papers=args.max_papers,
        use_cache=not args.no_cache,
        show_progress=not args.quiet,
        workers=args.workers,
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
