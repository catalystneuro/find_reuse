#!/usr/bin/env python3
"""
citation_context.py - Extract context around citations of a specific paper

This module finds where a cited paper is referenced in a citing paper's text
and extracts the surrounding context. Handles multiple citation formats:
- Author-year: (Smith et al., 2020), Smith et al. (2020)
- Numbered: [1], [1,2], [1-3]
- Direct DOI mentions
- Title mentions
"""

import json
import re
from pathlib import Path
from typing import Optional

import requests


def get_paper_metadata(doi: str, session: Optional[requests.Session] = None) -> Optional[dict]:
    """
    Get author names, publication year, and title for a DOI from CrossRef.
    """
    if session is None:
        session = requests.Session()
        session.headers.update({
            'User-Agent': 'CitationContext/1.0 (mailto:ben.dichter@catalystneuro.com)'
        })

    url = f"https://api.crossref.org/works/{doi}"

    try:
        resp = session.get(url, timeout=30)
        if resp.status_code == 200:
            data = resp.json()
            message = data.get('message', {})

            # Extract authors (last names)
            authors = []
            for author in message.get('author', []):
                family = author.get('family', '')
                if family:
                    authors.append(family)

            # Extract year
            year = None
            for date_field in ['published-print', 'published-online', 'published', 'created']:
                date_parts = message.get(date_field, {}).get('date-parts', [[]])
                if date_parts and date_parts[0]:
                    year = date_parts[0][0]
                    break

            # Extract title
            title = ''
            if message.get('title'):
                title = message['title'][0]

            return {
                'authors': authors,
                'year': year,
                'title': title,
                'doi': doi,
            }
    except Exception as e:
        print(f"Error fetching metadata for {doi}: {e}")

    return None


def find_doi_in_text(text: str, doi: str) -> list[int]:
    """Find all positions where a DOI appears in text."""
    positions = []
    # Escape special regex chars in DOI
    doi_escaped = re.escape(doi)
    for m in re.finditer(doi_escaped, text, re.IGNORECASE):
        positions.append(m.start())
    return positions


def find_reference_section_start(text: str) -> int:
    """
    Find where the reference section begins by looking for DOI-dense regions.

    Returns the position where references start, or len(text) if not found.
    """
    doi_positions = [m.start() for m in re.finditer(r'10\.\d{4}/', text)]

    if len(doi_positions) < 4:
        return len(text)

    # Find where DOIs become dense (4 DOIs within 1000 chars and stays dense)
    for i in range(len(doi_positions) - 3):
        span = doi_positions[i + 3] - doi_positions[i]
        if span < 1000:
            # Verify this density continues (not just a methods section with some DOIs)
            if i + 10 < len(doi_positions):
                next_span = doi_positions[i + 10] - doi_positions[i]
                if next_span < 5000:
                    return doi_positions[i]
            else:
                # Near end of text, probably references
                return doi_positions[i]

    return len(text)


def find_reference_number_for_doi(text: str, doi: str) -> Optional[int]:
    """
    Find the reference number associated with a DOI in the reference section.

    Handles multiple reference formats:
    1. Numbered references: "42. Author Name... doi:10.1234/..."
    2. Europe PMC format: DOIs on separate lines (counts position)

    Returns None if DOI not found in references.
    """
    doi_escaped = re.escape(doi)

    # Find the DOI position
    doi_match = re.search(doi_escaped, text, re.IGNORECASE)
    if not doi_match:
        return None

    doi_pos = doi_match.start()

    # Look backwards for an explicit reference number (up to 500 chars before DOI)
    search_start = max(0, doi_pos - 500)
    preceding_text = text[search_start:doi_pos]

    # Pattern 1: Explicit numbered reference at start of line
    # e.g., "42. Author" or "42 Author" but NOT "10.1016/..."
    ref_pattern = r'(?:^|\n)\s*(\d{1,3})(?:\.(?!\d)|[\s\)])(?![\d/])'
    ref_numbers = list(re.finditer(ref_pattern, preceding_text))

    # Filter out DOI prefixes
    valid_refs = []
    for m in ref_numbers:
        num = int(m.group(1))
        match_end = m.end()
        remaining = preceding_text[match_end:match_end + 20] if match_end < len(preceding_text) else ""
        if num == 10 and re.match(r'\d{4}/', remaining):
            continue
        valid_refs.append(num)

    if valid_refs:
        return valid_refs[-1]

    # Pattern 2: Europe PMC format - count DOI position in reference section
    ref_start = find_reference_section_start(text)

    if ref_start < len(text):
        # Get all DOIs in reference section
        ref_section = text[ref_start:]
        ref_dois = list(re.finditer(r'10\.\d{4,}/[^\s]+', ref_section))

        # Find which DOI number matches our target
        for i, m in enumerate(ref_dois):
            if doi.lower() in m.group().lower():
                # Reference numbers are 1-indexed
                return i + 1

    return None


