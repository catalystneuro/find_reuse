#!/usr/bin/env python3
"""
dandi_primary_papers.py - Find dandisets that reference their primary papers

This module queries the DANDI REST API to find all dandisets that have
relatedResource entries linking to their primary describing paper.

Paper references are found from two sources:
1. relatedResource entries with appropriate relations
2. DOIs found in the dandiset description text

The primary paper relation is typically indicated by:
- dcite:IsDescribedBy - The dataset is described by the paper (most common)
- dcite:IsPublishedIn - The dataset is published in the paper
- dcite:IsSupplementTo - The dataset supplements the paper

Only resources that are papers are included:
- If resourceType is specified, it must be: JournalArticle, Preprint, DataPaper,
  ConferencePaper, or ConferenceProceeding
- If resourceType is not specified, the resource is included (many papers lack type info)
- Resources with types like Software, Dataset, ComputationalNotebook are excluded

Usage:
    python dandi_primary_papers.py
    python dandi_primary_papers.py -o results.json
    python dandi_primary_papers.py --all-relations
    python dandi_primary_papers.py --citations -o results.json  # Include citation counts
    python dandi_primary_papers.py --citations --summary  # Show citation summary
    python dandi_primary_papers.py --fetch-text -o results.json  # Fetch citing paper texts
    python dandi_primary_papers.py --fetch-text --max-citing-papers 5  # Limit per dandiset

Citation counts include:
- Total citations (all time) from OpenAlex
- Citations after dandiset creation (potential reuse indicators)

Citing paper text fetching (--fetch-text):
- Gets papers that cite the primary papers after dandiset creation (potential reuse)
- Uses find_reuse.ArchiveFinder to fetch full text from multiple sources
- Sources include Europe PMC, NCBI PMC, CrossRef, bioRxiv/medRxiv, publisher HTML
- Results are cached to avoid repeated fetches
- Limited to --max-citing-papers per dandiset (default: 10)
"""

import argparse
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import requests
from tqdm import tqdm

# Cache directory for preprint→published lookups (shared with find_reuse.py)
PREPRINT_CACHE_DIR = Path('.preprint_cache')
# Cache file for alternate DOI lookups (published→preprint)
ALTERNATE_DOI_CACHE_FILE = Path('.alternate_doi_cache.json')


# DANDI API base URL
DANDI_API_URL = "https://api.dandiarchive.org/api"

# Relation types that indicate a primary/describing paper relationship
# These are DataCite relation types (dcite:)
PRIMARY_PAPER_RELATIONS = {
    'dcite:IsDescribedBy',   # Most common - dataset is described by the paper
    'dcite:IsPublishedIn',   # Dataset is published in the paper
    'dcite:IsSupplementTo',  # Dataset supplements the paper (data for the paper)
    'dcite:Describes',       # Inverse of IsDescribedBy (some dandisets use this)
}

# Additional relation types that may link to papers but aren't primary descriptors
SECONDARY_PAPER_RELATIONS = {
    'dcite:IsCitedBy',       # Dataset is cited by papers
    'dcite:IsReferencedBy',  # Dataset is referenced by papers
    'dcite:Cites',           # Dataset cites papers
    'dcite:IsSourceOf',      # Dataset is source of derived work
    'dcite:IsDerivedFrom',   # Dataset is derived from other sources
    'dcite:IsPartOf',        # Dataset is part of a larger work
}

# Resource types that represent papers (journal articles or preprints)
# If resourceType is set, it must be one of these to be included
PAPER_RESOURCE_TYPES = {
    'dcite:JournalArticle',
    'dcite:Preprint',
    'dcite:DataPaper',
    'dcite:ConferencePaper',
    'dcite:ConferenceProceeding',
}



