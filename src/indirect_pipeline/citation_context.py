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


# Tokens that, when they appear immediately after a candidate citation number,
# indicate the number is a quantity (e.g. "30 s", "30 trials") and not a citation.
# Used by find_numbered_citations to filter out false-positive matches like the
# "30" in "the first 30 s of all repetitions".
_QUANTITY_UNIT_RE = (
    r'\s*('
    # SI / time / electrical units, word-boundary anchored
    r'(?:[kMG]?Hz|kHz|MHz|GHz)\b'
    r'|[kMG]?Ω\b|ω\b|μs\b|ms\b|s\b|min\b|hr?\b|d\b'
    r'|%|°|nm\b|μm\b|mm\b|cm\b|m\b|mV\b|μV\b|V\b|nA\b|pA\b|μA\b|mA\b|A\b'
    # Common neuroscience-experiment count nouns
    r'|cells?\b|trials?\b|sessions?\b|neurons?\b|subjects?\b|participants?\b'
    r'|samples?\b|experiments?\b|repetitions?\b|animals?\b|mice\b|rats\b'
    r'|spikes?\b|recordings?\b|epochs?\b|years?\b|months?\b|weeks?\b|days?\b'
    r'|hours?\b|minutes?\b|seconds?\b'
    r')'
)

# Words that, when they precede a candidate citation number, indicate the number
# is a count being quantified ("first 30 cells", "last 30 trials") rather than
# a superscript citation. Closed lowercase stoplist; matched case-insensitively.
_QUANTITY_DETERMINERS = frozenset({
    'first', 'last', 'all', 'each', 'every', 'next', 'prior', 'previous',
    'past', 'top', 'bottom', 'these', 'those', 'about', 'approximately',
    'approx', 'roughly', 'nearly', 'almost', 'only', 'just', 'over', 'under',
    'remaining', 'final', 'initial', 'middle', 'other', 'another',
})


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


_CRCNS_CODE_TO_DOI: Optional[dict] = None


def _load_crcns_code_to_doi() -> dict:
    global _CRCNS_CODE_TO_DOI
    if _CRCNS_CODE_TO_DOI is None:
        path = Path(".crcns_doi_to_code.json")
        doi_to_code = json.loads(path.read_text())
        _CRCNS_CODE_TO_DOI = {code: doi for doi, code in doi_to_code.items()}
    return _CRCNS_CODE_TO_DOI


def get_dataset_deposit_doi(dandiset_id: str) -> Optional[str]:
    """
    Look up the deposit DOI for a dataset by its archive-agnostic identifier.

    Tries known archive mappings in order:
    - CRCNS codes (e.g. 'fcx-2', 'pvc-8') resolve via .crcns_doi_to_code.json.

    Returns None if the dataset isn't found in any known mapping. Additional
    archives (DANDI, OpenNeuro, etc.) can be added by extending this function
    with their own lookup paths.
    """
    crcns_doi = _load_crcns_code_to_doi().get(dandiset_id)
    if crcns_doi:
        return crcns_doi
    return None


def build_primary_citation_string(metadata: dict) -> Optional[str]:
    """
    Build the canonical author-year citation string for the primary paper,
    in the form a body author-year cite would use.

    - 1 author:   "Smith, 2018"
    - 2 authors:  "Smith and Jones, 2018"
    - 3+ authors: "Smith et al., 2018"

    Returns None if authors or year are missing.
    """
    if not metadata:
        return None
    authors = metadata.get('authors') or []
    year = metadata.get('year')
    if not authors or not year:
        return None
    if len(authors) == 1:
        return f"{authors[0]}, {year}"
    if len(authors) == 2:
        return f"{authors[0]} and {authors[1]}, {year}"
    return f"{authors[0]} et al., {year}"


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


def find_reference_number_in_parsed_bibliography(text: str, doi: str) -> Optional[int]:
    """
    Find the reference number whose parsed bibliography entry contains the
    given DOI as a substring. Uses `parse_numbered_bibliography`, which
    recognizes `[N]` and `N.` numbered anchors. Robust to entries where the
    bibliography text is just `[N] {DOI}` (the migrated CrossRef format) —
    no need to count anchor positions and no off-by-one when other entries
    contribute zero or multiple DOIs.
    """
    bibliography = parse_numbered_bibliography(text)
    if not bibliography:
        return None

    doi_lower = doi.lower()
    for number in sorted(bibliography):
        if doi_lower in bibliography[number].lower():
            return number
    return None