def find_numbered_citations(text: str, ref_number: int) -> list[int]:
    """
    Find all positions where a reference number is cited in the text.

    Handles formats:
    - [42]
    - [41,42,43]
    - [40-45]
    - (42), (15, 16), (17-20) - parenthetical citations
    - superscript-style: word42 or word42,43
    - space-separated: "circuits 5 , 7" (common in Europe PMC)
    """
    positions = []
    ref_str = str(ref_number)

    # Pattern 1: [42] or [41,42] or [40-45]
    bracket_pattern = r'\[([^\]]*)\]'
    for m in re.finditer(bracket_pattern, text):
        bracket_content = m.group(1)
        # Check if our number is in this bracket
        # Handle ranges like 40-45
        if re.search(rf'\b{ref_str}\b', bracket_content):
            positions.append(m.start())
        elif '-' in bracket_content or '–' in bracket_content:
            # Check ranges
            for range_match in re.finditer(r'(\d+)\s*[-–]\s*(\d+)', bracket_content):
                start_num = int(range_match.group(1))
                end_num = int(range_match.group(2))
                if start_num <= ref_number <= end_num:
                    positions.append(m.start())
                    break

    # Pattern 1b: Parenthetical (42), (15, 16), (17-20)
    # Must contain only numbers, commas, dashes, and spaces (no years like 2020)
    paren_cite_pattern = r'\((\d{1,3}(?:\s*[,–-]\s*\d{1,3})*)\)'
    for m in re.finditer(paren_cite_pattern, text):
        paren_content = m.group(1)
        # Skip if it looks like a year (4-digit number)
        if re.search(r'\b\d{4}\b', paren_content):
            continue
        # Check if our number is in this parenthesis
        if re.search(rf'\b{ref_str}\b', paren_content):
            positions.append(m.start())
        elif '-' in paren_content or '–' in paren_content:
            # Check ranges
            for range_match in re.finditer(r'(\d+)\s*[-–]\s*(\d+)', paren_content):
                start_num = int(range_match.group(1))
                end_num = int(range_match.group(2))
                if start_num <= ref_number <= end_num:
                    positions.append(m.start())
                    break

    # Pattern 2: Superscript style - number directly after word (no space)
    # e.g., "reported previously42" or "studies42,43"
    super_pattern = rf'[a-zA-Z]({ref_str})(?:[,\s]|$)'
    for m in re.finditer(super_pattern, text):
        positions.append(m.start())

    # Pattern 2b: Comma-separated superscript style
    # e.g., "cortex105,106" where we want to find 106 in the group
    # Match word followed by comma-separated numbers
    group_super_pattern = r'[a-zA-Z](\d{1,3}(?:,\d{1,3})+)'
    for m in re.finditer(group_super_pattern, text):
        numbers_str = m.group(1)
        # Split by comma and check if our number is in the list
        numbers = [int(n) for n in numbers_str.split(',')]
        if ref_number in numbers:
            positions.append(m.start())

    # Pattern 3: Space-separated superscript style (common in Europe PMC XML)
    # e.g., "circuits 5 , 7" or "dynamics 11 – 15" or "patterns 16 – 20"
    # Look for word followed by space and our reference number
    space_super_pattern = rf'[a-zA-Z]\s+{ref_str}(?:\s*[,–-]\s*\d+)*(?:\s|$|[,.])'
    for m in re.finditer(space_super_pattern, text):
        # Verify this is in a citation context (not just any number)
        # Check if there are other numbers nearby suggesting a citation list
        context = text[max(0, m.start() - 5):min(len(text), m.end() + 20)]
        # Count numbers in context - citations tend to cluster
        numbers_nearby = len(re.findall(r'\b\d{1,3}\b', context))
        if numbers_nearby >= 1:  # At least 1 reference number
            positions.append(m.start())

    # Pattern 5: Citation after closing parenthesis: "text) 62 using..."
    # Common when citations follow identifiers like RRIDs
    paren_super_pattern = rf'\)\s*{ref_str}(?:\s*[,–-]\s*\d+)*(?:\s|$|[,.])'
    for m in re.finditer(paren_super_pattern, text):
        positions.append(m.start())

    # Pattern 4: Check ranges with spaces like "11 – 15" for our number
    # Only match ranges that look like citations (preceded by text, not numbers/units)
    range_pattern = r'(\d{1,3})\s*[–-]\s*(\d{1,3})'
    for m in re.finditer(range_pattern, text):
        start_num = int(m.group(1))
        end_num = int(m.group(2))
        if start_num <= ref_number <= end_num:
            # Make sure this looks like a citation context
            before = text[max(0, m.start() - 30):m.start()]
            after = text[m.end():min(len(text), m.end() + 30)]

            # Skip if it looks like:
            # - DOI: "10." prefix
            # - ORCID: "0000-" pattern
            # - Frequency/units: "Hz", "kHz", "MHz", "Ω", "kΩ"
            # - Version/code: "-2532.", alphanumeric codes
            # - Measurement: numbers with units after
            skip_patterns = [
                r'10\.',  # DOI prefix
                r'0000-',  # ORCID
                r'\d{4}-\d{4}',  # ORCID continuation
                r'[kMG]?Hz',  # Frequency
                r'[kMG]?Ω',  # Impedance
                r'\d+\s*[kMG]?[Ωω]',  # More impedance
                r'[A-Z]\d+x\d+',  # Probe designations
                r'-\d{2,4}\.',  # Version codes
            ]

            should_skip = False
            for pattern in skip_patterns:
                if re.search(pattern, before + m.group() + after, re.IGNORECASE):
                    should_skip = True
                    break

            # Also skip if followed by units
            if re.match(r'\s*[kMG]?[HzΩωms%°]', after, re.IGNORECASE):
                should_skip = True

            # Also skip if preceded by pure numbers (not word endings)
            if re.search(r'\d\s*$', before):
                should_skip = True

            if not should_skip:
                # Additional check: require a letter before the range (word ending)
                if re.search(r'[a-zA-Z]\s*$', before):
                    positions.append(m.start())

    return list(set(positions))  # Remove duplicates


