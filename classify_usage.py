#!/usr/bin/env python3
"""
classify_usage.py - Classify dataset usage type using LLM

This script extracts context around dataset mentions in papers and uses an LLM
to classify whether the usage is:
- PRIMARY: The authors created/shared this dataset
- SECONDARY: The authors used/analyzed this existing dataset
- NEITHER: Not a real reference to using the dataset
- UNKNOWN: Cannot determine from context

Uses OpenRouter API for LLM access.

Usage:
    python classify_usage.py <DOI>
    python classify_usage.py --file dois.txt
    python classify_usage.py --from-cache
    python classify_usage.py --dry-run <DOI>
"""

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Optional

import requests

# Import from find_reuse.py
from find_reuse import ArchiveFinder, ARCHIVE_PATTERNS, CACHE_DIR
from llm_utils import get_api_key, call_openrouter_api, parse_json_response


# Classification categories
CLASSIFICATIONS = ['PRIMARY', 'SECONDARY', 'NEITHER', 'UNKNOWN']

# LLM configuration
# 262,144 context
# NOTE: Free tier has limits: 1000 requests/day (at 20 RPM) after account has > $10 credits
# If hit limits, use xiaomi/mimo-v2-flash for the affordable paid tier ($0.09/M input tokens, $0.29/M output tokens)
DEFAULT_MODEL = 'xiaomi/mimo-v2-flash:free'

# Context window size (in words)
CONTEXT_WORDS = 100

# Rate limiting (seconds between API calls)
# Free tier limit is 20 RPM = 1 request per 3 seconds. In practice, 0.5 seconds works fine.
API_DELAY = 0.5

# Error log file for citation extraction failures
CITATION_ERROR_LOG = Path('citation_extraction_errors.log')


def log_citation_error(doi: str, error_type: str, details: dict) -> None:
    """Log citation extraction errors for investigation."""
    timestamp = time.strftime('%Y-%m-%d %H:%M:%S')
    entry = {
        'timestamp': timestamp,
        'doi': doi,
        'error_type': error_type,
        **details
    }
    with open(CITATION_ERROR_LOG, 'a') as f:
        f.write(json.dumps(entry) + '\n')


## get_api_key is imported from llm_utils


def find_dandi_mentions_with_positions(text: str) -> list[dict]:
    """
    Find all DANDI dataset mentions in text with their character positions.

    Returns list of dicts with:
        - id: dataset ID (e.g., '000130')
        - pattern_type: which pattern matched
        - matched_string: the full matched text
        - start: start character position
        - end: end character position

    Deduplicates overlapping matches, keeping the longest match.
    """
    matches = []

    patterns = ARCHIVE_PATTERNS.get('DANDI Archive', [])
    for pattern, pattern_type in patterns:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            dataset_id = match.group(1)
            start, end = match.span()

            matches.append({
                'id': dataset_id,
                'pattern_type': pattern_type,
                'matched_string': match.group(0),
                'start': start,
                'end': end,
            })

    # Deduplicate overlapping or nearby matches for the same dataset ID
    # Keep the longest match when ranges overlap or are within PROXIMITY_THRESHOLD chars
    # This handles cases where the same DOI appears multiple times in bibliography text
    PROXIMITY_THRESHOLD = 200  # chars - mentions within this distance are merged

    matches.sort(key=lambda m: (m['id'], m['start'], -(m['end'] - m['start'])))

    deduped = []
    for match in matches:
        # Check if this match overlaps or is near any existing match for the same ID
        merged = False
        for existing in deduped:
            if existing['id'] == match['id']:
                # Check if ranges overlap OR are within proximity threshold
                if not (match['end'] < existing['start'] - PROXIMITY_THRESHOLD or
                        match['start'] > existing['end'] + PROXIMITY_THRESHOLD):
                    # Close enough - merge by extending the existing match's range
                    # and collecting matched_string if different
                    existing['start'] = min(existing['start'], match['start'])
                    existing['end'] = max(existing['end'], match['end'])
                    # Track all matched strings as a list
                    if 'matched_strings' not in existing:
                        existing['matched_strings'] = [existing['matched_string']]
                    if match['matched_string'] not in existing['matched_strings']:
                        existing['matched_strings'].append(match['matched_string'])
                    merged = True
                    break
        if not merged:
            deduped.append(match)

    # Sort by position
    deduped.sort(key=lambda m: m['start'])
    return deduped