def find_reference_number_for_doi(text: str, doi: str) -> Optional[int]:
    """
    Find the reference number associated with a DOI in the reference section.

    Handles multiple reference formats:
    1. Numbered bibliography entries that contain the DOI as a substring
       (covers the `[N] {DOI}` form emitted by the migrated CrossRef
       serialization). Most authoritative when `[N]` anchors are present.
    2. Numbered references near the DOI: "42. Author Name... doi:10.1234/..."
    3. Europe PMC format: DOIs on separate lines (counts position) — last
       resort, brittle when entries contribute zero or multiple DOIs.

    Returns None if DOI not found in references.
    """
    # Pattern 1c first: when the bibliography has parseable [N]/N. anchors,
    # finding the entry containing the DOI is more reliable than walking
    # backwards from the DOI for an anchor (which can be tripped up by stray
    # digits inside earlier DOIs or page numbers).
    parsed_match = find_reference_number_in_parsed_bibliography(text, doi)
    if parsed_match is not None:
        return parsed_match

    doi_escaped = re.escape(doi)

    # Find the DOI position
    doi_match = re.search(doi_escaped, text, re.IGNORECASE)
    if not doi_match:
        return None

    doi_pos = doi_match.start()

    # Look backwards for an explicit reference number (up to 500 chars before DOI)
    search_start = max(0, doi_pos - 500)
    preceding_text = text[search_start:doi_pos]

    # Pattern 1a: Explicit numbered reference at start of line
    # e.g., "\n42. Author" or "\n42 Author" but NOT "10.1016/..."
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

    # Pattern 1b: Reference number mid-line followed by ". AuthorName"
    # Handles Europe PMC format: "PMC4126853 30. Huszár R..."
    # Requires: space before number, period after, space(s), then capital letter
    # This avoids matching page numbers like "691 704.e5" (no space after period)
    ref_pattern_b = r'\s(\d{1,3})\.\s+[A-Z]'
    ref_numbers_b = list(re.finditer(ref_pattern_b, preceding_text))

    valid_refs_b = []
    for m in ref_numbers_b:
        num = int(m.group(1))
        # Skip DOI-like patterns (10.xxxx)
        if num == 10:
            remaining = preceding_text[m.end()-1:m.end()+20]
            if re.match(r'\d{4}/', remaining):
                continue
        valid_refs_b.append(num)

    if valid_refs_b:
        return valid_refs_b[-1]

    # Pattern 2: Europe PMC format - count DOI position in reference section
    # Deduplicate DOIs to handle concatenated text sources (e.g., europe_pmc+crossref)
    # where the same reference section appears twice
    ref_start = find_reference_section_start(text)

    if ref_start < len(text):
        # Get all DOIs in reference section
        ref_section = text[ref_start:]
        ref_dois = list(re.finditer(r'10\.\d{4,}/[^\s]+', ref_section))

        # Count unique DOIs only (first occurrence determines position)
        seen_dois = set()
        ref_number_counter = 0
        for m in ref_dois:
            # Normalize DOI: lowercase, strip trailing punctuation
            doi_text = m.group().lower().rstrip('.,;:)')
            if doi_text not in seen_dois:
                seen_dois.add(doi_text)
                ref_number_counter += 1
                if doi.lower() in doi_text:
                    return ref_number_counter

    return None