def normalize_author_name(name: str) -> str:
    """Normalize author name for matching - handle accents, etc."""
    import unicodedata
    # Normalize unicode characters (e.g., á -> a)
    normalized = unicodedata.normalize('NFKD', name)
    # Remove combining characters (accents)
    ascii_name = ''.join(c for c in normalized if not unicodedata.combining(c))
    return ascii_name


def find_author_citations(text: str, authors: list[str], year: int, year_tolerance: int = 1) -> list[int]:
    """
    Find all positions where author-year citations appear.

    Handles:
    - (Smith et al., 2020)
    - (Smith and Jones, 2020)
    - Smith et al. (2020)
    - Smith and Jones (2020)
    - (Smith, 2020)
    - Smith and Jones (42) - numbered reference with author name

    Args:
        text: Text to search
        authors: List of author last names
        year: Publication year
        year_tolerance: Also search for years +/- this value (for preprints vs published)
    """
    if not authors or not year:
        return []

    positions = []
    first_author = authors[0]
    # Also try normalized version (without accents)
    first_author_normalized = normalize_author_name(first_author)

    # Search for exact year and adjacent years (preprints may be cited with different year)
    years_to_search = [str(year)]
    for delta in range(1, year_tolerance + 1):
        years_to_search.append(str(year - delta))
        years_to_search.append(str(year + delta))

    # Escape special regex characters
    first_author_esc = re.escape(first_author)
    first_author_norm_esc = re.escape(first_author_normalized)

    # Build patterns based on number of authors
    # Include both original and normalized author names
    author_patterns = [first_author_esc]
    if first_author_normalized != first_author:
        author_patterns.append(first_author_norm_esc)

    patterns = []

    for author_esc in author_patterns:
        for year_str in years_to_search:
            if len(authors) == 1:
                # Single author: Smith, 2020 or Smith (2020)
                patterns.extend([
                    rf'\({author_esc}\s*,?\s*{year_str}[a-z]?\)',
                    rf'{author_esc}\s*\({year_str}[a-z]?\)',
                    rf'{author_esc}\s*,\s*{year_str}[a-z]?',
                ])
            elif len(authors) == 2:
                # Two authors: Smith and Jones, 2020
                second_author = authors[1]
                second_author_esc = re.escape(second_author)
                second_author_norm_esc = re.escape(normalize_author_name(second_author))
                for second_esc in [second_author_esc, second_author_norm_esc]:
                    patterns.extend([
                        rf'\({author_esc}\s+(?:and|&)\s+{second_esc}\s*,?\s*{year_str}[a-z]?\)',
                        rf'{author_esc}\s+(?:and|&)\s+{second_esc}\s*\({year_str}[a-z]?\)',
                        rf'{author_esc}\s+(?:and|&)\s+{second_esc}\s*,\s*{year_str}[a-z]?',
                    ])

            # Multiple authors: Smith et al., 2020
            if len(authors) >= 2:
                patterns.extend([
                    rf'\({author_esc}\s+et\s+al\.?\s*,?\s*{year_str}[a-z]?\)',
                    rf'{author_esc}\s+et\s+al\.?\s*\({year_str}[a-z]?\)',
                    rf'{author_esc}\s+et\s+al\.?\s*,\s*{year_str}[a-z]?',
                    # No-comma style: "Li et al. 2015" (common in Annual Reviews)
                    rf'{author_esc}\s+et\s+al\.?\s+{year_str}[a-z]?',
                ])

        # Numbered reference patterns (year-independent): Smith (42), Smith et al. (42)
        if len(authors) == 1:
            patterns.append(rf'{author_esc}\s*\(\d+\)')
        elif len(authors) == 2:
            second_author = authors[1]
            second_author_esc = re.escape(second_author)
            second_author_norm_esc = re.escape(normalize_author_name(second_author))
            for second_esc in [second_author_esc, second_author_norm_esc]:
                patterns.append(rf'{author_esc}\s+(?:and|&)\s+{second_esc}\s*\(\d+\)')
        if len(authors) >= 2:
            patterns.append(rf'{author_esc}\s+et\s+al\.?\s*\(\d+\)')

    # Search for all patterns
    for pattern in patterns:
        for m in re.finditer(pattern, text, re.IGNORECASE):
            positions.append(m.start())

    return positions