def extract_word_context(text: str, start: int, end: int, num_words: Optional[int] = None) -> dict:
    """
    Extract context around a match position, using word boundaries.

    Returns dict with:
        - context: the extracted text (no markers - matched_string is stored separately)
        - context_start: character position where context starts
        - context_end: character position where context ends
    """
    if num_words is None:
        num_words = CONTEXT_WORDS

    # Find word boundaries before the match
    before_text = text[:start]
    words_before = before_text.split()

    if len(words_before) > num_words:
        # Find the position where we should start (num_words words back)
        words_to_skip = len(words_before) - num_words
        context_start = 0
        word_count = 0
        for i, char in enumerate(before_text):
            if char.isspace() and i > 0 and not before_text[i-1].isspace():
                word_count += 1
                if word_count == words_to_skip:
                    context_start = i + 1
                    break
    else:
        context_start = 0

    # Find word boundaries after the match
    after_text = text[end:]
    words_after = after_text.split()

    if len(words_after) > num_words:
        # Find the position where we should end (num_words words forward)
        word_count = 0
        context_end_offset = len(after_text)
        in_word = False
        for i, char in enumerate(after_text):
            if not char.isspace():
                if not in_word:
                    in_word = True
            else:
                if in_word:
                    word_count += 1
                    in_word = False
                    if word_count == num_words:
                        context_end_offset = i
                        break
        context_end = end + context_end_offset
    else:
        context_end = len(text)

    # Build context without markers (matched_string is stored separately)
    context = text[context_start:context_end]

    return {
        'context': context.strip(),
        'context_start': context_start,
        'context_end': context_end,
    }


def find_bibliography_start(text: str) -> int:
    """
    Find the character position where the bibliography/references section starts.

    Returns the position of the section header, or len(text) if not found.
    """
    markers = [
        r'\bReferences\b',
        r'\bBibliography\b',
        r'\bLiterature Cited\b',
        r'\bCited Literature\b',
        r'\bReference List\b',
    ]

    last_marker_pos = -1
    for marker in markers:
        matches = list(re.finditer(marker, text, re.IGNORECASE))
        if matches:
            # Use the last occurrence (references are usually at the end)
            last_marker_pos = max(last_marker_pos, matches[-1].start())

    return last_marker_pos if last_marker_pos != -1 else len(text)


def is_in_bibliography_section(text: str, position: int) -> bool:
    """
    Check if a position is within the bibliography/references section.

    Returns True if the position appears to be in the bibliography.
    """
    text_before = text[:position].lower()

    # Common bibliography section markers
    markers = [
        r'\breferences\b',
        r'\bbibliography\b',
        r'\bliterature cited\b',
        r'\bcited literature\b',
        r'\breference list\b',
    ]

    # Find last occurrence of any marker
    last_marker_pos = -1
    for marker in markers:
        matches = list(re.finditer(marker, text_before, re.IGNORECASE))
        if matches:
            last_marker_pos = max(last_marker_pos, matches[-1].end())

    if last_marker_pos == -1:
        return False

    # Check if there's another major section after the marker
    # (like "Supplementary Materials", "Acknowledgments", or [HYPERLINKS])
    text_between = text[last_marker_pos:position]
    other_sections = ['acknowledgments', 'supplementary', 'appendix', 'author contributions', '[hyperlinks]']
    for section in other_sections:
        if section in text_between.lower():
            return False  # We passed the references section

    return True