def get_all_dandisets(session: requests.Session, show_progress: bool = True) -> list[dict]:
    """
    Fetch all dandisets from the DANDI API.

    Args:
        session: requests Session object
        show_progress: Whether to show a progress bar

    Returns:
        List of dandiset metadata dictionaries
    """
    url = f"{DANDI_API_URL}/dandisets/"
    params = {'page_size': 100}

    all_dandisets = []

    # First request to get total count
    resp = session.get(url, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    total_count = data['count']
    all_dandisets.extend(data['results'])

    # Create progress bar if requested
    pbar = tqdm(total=total_count, desc="Fetching dandisets", disable=not show_progress)
    pbar.update(len(data['results']))

    # Paginate through remaining results
    while data.get('next'):
        resp = session.get(data['next'], timeout=30)
        resp.raise_for_status()
        data = resp.json()
        all_dandisets.extend(data['results'])
        pbar.update(len(data['results']))
        time.sleep(0.1)  # Rate limiting

    pbar.close()
    return all_dandisets


def get_dandiset_version_metadata(
    session: requests.Session,
    dandiset_id: str,
    version: str
) -> Optional[dict]:
    """
    Fetch metadata for a specific dandiset version.

    Args:
        session: requests Session object
        dandiset_id: The dandiset identifier (e.g., "000003")
        version: The version string (e.g., "0.250624.0409")

    Returns:
        Version metadata dictionary, or None if request fails
    """
    url = f"{DANDI_API_URL}/dandisets/{dandiset_id}/versions/{version}/"

    try:
        resp = session.get(url, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException:
        return None


def has_doi_identifier(resource: dict) -> bool:
    """
    Check if a relatedResource entry has a DOI identifier.

    Args:
        resource: A relatedResource dictionary

    Returns:
        True if the resource has a DOI in identifier or URL, False otherwise
    """
    # Check identifier field for DOI
    identifier = resource.get('identifier', '') or ''
    if identifier:
        # Common DOI formats in identifier
        if identifier.startswith('doi:') or identifier.startswith('DOI:'):
            return True
        if identifier.startswith('10.'):
            return True
        if 'doi.org/' in identifier:
            return True

    # Check URL for DOI
    url = resource.get('url', '') or ''
    if url:
        if 'doi.org/' in url:
            return True
        # bioRxiv/medRxiv URLs contain DOIs
        if 'biorxiv.org/content/10.' in url or 'medrxiv.org/content/10.' in url:
            return True

    return False


def is_paper_resource(resource: dict) -> bool:
    """
    Check if a relatedResource entry represents a paper (journal article or preprint).

    Filtering logic:
    1. If resourceType is specified, it must be a paper type
    2. The resource must have a DOI identifier (in identifier field or URL)

    Args:
        resource: A relatedResource dictionary

    Returns:
        True if the resource is (or could be) a paper, False otherwise
    """
    resource_type = resource.get('resourceType')

    # If type is specified, it must be a paper type
    if resource_type is not None and resource_type not in PAPER_RESOURCE_TYPES:
        return False

    # Must have a DOI identifier
    return has_doi_identifier(resource)


def extract_doi_from_resource(resource: dict) -> Optional[str]:
    """
    Extract a DOI from a relatedResource entry.

    The DOI may be in the 'identifier' field or extractable from the 'url'.

    Args:
        resource: A relatedResource dictionary

    Returns:
        The DOI string if found, or None
    """
    # Check identifier field first
    identifier = resource.get('identifier', '')
    if identifier:
        # Clean up common DOI formats
        if identifier.startswith('doi:'):
            return identifier[4:]
        if identifier.startswith('DOI:'):
            return identifier[4:]
        if identifier.startswith('10.'):
            return identifier
        if 'doi.org/' in identifier:
            return identifier.split('doi.org/')[-1]

    # Try to extract from URL
    url = resource.get('url', '')
    if url:
        if 'doi.org/' in url:
            return url.split('doi.org/')[-1]
        # bioRxiv/medRxiv URLs contain DOI
        if 'biorxiv.org/content/' in url or 'medrxiv.org/content/' in url:
            # Extract DOI from URL like https://www.biorxiv.org/content/10.1101/2021.03.09.434621v2
            match = re.search(r'(10\.\d+/[^\s/v]+)', url)
            if match:
                return match.group(1)
        # eLife: https://elifesciences.org/articles/55130 → 10.7554/eLife.55130
        match = re.match(r'https?://elifesciences\.org/articles/(\d+)', url)
        if match:
            return f'10.7554/eLife.{match.group(1)}'
        # Nature: https://www.nature.com/articles/XXXX → 10.1038/XXXX
        match = re.match(r'https?://(?:www\.)?nature\.com/articles/([^\s?#]+)', url)
        if match:
            return f'10.1038/{match.group(1)}'
        # Cell Press: https://www.cell.com/neuron/fulltext/S0896-... → need CrossRef lookup
        # PubMed: https://pubmed.ncbi.nlm.nih.gov/XXXXX → need API lookup
        # Generic: try extracting any DOI-like pattern from URL
        match = re.search(r'(10\.\d{4,9}/[^\s,;)]+)', url)
        if match:
            return match.group(1)

    return None


def get_openalex_paper_data(
    session: requests.Session,
    doi: str
) -> Optional[dict]:
    """
    Get paper data from OpenAlex API including publication date and citation count.

    Args:
        session: requests Session object
        doi: The DOI of the paper

    Returns:
        Dictionary with publication_date, cited_by_count, and openalex_id, or None if not found
    """
    # Clean up DOI
    doi_clean = doi.strip()
    if doi_clean.startswith('doi:') or doi_clean.startswith('DOI:'):
        doi_clean = doi_clean[4:]

    url = f"https://api.openalex.org/works/doi:{doi_clean}"

    try:
        resp = session.get(url, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            pub_date_str = data.get('publication_date')
            return {
                'publication_date': pub_date_str,
                'cited_by_count': data.get('cited_by_count', 0),
                'openalex_id': data.get('id'),
            }
    except requests.RequestException:
        pass

    return None


def _get_preprint_cache_path(doi: str) -> Path:
    """Get cache file path for preprint lookup (shared format with find_reuse.py)."""
    safe_doi = doi.replace('/', '_').replace(':', '_').replace('\\', '_')
    return PREPRINT_CACHE_DIR / f"{safe_doi}.json"


def _load_alternate_doi_cache() -> dict:
    """Load the published→preprint alternate DOI cache."""
    if ALTERNATE_DOI_CACHE_FILE.exists():
        try:
            with open(ALTERNATE_DOI_CACHE_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save_alternate_doi_cache(cache: dict) -> None:
    """Save the published→preprint alternate DOI cache."""
    try:
        with open(ALTERNATE_DOI_CACHE_FILE, 'w') as f:
            json.dump(cache, f, indent=2)
    except OSError:
        pass


def get_alternate_doi(
    session: requests.Session,
    doi: str,
    alt_cache: Optional[dict] = None,
) -> Optional[str]:
    """
    Look up the alternate version of a DOI (preprint↔published).

    For preprints (10.1101/*): uses bioRxiv API to find published DOI.
    For published papers: searches OpenAlex by title to find a preprint version.

    Results are cached:
    - Preprint lookups: .preprint_cache/ (shared with find_reuse.py)
    - Published lookups: .alternate_doi_cache.json

    Args:
        session: requests Session object
        doi: The DOI to look up an alternate version for
        alt_cache: Optional pre-loaded alternate DOI cache dict (for published→preprint).
                   If None, will be loaded from disk.

    Returns:
        The alternate version DOI if found, or None.
    """
    doi_clean = doi.strip().lower()

    if doi_clean.startswith('10.1101/'):
        # --- Preprint → Published: use bioRxiv API ---
        return _lookup_published_version(session, doi_clean)
    else:
        # --- Published → Preprint: use OpenAlex title search ---
        return _lookup_preprint_version(session, doi_clean, alt_cache)


def _lookup_published_version(session: requests.Session, preprint_doi: str) -> Optional[str]:
    """Look up published version of a bioRxiv/medRxiv preprint."""
    PREPRINT_CACHE_DIR.mkdir(exist_ok=True)
    cache_path = _get_preprint_cache_path(preprint_doi)

    # Check cache first
    if cache_path.exists():
        try:
            with open(cache_path) as f:
                data = json.load(f)
                pub_info = data.get('published_info')
                if pub_info and pub_info.get('published_doi'):
                    return pub_info['published_doi']
                return None
        except (json.JSONDecodeError, OSError):
            pass

    # Query bioRxiv API
    for server in ['biorxiv', 'medrxiv']:
        url = f"https://api.biorxiv.org/pubs/{server}/{preprint_doi}"
        try:
            resp = session.get(url, timeout=30)
            if resp.status_code == 200:
                data = resp.json()
                if data.get('collection') and len(data['collection']) > 0:
                    pub_info = data['collection'][0]
                    published_doi = pub_info.get('published_doi')

                    result = {
                        'published_doi': published_doi or '',
                        'published_journal': pub_info.get('published_journal', ''),
                        'published_date': pub_info.get('published_date', ''),
                        'preprint_title': pub_info.get('preprint_title', ''),
                    } if published_doi else None

                    # Cache result
                    try:
                        with open(cache_path, 'w') as f:
                            json.dump({
                                'preprint_doi': preprint_doi,
                                'published_info': result,
                                'cached_at': datetime.now().isoformat(),
                            }, f)
                    except OSError:
                        pass

                    return published_doi if published_doi else None

            time.sleep(0.3)
        except requests.RequestException:
            pass

    # Cache negative result
    try:
        with open(cache_path, 'w') as f:
            json.dump({
                'preprint_doi': preprint_doi,
                'published_info': None,
                'cached_at': datetime.now().isoformat(),
            }, f)
    except OSError:
        pass

    return None


def _lookup_preprint_version(
    session: requests.Session,
    published_doi: str,
    alt_cache: Optional[dict] = None,
) -> Optional[str]:
    """Look up preprint version of a published paper via OpenAlex title search."""
    # Check cache
    if alt_cache is None:
        alt_cache = _load_alternate_doi_cache()

    if published_doi in alt_cache:
        return alt_cache[published_doi] or None  # "" means cached negative

    # Step 1: Get title from OpenAlex
    try:
        resp = session.get(
            f"https://api.openalex.org/works/doi:{published_doi}",
            params={'select': 'title'},
            timeout=10,
        )
        if resp.status_code != 200:
            return None
        title = resp.json().get('title')
        if not title or len(title) < 10:
            return None
    except requests.RequestException:
        return None

    time.sleep(0.05)

    # Step 2: Search OpenAlex for preprints with matching title
    try:
        resp = session.get(
            "https://api.openalex.org/works",
            params={
                'filter': f'title.search:"{title}",type:preprint',
                'select': 'doi,title',
                'per_page': 5,
            },
            timeout=10,
        )
        if resp.status_code != 200:
            return None

        results = resp.json().get('results', [])
        for work in results:
            work_doi = (work.get('doi') or '').replace('https://doi.org/', '').lower()
            work_title = (work.get('title') or '').lower().strip()
            if work_doi.startswith('10.1101/') and work_title == title.lower().strip():
                # Found a matching preprint
                alt_cache[published_doi] = work_doi
                _save_alternate_doi_cache(alt_cache)
                return work_doi

    except requests.RequestException:
        pass

    # Cache negative result
    alt_cache[published_doi] = ""
    _save_alternate_doi_cache(alt_cache)
    return None


def get_citations_after_date(
    session: requests.Session,
    openalex_id: str,
    after_date: str
) -> Optional[int]:
    """
    Get the count of citations to a paper published after a specific date.

    Args:
        session: requests Session object
        openalex_id: The OpenAlex ID of the paper (e.g., "W123456789")
        after_date: Date string in YYYY-MM-DD format

    Returns:
        Count of citations after the date, or None if request fails
    """
    # Extract just the ID part if full URL
    if '/' in openalex_id:
        openalex_id = openalex_id.split('/')[-1]

    url = f"https://api.openalex.org/works?filter=cites:{openalex_id},publication_date:>{after_date}&per_page=1"

    try:
        resp = session.get(url, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            return data.get('meta', {}).get('count', 0)
    except requests.RequestException:
        pass

    return None


def add_citation_counts(
    results: list[dict],
    show_progress: bool = True,
    rate_limit: float = 0.05
) -> list[dict]:
    """
    Add citation counts to the paper relations in results.

    Fetches:
    - Paper publication dates and total citation counts from OpenAlex
    - Citation counts filtered to only include papers published after dandiset creation

    Note: dandiset_created is already populated by find_dandisets_with_primary_papers()

    Args:
        results: List of results from find_dandisets_with_primary_papers
        show_progress: Whether to show progress bars
        rate_limit: Delay between API calls in seconds

    Returns:
        Updated results with citation data added to each paper relation
    """
    session = requests.Session()
    session.headers.update({
        'User-Agent': 'DANDIPrimaryPapers/1.0 (https://github.com/dandi; mailto:ben.dichter@catalystneuro.com)'
    })

    # Step 1: Get OpenAlex data for all unique DOIs (including alternate versions)
    all_dois = set()
    for result in results:
        for paper in result['paper_relations']:
            if paper.get('doi'):
                all_dois.add(paper['doi'])

    # Also collect alternate DOIs (preprint↔published)
    alt_doi_map = {}  # primary doi -> alternate doi
    print("Looking up alternate DOIs (preprint↔published)...", file=sys.stderr)
    for doi in tqdm(list(all_dois), desc="Looking up alternate DOIs", disable=not show_progress):
        alt = get_alternate_doi(session, doi)
        if alt:
            alt_doi_map[doi] = alt
            all_dois.add(alt)
        time.sleep(rate_limit)

    if alt_doi_map:
        print(f"  Found {len(alt_doi_map)} alternate versions", file=sys.stderr)

    doi_data = {}
    pbar = tqdm(all_dois, desc="Fetching paper data from OpenAlex", disable=not show_progress)

    for doi in pbar:
        pbar.set_postfix({'doi': doi[:30]})
        data = get_openalex_paper_data(session, doi)
        if data:
            doi_data[doi] = data
        time.sleep(rate_limit)

    # Step 2: For each paper, get citations after dandiset creation (both versions)
    pbar = tqdm(results, desc="Fetching citations after dandiset creation", disable=not show_progress)

    for result in pbar:
        ds_id = result['dandiset_id']
        pbar.set_postfix({'dandiset': ds_id})

        # Parse dandiset creation date (already populated by find_dandisets_with_primary_papers)
        ds_created_str = result.get('dandiset_created')
        ds_created = None
        if ds_created_str:
            try:
                ds_created = datetime.fromisoformat(ds_created_str.replace('Z', '+00:00'))
            except ValueError:
                pass

        total_citations = 0
        total_citations_after = 0

        for paper in result['paper_relations']:
            doi = paper.get('doi')

            if not doi or doi not in doi_data:
                paper['publication_date'] = None
                paper['citation_count'] = None
                paper['citations_after_dandiset_created'] = None
                continue

            paper_info = doi_data[doi]
            paper['publication_date'] = paper_info['publication_date']
            paper['citation_count'] = paper_info['cited_by_count']
            total_citations += paper_info['cited_by_count'] or 0

            # Also add citation count from alternate version
            alt_doi = alt_doi_map.get(doi)
            if alt_doi and alt_doi in doi_data:
                alt_info = doi_data[alt_doi]
                paper['citation_count'] = (paper['citation_count'] or 0) + (alt_info.get('cited_by_count') or 0)
                total_citations += alt_info.get('cited_by_count') or 0
                paper['alternate_doi'] = alt_doi

            # Get citations after dandiset creation
            if ds_created and paper_info.get('openalex_id'):
                from_date = ds_created.strftime('%Y-%m-%d')
                citations_after = get_citations_after_date(
                    session, paper_info['openalex_id'], from_date
                )
                paper['citations_after_dandiset_created'] = citations_after or 0

                # Also count citations of alternate version after creation
                if alt_doi and alt_doi in doi_data:
                    alt_oa_id = doi_data[alt_doi].get('openalex_id')
                    if alt_oa_id:
                        alt_citations_after = get_citations_after_date(
                            session, alt_oa_id, from_date
                        )
                        if alt_citations_after:
                            paper['citations_after_dandiset_created'] += alt_citations_after
                        time.sleep(rate_limit)

                if paper['citations_after_dandiset_created']:
                    total_citations_after += paper['citations_after_dandiset_created']
                time.sleep(rate_limit)
            else:
                paper['citations_after_dandiset_created'] = None

        result['total_citations'] = total_citations
        result['total_citations_after_created'] = total_citations_after

    return results


def get_citing_papers(
    session: requests.Session,
    openalex_id: str,
    after_date: str,
    max_results: int = 10
) -> list[dict]:
    """
    Get the DOIs and metadata of papers that cite a given paper after a specific date.

    Args:
        session: requests Session object
        openalex_id: The OpenAlex ID of the cited paper (e.g., "W123456789")
        after_date: Date string in YYYY-MM-DD format
        max_results: Maximum number of citing papers to return

    Returns:
        List of dicts with DOI, title, publication_date, and openalex_id of citing papers
    """
    # Extract just the ID part if full URL
    if '/' in openalex_id:
        openalex_id = openalex_id.split('/')[-1]

    per_page = min(max_results, 200)  # OpenAlex max is 200
    base_url = f"https://api.openalex.org/works?filter=cites:{openalex_id},publication_date:>{after_date}&per_page={per_page}&mailto=ben.dichter@catalystneuro.com"

    citing_papers = []
    cursor = '*'

    while cursor and len(citing_papers) < max_results:
        url = f"{base_url}&cursor={cursor}"
        try:
            resp = session.get(url, timeout=30)
            if resp.status_code != 200:
                break

            data = resp.json()
            results = data.get('results', [])
            if not results:
                break

            for work in results:
                doi = work.get('doi', '')
                if doi:
                    # Clean up DOI format (OpenAlex returns full URL)
                    doi = doi.replace('https://doi.org/', '')

                # Get journal/source name from primary_location
                journal = None
                primary_location = work.get('primary_location', {})
                if primary_location:
                    source = primary_location.get('source', {})
                    if source:
                        journal = source.get('display_name')

                citing_papers.append({
                    'doi': doi,
                    'title': work.get('title', ''),
                    'publication_date': work.get('publication_date'),
                    'journal': journal,
                    'openalex_id': work.get('id'),
                })

                if len(citing_papers) >= max_results:
                    break

            # Get next cursor for pagination
            meta = data.get('meta', {})
            cursor = meta.get('next_cursor')

        except requests.RequestException:
            break

    return citing_papers


def fetch_citing_paper_texts(
    results: list[dict],
    max_citing_papers_per_dandiset: int = 10,
    max_total_papers: int | None = None,
    show_progress: bool = True,
    verbose: bool = False,
    rate_limit: float = 0.1,
    cache_dir: str = "/Volumes/microsd64/data/"
) -> list[dict]:
    """
    Fetch full text for papers that cite the primary papers after dandiset creation.

    This function:
    1. Gets the list of citing papers from OpenAlex (filtered by date)
    2. Fetches full text for each citing paper using ArchiveFinder

    Requires that add_citation_counts() was called first to populate openalex_id
    and dandiset_created fields.

    Args:
        results: List of results from find_dandisets_with_primary_papers (with citations)
        max_citing_papers_per_dandiset: Maximum citing papers to fetch per dandiset
        max_total_papers: Maximum total papers to fetch across all dandisets (None = no limit)
        show_progress: Whether to show progress bars
        verbose: Whether to show verbose output from ArchiveFinder
        rate_limit: Delay between API calls in seconds
        cache_dir: Directory to store cached paper texts

    Returns:
        Updated results with citing_papers added to each dandiset result
    """
    # Import ArchiveFinder from find_reuse module
    from find_reuse import ArchiveFinder

    finder = ArchiveFinder(verbose=verbose, use_cache=True, cache_dir=cache_dir)

    session = requests.Session()
    session.headers.update({
        'User-Agent': 'DANDIPrimaryPapers/1.0 (https://github.com/dandi; mailto:ben.dichter@catalystneuro.com)'
    })

    # Step 1: Get citing papers for each dandiset
    pbar = tqdm(results, desc="Fetching citing papers from OpenAlex", disable=not show_progress)

    for result in pbar:
        ds_id = result['dandiset_id']
        pbar.set_postfix({'dandiset': ds_id})

        ds_created_str = result.get('dandiset_created')
        if not ds_created_str:
            result['citing_papers'] = []
            continue

        # Parse dandiset creation date to get the filter date
        try:
            ds_created = datetime.fromisoformat(ds_created_str.replace('Z', '+00:00'))
            from_date = ds_created.strftime('%Y-%m-%d')
        except ValueError:
            result['citing_papers'] = []
            continue

        # Collect citing papers from all primary papers (including alternate versions)
        all_citing = []
        seen_citing_dois = set()

        for paper in result['paper_relations']:
            doi = paper.get('doi')
            if not doi:
                continue

            # Build list of (openalex_id, doi) for all versions of this paper
            openalex_ids = []

            openalex_data = get_openalex_paper_data(session, doi)
            if openalex_data and openalex_data.get('openalex_id'):
                openalex_ids.append((openalex_data['openalex_id'], doi))

            # Look up alternate version (preprint↔published)
            alt_doi = get_alternate_doi(session, doi)
            if alt_doi:
                alt_data = get_openalex_paper_data(session, alt_doi)
                if alt_data and alt_data.get('openalex_id'):
                    openalex_ids.append((alt_data['openalex_id'], alt_doi))

            if not openalex_ids:
                continue

            # Query citations from ALL versions of this paper
            for oa_id, version_doi in openalex_ids:
                citing = get_citing_papers(
                    session, oa_id, from_date,
                    max_results=max_citing_papers_per_dandiset
                )

                for c in citing:
                    c_doi = c.get('doi')
                    if c_doi and c_doi not in seen_citing_dois:
                        c['cited_paper_doi'] = doi  # Always use original DOI from metadata
                        all_citing.append(c)
                        seen_citing_dois.add(c_doi)

                time.sleep(rate_limit)

            # Stop if we have enough citing papers
            if len(all_citing) >= max_citing_papers_per_dandiset:
                break

        # Limit to max per dandiset
        result['citing_papers'] = all_citing[:max_citing_papers_per_dandiset]

    # Step 2: Collect all unique citing paper DOIs to fetch
    all_citing_dois = []
    seen_dois = set()
    for result in results:
        for citing in result.get('citing_papers', []):
            doi = citing.get('doi')
            if doi and doi not in seen_dois:
                all_citing_dois.append(doi)
                seen_dois.add(doi)

    # Apply max_total_papers limit
    if max_total_papers is not None and len(all_citing_dois) > max_total_papers:
        print(f"Found {len(all_citing_dois)} unique citing papers, limiting to {max_total_papers}", file=sys.stderr)
        all_citing_dois = all_citing_dois[:max_total_papers]
    else:
        print(f"Found {len(all_citing_dois)} unique citing papers to fetch", file=sys.stderr)

    # Step 3: Fetch text for all unique citing paper DOIs (parallel)
    doi_texts = {}
    n_workers = 8

    # Separate cached vs uncached DOIs for better progress tracking
    cached_dois = []
    uncached_dois = []
    for doi in all_citing_dois:
        safe = doi.replace('/', '_').replace(':', '_').replace('\\', '_')
        cache_path = Path(cache_dir) / f"{safe}.json"
        if cache_path.exists():
            cached_dois.append(doi)
        else:
            uncached_dois.append(doi)

    print(f"  {len(cached_dois)} already cached, {len(uncached_dois)} to fetch", file=sys.stderr)

    # Fetch cached papers quickly (single-threaded, no rate limit needed)
    for doi in tqdm(cached_dois, desc="Loading cached papers", disable=not show_progress):
        text, source, from_cache = finder.get_paper_text(doi)
        if text:
            doi_texts[doi] = {
                'text': text, 'source': source, 'text_length': len(text),
            }
        else:
            doi_texts[doi] = {
                'text': None, 'source': None, 'text_length': 0,
                'error': 'Could not retrieve paper text',
            }

    # Fetch uncached papers in parallel
    if uncached_dois:
        import concurrent.futures
        import threading

        # Each thread gets its own ArchiveFinder (owns its own requests.Session)
        thread_local = threading.local()

        def get_finder():
            if not hasattr(thread_local, 'finder'):
                thread_local.finder = ArchiveFinder(
                    verbose=verbose, use_cache=True, cache_dir=cache_dir
                )
            return thread_local.finder

        def fetch_one(doi):
            f = get_finder()
            text, source, from_cache = f.get_paper_text(doi)
            if not from_cache:
                time.sleep(0.2)  # Light rate limit per thread
            if text:
                return doi, {
                    'text': text, 'source': source, 'text_length': len(text),
                }
            else:
                return doi, {
                    'text': None, 'source': None, 'text_length': 0,
                    'error': 'Could not retrieve paper text',
                }

        pbar = tqdm(total=len(uncached_dois), desc=f"Fetching paper texts ({n_workers} workers)",
                    disable=not show_progress)
        with concurrent.futures.ThreadPoolExecutor(max_workers=n_workers) as executor:
            futures = {executor.submit(fetch_one, doi): doi for doi in uncached_dois}
            for future in concurrent.futures.as_completed(futures):
                doi, result = future.result()
                doi_texts[doi] = result
                pbar.set_postfix({'doi': doi[:35]})
                pbar.update(1)
        pbar.close()

    # Step 4: Add text metadata to citing papers in results (text is stored in cache files)
    for result in results:
        for citing in result.get('citing_papers', []):
            doi = citing.get('doi')
            if doi and doi in doi_texts:
                text_info = doi_texts[doi]
                # Don't include paper_text in output - it's stored in individual cache files
                citing['text_source'] = text_info.get('source')
                citing['text_length'] = text_info.get('text_length', 0)
                citing['text_cached'] = text_info.get('text') is not None
                if text_info.get('error'):
                    citing['text_error'] = text_info['error']
            else:
                citing['text_source'] = None
                citing['text_length'] = 0
                citing['text_cached'] = False
                citing['text_error'] = 'No DOI available'

    return results


def extract_dois_from_description(description: str) -> list[str]:
    """
    Extract DOIs from a dandiset description text.

    Args:
        description: The dandiset description text

    Returns:
        List of DOI strings found in the description
    """
    if not description:
        return []

    # DOI pattern - matches 10.XXXX/... stopping at whitespace or punctuation
    # that typically ends a DOI (but not hyphens, dots, or slashes within)
    doi_pattern = r'10\.\d{4,}/[^\s\]\)>"\',;]+'

    dois = re.findall(doi_pattern, description)

    # Clean up trailing punctuation that might have been captured
    cleaned_dois = []
    for doi in dois:
        # Remove trailing periods, semicolons, etc.
        doi = doi.rstrip('.;,')
        if doi and doi not in cleaned_dois:
            cleaned_dois.append(doi)

    return cleaned_dois


def find_dandisets_with_primary_papers(
    include_secondary: bool = False,
    show_progress: bool = True,
    rate_limit: float = 0.1,
    previous_results: Optional[list[dict]] = None,
    max_dandisets: Optional[int] = None,
) -> list[dict]:
    """
    Find all dandisets that have relatedResource entries linking to primary papers.

    Args:
        include_secondary: If True, also include secondary relation types
        show_progress: Whether to show progress bars
        rate_limit: Delay between API calls in seconds
        previous_results: If provided, skip version metadata fetch for dandisets
            whose draft_modified timestamp hasn't changed since the last scan.

    Returns:
        List of dictionaries with dandiset info and paper references
    """
    session = requests.Session()
    session.headers.update({
        'User-Agent': 'DANDIPrimaryPapers/1.0 (https://github.com/dandi; mailto:ben.dichter@catalystneuro.com)'
    })

    # Determine which relations to look for
    target_relations = PRIMARY_PAPER_RELATIONS.copy()
    if include_secondary:
        target_relations.update(SECONDARY_PAPER_RELATIONS)

    # Build cache of previous results keyed by dandiset ID
    prev_by_id = {}
    if previous_results:
        for r in previous_results:
            prev_by_id[r['dandiset_id']] = r

    # Fetch all dandisets
    dandisets = get_all_dandisets(session, show_progress)

    if max_dandisets is not None:
        dandisets = sorted(dandisets, key=lambda d: d['identifier'])[:max_dandisets]
        print(f"Capped to first {len(dandisets)} dandisets (--max-dandisets)", file=sys.stderr)

    results = []
    cache_hits = 0

    # Process each dandiset
    pbar = tqdm(dandisets, desc="Checking paper relations", disable=not show_progress)

    for ds in pbar:
        ds_id = ds['identifier']
        pbar.set_postfix({'dandiset': ds_id})

        # Get the most recent published version if available, otherwise use draft
        pub_version = ds.get('most_recent_published_version')
        draft_version = ds.get('draft_version')

        if pub_version:
            version = pub_version['version']
            version_info = pub_version
        elif draft_version:
            version = 'draft'
            version_info = draft_version
        else:
            continue

        # Cache validation: skip if draft hasn't been modified since last scan
        draft_modified = draft_version.get('modified') if draft_version else None
        if ds_id in prev_by_id and draft_modified:
            prev_draft_modified = prev_by_id[ds_id].get('draft_modified')
            if prev_draft_modified and prev_draft_modified == draft_modified:
                # Metadata unchanged — reuse previous result
                results.append(prev_by_id[ds_id])
                cache_hits += 1
                continue

        # Fetch full version metadata
        metadata = get_dandiset_version_metadata(session, ds_id, version)
        if not metadata:
            continue

        # Get relatedResource entries (at top level of version response)
        resources = metadata.get('relatedResource', [])

        # Filter for paper relations with appropriate resource types
        # Deduplicate by DOI - keep first occurrence
        paper_resources = []
        seen_dois = set()

        for resource in resources:
            relation = resource.get('relation', '')
            # Must have a matching relation AND be a paper resource type
            if relation in target_relations and is_paper_resource(resource):
                doi = extract_doi_from_resource(resource)
                # Skip if we've already seen this DOI
                if doi and doi in seen_dois:
                    continue
                if doi:
                    seen_dois.add(doi)
                paper_resources.append({
                    'relation': relation,
                    'url': resource.get('url'),
                    'name': resource.get('name'),
                    'identifier': resource.get('identifier'),
                    'resource_type': resource.get('resourceType'),
                    'doi': doi,
                    'source': 'relatedResource',
                })

        # Also extract DOIs from the description field
        description = metadata.get('description', '')
        description_dois = extract_dois_from_description(description)

        for doi in description_dois:
            if doi not in seen_dois:
                paper_resources.append({
                    'relation': 'description',
                    'url': f"https://doi.org/{doi}",
                    'name': None,
                    'identifier': doi,
                    'resource_type': None,
                    'doi': doi,
                    'source': 'description',
                })
                seen_dois.add(doi)

        if paper_resources:
            # Determine when data became publicly accessible:
            # 1. embargoedUntil (set on unembargo, available since dandi-archive v0.23.0)
            # 2. Fall back to dandiset creation date
            embargoed_until = None
            for access_entry in metadata.get('access', []):
                eu = access_entry.get('embargoedUntil')
                if eu:
                    embargoed_until = eu
                    break

            results.append({
                'dandiset_id': ds_id,
                'dandiset_name': version_info.get('name'),
                'dandiset_version': version,
                'dandiset_url': f"https://dandiarchive.org/dandiset/{ds_id}/{version}",
                'dandiset_doi': metadata.get('doi'),
                'dandiset_created': ds.get('created'),
                'embargo_status': ds.get('embargo_status'),
                'embargoed_until': embargoed_until,
                'data_accessible': embargoed_until or ds.get('created'),
                'draft_modified': draft_version.get('modified') if draft_version else None,
                'contact_person': ds.get('contact_person'),
                'paper_relations': paper_resources,
            })

        time.sleep(rate_limit)

    if cache_hits and show_progress:
        print(f"  Cache hits (unchanged draft): {cache_hits}/{len(dandisets)}", file=sys.stderr)

    return results


def get_relation_summary(results: list[dict]) -> dict:
    """
    Generate a summary of relation types found.

    Args:
        results: List of results from find_dandisets_with_primary_papers

    Returns:
        Dictionary with counts by relation type
    """
    relation_counts = {}
    for result in results:
        for paper in result['paper_relations']:
            relation = paper['relation']
            relation_counts[relation] = relation_counts.get(relation, 0) + 1

    return dict(sorted(relation_counts.items(), key=lambda x: -x[1]))


def main():
    parser = argparse.ArgumentParser(
        description='Find DANDI dandisets that reference their primary papers'
    )
    parser.add_argument(
        '-o', '--output',
        help='Output file path (JSON format). If not specified, prints to stdout.'
    )
    parser.add_argument(
        '--all-relations',
        action='store_true',
        help='Include secondary relation types (IsCitedBy, IsReferencedBy, etc.)'
    )
    parser.add_argument(
        '--no-progress',
        action='store_true',
        help='Disable progress bars'
    )
    parser.add_argument(
        '--summary',
        action='store_true',
        help='Print a summary instead of full results'
    )
    parser.add_argument(
        '--citations',
        action='store_true',
        help='Fetch citation counts from OpenAlex (slower, requires additional API calls)'
    )
    parser.add_argument(
        '--fetch-text',
        action='store_true',
        help='Fetch full text of citing papers (papers that cite primary papers after dandiset creation)'
    )
    parser.add_argument(
        '--max-citing-papers',
        type=int,
        default=10,
        help='Maximum number of citing papers to fetch per dandiset (default: 10)'
    )
    parser.add_argument(
        '--verbose', '-v',
        action='store_true',
        help='Enable verbose output for paper text fetching'
    )
    parser.add_argument(
        '--max-papers',
        type=int,
        default=None,
        help='Maximum total number of citing papers to fetch (default: all)'
    )
    parser.add_argument(
        '--max-dandisets',
        type=int,
        default=None,
        help='Cap to the first N dandisets (sorted by dandiset_id) for fast iteration. Default: all.'
    )
    parser.add_argument(
        '--cache-dir',
        type=str,
        default='/Volumes/microsd64/data/',
        help='Directory to store cached paper texts (default: /Volumes/microsd64/data/)'
    )

    args = parser.parse_args()

    # Load previous results for cache validation (if output file exists)
    previous_results = None
    if args.output:
        output_path = Path(args.output)
        if output_path.exists():
            try:
                with open(output_path) as f:
                    prev_data = json.load(f)
                previous_results = prev_data.get('results', [])
                print(f"Loaded {len(previous_results)} previous results for cache validation", file=sys.stderr)
            except (json.JSONDecodeError, OSError):
                pass

    # Find dandisets with paper relations
    results = find_dandisets_with_primary_papers(
        include_secondary=args.all_relations,
        show_progress=not args.no_progress,
        previous_results=previous_results,
        max_dandisets=args.max_dandisets,
    )

    # Optionally fetch citation counts
    if args.citations:
        results = add_citation_counts(results, show_progress=not args.no_progress)

    # Optionally fetch citing paper texts (requires --citations to have dandiset_created)
    if args.fetch_text:
        if not args.citations:
            print("Warning: --fetch-text requires --citations to get dandiset creation dates. Enabling --citations.", file=sys.stderr)
            results = add_citation_counts(results, show_progress=not args.no_progress)
        results = fetch_citing_paper_texts(
            results,
            max_citing_papers_per_dandiset=args.max_citing_papers,
            max_total_papers=args.max_papers,
            show_progress=not args.no_progress,
            verbose=args.verbose,
            cache_dir=args.cache_dir
        )

    if args.summary:
        # Print summary
        print(f"\nFound {len(results)} dandisets with primary paper relations")
        print("\nRelation type breakdown:")
        summary = get_relation_summary(results)
        for relation, count in summary.items():
            print(f"  {relation}: {count}")

        if args.citations:
            total_citations = sum(r.get('total_citations', 0) for r in results)
            total_citations_after = sum(r.get('total_citations_after_created', 0) for r in results)
            print(f"\nCitation summary:")
            print(f"  Total citations (all time): {total_citations}")
            print(f"  Citations after dandiset created: {total_citations_after}")

            # Show top dandisets by citations after creation
            sorted_results = sorted(results, key=lambda x: x.get('total_citations_after_created', 0) or 0, reverse=True)
            print(f"\nTop 20 dandisets by citations after creation (potential reuse):")
            for r in sorted_results[:20]:
                print(f"  {r['dandiset_id']}: {r.get('total_citations_after_created', 0)} citations after, {r.get('total_citations', 0)} total")

        print("\nDandisets:")
        for result in results:
            dois = [p['doi'] for p in result['paper_relations'] if p['doi']]
            doi_str = ', '.join(dois) if dois else '(no DOI found)'
            if args.citations:
                citations_after = result.get('total_citations_after_created', 0)
                citations_total = result.get('total_citations', 0)
                print(f"  {result['dandiset_id']}: {doi_str} ({citations_after} after / {citations_total} total)")
            else:
                print(f"  {result['dandiset_id']}: {doi_str}")
    else:
        # Output full results
        output = {
            'count': len(results),
            'relation_summary': get_relation_summary(results),
            'results': results,
        }

        # Add citation summary if citations were fetched
        if args.citations:
            total_citations = sum(r.get('total_citations', 0) for r in results)
            total_citations_after = sum(r.get('total_citations_after_created', 0) for r in results)
            output['citation_summary'] = {
                'total_citations': total_citations,
                'total_citations_after_dandiset_created': total_citations_after,
            }

        if args.output:
            with open(args.output, 'w') as f:
                json.dump(output, f, indent=2)
            print(f"Results written to {args.output}", file=sys.stderr)
        else:
            print(json.dumps(output, indent=2))


if __name__ == '__main__':
    main()