def find_title_mentions(text: str, title: str) -> list[int]:
    """Find positions where the paper title is mentioned."""
    if not title or len(title) < 20:
        return []

    positions = []

    # Use first significant words of title (skip common words)
    stop_words = {'the', 'a', 'an', 'of', 'in', 'on', 'for', 'and', 'or', 'to', 'with'}
    words = [w for w in title.split() if w.lower() not in stop_words]

    if len(words) >= 3:
        # Search for first 3-5 significant words together
        search_phrase = ' '.join(words[:min(5, len(words))])
        search_escaped = re.escape(search_phrase)
        for m in re.finditer(search_escaped, text, re.IGNORECASE):
            positions.append(m.start())

    return positions


def extract_context(text: str, position: int, context_chars: int = 500) -> dict:
    """Extract context around a position, trying to align to sentence boundaries."""
    start = max(0, position - context_chars)
    end = min(len(text), position + context_chars)

    # Try to extend to sentence boundaries
    # Look for sentence start (after . ! ? followed by space and capital)
    if start > 0:
        # Search backwards for sentence boundary
        search_region = text[max(0, start-100):start]
        sent_end = max(
            search_region.rfind('. '),
            search_region.rfind('.\n'),
            search_region.rfind('? '),
            search_region.rfind('! ')
        )
        if sent_end != -1:
            start = max(0, start - 100) + sent_end + 2

    # Look for sentence end
    if end < len(text):
        search_region = text[end:min(len(text), end+100)]
        sent_end = min(
            search_region.find('. ') if search_region.find('. ') != -1 else 9999,
            search_region.find('.\n') if search_region.find('.\n') != -1 else 9999,
            search_region.find('? ') if search_region.find('? ') != -1 else 9999,
            search_region.find('! ') if search_region.find('! ') != -1 else 9999,
        )
        if sent_end != 9999:
            end = end + sent_end + 1

    return {
        'context': text[start:end].strip(),
        'start': start,
        'end': end,
        'citation_position': position,
    }