def extract_bibliography_entry(text: str, position: int, max_chars: int = 500) -> str:
    """
    Extract the bibliography entry containing the given position.

    Looks for the entry boundaries (reference numbers) and returns
    the text of that entry, including the reference number at the start.
    Works with both newline-separated and inline reference formats like:
    "29. Author et al. Title... 30. Next Author..."
    """
    # Search backwards for start of entry (up to max_chars)
    search_start = max(0, position - max_chars)
    text_before = text[search_start:position]

    # Patterns to find the start of a bibliography entry
    # Must work for inline format: "...text 29. Author Name..." or "...text 30. DANDI Archive..."
    # The key is finding "number." or "[number]" followed by text
    entry_start_patterns = [
        r'(?:^|\s)(\d{1,4})\.\s+[A-Z]',        # "29. A" (number, dot, space, capital)
        r'(?:^|\s)\[(\d{1,4})\]\s+[A-Z]',      # "[29] A" (bracketed number, space, capital)
        r'(?:^|\n)(\d{1,4})\.\s',              # Start of line: "29. "
    ]

    # Find the last (closest) reference number before our position
    entry_start = search_start
    best_match_pos = -1

    for pattern in entry_start_patterns:
        for match in re.finditer(pattern, text_before):
            match_pos = match.start()
            if match_pos > best_match_pos:
                best_match_pos = match_pos
                # Start at the digit, not the space before it
                entry_start = search_start + match_pos
                # Skip leading whitespace/newline to get to the number
                while entry_start < position and text[entry_start] in ' \t\n':
                    entry_start += 1

    # Search forward for end of entry (start of next entry)
    search_end = min(len(text), position + max_chars)
    text_after = text[position:search_end]

    # Patterns to find the start of the next entry
    # These need to find "number." patterns that start a new entry
    end_patterns = [
        r'\s(\d{1,4})\.\s+[A-Z]',              # " 30. A" (space, number, dot, space, capital)
        r'\s\[(\d{1,4})\]\s+[A-Z]',            # " [30] A"
        r'\n(\d{1,4})\.\s',                     # newline + "30. "
    ]

    entry_end = search_end
    for pattern in end_patterns:
        match = re.search(pattern, text_after)
        if match:
            # End at the space before the next reference number
            candidate_end = position + match.start()
            if candidate_end < entry_end:
                entry_end = candidate_end

    entry_text = text[entry_start:entry_end].strip()

    # Clean up: remove trailing content that looks like the start of hyperlinks section
    hyperlinks_match = re.search(r'\n?\[HYPERLINKS\]', entry_text)
    if hyperlinks_match:
        entry_text = entry_text[:hyperlinks_match.start()].strip()

    return entry_text


def citation_matches_ref(citation_text: str, ref_num: str) -> bool:
    """
    Check if citation text includes ref_num, handling ranges like [1-5].

    Args:
        citation_text: The matched citation text (e.g., "[48-52]", "[1,2,3]")
        ref_num: The reference number to check for (e.g., "50")

    Returns:
        True if the citation includes the reference number.
    """
    try:
        ref_int = int(ref_num)
    except ValueError:
        return False

    # Remove brackets and parentheses
    inner = citation_text.strip('[]() ')

    # Split by comma
    parts = inner.split(',')
    for part in parts:
        part = part.strip()
        if '-' in part:
            # Range like "3-7"
            try:
                range_parts = part.split('-')
                if len(range_parts) == 2:
                    start, end = int(range_parts[0]), int(range_parts[1])
                    if start <= ref_int <= end:
                        return True
            except ValueError:
                continue
        else:
            # Single number
            try:
                if int(part) == ref_int:
                    return True
            except ValueError:
                continue

    return False


def detect_paper_citation_style(
    sample_text: str,
    api_key: str,
    model: str = DEFAULT_MODEL,
    return_full_interaction: bool = False,
) -> dict:
    """
    Use LLM to determine the citation style used in a paper.

    This should be called ONCE per paper, then the result reused for all
    bibliography entries in that paper.

    Args:
        sample_text: A sample of body text (~5000 chars) to detect citation style
        api_key: OpenRouter API key
        model: Model to use
        return_full_interaction: If True, return dict with 'result', 'prompt', 'raw_response'

    Returns:
        Dict with citation_style ('numbered', 'author-year', 'superscript').
        Or full interaction dict if return_full_interaction=True
    """
    prompt = f"""Analyze this sample of paper text to determine the citation style used.

SAMPLE OF PAPER TEXT:
{sample_text[:5000]}

What citation style is used in this paper?
- numbered: Citations appear as [1], [2], [1,2,3], [1-5], (1), (2), etc.
- superscript: Citations appear as superscript numbers (in plain text may look like "text 1 ." or "word1,2")
- author-year: Citations appear as (Smith et al., 2024) or Smith et al. (2024)

Respond with ONLY a raw JSON object (no markdown, no code blocks, no extra text):
{{"citation_style": "numbered|author-year|superscript"}}"""

    return call_openrouter_api(prompt, api_key, model, return_full_interaction=return_full_interaction)