def normalize_title_for_matching(text: str) -> str:
    """
    Normalize text for title matching: lowercase, strip non-alphanumeric,
    collapse whitespace. Folds em/en-dashes, hyphens, and punctuation
    differences that vary across preprint vs published versions.
    """
    text = text.lower()
    text = re.sub(r'[^a-z0-9]+', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def parse_numbered_bibliography(text: str) -> dict[int, str]:
    """
    Parse the citing paper's numbered bibliography into a {N: entry_text} map.

    Each entry runs from its anchor's end to the next anchor's start. The
    cached text for some preprints contains *two* numbered bibliographies in
    series (e.g. the playwright-rendered references followed by the CrossRef-
    appended references), and the two can disagree on identity at the same
    number — CrossRef's deposited reference list for a preprint is sometimes
    incomplete or misnumbered relative to the publisher-rendered page.

    To avoid mixing entries from two competing bibliographies, anchors are
    partitioned into groups by numbering restart (a new group begins whenever
    the next anchor's number is ≤ a number already seen in the current
    group). One bibliography dict is built per group, and the group with the
    largest total body content is returned. When a single bibliography is
    present, it's the only group and is returned unchanged.
    """
    anchor_patterns = [
        re.compile(r'(?:^|\n)\s*\[(\d{1,3})\]\.\s*↵'),
        re.compile(r'(?:^|\n)\s*\[(\d{1,3})\]\s+(?=[A-Z])'),
        re.compile(r'(?:^|\n)\s*\[(\d{1,3})\]\s+(?=\S)'),
        re.compile(r'(?:^|\n)\s*(\d{1,3})\.\s*↵'),
        re.compile(r'(?:^|\n)\s*(\d{1,3})\.\s+(?=[A-Z])'),
        # Playwright bibliography format with no whitespace between `N.` and
        # the author surname, e.g. `\n15.Fujisawa, S., Amarasingham, ...`.
        # Requires capital letter followed by lowercase to limit false matches
        # on fragments like equation numbers.
        re.compile(r'(?:^|\n)(\d{1,3})\.(?=[A-Z][a-z])'),
    ]

    anchors = []
    for pattern in anchor_patterns:
        for match in pattern.finditer(text):
            anchors.append((match.start(), match.end(), int(match.group(1))))

    anchors.sort(key=lambda anchor: anchor[0])
    if not anchors:
        return {}

    # Some anchor patterns overlap (e.g. `[N] (?=[A-Z])` and `[N] (?=\S)`
    # both fire on `[6] Author`). Dedupe by start position so the same anchor
    # is only counted once — otherwise a duplicate looks like a numbering
    # restart and fragments the bibliography into one-anchor groups.
    deduped = []
    seen_positions = set()
    for anchor in anchors:
        if anchor[0] in seen_positions:
            continue
        seen_positions.add(anchor[0])
        deduped.append(anchor)
    anchors = deduped

    # Partition anchors into groups by numbering restart.
    groups = []
    current_group = []
    current_max = -1
    for anchor in anchors:
        number = anchor[2]
        if number <= current_max:
            groups.append(current_group)
            current_group = []
            current_max = -1
        current_group.append(anchor)
        if number > current_max:
            current_max = number
    if current_group:
        groups.append(current_group)

    # Build a bibliography dict for each group; pick the group with the most
    # total body content. Within a group, the entry body runs from the
    # anchor's end to the next anchor's start in the same group; the last
    # entry runs to a small fixed window past its anchor.
    best_bibliography = {}
    best_total_length = -1
    for group in groups:
        bibliography = {}
        for index, (_, end, number) in enumerate(group):
            if index + 1 < len(group):
                next_start = group[index + 1][0]
            else:
                next_start = min(end + 1500, len(text))
            bibliography[number] = text[end:next_start].strip()
        total_length = sum(len(body) for body in bibliography.values())
        if total_length > best_total_length:
            best_total_length = total_length
            best_bibliography = bibliography

    return best_bibliography


def find_reference_number_by_title(
    citing_paper_text: str,
    cited_title: str,
) -> Optional[int]:
    """
    Find the reference number whose bibliography entry contains the cited
    title. Title-based matching is robust to preprint↔published DOI mismatches:
    titles are stable across versions even when DOIs, years, and author
    formatting are not.

    Returns None if the title is too short to be discriminating, or if no
    bibliography entry contains the normalized title as a substring.
    """
    cited_normalized = normalize_title_for_matching(cited_title)
    if len(cited_normalized.split()) < 4:
        return None

    bibliography = parse_numbered_bibliography(citing_paper_text)
    if not bibliography:
        return None

    candidates = []
    for number, entry in bibliography.items():
        entry_normalized = normalize_title_for_matching(entry)
        if cited_normalized in entry_normalized:
            candidates.append((len(entry), number))

    if not candidates:
        return None

    candidates.sort()
    return candidates[0][1]


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
        if numbers_nearby < 1:
            continue

        # Skip if the number is followed by a measurement unit or quantity noun
        # (e.g. "30 s", "30 trials") — the number is a quantity, not a citation.
        after = text[m.end():min(len(text), m.end() + 30)]
        if re.match(_QUANTITY_UNIT_RE, after, re.IGNORECASE):
            continue

        # Skip if the preceding word is a quantity-determiner (e.g. "first 30",
        # "last 30"). The matched letter (m.start()) is the final letter of the
        # preceding word; walk backward over alphabetic chars to find the word.
        word_start = m.start()
        while word_start > 0 and text[word_start - 1].isalpha():
            word_start -= 1
        preceding_word = text[word_start:m.start() + 1].lower()
        if preceding_word in _QUANTITY_DETERMINERS:
            continue

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
                    # Intermediate-name style: "Coen-Cagli, Kohn et al. 2015"
                    rf'\({author_esc}\s*,\s*[A-Z][A-Za-z\-]+\s+et\s+al\.?\s*,?\s*{year_str}[a-z]?\)',
                    rf'{author_esc}\s*,\s*[A-Z][A-Za-z\-]+\s+et\s+al\.?\s*\({year_str}[a-z]?\)',
                    rf'{author_esc}\s*,\s*[A-Z][A-Za-z\-]+\s+et\s+al\.?\s*,?\s*{year_str}[a-z]?',
                    # Serial-comma & style: "Coen-Cagli, Kohn, & Schwartz, 2015"
                    # Supports arbitrary intermediate names; `&` or `and` before final author;
                    # optional Oxford comma before the connector.
                    rf'\({author_esc}\s*,\s*[A-Z][A-Za-z\-]+(?:\s*,\s*[A-Z][A-Za-z\-]+)*\s*,?\s*(?:&|and)\s*[A-Z][A-Za-z\-]+\s*,?\s*{year_str}[a-z]?\)',
                    rf'{author_esc}\s*,\s*[A-Z][A-Za-z\-]+(?:\s*,\s*[A-Z][A-Za-z\-]+)*\s*,?\s*(?:&|and)\s*[A-Z][A-Za-z\-]+\s*\({year_str}[a-z]?\)',
                    rf'{author_esc}\s*,\s*[A-Z][A-Za-z\-]+(?:\s*,\s*[A-Z][A-Za-z\-]+)*\s*,?\s*(?:&|and)\s*[A-Z][A-Za-z\-]+\s*,?\s*{year_str}[a-z]?',
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

    # Combined-year scan: cites like "Fujisawa et al., 2015, 2008" or
    # "Smith, 2015, 2008" pack two papers into one author-year fragment.
    # The base patterns only catch the first year; this loop walks every
    # year in the run and records a hit if the target year sits in any
    # slot beyond the first.
    target_years = set(years_to_search)
    for author_esc in author_patterns:
        if len(authors) >= 2:
            multi_year_patterns = [
                rf'{author_esc}\s+et\s+al\.?\s*,\s*\d{{4}}[a-z]?(?:\s*,\s*\d{{4}}[a-z]?)+',
            ]
        else:
            multi_year_patterns = [
                rf'{author_esc}\s*,\s*\d{{4}}[a-z]?(?:\s*,\s*\d{{4}}[a-z]?)+',
            ]
        for pattern in multi_year_patterns:
            for m in re.finditer(pattern, text, re.IGNORECASE):
                years_in_match = re.findall(r'\d{4}', m.group(0))
                # Skip the first year — already covered by the base patterns.
                if any(y in target_years for y in years_in_match[1:]):
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

    # Method 1a: Match the cited paper's title against parsed numbered
    # bibliography entries. Robust to preprint↔published DOI mismatches —
    # titles are stable across versions even when DOIs and years are not.
    ref_number = None
    if metadata.get('title'):
        ref_number = find_reference_number_by_title(
            citing_paper_text, metadata['title']
        )

    # Method 1b: Fall back to DOI walkback if title match failed.
    if ref_number is None:
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


def get_context_text(
    citing_doi: str,
    context_start: int,
    context_end: int,
    cache_dir: Path,
) -> str:
    """Extract context text from a cached paper file on the fly."""
    cache_file = cache_dir / f"{citing_doi.replace('/', '_')}.json"
    with open(cache_file) as f:
        data = json.load(f)
    text = data.get('text', '')
    return text[context_start:context_end]


def get_paper_text_prefix(citing_doi: str, cache_dir: Path, max_chars: int = 8000) -> str:
    """Read the first N characters of a cached paper's text."""
    cache_file = cache_dir / f"{citing_doi.replace('/', '_')}.json"
    with open(cache_file) as f:
        data = json.load(f)
    return data.get('text', '')[:max_chars]


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