def is_in_reference_section(text: str, position: int) -> bool:
    """Check if a position is likely in the reference section (not main text)."""
    # Look for reference section markers before this position
    text_before = text[max(0, position-5000):position].lower()

    ref_markers = ['references\n', 'bibliography\n', 'literature cited', 'works cited']
    for marker in ref_markers:
        if marker in text_before:
            # Check if there's main text content after the marker
            marker_pos = text_before.rfind(marker)
            text_after_marker = text_before[marker_pos:]
            # If mostly DOIs/numbers after marker, we're in references
            doi_count = len(re.findall(r'10\.\d{4}/', text_after_marker))
            if doi_count > 3:
                return True

    # Also check if position is in a region dense with DOIs (crossref section)
    surrounding = text[max(0, position-200):min(len(text), position+200)]
    doi_count = len(re.findall(r'10\.\d{4}/', surrounding))
    if doi_count > 2:
        return True

    return False


def find_citation_contexts(
    citing_paper_text: str,
    cited_doi: str,
    context_chars: int = 500,
    session: Optional[requests.Session] = None,
    exclude_reference_section: bool = True
) -> list[dict]:
    """
    Find all citations of a paper in the citing paper's text and extract context.

    Args:
        citing_paper_text: Full text of the citing paper
        cited_doi: DOI of the paper being cited
        context_chars: Number of characters to include around the citation
        session: Optional requests session for API calls
        exclude_reference_section: If True, exclude citations found in reference section

    Returns:
        List of dicts with citation info and context for each citation found
    """
    # Get metadata for the cited paper
    metadata = get_paper_metadata(cited_doi, session)
    if not metadata:
        return []

    results = []
    seen_positions = set()

    # Method 1: Find DOI directly in text and get reference number
    ref_number = find_reference_number_for_doi(citing_paper_text, cited_doi)

    # Method 2: Find numbered citations if we found a reference number
    if ref_number:
        positions = find_numbered_citations(citing_paper_text, ref_number)
        for pos in positions:
            if pos not in seen_positions:
                if exclude_reference_section and is_in_reference_section(citing_paper_text, pos):
                    continue
                seen_positions.add(pos)
                ctx = extract_context(citing_paper_text, pos, context_chars)
                ctx['method'] = 'numbered_citation'
                ctx['reference_number'] = ref_number
                results.append(ctx)

    # Method 3: Find author-year citations
    if metadata['authors'] and metadata['year']:
        positions = find_author_citations(
            citing_paper_text,
            metadata['authors'],
            metadata['year']
        )
        for pos in positions:
            pos_bucket = pos // 100  # Group nearby positions
            if pos_bucket not in seen_positions:
                if exclude_reference_section and is_in_reference_section(citing_paper_text, pos):
                    continue
                seen_positions.add(pos_bucket)
                ctx = extract_context(citing_paper_text, pos, context_chars)
                ctx['method'] = 'author_year'
                ctx['authors'] = metadata['authors']
                ctx['year'] = metadata['year']
                results.append(ctx)

    # Method 4: Find title mentions (less common but useful)
    if metadata['title']:
        positions = find_title_mentions(citing_paper_text, metadata['title'])
        for pos in positions:
            pos_bucket = pos // 100
            if pos_bucket not in seen_positions:
                if exclude_reference_section and is_in_reference_section(citing_paper_text, pos):
                    continue
                seen_positions.add(pos_bucket)
                ctx = extract_context(citing_paper_text, pos, context_chars)
                ctx['method'] = 'title_mention'
                ctx['title'] = metadata['title']
                results.append(ctx)

    # Sort by position
    results.sort(key=lambda x: x['citation_position'])

    # Add metadata to all results
    for r in results:
        r['cited_doi'] = cited_doi
        r['cited_metadata'] = metadata

    return results