def extract_reference_number_from_bib_entry(bib_entry: str, dandi_pattern: Optional[str] = None) -> Optional[str]:
    """
    Extract the reference number from a bibliography entry.

    Handles cases where the entry might not start cleanly at the reference number,
    e.g., when text extraction artifacts appear before the number.

    Args:
        bib_entry: Text of the bibliography entry (e.g., "29. Author Name. Title...")
        dandi_pattern: Optional pattern/text to locate within the entry to find the right ref number

    Returns:
        Reference number as string, or None if not found.
    """
    # First try patterns at the start of the entry
    # Use 1-3 digits to avoid matching years (like 2025)
    start_patterns = [
        r'^(\d{1,3})\.\s',              # "29. Author..."
        r'^\[(\d{1,3})\]\s',            # "[29] Author..."
        r'^(\d{1,3})\s+[A-Z]',          # "29 Author..."
    ]

    for pattern in start_patterns:
        match = re.match(pattern, bib_entry.strip())
        if match:
            return match.group(1)

    # Find position of DANDI mention in the bib entry (if provided)
    dandi_pos = len(bib_entry)  # Default to end if not found
    if dandi_pattern:
        dandi_match = re.search(re.escape(dandi_pattern), bib_entry)
        if dandi_match:
            dandi_pos = dandi_match.start()

    # Look for reference number patterns and find the one that precedes the DANDI mention
    # Look for patterns like "49 Clemens A M" or "50 Ramachandran S"
    # Use 1-3 digits to avoid matching years (like 2025)
    anywhere_patterns = [
        r'\b(\d{1,3})\.\s+[A-Z][a-z]+\s+[A-Z]',    # "50. Ramachandran S" (dot format)
        r'\b(\d{1,3})\s+[A-Z][a-z]+\s+[A-Z]',       # "50 Ramachandran S" (space format, common in PMC)
        r'\[(\d{1,3})\]\s+[A-Z]',                    # "[50] Author"
    ]

    best_match = None
    best_pos = -1

    for pattern in anywhere_patterns:
        for match in re.finditer(pattern, bib_entry):
            # Find the match that's closest to (but before) the DANDI position
            match_pos = match.start()
            if match_pos < dandi_pos and match_pos > best_pos:
                best_match = match.group(1)
                best_pos = match_pos

    return best_match


def find_citations_programmatically(
    text: str,
    patterns: dict,
    bib_start: int,
) -> list[tuple[int, int, str]]:
    """
    Search full paper body for citation patterns determined by LLM.

    Args:
        text: Full paper text
        patterns: Dict from llm_get_citation_patterns with citation_style, reference_number, etc.
        bib_start: Character position where bibliography starts

    Returns:
        List of (start, end, matched_text) tuples.
    """
    body_text = text[:bib_start]  # Only search before bibliography
    matches = []

    citation_style = patterns.get('citation_style', 'numbered')

    if citation_style in ('numbered', 'superscript'):
        ref_num = patterns.get('reference_number')
        if ref_num:
            # Ensure ref_num is a string (LLM might return it as int)
            ref_num = str(ref_num)
            # Search for all variations of this reference number
            search_patterns = [
                rf'\[{ref_num}\]',                           # [50]
                rf'\({ref_num}\)',                           # (50)
                rf'\[[\d,\s]*\b{ref_num}\b[\d,\s]*\]',      # [49,50,51]
                rf'\([\d,\s]*\b{ref_num}\b[\d,\s]*\)',      # (49,50,51)
            ]

            # Also search for superscript-style bare numbers
            # When superscript formatting is lost in text extraction, citations appear as:
            # "text) 29 ." or "Archive 29 ." or "word 29,"
            # Always try these patterns since we can't reliably distinguish numbered from superscript
            superscript_patterns = [
                rf'(?<=[)\]a-zA-Z])\s+{ref_num}\s+[.,]',    # "text) 50 ." or "word 50 ,"
                rf'(?<=[)\]a-zA-Z])\s+{ref_num}\s+[A-Z]',   # "text 50 The" (before next sentence)
                rf'(?<=[)\]a-zA-Z])\s+{ref_num}(?=\s|$)',   # "text) 50" at end or before space
            ]
            search_patterns.extend(superscript_patterns)

            # Handle ranges [48-52] where ref_num is included
            range_pattern = r'\[(\d+)-(\d+)\]'
            for match in re.finditer(range_pattern, body_text):
                try:
                    start_ref, end_ref = int(match.group(1)), int(match.group(2))
                    if start_ref <= int(ref_num) <= end_ref:
                        matches.append((match.start(), match.end(), match.group(0)))
                except ValueError:
                    continue

            # Also check parentheses ranges
            paren_range_pattern = r'\((\d+)-(\d+)\)'
            for match in re.finditer(paren_range_pattern, body_text):
                try:
                    start_ref, end_ref = int(match.group(1)), int(match.group(2))
                    if start_ref <= int(ref_num) <= end_ref:
                        matches.append((match.start(), match.end(), match.group(0)))
                except ValueError:
                    continue

            # Search explicit patterns
            for pattern in search_patterns:
                for match in re.finditer(pattern, body_text):
                    # Skip if clearly not a citation (e.g., "Figure 50", "Table 50")
                    context = body_text[max(0, match.start()-30):match.end()+30]
                    if re.search(r'(?:figure|table|fig\.|tab\.)\s*' + ref_num, context, re.I):
                        continue
                    matches.append((match.start(), match.end(), match.group(0)))

    elif citation_style == 'author-year':
        first_author = patterns.get('first_author_lastname')
        year = patterns.get('year')
        if first_author and year:
            # Build author-year search patterns
            author_patterns = [
                rf'\({first_author}\s+et\s+al\.,?\s*{year}\)',      # (Smith et al., 2024)
                rf'{first_author}\s+et\s+al\.\s*\({year}\)',        # Smith et al. (2024)
                rf'\({first_author}\s+and\s+\w+,?\s*{year}\)',      # (Smith and Jones, 2024)
                rf'{first_author}\s+and\s+\w+\s*\({year}\)',        # Smith and Jones (2024)
                rf'\({first_author},?\s*{year}\)',                   # (Smith, 2024) or (Smith 2024)
                rf'{first_author}\s*\({year}\)',                     # Smith (2024)
            ]

            for pattern in author_patterns:
                for match in re.finditer(pattern, body_text, re.IGNORECASE):
                    matches.append((match.start(), match.end(), match.group(0)))

    # Deduplicate overlapping matches
    matches = sorted(set(matches), key=lambda x: x[0])
    return matches


def find_body_citations_for_bib_mention(
    text: str,
    mention: dict,
    citation_style: str,
    bib_start: int,
    doi: str,
    context_words: int = CONTEXT_WORDS,
    verbose: bool = False,
) -> list[dict]:
    """
    Find body text citations for a bibliography entry.

    This uses an already-detected citation style (from detect_paper_citation_style)
    to search for citations of a specific reference in the body text.

    Args:
        text: Full paper text
        mention: Dict with 'id', 'start', 'end', etc. from find_dandi_mentions_with_positions
        citation_style: The paper's citation style ('numbered', 'author-year', 'superscript')
        bib_start: Character position where bibliography starts
        doi: DOI of the paper (for error logging)
        context_words: Number of context words to extract
        verbose: Print progress to stderr

    Returns:
        List of dicts with citation context information
    """
    results = []

    # 1. Extract the bibliography entry
    bib_entry = extract_bibliography_entry(text, mention['start'])

    if verbose:
        print(f"    Bibliography entry: {bib_entry[:100]}...", file=sys.stderr)

    # 2. Extract reference number from the bibliography entry
    # Pass the matched_string (DANDI pattern) to locate the correct reference
    dandi_pattern = mention.get('matched_string')
    if isinstance(dandi_pattern, list):
        dandi_pattern = dandi_pattern[0]  # Use first if multiple
    ref_num = extract_reference_number_from_bib_entry(bib_entry, dandi_pattern)

    if not ref_num:
        log_citation_error(doi, 'no_reference_number', {
            'bib_entry': bib_entry[:300],
            'position': mention['start'],
        })
        return results

    if verbose:
        print(f"    Reference number: {ref_num}", file=sys.stderr)

    # 3. Build patterns dict for find_citations_programmatically
    patterns = {
        'citation_style': citation_style,
        'reference_number': ref_num,
    }

    # 4. Programmatically search body for citations
    citations = find_citations_programmatically(text, patterns, bib_start)

    if verbose:
        print(f"    Found {len(citations)} body citation(s)", file=sys.stderr)

    if not citations:
        log_citation_error(doi, 'no_citations_found', {
            'ref_num': ref_num,
            'citation_style': citation_style,
        })
        return results

    # 5. Extract context around each citation, deduplicating overlapping matches
    # Multiple regex patterns can match the same citation (e.g., " 29 ." and " 29")
    # Group by position and merge overlapping contexts
    groups = []  # list of {'start': int, 'end': int, 'matched_strings': list}
    for start, end, matched_text in citations:
        # Check if this overlaps with an existing group (positions within 10 chars)
        merged = False
        for group in groups:
            if not (end < group['start'] - 10 or start > group['end'] + 10):
                # Overlapping - merge
                group['start'] = min(group['start'], start)
                group['end'] = max(group['end'], end)
                if matched_text not in group['matched_strings']:
                    group['matched_strings'].append(matched_text)
                merged = True
                break
        if not merged:
            groups.append({'start': start, 'end': end, 'matched_strings': [matched_text]})

    # Extract context for each unique position group
    for group in groups:
        context_info = extract_word_context(text, group['start'], group['end'], context_words)
        results.append({
            'dataset_id': mention['id'],
            'pattern_type': 'body_citation',
            'matched_string': group['matched_strings'],
            'context': context_info['context'],
            'source': 'body_citation',
            'citation_style': citation_style,
            'reference_number': ref_num,
            'bib_entry': bib_entry,  # Include full bibliography entry for context
        })

    return results