def estimate_main_text_length(text: str) -> int:
    """
    Estimate how much of the text is actual main content vs references/metadata.

    Returns approximate length of main text (before reference section).
    """
    return find_reference_section_start(text)


def find_citation_in_cached_paper(
    cache_file: Path,
    cited_doi: str,
    context_chars: int = 500,
    session: Optional[requests.Session] = None
) -> dict:
    """
    Find citations of a paper in a cached paper file.
    """
    with open(cache_file) as f:
        data = json.load(f)

    citing_doi = data.get('doi', cache_file.stem.replace('_', '/'))
    text = data.get('text', '')

    if not text:
        return {
            'citing_doi': citing_doi,
            'cited_doi': cited_doi,
            'citations': [],
            'error': 'No text in cache file',
        }

    # Check if we have enough main text
    main_text_length = estimate_main_text_length(text)
    if main_text_length < 1000:
        return {
            'citing_doi': citing_doi,
            'cited_doi': cited_doi,
            'source': data.get('source', ''),
            'text_length': len(text),
            'main_text_length': main_text_length,
            'num_citations': 0,
            'citations': [],
            'error': 'Insufficient main text (only references/metadata)',
        }

    citations = find_citation_contexts(text, cited_doi, context_chars, session)

    return {
        'citing_doi': citing_doi,
        'cited_doi': cited_doi,
        'source': data.get('source', ''),
        'text_length': len(text),
        'main_text_length': main_text_length,
        'num_citations': len(citations),
        'citations': citations,
    }


if __name__ == '__main__':
    # Test with known examples
    cache_dir = Path("/Volumes/microsd64/data/")

    # Load the results to get citing paper -> cited paper relationships
    with open(cache_dir / "dandi_all_results.json") as f:
        results_data = json.load(f)

    # Collect test cases: (citing_doi, cited_doi)
    test_cases = []
    for result in results_data['results'][:20]:
        for citing in result.get('citing_papers', [])[:2]:
            citing_doi = citing.get('doi')
            cited_doi = citing.get('cited_paper_doi')
            if citing_doi and cited_doi:
                cache_file = cache_dir / f"{citing_doi.replace('/', '_')}.json"
                if cache_file.exists():
                    test_cases.append((citing_doi, cited_doi, cache_file))
                    if len(test_cases) >= 20:
                        break
        if len(test_cases) >= 20:
            break

    print(f"Testing {len(test_cases)} cases\n")
    print("=" * 80)

    success_count = 0
    low_quality_count = 0
    for i, (citing_doi, cited_doi, cache_file) in enumerate(test_cases, 1):
        print(f"\nTest {i}: {citing_doi}")
        print(f"  Cited: {cited_doi}")

        result = find_citation_in_cached_paper(cache_file, cited_doi, context_chars=400)

        print(f"  Source: {result.get('source', 'unknown')}")
        print(f"  Text: {result.get('text_length', 0)} chars, Main: {result.get('main_text_length', 0)} chars")

        if result.get('error'):
            print(f"  Error: {result['error']}")
            if 'Insufficient' in result.get('error', ''):
                low_quality_count += 1
        elif result['num_citations'] > 0:
            success_count += 1
            print(f"  Found {result['num_citations']} citation(s)")
            for j, citation in enumerate(result['citations'][:2], 1):
                print(f"\n  Citation {j} ({citation['method']}):")
                context = citation['context']
                # Truncate for display
                if len(context) > 300:
                    context = context[:150] + " ... " + context[-150:]
                print(f"    {context}")
        else:
            print(f"  Found 0 citations in main text")

        print("-" * 80)

    valid_cases = len(test_cases) - low_quality_count
    print(f"\n\nSummary:")
    print(f"  Total test cases: {len(test_cases)}")
    print(f"  Low quality (refs only): {low_quality_count}")
    print(f"  Valid papers: {valid_cases}")
    print(f"  Citations found: {success_count}/{valid_cases} ({100*success_count/valid_cases:.1f}% of valid papers)")