def build_classification_prompt(dataset_ids: list[str], contexts: list[str]) -> str:
    """Build the prompt for LLM classification of a paper."""
    # Format the contexts with dataset IDs
    excerpts = []
    for i, (dataset_id, context) in enumerate(zip(dataset_ids, contexts), 1):
        excerpts.append(f"Excerpt {i} (Dataset {dataset_id}):\n{context}")

    excerpts_text = "\n\n".join(excerpts)

    return f"""Analyze these excerpts from a scientific paper and classify how the paper uses DANDI datasets.

Dataset IDs mentioned: {', '.join(sorted(set(dataset_ids)))}

{excerpts_text}

Based on ALL the excerpts above, classify the paper's relationship to these DANDI datasets as one of:
- PRIMARY: The authors of THIS PAPER created and shared this dataset (e.g., "we deposited our data", "data are available at", "our dataset", "we recorded", "we acquired")
- SECONDARY: The authors used or analyzed an existing dataset (e.g., "we downloaded data from", "we used the dataset", "data were obtained from", "derived from", "we analyzed data from")
- NEITHER: Not a real reference to using the dataset (e.g., general mention of the archive, methodology description)
- UNKNOWN: Cannot determine from the context provided

Key guidance: Look for language indicating ownership ("our data", "we recorded") vs. usage ("we used", "derived from", "obtained from"). If there are body text citations (excerpts marked as body_citation), prioritize those for classification since they show how the paper actually uses the dataset.

Respond with ONLY a raw JSON object (no markdown, no code blocks, no extra text):
{{"classification": "PRIMARY|SECONDARY|NEITHER|UNKNOWN", "confidence": "high|medium|low", "reasoning": "Brief explanation"}}"""


## call_openrouter_api is imported from llm_utils


def classify_paper_usage(
    dataset_ids: list[str],
    contexts: list[str],
    api_key: str,
    model: Optional[str] = None,
    return_full_interaction: bool = False,
) -> dict:
    """
    Classify a paper's dataset usage using the LLM.

    Args:
        dataset_ids: List of dataset IDs mentioned
        contexts: List of context strings for each mention
        api_key: OpenRouter API key
        model: Model to use
        return_full_interaction: If True, return dict with 'result', 'prompt', 'raw_response'

    Returns:
        Classification result, or full interaction dict if return_full_interaction=True
    """
    prompt = build_classification_prompt(dataset_ids, contexts)
    model = model or DEFAULT_MODEL
    return call_openrouter_api(prompt, api_key, model, return_full_interaction=return_full_interaction)


def classify_paper(
    doi: str,
    finder: ArchiveFinder,
    api_key: str,
    model: Optional[str] = None,
    dry_run: bool = False,
    verbose: bool = False,
    context_words: int = CONTEXT_WORDS,
) -> dict:
    """
    Classify a paper's DANDI dataset usage.

    Returns dict with DOI, classification, and metadata.
    """
    # Get paper text
    text, source, from_cache = finder.get_paper_text(doi)

    if not text:
        return {
            'doi': doi,
            'error': 'Could not retrieve paper text',
            'classification': None,
        }

    # Find all DANDI mentions with positions
    mentions = find_dandi_mentions_with_positions(text)

    if not mentions:
        return {
            'doi': doi,
            'source': source,
            'text_length': len(text),
            'dataset_ids': [],
            'classification': None,
            'confidence': None,
            'reasoning': 'No DANDI mentions found',
        }

    # Get unique dataset IDs, excluding placeholder IDs like "123456"
    PLACEHOLDER_IDS = {'123456'}  # Known placeholder IDs that aren't real datasets
    dataset_ids = sorted(set(m['id'] for m in mentions if m['id'] not in PLACEHOLDER_IDS))

    if verbose:
        print(f"Found {len(mentions)} DANDI mention(s) for {len(dataset_ids)} dataset(s) in {doi}", file=sys.stderr)

    # Check if any mentions are in the bibliography section
    # If so, detect citation style ONCE for the whole paper
    bib_start = find_bibliography_start(text)
    has_bib_mentions = any(is_in_bibliography_section(text, m['start']) for m in mentions)
    citation_style = None
    all_llm_interactions = []  # Collect all LLM calls

    if has_bib_mentions and api_key:
        # Detect citation style once for the paper
        sample_text = text[:min(5000, bib_start)]
        try:
            llm_response = detect_paper_citation_style(
                sample_text, api_key, model or DEFAULT_MODEL, return_full_interaction=True
            )
            style_result = llm_response['result']
            citation_style = style_result.get('citation_style')

            all_llm_interactions.append({
                'type': 'citation_style_detection',
                'prompt': llm_response['prompt'],
                'raw_response': llm_response['raw_response'],
            })

            if verbose:
                print(f"  Detected citation style: {citation_style}", file=sys.stderr)

            if citation_style not in ('numbered', 'author-year', 'superscript'):
                log_citation_error(doi, 'llm_style_detection_failed', {
                    'llm_response': style_result,
                })
                citation_style = None
        except Exception as e:
            log_citation_error(doi, 'api_error', {
                'error': str(e),
                'endpoint': 'citation_style_detection',
            })

    # Extract context for each mention
    contexts = []
    mention_details = []
    for mention in mentions:
        # Always extract the direct context around the mention
        context_info = extract_word_context(text, mention['start'], mention['end'], context_words)
        contexts.append(context_info['context'])
        # Use matched_strings (list) if available from deduplication, else single matched_string
        matched = mention.get('matched_strings', [mention['matched_string']])

        # Check if this mention is in the bibliography section
        in_bibliography = is_in_bibliography_section(text, mention['start'])

        # Check if this mention is in the [HYPERLINKS] section (appended URLs from XML)
        hyperlinks_marker = '\n\n[HYPERLINKS]\n'
        hyperlinks_start = text.find(hyperlinks_marker)
        in_hyperlinks = hyperlinks_start != -1 and mention['start'] >= hyperlinks_start

        mention_detail = {
            'dataset_id': mention['id'],
            'pattern_type': mention['pattern_type'],
            'matched_string': matched if len(matched) > 1 else matched[0],
            'context': context_info['context'],
            'in_bibliography': in_bibliography,
            'in_hyperlinks': in_hyperlinks,
        }
        mention_details.append(mention_detail)

        # If in bibliography, try to find body text citations for better context
        if in_bibliography and citation_style:
            if verbose:
                print(f"  Mention in bibliography, searching for body citations...", file=sys.stderr)

            body_citations = find_body_citations_for_bib_mention(
                text=text,
                mention=mention,
                citation_style=citation_style,
                bib_start=bib_start,
                doi=doi,
                context_words=context_words,
                verbose=verbose,
            )

            # Add body citation contexts
            for body_cite in body_citations:
                contexts.append(body_cite['context'])
                mention_details.append(body_cite)

    # Build result
    result = {
        'doi': doi,
        'source': source,
        'text_length': len(text),
        'dataset_ids': dataset_ids,
        'num_mentions': len(mentions),
        'mentions': mention_details,
    }

    if dry_run:
        result['classification'] = '(dry run - not classified)'
        result['confidence'] = None
        result['reasoning'] = None
        result['classified_at'] = None
        result['llm_interactions'] = None
    else:
        # Call LLM for classification
        try:
            all_dataset_ids = [m['id'] for m in mentions]
            llm_response = classify_paper_usage(
                all_dataset_ids,
                contexts,
                api_key,
                model,
                return_full_interaction=True,
            )

            # Extract the classification result
            llm_result = llm_response['result']
            result['classification'] = llm_result.get('classification')
            result['confidence'] = llm_result.get('confidence')
            result['reasoning'] = llm_result.get('reasoning')

            # Add the classification call to all_llm_interactions
            all_llm_interactions.append({
                'type': 'classification',
                'prompt': llm_response['prompt'],
                'raw_response': llm_response['raw_response'],
            })

            # Store timestamp and all LLM interactions
            result['classified_at'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
            result['llm_interactions'] = all_llm_interactions

        except Exception as e:
            result['classification'] = 'ERROR'
            result['confidence'] = None
            result['reasoning'] = str(e)
            result['classified_at'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
            result['llm_interactions'] = all_llm_interactions if all_llm_interactions else None

    if verbose:
        print(f"  Classification: {result.get('classification')}", file=sys.stderr)

    return result


def get_cached_dois_with_dandi() -> list[str]:
    """Get list of DOIs from cache that have DANDI mentions."""
    if not CACHE_DIR.exists():
        return []

    dois_with_dandi = []
    for cache_file in CACHE_DIR.glob('*.json'):
        try:
            with open(cache_file) as f:
                data = json.load(f)

            text = data.get('text', '')
            if text:
                mentions = find_dandi_mentions_with_positions(text)
                if mentions:
                    dois_with_dandi.append(data.get('doi', cache_file.stem.replace('_', '/')))
        except (json.JSONDecodeError, KeyError):
            continue

    return dois_with_dandi


def main():
    parser = argparse.ArgumentParser(
        description='Classify dataset usage type using LLM',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Classify a single paper
    python classify_usage.py 10.1038/s41593-024-01783-4

    # Classify papers from a file
    python classify_usage.py --file dois.txt

    # Process all cached papers with DANDI mentions
    python classify_usage.py --from-cache

    # Dry run to see contexts without calling LLM
    python classify_usage.py --dry-run 10.1038/s41593-024-01783-4

    # Use a different model
    python classify_usage.py --model anthropic/claude-3.5-haiku 10.1038/s41593-024-01783-4
        """
    )

    parser.add_argument('doi', nargs='?', help='DOI to analyze')
    parser.add_argument('--file', '-f', help='File containing DOIs (one per line)')
    parser.add_argument('--from-cache', action='store_true',
                        help='Process all cached papers with DANDI mentions')
    parser.add_argument('--output', '-o', help='Output file (default: stdout)')
    parser.add_argument('--model', help=f'Model to use (default: {DEFAULT_MODEL})')
    parser.add_argument('--dry-run', action='store_true',
                        help='Show contexts without calling LLM')
    parser.add_argument('--verbose', '-v', action='store_true',
                        help='Print progress to stderr')
    parser.add_argument('--context-words', type=int, default=CONTEXT_WORDS,
                        help=f'Number of words of context (default: {CONTEXT_WORDS})')

    args = parser.parse_args()

    # Determine input DOIs
    dois = []
    if args.from_cache:
        dois = get_cached_dois_with_dandi()
        if args.verbose:
            print(f"Found {len(dois)} cached papers with DANDI mentions", file=sys.stderr)
    elif args.file:
        with open(args.file) as f:
            dois = [line.strip() for line in f if line.strip() and not line.startswith('#')]
    elif args.doi:
        dois = [args.doi]
    else:
        parser.error('Must provide DOI, --file, or --from-cache')

    # Get API key
    if args.dry_run:
        api_key = ''
    else:
        try:
            api_key = get_api_key()
        except ValueError as e:
            parser.error(str(e))

    if args.verbose and not args.dry_run:
        print(f"Using OpenRouter API with model: {args.model or DEFAULT_MODEL}", file=sys.stderr)

    # Initialize finder
    finder = ArchiveFinder(verbose=args.verbose, use_cache=True)

    # Process DOIs
    results = []

    # For iterative output, open the file and write as we go
    output_file = None
    if args.output and len(dois) > 1:
        output_file = open(args.output, 'w')
        output_file.write('[\n')  # Start JSON array

    try:
        for i, doi in enumerate(dois):
            if args.verbose:
                print(f"\nProcessing: {doi} ({i+1}/{len(dois)})", file=sys.stderr)

            result = classify_paper(
                doi,
                finder,
                api_key,
                args.model,
                dry_run=args.dry_run,
                verbose=args.verbose,
                context_words=args.context_words,
            )
            results.append(result)

            # Write iteratively if output file specified and multiple DOIs
            if output_file:
                if i > 0:
                    output_file.write(',\n')
                output_file.write(json.dumps(result, indent=2))
                output_file.flush()  # Ensure it's written to disk
    finally:
        if output_file:
            output_file.write('\n]')  # Close JSON array
            output_file.close()
            if args.verbose:
                print(f"\nResults written to {args.output}", file=sys.stderr)

    # Output results (for single DOI or stdout)
    if not args.output:
        output = json.dumps(results if len(results) > 1 else results[0], indent=2)
        print(output)
    elif len(dois) == 1:
        # Single DOI to file - write normally
        with open(args.output, 'w') as f:
            f.write(json.dumps(results[0], indent=2))
        if args.verbose:
            print(f"\nResults written to {args.output}", file=sys.stderr)


if __name__ == '__main__':
    main()
