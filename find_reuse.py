#!/usr/bin/env python3
"""
find_reuse.py - Find dataset references in scientific papers

This module extracts text from papers (given a DOI) and identifies references
to datasets on multiple archives (DANDI Archive, OpenNeuro, Figshare, PhysioNet, EBRAINS).

Usage:
    python find_reuse.py <DOI>
    python find_reuse.py --file dois.txt
    python find_reuse.py --discover -o results.json
    
Output is always JSON.
"""

import argparse
from datetime import datetime
import json
from pathlib import Path
import re
import sys
import time
from typing import Optional
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup
from tqdm import tqdm

# Try to import playwright for bioRxiv/medRxiv full text
try:
    from playwright.sync_api import sync_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False


# Cache directory for storing paper full text
CACHE_DIR = Path(__file__).parent / '.paper_cache'

# Cache directory for preprint-publication links
PREPRINT_CACHE_DIR = Path(__file__).parent / '.preprint_cache'


# Data descriptor journal DOI patterns - journals that primarily publish dataset descriptions
DATA_DESCRIPTOR_JOURNALS = {
    'Data (MDPI)': {
        'doi_prefix': '10.3390/data',
        'pattern': r'^10\.3390/data\d+',
    },
    'Scientific Data (Nature)': {
        'doi_prefix': '10.1038/s41597',
        'pattern': r'^10\.1038/s41597-',
    },
}


# Search terms for discovering papers - used to build Europe PMC queries
# Each archive has terms that will be combined with OR
# 'exclude' terms are used with NOT to filter false positives
ARCHIVE_SEARCH_TERMS = {
    'DANDI Archive': {
        'names': ['dandi', 'dandiarchive'],
        'urls': ['dandiarchive.org'],
        'search_terms': ['dandiset', 'DANDI Archive'],
        'doi_prefixes': ['10.48324/dandi'],
        'exclude': [
            'dandi bioscience', 'dandi bio', 'roberto dandi',  # Biotech company, not the archive
            'dandi march',  # Historical: India's salt march to Dandi
            'dandi district', 'lake dandi', 'meta robi',  # Geographic: Ethiopian locations
        ],
    },
    'OpenNeuro': {
        'names': ['openneuro'],
        'urls': ['openneuro.org'],
        'doi_prefixes': ['10.18112/openneuro'],
    },
    'EBRAINS': {
        'names': ['ebrains'],
        'urls': ['kg.ebrains.eu', 'ebrains.eu/datasets', 'data.ebrains.eu'],
        'doi_prefixes': ['10.25493'],
    },
    'Figshare': {
        'names': ['figshare'],
        'urls': ['figshare.com'],
        'doi_prefixes': ['10.6084/m9.figshare'],
    },
    'PhysioNet': {
        'names': ['physionet'],
        'urls': ['physionet.org'],
        'doi_prefixes': ['10.13026'],
    },
}


# Pattern to detect DANDI citations without explicit IDs
# Matches: "Title here" DANDI Archive  or  "Title here." DANDI Archive
# Supports both straight quotes (") and curly quotes (" ")
DANDI_CITATION_PATTERN = r'["\u201C\u201D]([^"\u201C\u201D]{20,})["\u201C\u201D]\s*\.?\s*DANDI\s*Archive'


# Archive reference patterns - dictionary of archive name to list of (pattern, pattern_type) tuples
ARCHIVE_PATTERNS = {
    'DANDI Archive': [
        # DOI format: 10.48324/dandi.000130 or 10.48324/dandi.000130/0.210914.1539
        (r'10\.48324/dandi\.(\d{6})(?:/[\d.]+)?', 'doi'),
        # URL formats
        (r'dandiarchive\.org/dandiset/(\d{6})', 'url'),
        (r'gui\.dandiarchive\.org/#/dandiset/(\d{6})', 'gui_url'),
        # Direct text mentions
        (r'DANDI:\s*(\d{6})', 'text_colon'),
        (r'DANDI\s+(\d{6})', 'text_space'),
        (r'dandiset\s+(\d{6})', 'dandiset_text'),
        (r'dandiset/(\d{6})', 'dandiset_path'),
        # DANDI archive identifier pattern (with colon, space, or comma separator)
        (r'DANDI(?:\s+archive)?(?:\s+identifier)?[,:\s]+(\d{6})', 'identifier'),
        # "Dandiarchive.org, ID:000221" format (seen in Cell papers)
        (r'(?:dandiarchive\.org|DANDI)[,\s]+ID[:\s]*(\d{6})', 'id_format'),
        # "DANDI ID#: 000978" format (seen in eNeuro papers)
        (r'DANDI\s+ID#[:\s]+(\d{6})', 'id_hash_format'),
        # "DANDI Archive ID: 000467" format (seen in Current Biology papers)
        (r'DANDI\s+Archive\s+ID[:\s]+(\d{6})', 'archive_id'),
        # "DANDI (dataset IDs: 000209 and 000020)" format (seen in iScience papers)
        (r'DANDI\s*\(dataset\s+IDs?:?\s*(\d{6})', 'dataset_ids_paren'),
        # Capture additional IDs after "and" within DANDI parentheses
        (r'DANDI\s*\([^)]*\band\s+(\d{6})\)', 'dataset_ids_paren_and'),
    ],
    'OpenNeuro': [
        # DOI format: 10.18112/openneuro.ds000001
        (r'10\.18112/openneuro\.(ds\d{6})', 'doi'),
        # URL formats
        (r'openneuro\.org/datasets/(ds\d{6})', 'url'),
        # Direct text mentions
        (r'OpenNeuro:\s*(ds\d{6})', 'text_colon'),
        (r'OpenNeuro\s+(ds\d{6})', 'text_space'),
        # Dataset ID patterns (ds followed by 6 digits)
        (r'\b(ds\d{6})\b', 'dataset_id'),
    ],
    'Figshare': [
        # DOI format: 10.6084/m9.figshare.9598406 or 10.6084/m9.figshare.9598406.v2
        (r'10\.6084/m9\.figshare\.(\d+)', 'doi'),
        # URL formats
        (r'figshare\.com/articles/[^/]+/(\d+)', 'url'),
        (r'figshare\.com/ndownloader/files/(\d+)', 'download_url'),
        # Direct text mentions
        (r'figshare:\s*(\d{6,})', 'text_colon'),
        (r'figshare\s+(\d{6,})', 'text_space'),
    ],
    'PhysioNet': [
        # DOI format: 10.13026/C2KX0P or 10.13026/xxxx-xxxx
        (r'10\.13026/([A-Za-z0-9-]+)', 'doi'),
        # URL formats - must be followed by version number, exclude common path segments
        (r'physionet\.org/content/([a-z][a-z0-9-]{2,})/\d', 'url'),
        (r'physionet\.org/physiobank/database/([a-z][a-z0-9-]{2,})', 'physiobank_url'),
        # Direct text mentions - require database name pattern (lowercase, longer names)
        (r'PhysioNet\s+database\s+([a-z][a-z0-9-]{3,})', 'text_database'),
    ],
    'EBRAINS': [
        # DOI format: 10.25493/xxxx-xxxx (EBRAINS Knowledge Graph DOIs)
        (r'10\.25493/([A-Za-z0-9-]+)', 'doi'),
        # Knowledge Graph URL formats - with optional entity type (Project, Dataset, etc.)
        (r'kg\.ebrains\.eu/search/instances/(?:[A-Za-z]+/)?([a-f0-9-]{36})', 'kg_url'),
        (r'search\.kg\.ebrains\.eu/instances/(?:[A-Za-z]+/)?([a-f0-9-]{36})', 'kg_search_url'),
        # Knowledge Graph "live" URLs with schema path (e.g., /search/live/minds/core/dataset/v1.0.0/)
        (r'kg\.ebrains\.eu/search/live/[a-z/._0-9]+/([a-f0-9-]{36})', 'kg_live_url'),
        # Dataset viewer URLs
        (r'data\.ebrains\.eu/datasets/([a-f0-9-]{36})', 'data_url'),
        # Direct text mentions with UUID
        (r'EBRAINS[:\s]+([a-f0-9-]{36})', 'text_uuid'),
        # EBRAINS dataset mentions with identifier
        (r'EBRAINS\s+(?:dataset|data\s*set)[:\s]+([A-Za-z0-9-]+)', 'text_dataset'),
    ],
}


class ArchiveFinder:
    """Find dataset references from multiple archives in papers."""
    
    def __init__(self, verbose: bool = False, use_cache: bool = True, follow_references: bool = False):
        self.verbose = verbose
        self.use_cache = use_cache
        self.follow_references = follow_references
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'ArchiveFinder/1.0 (https://github.com/dandi; mailto:ben.dichter@catalystneuro.com)'
        })
        
        # Ensure cache directories exist
        if self.use_cache:
            CACHE_DIR.mkdir(exist_ok=True)
            PREPRINT_CACHE_DIR.mkdir(exist_ok=True)
    
    def _get_cache_path(self, doi: str) -> Path:
        """Get cache file path for a DOI."""
        # Sanitize DOI for use as filename (replace / and other unsafe chars)
        safe_doi = doi.replace('/', '_').replace(':', '_').replace('\\', '_')
        return CACHE_DIR / f"{safe_doi}.json"
    
    def _get_cached_text(self, doi: str) -> Optional[tuple[str, str]]:
        """Get cached paper text if available."""
        if not self.use_cache:
            return None
        
        cache_path = self._get_cache_path(doi)
        if cache_path.exists():
            try:
                with open(cache_path, 'r') as f:
                    data = json.load(f)
                    self.log(f"Cache hit for DOI: {doi}")
                    return data.get('text'), data.get('source', '')
            except Exception as e:
                self.log(f"Cache read error: {e}")
        return None
    
    def _cache_text(self, doi: str, text: str, source: str):
        """Cache paper text."""
        if not self.use_cache:
            return
        
        cache_path = self._get_cache_path(doi)
        try:
            with open(cache_path, 'w') as f:
                json.dump({
                    'doi': doi,
                    'text': text,
                    'source': source,
                    'cached_at': datetime.now().isoformat()
                }, f)
            self.log(f"Cached text for DOI: {doi}")
        except Exception as e:
            self.log(f"Cache write error: {e}")
    
    def is_preprint_doi(self, doi: str) -> bool:
        """Check if a DOI is from a preprint server (bioRxiv/medRxiv)."""
        return doi.startswith('10.1101/')
    
    def _get_preprint_cache_path(self, doi: str) -> Path:
        """Get cache file path for preprint lookup."""
        safe_doi = doi.replace('/', '_').replace(':', '_').replace('\\', '_')
        return PREPRINT_CACHE_DIR / f"{safe_doi}.json"
    
    def get_published_version(self, preprint_doi: str) -> Optional[dict]:
        """
        Look up the published version of a bioRxiv/medRxiv preprint.
        
        Uses the bioRxiv API: https://api.biorxiv.org/pubs/biorxiv/{doi}
        
        Returns dict with published_doi, published_journal, published_date if found,
        or None if no published version exists.
        """
        if not self.is_preprint_doi(preprint_doi):
            return None
        
        cache_path = self._get_preprint_cache_path(preprint_doi)
        
        # Check cache first
        if self.use_cache and cache_path.exists():
            try:
                with open(cache_path, 'r') as f:
                    data = json.load(f)
                    self.log(f"Preprint cache hit for DOI: {preprint_doi}")
                    return data.get('published_info')
            except Exception as e:
                self.log(f"Preprint cache read error: {e}")
        
        self.log(f"Looking up published version of preprint: {preprint_doi}")
        
        # Try bioRxiv API first, then medRxiv
        for server in ['biorxiv', 'medrxiv']:
            url = f"https://api.biorxiv.org/pubs/{server}/{preprint_doi}"
            
            try:
                resp = self.session.get(url, timeout=30)
                if resp.status_code == 200:
                    data = resp.json()
                    
                    if data.get('collection') and len(data['collection']) > 0:
                        pub_info = data['collection'][0]
                        published_doi = pub_info.get('published_doi')
                        
                        if published_doi:
                            result = {
                                'published_doi': published_doi,
                                'published_journal': pub_info.get('published_journal', ''),
                                'published_date': pub_info.get('published_date', ''),
                                'preprint_title': pub_info.get('preprint_title', ''),
                            }
                            
                            # Cache the result
                            if self.use_cache:
                                try:
                                    with open(cache_path, 'w') as f:
                                        json.dump({
                                            'preprint_doi': preprint_doi,
                                            'published_info': result,
                                            'cached_at': datetime.now().isoformat()
                                        }, f)
                                except Exception as e:
                                    self.log(f"Preprint cache write error: {e}")
                            
                            self.log(f"Found published version: {published_doi} in {result['published_journal']}")
                            return result
                
                time.sleep(0.3)  # Rate limiting
                
            except Exception as e:
                self.log(f"bioRxiv API error for {server}: {e}")
        
        # Cache negative result (no published version found)
        if self.use_cache:
            try:
                with open(cache_path, 'w') as f:
                    json.dump({
                        'preprint_doi': preprint_doi,
                        'published_info': None,
                        'cached_at': datetime.now().isoformat()
                    }, f)
            except Exception as e:
                self.log(f"Preprint cache write error: {e}")
        
        self.log(f"No published version found for: {preprint_doi}")
        return None
    
    def find_preprint_duplicates(self, results: list[dict]) -> dict:
        """
        Find duplicate entries where both preprint and published versions exist.
        
        Args:
            results: List of paper results with DOIs
            
        Returns dict with:
            - duplicates: List of (preprint_doi, published_doi) tuples found in results
            - preprint_to_published: Mapping of preprint DOIs to their published versions
            - published_to_preprint: Mapping of published DOIs to their preprint versions
        """
        # Get all DOIs from results
        all_dois = set(r['doi'] for r in results if r.get('doi'))
        
        # Find preprints and look up their published versions
        preprint_to_published = {}
        published_to_preprint = {}
        duplicates = []
        
        for result in results:
            doi = result.get('doi')
            if doi and self.is_preprint_doi(doi):
                pub_info = self.get_published_version(doi)
                if pub_info and pub_info.get('published_doi'):
                    pub_doi = pub_info['published_doi']
                    preprint_to_published[doi] = pub_info
                    published_to_preprint[pub_doi] = {
                        'preprint_doi': doi,
                        'preprint_title': pub_info.get('preprint_title', '')
                    }
                    
                    # Check if both are in results
                    if pub_doi in all_dois:
                        duplicates.append((doi, pub_doi))
                        self.log(f"Found duplicate: {doi} -> {pub_doi}")
        
        return {
            'duplicates': duplicates,
            'preprint_to_published': preprint_to_published,
            'published_to_preprint': published_to_preprint,
        }
    
    def deduplicate_results(self, results: list[dict], papers_without_datasets: list[dict] | None = None, prefer_published: bool = True) -> tuple[list[dict], list[dict] | None, dict]:
        """
        Remove duplicate entries where both preprint and published versions exist.
        
        This checks for duplicates both within the results array and across
        results and papers_without_datasets arrays.
        
        Args:
            results: List of paper results with dataset references
            papers_without_datasets: Optional list of papers without dataset references
            prefer_published: If True, keep published version; if False, keep preprint
            
        Returns tuple of:
            - Deduplicated results list
            - Deduplicated papers_without_datasets list (or None if not provided)
            - Deduplication metadata with info about removed duplicates
        """
        dup_info = self.find_preprint_duplicates(results)
        
        # Build set of DOIs to remove from results
        dois_to_remove_from_results = set()
        removed_entries = []
        
        # Handle duplicates within results array
        for preprint_doi, published_doi in dup_info['duplicates']:
            if prefer_published:
                dois_to_remove_from_results.add(preprint_doi)
                removed_entries.append({
                    'removed_doi': preprint_doi,
                    'kept_doi': published_doi,
                    'reason': 'preprint_has_published_version',
                    'removed_from': 'results'
                })
            else:
                dois_to_remove_from_results.add(published_doi)
                removed_entries.append({
                    'removed_doi': published_doi,
                    'kept_doi': preprint_doi,
                    'reason': 'published_has_preprint_version',
                    'removed_from': 'results'
                })
        
        # Handle cross-array duplicates: preprints in papers_without_datasets
        # whose published versions are in results
        dois_to_remove_from_no_datasets = set()
        cross_array_duplicates = []
        
        if papers_without_datasets:
            # Get all DOIs from results
            results_dois = set(r['doi'] for r in results if r.get('doi'))
            
            for paper in papers_without_datasets:
                doi = paper.get('doi')
                if doi and self.is_preprint_doi(doi):
                    pub_info = self.get_published_version(doi)
                    if pub_info and pub_info.get('published_doi'):
                        pub_doi = pub_info['published_doi']
                        
                        # Check if published version is in results
                        if pub_doi in results_dois:
                            cross_array_duplicates.append((doi, pub_doi))
                            
                            if prefer_published:
                                # Remove preprint from papers_without_datasets
                                dois_to_remove_from_no_datasets.add(doi)
                                removed_entries.append({
                                    'removed_doi': doi,
                                    'kept_doi': pub_doi,
                                    'reason': 'preprint_in_no_datasets_has_published_in_results',
                                    'removed_from': 'papers_without_datasets'
                                })
                                self.log(f"Cross-array duplicate: removing {doi} from papers_without_datasets (published version {pub_doi} in results)")
                            else:
                                # Remove published from results
                                dois_to_remove_from_results.add(pub_doi)
                                removed_entries.append({
                                    'removed_doi': pub_doi,
                                    'kept_doi': doi,
                                    'reason': 'published_in_results_has_preprint_in_no_datasets',
                                    'removed_from': 'results'
                                })
                                self.log(f"Cross-array duplicate: removing {pub_doi} from results (preprint version {doi} preferred)")
        
        # Filter results
        deduplicated_results = []
        for result in results:
            doi = result.get('doi')
            if doi not in dois_to_remove_from_results:
                # Add preprint link info to published papers
                if doi in dup_info['published_to_preprint']:
                    result = result.copy()
                    result['preprint_doi'] = dup_info['published_to_preprint'][doi]['preprint_doi']
                # Add published link info to preprints
                elif doi in dup_info['preprint_to_published']:
                    result = result.copy()
                    pub_info = dup_info['preprint_to_published'][doi]
                    result['published_doi'] = pub_info['published_doi']
                    result['published_journal'] = pub_info.get('published_journal', '')
                deduplicated_results.append(result)
        
        # Filter papers_without_datasets and check published versions for dataset refs
        deduplicated_no_datasets = None
        promoted_to_results = []  # Papers promoted from no_datasets to results
        
        # Get all DOIs from deduplicated results for checking
        all_results_dois = set(r['doi'] for r in deduplicated_results if r.get('doi'))
        
        if papers_without_datasets is not None:
            deduplicated_no_datasets = []
            for paper in papers_without_datasets:
                doi = paper.get('doi')
                if doi in dois_to_remove_from_no_datasets:
                    continue  # Skip removed duplicates
                
                # For preprints without dataset refs, check if published version has refs
                if doi and self.is_preprint_doi(doi):
                    pub_info = self.get_published_version(doi)
                    if pub_info and pub_info.get('published_doi'):
                        pub_doi = pub_info['published_doi']
                        
                        # Skip if published version already in results
                        if pub_doi not in all_results_dois:
                            # Analyze the published version for dataset references
                            self.log(f"Analyzing published version {pub_doi} for preprint {doi}")
                            pub_result, _ = self.find_references(pub_doi)
                            
                            if pub_result.get('archives'):
                                # Published version has dataset refs - add to results
                                pub_result['preprint_doi'] = doi
                                pub_result['title'] = paper.get('title', pub_info.get('preprint_title', ''))
                                pub_result['published_journal'] = pub_info.get('published_journal', '')
                                promoted_to_results.append(pub_result)
                                
                                # Track this promotion
                                removed_entries.append({
                                    'removed_doi': doi,
                                    'kept_doi': pub_doi,
                                    'reason': 'preprint_replaced_by_published_with_datasets',
                                    'removed_from': 'papers_without_datasets',
                                    'added_to': 'results'
                                })
                                self.log(f"Promoted {pub_doi} to results (has datasets), replacing preprint {doi}")
                                continue  # Don't add preprint to deduplicated_no_datasets
                            else:
                                # Published version also has no dataset refs - annotate preprint
                                paper = paper.copy()
                                paper['published_doi'] = pub_doi
                                paper['published_journal'] = pub_info.get('published_journal', '')
                        else:
                            # Published version already in results - remove preprint, don't add to no_datasets
                            removed_entries.append({
                                'removed_doi': doi,
                                'kept_doi': pub_doi,
                                'reason': 'preprint_in_no_datasets_has_published_in_results',
                                'removed_from': 'papers_without_datasets'
                            })
                            self.log(f"Removing preprint {doi} from papers_without_datasets (published version {pub_doi} already in results)")
                            continue  # Don't add preprint to deduplicated_no_datasets
                
                deduplicated_no_datasets.append(paper)
        
        # Add promoted papers to results
        deduplicated_results.extend(promoted_to_results)
        
        metadata = {
            'removed': removed_entries,
            'total_duplicates_found': len(dup_info['duplicates']) + len(cross_array_duplicates),
            'within_results_duplicates': len(dup_info['duplicates']),
            'cross_array_duplicates': len(cross_array_duplicates),
            'preprint_links': dup_info
        }
        
        self.log(f"Removed {len(removed_entries)} duplicate entries ({len(dup_info['duplicates'])} within results, {len(cross_array_duplicates)} cross-array)")
        return deduplicated_results, deduplicated_no_datasets, metadata
    
    def log(self, message: str):
        """Print message if verbose mode is enabled."""
        if self.verbose:
            print(f"[DEBUG] {message}", file=sys.stderr)
    
    def find_archive_ids(self, text: str, archive_name: str) -> list[dict]:
        """
        Find all dataset IDs for a specific archive in the given text.
        
        Returns list of matches with id, pattern type, matched string, and full DOI if applicable.
        """
        matches = []
        seen_ids = set()
        
        patterns = ARCHIVE_PATTERNS.get(archive_name, [])
        for pattern, pattern_type in patterns:
            for match in re.finditer(pattern, text, re.IGNORECASE):
                dataset_id = match.group(1)
                matched_str = match.group(0)
                
                if dataset_id not in seen_ids:
                    match_info = {
                        'id': dataset_id,
                        'pattern_type': pattern_type,
                        'matched_string': matched_str
                    }
                    
                    # Include full DOI when pattern type is 'doi'
                    if pattern_type == 'doi':
                        match_info['doi'] = matched_str
                    
                    matches.append(match_info)
                    seen_ids.add(dataset_id)
        
        return matches
    
    def find_all_archive_references(self, text: str) -> dict[str, list[dict]]:
        """
        Find dataset references from all configured archives.
        
        Returns dict mapping archive name to list of matches.
        """
        results = {}
        for archive_name in ARCHIVE_PATTERNS:
            matches = self.find_archive_ids(text, archive_name)
            if matches:
                results[archive_name] = matches
        return results
    
    def find_unlinked_dandi_citations(self, text: str) -> list[str]:
        """
        Find potential DANDI citations that don't have explicit IDs.
        
        These are citations like:
        "Dataset Title Here." DANDI Archive.
        
        Returns list of citation titles that should be searched in DANDI.
        """
        matches = re.findall(DANDI_CITATION_PATTERN, text, re.IGNORECASE)
        return matches
    
    def search_dandi_api(self, query: str, limit: int = 10) -> list[dict]:
        """
        Search the DANDI Archive API for datasets matching a query.
        
        Args:
            query: Search query (e.g., dataset title or keywords)
            limit: Maximum number of results to return
            
        Returns list of matching datasets with id, name, and other metadata.
        """
        # Normalize query: replace en-dashes, em-dashes, and other special chars with spaces
        normalized_query = query.replace('–', ' ').replace('—', ' ').replace('-', ' ')
        # Remove trailing punctuation that might interfere with search
        normalized_query = normalized_query.rstrip('.,;:!?')
        # Remove extra whitespace
        normalized_query = ' '.join(normalized_query.split())
        
        self.log(f"Searching DANDI API for: {normalized_query[:50]}...")
        
        url = "https://api.dandiarchive.org/api/dandisets/"
        params = {
            'search': normalized_query,
            'page_size': limit,
            'draft': 'true',
            'empty': 'false',
            'embargoed': 'false',
        }
        
        try:
            resp = self.session.get(url, params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            
            results = []
            for item in data.get('results', []):
                dandiset_id = item.get('identifier', '')
                # Get the most recent version info
                most_recent = item.get('most_recent_published_version') or item.get('draft_version', {})
                
                results.append({
                    'id': dandiset_id,
                    'name': most_recent.get('name', ''),
                    'version': most_recent.get('version', 'draft'),
                    'asset_count': most_recent.get('asset_count', 0),
                    'size': most_recent.get('size', 0),
                    'url': f"https://dandiarchive.org/dandiset/{dandiset_id}",
                })
            
            self.log(f"Found {len(results)} DANDI datasets")
            return results
            
        except Exception as e:
            self.log(f"DANDI API search error: {e}")
            return []
    
    def resolve_unlinked_dandi_citations(self, text: str) -> list[dict]:
        """
        Find and resolve DANDI citations that don't have explicit IDs.
        
        Extracts citation titles mentioning "DANDI Archive" and searches
        the DANDI API to find matching datasets.
        
        Returns list of resolved citations with the original title and matched dataset(s).
        """
        resolved = []
        
        # Find potential citations
        citation_titles = self.find_unlinked_dandi_citations(text)
        
        if not citation_titles:
            return resolved
        
        self.log(f"Found {len(citation_titles)} potential unlinked DANDI citations")
        
        for title in citation_titles:
            # Search DANDI API for this title
            matches = self.search_dandi_api(title, limit=5)
            
            if matches:
                # Check if any match has high similarity to the citation title
                best_matches = []
                title_lower = title.lower()
                
                for match in matches:
                    match_name_lower = match['name'].lower()
                    # Check for substantial overlap in words
                    title_words = set(title_lower.split())
                    match_words = set(match_name_lower.split())
                    
                    # Calculate word overlap
                    common_words = title_words & match_words
                    # Ignore common words that aren't meaningful
                    stopwords = {'the', 'a', 'an', 'in', 'of', 'to', 'and', 'for', 'with', 'on', 'at'}
                    meaningful_common = common_words - stopwords
                    meaningful_title = title_words - stopwords
                    
                    if meaningful_title and len(meaningful_common) >= len(meaningful_title) * 0.5:
                        best_matches.append(match)
                
                if best_matches:
                    resolved.append({
                        'citation_title': title,
                        'matched_datasets': best_matches,
                        'pattern_type': 'unlinked_citation',
                    })
            
            # Rate limiting
            time.sleep(0.3)
        
        return resolved
    
    def get_pmcid_for_doi(self, doi: str) -> Optional[str]:
        """
        Get PMCID for a DOI using NCBI ID converter.
        
        Returns the PMCID if found, None otherwise.
        """
        # Use NCBI ID converter which is more reliable than Europe PMC for some papers
        converter_url = "https://www.ncbi.nlm.nih.gov/pmc/utils/idconv/v1.0/"
        params = {
            'ids': doi,
            'format': 'json',
            'tool': 'dandi_finder',
            'email': 'info@dandiarchive.org'
        }
        
        try:
            resp = self.session.get(converter_url, params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            
            records = data.get('records', [])
            if records and records[0].get('pmcid'):
                return records[0]['pmcid']
        except Exception as e:
            self.log(f"Error getting PMCID: {e}")
        
        return None
    
    def get_text_from_europe_pmc(self, doi: str) -> tuple[Optional[str], Optional[str]]:
        """
        Get full text from Europe PMC.
        
        Supports both PMC articles (via PMCID) and preprints (via PPR ID).
        Abstract-only results are skipped since DANDI refs are rarely in abstracts.
        
        Returns tuple of (text, pmcid) - pmcid is returned even if text fetch fails,
        for potential Playwright fallback.
        """
        self.log(f"Trying Europe PMC for DOI: {doi}")
        pmcid_found = None
        
        # Search for the article by DOI
        search_url = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
        params = {
            'query': f'DOI:"{doi}"',
            'format': 'json',
            'resultType': 'core'
        }
        
        try:
            resp = self.session.get(search_url, params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            
            if data.get('resultList', {}).get('result'):
                result = data['resultList']['result'][0]
                pmcid = result.get('pmcid')
                
                # Try PMCID first (for published articles)
                if pmcid:
                    pmcid_found = pmcid  # Save for potential Playwright fallback
                    self.log(f"Found PMCID: {pmcid}, fetching full text")
                    fulltext_url = f"https://www.ebi.ac.uk/europepmc/webservices/rest/{pmcid}/fullTextXML"
                    
                    try:
                        ft_resp = self.session.get(fulltext_url, timeout=30)
                        if ft_resp.status_code == 200:
                            soup = BeautifulSoup(ft_resp.content, 'lxml-xml')
                            # Extract all text content
                            text = soup.get_text(separator=' ', strip=True)
                            
                            # Also extract hyperlink URLs (ext-link elements contain href attributes)
                            # These often contain DANDI URLs that aren't in the visible text
                            ext_links = []
                            for link in soup.find_all('ext-link'):
                                href = link.get('xlink:href', '')
                                if href:
                                    ext_links.append(href)
                            
                            if ext_links:
                                self.log(f"Found {len(ext_links)} hyperlinks in XML")
                                text = text + '\n\n[HYPERLINKS]\n' + '\n'.join(ext_links)
                            
                            return text, pmcid_found
                    except Exception as e:
                        self.log(f"Error fetching full text: {e}")
                
                # Try PPR ID for preprints (bioRxiv, medRxiv, etc.)
                full_text_ids = result.get('fullTextIdList', {}).get('fullTextId', [])
                for ft_id in full_text_ids:
                    if ft_id.startswith('PPR'):
                        self.log(f"Found preprint ID: {ft_id}, fetching full text")
                        fulltext_url = f"https://www.ebi.ac.uk/europepmc/webservices/rest/{ft_id}/fullTextXML"
                        
                        try:
                            ft_resp = self.session.get(fulltext_url, timeout=30)
                            if ft_resp.status_code == 200:
                                soup = BeautifulSoup(ft_resp.content, 'lxml-xml')
                                text = soup.get_text(separator=' ', strip=True)
                                
                                # Also extract hyperlink URLs from preprint XML
                                ext_links = []
                                for link in soup.find_all('ext-link'):
                                    href = link.get('xlink:href', '')
                                    if href:
                                        ext_links.append(href)
                                
                                if ext_links:
                                    self.log(f"Found {len(ext_links)} hyperlinks in preprint XML")
                                    text = text + '\n\n[HYPERLINKS]\n' + '\n'.join(ext_links)
                                
                                return text, pmcid_found
                        except Exception as e:
                            self.log(f"Error fetching preprint full text: {e}")
                
                if not pmcid and not full_text_ids:
                    self.log("No PMCID or preprint ID available, skipping abstract-only result")
                    
        except Exception as e:
            self.log(f"Europe PMC error: {e}")
        
        return None, pmcid_found
    
    def get_text_from_pmc(self, doi: str) -> tuple[Optional[str], Optional[str]]:
        """
        Get full text from NCBI PubMed Central.
        
        Returns tuple of (text, pmcid) - pmcid is returned for potential Playwright fallback.
        """
        self.log(f"Trying NCBI PMC for DOI: {doi}")
        
        # First, convert DOI to PMCID using ID converter
        converter_url = "https://www.ncbi.nlm.nih.gov/pmc/utils/idconv/v1.0/"
        params = {
            'ids': doi,
            'format': 'json',
            'tool': 'dandi_finder',
            'email': 'info@dandiarchive.org'
        }
        
        try:
            resp = self.session.get(converter_url, params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            
            records = data.get('records', [])
            if records and records[0].get('pmcid'):
                pmcid = records[0]['pmcid']
                self.log(f"Found PMCID: {pmcid}")
                
                # Fetch full text XML
                efetch_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
                params = {
                    'db': 'pmc',
                    'id': pmcid,
                    'rettype': 'xml',
                    'tool': 'dandi_finder',
                    'email': 'info@dandiarchive.org'
                }
                
                ft_resp = self.session.get(efetch_url, params=params, timeout=30)
                if ft_resp.status_code == 200:
                    soup = BeautifulSoup(ft_resp.content, 'lxml-xml')
                    return soup.get_text(separator=' ', strip=True), pmcid
                    
        except Exception as e:
            self.log(f"NCBI PMC error: {e}")
        
        return None, None
    
    def get_text_from_crossref(self, doi: str) -> Optional[str]:
        """
        Get metadata from CrossRef (title, abstract, references).
        
        This is a fallback that provides limited text.
        """
        self.log(f"Trying CrossRef for DOI: {doi}")
        
        url = f"https://api.crossref.org/works/{quote(doi, safe='')}"
        
        try:
            resp = self.session.get(url, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            
            message = data.get('message', {})
            text_parts = []
            
            # Title
            if message.get('title'):
                text_parts.extend(message['title'])
            
            # Abstract
            if message.get('abstract'):
                # Remove HTML tags from abstract
                abstract = BeautifulSoup(message['abstract'], 'html.parser').get_text()
                text_parts.append(abstract)
            
            # References (might contain DANDI DOIs)
            for ref in message.get('reference', []):
                if ref.get('DOI'):
                    text_parts.append(ref['DOI'])
                if ref.get('unstructured'):
                    text_parts.append(ref['unstructured'])
            
            if text_parts:
                return '\n\n'.join(text_parts)
                
        except Exception as e:
            self.log(f"CrossRef error: {e}")
        
        return None
    
    def get_paper_metadata(self, doi: str) -> dict:
        """
        Get paper metadata from CrossRef (journal name, publication date).
        
        Returns dict with journal and date fields.
        """
        url = f"https://api.crossref.org/works/{quote(doi, safe='')}"
        
        result = {
            'journal': None,
            'date': None,
        }
        
        try:
            resp = self.session.get(url, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            
            message = data.get('message', {})
            
            # Get journal name (try container-title first, then publisher)
            container_title = message.get('container-title', [])
            if container_title:
                result['journal'] = container_title[0]
            elif message.get('publisher'):
                result['journal'] = message['publisher']
            
            # Get publication date (prefer published-print, then published-online, then created)
            date_parts = None
            for date_field in ['published-print', 'published-online', 'published', 'created']:
                if message.get(date_field, {}).get('date-parts'):
                    date_parts = message[date_field]['date-parts'][0]
                    break
            
            if date_parts:
                # Format as YYYY-MM-DD (or partial if not all parts available)
                if len(date_parts) >= 3:
                    result['date'] = f"{date_parts[0]:04d}-{date_parts[1]:02d}-{date_parts[2]:02d}"
                elif len(date_parts) >= 2:
                    result['date'] = f"{date_parts[0]:04d}-{date_parts[1]:02d}"
                elif len(date_parts) >= 1:
                    result['date'] = f"{date_parts[0]:04d}"
                    
        except Exception as e:
            self.log(f"CrossRef metadata error: {e}")
        
        return result
    
    def get_reference_dois(self, doi: str) -> list[dict]:
        """
        Extract DOIs of all references from a paper using CrossRef.
        
        Returns list of dicts with DOI, title (if available), and journal info.
        """
        self.log(f"Getting reference DOIs for: {doi}")
        
        url = f"https://api.crossref.org/works/{quote(doi, safe='')}"
        
        try:
            resp = self.session.get(url, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            
            message = data.get('message', {})
            references = []
            
            for ref in message.get('reference', []):
                ref_doi = ref.get('DOI')
                if ref_doi:
                    references.append({
                        'doi': ref_doi,
                        'unstructured': ref.get('unstructured', ''),
                        'article_title': ref.get('article-title', ''),
                        'journal_title': ref.get('journal-title', ''),
                    })
            
            self.log(f"Found {len(references)} reference DOIs")
            return references
            
        except Exception as e:
            self.log(f"CrossRef reference extraction error: {e}")
            return []
    
    def is_data_descriptor_doi(self, doi: str) -> Optional[str]:
        """
        Check if a DOI is from a data descriptor journal.
        
        Returns the journal name if it's a data descriptor, None otherwise.
        """
        for journal_name, info in DATA_DESCRIPTOR_JOURNALS.items():
            if re.match(info['pattern'], doi):
                return journal_name
        return None
    
    def find_data_descriptor_citations(self, doi: str) -> list[dict]:
        """
        Find citations to data descriptor papers in a paper's references.
        
        Returns list of data descriptor DOIs with journal info.
        """
        references = self.get_reference_dois(doi)
        data_descriptors = []
        
        for ref in references:
            ref_doi = ref['doi']
            journal = self.is_data_descriptor_doi(ref_doi)
            if journal:
                self.log(f"Found data descriptor citation: {ref_doi} ({journal})")
                data_descriptors.append({
                    'doi': ref_doi,
                    'journal': journal,
                    'title': ref.get('article_title') or ref.get('unstructured', '')[:100],
                })
        
        return data_descriptors
    
    def follow_data_descriptor_chain(self, doi: str) -> list[dict]:
        """
        Follow citations to data descriptor papers and extract datasets from them.
        
        Returns list of indirect references with the data descriptor info and datasets found.
        """
        indirect_refs = []
        
        # Find data descriptor citations
        data_descriptors = self.find_data_descriptor_citations(doi)
        
        for dd in data_descriptors:
            dd_doi = dd['doi']
            self.log(f"Following data descriptor: {dd_doi}")
            
            # Get the data descriptor's text and find datasets
            dd_text, dd_source, dd_from_cache = self.get_paper_text(dd_doi)
            
            if dd_text:
                # Find archive references in the data descriptor
                dd_archives = self.find_all_archive_references(dd_text)
                
                if dd_archives:
                    indirect_refs.append({
                        'via_data_descriptor': {
                            'doi': dd_doi,
                            'journal': dd['journal'],
                            'title': dd['title'],
                            'source': dd_source,
                        },
                        'datasets': {
                            archive: {
                                'dataset_ids': list(set(m['id'] for m in matches)),
                                'matches': matches
                            }
                            for archive, matches in dd_archives.items()
                        }
                    })
            
            # Rate limiting - only if we made API calls (not from cache)
            if not dd_from_cache:
                time.sleep(0.5)
        
        return indirect_refs
    
    def get_text_from_pmc_playwright(self, pmcid: str) -> Optional[str]:
        """
        Get full text from PMC using Playwright.
        
        The PMC API sometimes returns incomplete text (e.g., author manuscripts
        missing data availability sections). This method scrapes the full HTML
        page which often contains more complete content.
        
        Args:
            pmcid: The PMC ID (e.g., 'PMC11093107')
            
        Requires: pip install playwright && playwright install chromium
        """
        if not PLAYWRIGHT_AVAILABLE:
            self.log("Playwright not available, skipping PMC browser fetch")
            return None
        
        self.log(f"Trying PMC via Playwright for PMCID: {pmcid}")
        
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(
                    headless=True,
                    args=['--disable-blink-features=AutomationControlled']
                )
                context = browser.new_context(
                    user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
                )
                page = context.new_page()
                
                url = f'https://pmc.ncbi.nlm.nih.gov/articles/{pmcid}/'
                self.log(f"Navigating to: {url}")
                
                page.goto(url, wait_until='domcontentloaded', timeout=30000)
                page.wait_for_timeout(5000)  # Wait for page to fully render
                
                # Check if page loaded successfully
                title = page.title()
                if 'not found' in title.lower() or '404' in title or 'error' in title.lower():
                    self.log(f"Page not found for {pmcid}")
                    browser.close()
                    return None
                
                # Get full page text
                text = page.inner_text('body')
                
                if text and len(text) > 1000:
                    self.log(f"Got {len(text)} chars from PMC via Playwright")
                    browser.close()
                    return text
                
                browser.close()
                
        except Exception as e:
            self.log(f"PMC Playwright error: {e}")
        
        return None
    
    def get_text_from_biorxiv_playwright(self, doi: str) -> Optional[str]:
        """
        Get full text from bioRxiv/medRxiv using Playwright to bypass Cloudflare.
        
        This method uses a headless browser to fetch the full text from
        bioRxiv or medRxiv preprints, which are protected by Cloudflare.
        
        Requires: pip install playwright && playwright install chromium
        """
        if not PLAYWRIGHT_AVAILABLE:
            self.log("Playwright not available, skipping bioRxiv browser fetch")
            return None
        
        if not self.is_preprint_doi(doi):
            return None
        
        self.log(f"Trying bioRxiv/medRxiv via Playwright for DOI: {doi}")
        
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(
                    headless=True,
                    args=['--disable-blink-features=AutomationControlled']
                )
                context = browser.new_context(
                    user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
                )
                page = context.new_page()
                
                # Try bioRxiv first, then medRxiv
                for server in ['biorxiv', 'medrxiv']:
                    url = f'https://www.{server}.org/content/{doi}v1.full'
                    self.log(f"Navigating to: {url}")
                    
                    try:
                        page.goto(url, wait_until='domcontentloaded', timeout=30000)
                        
                        # Wait for Cloudflare challenge to complete
                        page.wait_for_timeout(10000)
                        
                        # Check if page loaded successfully
                        title = page.title()
                        if 'not found' in title.lower() or '404' in title:
                            self.log(f"Page not found on {server}")
                            continue
                        
                        # Try to find article content, fall back to body
                        article = page.query_selector('article')
                        if article:
                            text = article.inner_text()
                        else:
                            # bioRxiv may not have an article element, use body
                            text = page.inner_text('body')
                        
                        if text and len(text) > 1000:  # Minimum content threshold
                            self.log(f"Got {len(text)} chars from {server} via Playwright")
                            browser.close()
                            return text
                    except Exception as e:
                        self.log(f"Error fetching from {server}: {e}")
                        continue
                
                browser.close()
                
        except Exception as e:
            self.log(f"Playwright error: {e}")
        
        return None
    
    def get_text_from_publisher_playwright(self, doi: str) -> Optional[str]:
        """
        Scrape full text from publisher's HTML page using Playwright.
        
        This is a fallback for when regular HTTP requests fail (403 Forbidden, etc).
        Works with many publishers that block automated requests.
        
        Requires: pip install playwright && playwright install chromium
        """
        if not PLAYWRIGHT_AVAILABLE:
            self.log("Playwright not available, skipping publisher browser fetch")
            return None
        
        self.log(f"Trying publisher HTML via Playwright for DOI: {doi}")
        
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(
                    headless=True,
                    args=['--disable-blink-features=AutomationControlled']
                )
                context = browser.new_context(
                    user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
                )
                page = context.new_page()
                
                # Resolve DOI to get the actual publisher URL
                doi_url = f'https://doi.org/{doi}'
                self.log(f"Navigating to: {doi_url}")
                
                page.goto(doi_url, wait_until='domcontentloaded', timeout=30000)
                page.wait_for_timeout(5000)  # Wait for page to fully render
                
                # Check if page loaded successfully
                title = page.title()
                if 'not found' in title.lower() or '404' in title or 'error' in title.lower():
                    self.log(f"Page not found for {doi}")
                    browser.close()
                    return None
                
                # Get full page text
                text = page.inner_text('body')
                
                if text and len(text) > 1000:
                    self.log(f"Got {len(text)} chars from publisher via Playwright")
                    browser.close()
                    return text
                
                browser.close()
                
        except Exception as e:
            self.log(f"Publisher Playwright error: {e}")
        
        return None
    
    def get_text_from_publisher_html(self, doi: str) -> Optional[str]:
        """
        Scrape full text from publisher's open access HTML page.
        
        Works with Nature, Springer, Cell, Elsevier, and other open access papers.
        Falls back to Playwright if regular HTTP request fails.
        """
        self.log(f"Trying publisher HTML for DOI: {doi}")
        
        # Resolve DOI to get the actual publisher URL
        doi_url = f"https://doi.org/{doi}"
        
        try:
            # Use browser-like headers
            headers = {
                'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.5',
            }
            
            resp = self.session.get(doi_url, headers=headers, timeout=30, allow_redirects=True)
            resp.raise_for_status()
            
            # Check if this is an HTML page (not a PDF or other binary)
            content_type = resp.headers.get('content-type', '')
            if 'text/html' not in content_type:
                self.log(f"Not HTML content: {content_type}")
                return None
            
            soup = BeautifulSoup(resp.content, 'html.parser')
            
            # Remove script and style elements
            for element in soup(['script', 'style', 'nav', 'header', 'footer']):
                element.decompose()
            
            # Try to find the article content
            # Different publishers use different structures
            article_content = None
            
            # Try common article selectors
            selectors = [
                'article',
                '[role="main"]',
                '.article-content',
                '.article__body',
                '#article-body',
                '.c-article-body',  # Nature
                '.article-section',
                'main',
            ]
            
            for selector in selectors:
                article_content = soup.select_one(selector)
                if article_content:
                    break
            
            if article_content:
                text = article_content.get_text(separator=' ', strip=True)
            else:
                # Fall back to full page text
                text = soup.get_text(separator=' ', strip=True)
            
            # Only return if we got substantial content
            if len(text) > 1000:  # Minimum content threshold
                self.log(f"Got {len(text)} chars from publisher HTML")
                return text
            else:
                self.log(f"Insufficient content from HTML ({len(text)} chars)")
                # Try Playwright as fallback for JavaScript-rendered pages
                self.log("Trying Playwright fallback for insufficient HTML content")
                return self.get_text_from_publisher_playwright(doi)
                
        except Exception as e:
            self.log(f"Publisher HTML error: {e}")
            # Try Playwright as fallback for 403/blocked requests
            self.log("Trying Playwright fallback for publisher HTML")
            return self.get_text_from_publisher_playwright(doi)
        
        return None
    
    def get_paper_text(self, doi: str) -> tuple[Optional[str], str, bool]:
        """
        Get paper text from multiple sources.
        
        Returns tuple of (text, source_name, from_cache) or (None, '', False) if not found.
        Combines text from multiple sources to maximize coverage.
        """
        # Check cache first
        cached = self._get_cached_text(doi)
        if cached and cached[0]:
            return cached[0], cached[1], True
        
        text_parts = []
        sources_used = []
        pmcid = None  # Track PMCID for potential Playwright fallback
        
        # Try Europe PMC first (returns tuple of text, pmcid)
        text, europe_pmc_pmcid = self.get_text_from_europe_pmc(doi)
        if europe_pmc_pmcid:
            pmcid = europe_pmc_pmcid  # Save PMCID even if text fetch failed
        if text and len(text) > 100:
            self.log(f"Got text from europe_pmc ({len(text)} chars)")
            text_parts.append(text)
            sources_used.append('europe_pmc')
        else:
            time.sleep(0.5)
            # Try NCBI PMC (returns tuple with pmcid)
            text, ncbi_pmcid = self.get_text_from_pmc(doi)
            if ncbi_pmcid:
                pmcid = ncbi_pmcid
            if text and len(text) > 100:
                self.log(f"Got text from ncbi_pmc ({len(text)} chars)")
                text_parts.append(text)
                sources_used.append('ncbi_pmc')
            time.sleep(0.5)
        
        # Always try CrossRef for references (they often contain DANDI DOIs)
        crossref_text = self.get_text_from_crossref(doi)
        if crossref_text and len(crossref_text) > 100:
            self.log(f"Got text from crossref ({len(crossref_text)} chars)")
            text_parts.append(crossref_text)
            if 'crossref' not in sources_used:
                sources_used.append('crossref')
        time.sleep(0.5)
        
        # If PMC text is short (might be missing data availability section), try Playwright
        # PMC author manuscripts often have incomplete text via API
        MIN_PMC_TEXT_FOR_COMPLETENESS = 15000  # Full papers are usually > 15k chars
        pmc_text_length = len(text_parts[0]) if text_parts and sources_used and sources_used[0] in ('europe_pmc', 'ncbi_pmc') else 0
        
        if pmc_text_length > 0 and pmc_text_length < MIN_PMC_TEXT_FOR_COMPLETENESS:
            # If we don't have PMCID from ncbi_pmc, get it now
            if not pmcid:
                pmcid = self.get_pmcid_for_doi(doi)
            if pmcid:
                self.log(f"PMC text seems short ({pmc_text_length} chars), trying Playwright for {pmcid}")
                playwright_text = self.get_text_from_pmc_playwright(pmcid)
                if playwright_text and len(playwright_text) > pmc_text_length:
                    self.log(f"Got better text from PMC Playwright ({len(playwright_text)} chars vs {pmc_text_length})")
                    # Replace the PMC API text with Playwright text
                    text_parts[0] = playwright_text
                    sources_used[0] = 'pmc_playwright'
        
        # If we don't have PMC full text, try other sources
        if not sources_used or sources_used == ['crossref']:
            # For bioRxiv/medRxiv preprints, try Playwright first
            if self.is_preprint_doi(doi):
                playwright_text = self.get_text_from_biorxiv_playwright(doi)
                if playwright_text and len(playwright_text) > 1000:
                    self.log(f"Got text from Playwright ({len(playwright_text)} chars)")
                    text_parts.append(playwright_text)
                    sources_used.append('playwright_biorxiv')
            
            # Try scraping publisher HTML as fallback
            if not sources_used or sources_used == ['crossref']:
                publisher_text = self.get_text_from_publisher_html(doi)
                if publisher_text and len(publisher_text) > 1000:
                    self.log(f"Got text from publisher ({len(publisher_text)} chars)")
                    text_parts.append(publisher_text)
                    sources_used.append('publisher_html')
            
            # If publisher HTML failed (e.g., Cloudflare) but we have a PMCID, try PMC Playwright
            # This works for journals like JNeurosci that block scraping but have PMC copies
            if (not sources_used or sources_used == ['crossref']) and pmcid:
                self.log(f"Publisher blocked, trying PMC Playwright for {pmcid}")
                pmc_playwright_text = self.get_text_from_pmc_playwright(pmcid)
                if pmc_playwright_text and len(pmc_playwright_text) > 1000:
                    self.log(f"Got text from PMC Playwright ({len(pmc_playwright_text)} chars)")
                    text_parts.append(pmc_playwright_text)
                    sources_used.append('pmc_playwright')
        
        if text_parts:
            combined_text = '\n\n'.join(text_parts)
            source_str = '+'.join(sources_used)
            # Cache the result
            self._cache_text(doi, combined_text, source_str)
            return combined_text, source_str, False
        
        return None, '', False
    
    def find_references(self, doi: str) -> tuple[dict, bool]:
        """
        Find dataset references from all archives in a paper given its DOI.
        
        Returns tuple of (result_dict, from_cache).
        Result dict contains DOI, found dataset IDs by archive, source, and match details.
        If follow_references is enabled, also follows citations to data descriptor papers.
        """
        result = {
            'doi': doi,
            'archives': {},
            'source': '',
            'text_length': 0,
            'error': None
        }
        
        # Get paper text
        text, source, from_cache = self.get_paper_text(doi)
        
        if not text:
            result['error'] = 'Could not retrieve paper text'
            return result, from_cache
        
        result['source'] = source
        result['text_length'] = len(text)
        
        # Check if we have sufficient content (not just CrossRef metadata)
        # CrossRef-only results are typically < 1000 chars and just have title + references
        # Full papers should have at least 3000 chars
        MIN_FULL_TEXT_LENGTH = 3000
        
        if len(text) < MIN_FULL_TEXT_LENGTH and source == 'crossref':
            result['error'] = 'Insufficient content (CrossRef metadata only, no full text available)'
            return result, from_cache
        
        # Find direct references from all archives
        archive_matches = self.find_all_archive_references(text)
        
        for archive_name, matches in archive_matches.items():
            result['archives'][archive_name] = {
                'dataset_ids': list(set(m['id'] for m in matches)),
                'matches': matches
            }
        
        # Find and resolve unlinked DANDI citations (e.g., "Title" DANDI Archive without ID)
        unlinked_citations = self.resolve_unlinked_dandi_citations(text)
        if unlinked_citations:
            result['unlinked_citations'] = unlinked_citations
            # Also add these to the DANDI Archive results
            for citation in unlinked_citations:
                for dataset in citation.get('matched_datasets', []):
                    dataset_id = dataset['id']
                    if 'DANDI Archive' not in result['archives']:
                        result['archives']['DANDI Archive'] = {
                            'dataset_ids': [],
                            'matches': []
                        }
                    if dataset_id not in result['archives']['DANDI Archive']['dataset_ids']:
                        result['archives']['DANDI Archive']['dataset_ids'].append(dataset_id)
                        result['archives']['DANDI Archive']['matches'].append({
                            'id': dataset_id,
                            'pattern_type': 'unlinked_citation',
                            'matched_string': citation['citation_title'],
                            'resolved_name': dataset['name'],
                            'url': dataset['url'],
                        })
        
        # Follow citations to data descriptor papers if enabled
        if self.follow_references:
            indirect_refs = self.follow_data_descriptor_chain(doi)
            if indirect_refs:
                result['indirect_references'] = indirect_refs
        
        return result, from_cache
    
    def search_openalex(self, search_terms: list[str], max_results: int = 200) -> list[dict]:
        """
        Search OpenAlex for papers matching search terms in full text.
        
        OpenAlex provides fulltext search for papers including preprints not in Europe PMC.
        
        Returns list of papers with DOI and title.
        """
        self.log(f"Searching OpenAlex for terms: {search_terms}")
        
        openalex_url = "https://api.openalex.org/works"
        papers = []
        seen_dois = set()
        
        try:
            for term in search_terms:
                params = {
                    'filter': f'fulltext.search:{term}',
                    'per_page': min(50, max_results),
                    'mailto': 'ben.dichter@catalystneuro.com'
                }
                
                resp = self.session.get(openalex_url, params=params, timeout=60)
                if resp.status_code != 200:
                    self.log(f"OpenAlex error for '{term}': {resp.status_code}")
                    continue
                
                data = resp.json()
                count = data.get('meta', {}).get('count', 0)
                self.log(f"OpenAlex found {count} papers for '{term}'")
                
                for item in data.get('results', []):
                    doi = item.get('doi', '')
                    if doi:
                        # Clean up DOI format (OpenAlex returns full URL)
                        doi = doi.replace('https://doi.org/', '')
                        if doi not in seen_dois:
                            seen_dois.add(doi)
                            papers.append({
                                'doi': doi,
                                'title': item.get('title', ''),
                                'openalex_id': item.get('id', ''),
                            })
                
                time.sleep(0.5)  # Rate limiting
                
                if len(papers) >= max_results:
                    break
            
            self.log(f"Found {len(papers)} unique papers from OpenAlex")
            return papers[:max_results]
            
        except Exception as e:
            self.log(f"OpenAlex search error: {e}")
            return []
    
    def search_europe_pmc(self, query: str, max_results: int = 1000) -> list[dict]:
        """
        Search Europe PMC for papers matching a query (searches full text).
        
        Returns list of papers with PMID, DOI, and title.
        """
        self.log(f"Searching Europe PMC: {query}")
        
        search_url = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
        papers = []
        cursor_mark = '*'
        page_size = min(100, max_results)  # Europe PMC max is 1000 per page
        
        try:
            while len(papers) < max_results:
                params = {
                    'query': query,  # Removed OPEN_ACCESS:Y filter - it incorrectly excludes preprints
                    'format': 'json',
                    'pageSize': page_size,
                    'cursorMark': cursor_mark,
                    'resultType': 'core'
                }
                
                resp = self.session.get(search_url, params=params, timeout=60)
                resp.raise_for_status()
                data = resp.json()
                
                results = data.get('resultList', {}).get('result', [])
                if not results:
                    break
                
                for r in results:
                    doi = r.get('doi')
                    if doi:
                        papers.append({
                            'pmid': r.get('pmid'),
                            'doi': doi,
                            'title': r.get('title'),
                            'pmcid': r.get('pmcid'),
                        })
                
                # Check if there are more results
                next_cursor = data.get('nextCursorMark')
                if not next_cursor or next_cursor == cursor_mark:
                    break
                cursor_mark = next_cursor
                
                time.sleep(0.5)  # Rate limiting
            
            self.log(f"Found {len(papers)} papers from Europe PMC")
            return papers[:max_results]
            
        except Exception as e:
            self.log(f"Europe PMC search error: {e}")
            return []
    
    def _build_europe_pmc_query(self, archive_name: str) -> str:
        """Build a Europe PMC query from archive search terms.
        
        Returns query wrapped in parentheses for proper AND/OR precedence
        when combined with OPEN_ACCESS:Y filter.
        """
        terms = ARCHIVE_SEARCH_TERMS.get(archive_name, {})
        query_parts = []
        
        # For Europe PMC full-text search, URLs and DOI prefixes are most effective
        # Names can match too broadly in full text
        for url in terms.get('urls', []):
            query_parts.append(f'"{url}"')
        
        for doi_prefix in terms.get('doi_prefixes', []):
            query_parts.append(f'"{doi_prefix}"')
        
        # Add specific search terms (like "dandiset" which is specific enough)
        for term in terms.get('search_terms', []):
            query_parts.append(f'"{term}"')
        
        query = '(' + ' OR '.join(query_parts) + ')'
        
        # Add exclusion terms with NOT
        exclude_terms = terms.get('exclude', [])
        if exclude_terms:
            exclude_parts = [f'"{term}"' for term in exclude_terms]
            query = f'{query} NOT ({" OR ".join(exclude_parts)})'
        
        return query
    
    def discover_papers(self, max_results: int = 100, archives: list[str] | None = None) -> dict:
        """
        Discover papers that reference datasets from any supported archive.
        
        Searches Europe PMC (full text) for each archive, then analyzes each paper
        to extract specific dataset references.
        
        Note: PubMed search was removed because its [All Fields] search matches author
        names, causing many false positives with minimal benefit (analysis showed 98.97%
        coverage with Europe PMC alone).
        
        Args:
            max_results: Maximum number of results per archive.
            archives: List of archive names to search. If None, searches all archives.
        
        Returns dict with query metadata and results by DOI.
        """
        # Determine which archives to search
        if archives is None:
            archives_to_search = list(ARCHIVE_SEARCH_TERMS.keys())
        else:
            archives_to_search = [a for a in archives if a in ARCHIVE_SEARCH_TERMS]
        
        self.log(f"Searching archives: {', '.join(archives_to_search)}")
        
        # Build Europe PMC queries from ARCHIVE_SEARCH_TERMS
        europe_pmc_queries = {}
        
        for archive_name in archives_to_search:
            europe_pmc_queries[archive_name] = self._build_europe_pmc_query(archive_name)
        
        # Collect papers from each archive search, tracking which search found them
        all_papers = {}  # DOI -> paper info with search_sources
        search_stats = {'europe_pmc': {}, 'openalex': {}}
        
        # Search Europe PMC (full text search)
        for archive_name, query in europe_pmc_queries.items():
            self.log(f"Searching Europe PMC for {archive_name}: {query}")
            papers = self.search_europe_pmc(query, max_results)
            search_stats['europe_pmc'][archive_name] = len(papers)
            self.log(f"Found {len(papers)} papers from Europe PMC for {archive_name}")
            
            for paper in papers:
                doi = paper.get('doi')
                if not doi:
                    continue
                
                if doi in all_papers:
                    all_papers[doi]['search_sources'].append(f"europe_pmc:{archive_name}")
                else:
                    paper['search_sources'] = [f"europe_pmc:{archive_name}"]
                    all_papers[doi] = paper
            
            time.sleep(1)
        
        # Search OpenAlex (fulltext search for preprints and papers not in Europe PMC)
        for archive_name in archives_to_search:
            terms = ARCHIVE_SEARCH_TERMS.get(archive_name, {})
            # Build OpenAlex search terms from URLs, DOI prefixes, and specific search terms
            openalex_terms = []
            openalex_terms.extend(terms.get('urls', []))
            openalex_terms.extend(terms.get('doi_prefixes', []))
            openalex_terms.extend(terms.get('search_terms', []))
            
            if openalex_terms:
                self.log(f"Searching OpenAlex for {archive_name}")
                papers = self.search_openalex(openalex_terms, max_results)
                search_stats['openalex'][archive_name] = len(papers)
                self.log(f"Found {len(papers)} papers from OpenAlex for {archive_name}")
                
                for paper in papers:
                    doi = paper.get('doi')
                    if not doi:
                        continue
                    
                    if doi in all_papers:
                        all_papers[doi]['search_sources'].append(f"openalex:{archive_name}")
                    else:
                        paper['search_sources'] = [f"openalex:{archive_name}"]
                        all_papers[doi] = paper
                
                time.sleep(1)
        
        # Convert to list
        papers_list = list(all_papers.values())
        
        # Prepare results
        result = {
            'query_metadata': {
                'europe_pmc_queries': europe_pmc_queries,
                'search_stats': search_stats,
                'timestamp': datetime.now().isoformat(),
                'total_unique_papers': len(papers_list),
                'max_results_per_archive': max_results,
            },
            'results': [],
            'papers_without_datasets': []
        }
        
        if not papers_list:
            self.log("No papers found")
            return result
        
        # Process each paper
        self.log(f"Processing {len(papers_list)} unique papers...")
        papers_with_datasets = 0
        papers_by_archive = {}  # Track papers with datasets by archive
        datasets_by_archive = {}  # Track unique dataset IDs per archive
        papers_exclusive_to_archive = {}  # Track papers that ONLY reference one archive
        
        paper_iterator = tqdm(papers_list, desc="Analyzing papers", file=sys.stderr)
        
        for paper in paper_iterator:
            doi = paper.get('doi')
            if not doi:
                continue
            
            paper_iterator.set_postfix_str(doi[:40] + "..." if len(doi) > 40 else doi)
            
            # Find dataset references (searches ALL archive patterns)
            paper_result, from_cache = self.find_references(doi)
            
            # Add paper metadata
            paper_result['pmid'] = paper.get('pmid')
            paper_result['title'] = paper.get('title')
            paper_result['search_sources'] = paper.get('search_sources', [])
            
            # Get journal and date from CrossRef
            metadata = self.get_paper_metadata(doi)
            paper_result['journal'] = metadata.get('journal')
            paper_result['date'] = metadata.get('date')
            
            # Track papers with and without dataset references
            if paper_result.get('archives'):
                result['results'].append(paper_result)
                papers_with_datasets += 1
                
                archives_in_paper = list(paper_result['archives'].keys())
                
                # Track by archive
                for archive_name in archives_in_paper:
                    if archive_name not in papers_by_archive:
                        papers_by_archive[archive_name] = 0
                    papers_by_archive[archive_name] += 1
                    
                    # Track unique dataset IDs per archive
                    if archive_name not in datasets_by_archive:
                        datasets_by_archive[archive_name] = set()
                    for dataset_id in paper_result['archives'][archive_name].get('dataset_ids', []):
                        datasets_by_archive[archive_name].add(dataset_id)
                
                # Track papers exclusive to one archive
                if len(archives_in_paper) == 1:
                    archive_name = archives_in_paper[0]
                    if archive_name not in papers_exclusive_to_archive:
                        papers_exclusive_to_archive[archive_name] = 0
                    papers_exclusive_to_archive[archive_name] += 1
            else:
                # Store DOI of papers without datasets with content info
                # Include which archive search returned this paper
                result['papers_without_datasets'].append({
                    'doi': doi,
                    'pmid': paper.get('pmid'),
                    'title': paper.get('title'),
                    'search_sources': paper.get('search_sources', []),
                    'source': paper_result.get('source', ''),
                    'text_length': paper_result.get('text_length', 0),
                    'error': paper_result.get('error')
                })
            
            # Rate limiting - only if we made API calls (not from cache)
            if not from_cache:
                time.sleep(1)
        
        result['query_metadata']['papers_with_datasets'] = papers_with_datasets
        result['query_metadata']['papers_by_archive'] = papers_by_archive
        result['query_metadata']['papers_exclusive_to_archive'] = papers_exclusive_to_archive
        result['query_metadata']['unique_datasets_by_archive'] = {
            archive: len(ids) for archive, ids in datasets_by_archive.items()
        }
        
        return result


def main():
    parser = argparse.ArgumentParser(
        description='Find dataset references (DANDI Archive, OpenNeuro, Figshare, PhysioNet, EBRAINS) in scientific papers'
    )
    parser.add_argument(
        'doi',
        nargs='?',
        help='DOI of the paper to analyze'
    )
    parser.add_argument(
        '--file', '-f',
        help='File containing DOIs (one per line)'
    )
    parser.add_argument(
        '--discover',
        action='store_true',
        help='Discover papers from Europe PMC that reference datasets'
    )
    parser.add_argument(
        '--max-results', '-n',
        type=int,
        default=100,
        help='Maximum number of results per archive to process (default: 100)'
    )
    parser.add_argument(
        '--output', '-o',
        help='Output file path (default: stdout)'
    )
    parser.add_argument(
        '--verbose', '-v',
        action='store_true',
        help='Enable verbose output'
    )
    parser.add_argument(
        '--no-follow-references',
        action='store_true',
        help='Disable following citations to data descriptor papers (Scientific Data, Data) for indirect dataset references'
    )
    parser.add_argument(
        '--archives',
        nargs='+',
        choices=list(ARCHIVE_SEARCH_TERMS.keys()),
        default=None,
        help='Archives to search (default: all). Choices: ' + ', '.join(ARCHIVE_SEARCH_TERMS.keys())
    )
    parser.add_argument(
        '--exclude-archives',
        nargs='+',
        choices=list(ARCHIVE_SEARCH_TERMS.keys()),
        default=[],
        help='Archives to exclude from search. Useful to disable Figshare and PhysioNet which can have many false positives.'
    )
    parser.add_argument(
        '--deduplicate',
        action='store_true',
        help='Remove duplicate entries where a bioRxiv/medRxiv preprint and its published version both appear. Keeps the published version by default.'
    )
    parser.add_argument(
        '--prefer-preprint',
        action='store_true',
        help='When deduplicating, keep the preprint instead of the published version.'
    )
    
    args = parser.parse_args()
    
    finder = ArchiveFinder(
        verbose=args.verbose,
        follow_references=not args.no_follow_references
    )
    
    # Discovery mode
    if args.discover:
        # Determine which archives to search
        if args.archives:
            archives = args.archives
        else:
            archives = list(ARCHIVE_SEARCH_TERMS.keys())
        
        # Apply exclusions
        if args.exclude_archives:
            archives = [a for a in archives if a not in args.exclude_archives]
        
        result = finder.discover_papers(max_results=args.max_results, archives=archives)
        
        # Apply deduplication if requested
        if args.deduplicate and result.get('results'):
            original_results_count = len(result['results'])
            original_no_datasets_count = len(result.get('papers_without_datasets', []))
            
            result['results'], deduped_no_datasets, dedup_metadata = finder.deduplicate_results(
                result['results'],
                papers_without_datasets=result.get('papers_without_datasets'),
                prefer_published=not args.prefer_preprint
            )
            
            # Update papers_without_datasets if it was provided and deduplicated
            if deduped_no_datasets is not None:
                result['papers_without_datasets'] = deduped_no_datasets
            
            result['deduplication'] = {
                'enabled': True,
                'prefer': 'preprint' if args.prefer_preprint else 'published',
                'original_results_count': original_results_count,
                'deduplicated_results_count': len(result['results']),
                'original_no_datasets_count': original_no_datasets_count,
                'deduplicated_no_datasets_count': len(result.get('papers_without_datasets', [])),
                'within_results_duplicates': dedup_metadata['within_results_duplicates'],
                'cross_array_duplicates': dedup_metadata['cross_array_duplicates'],
                'removed': dedup_metadata['removed'],
            }
            # Update metadata
            result['query_metadata']['papers_with_datasets_after_dedup'] = len(result['results'])
        
        output_json = json.dumps(result, indent=2)
        
        if args.output:
            with open(args.output, 'w') as f:
                f.write(output_json)
            print(f"Results saved to {args.output}", file=sys.stderr)
        else:
            print(output_json)
        return
    
    # DOI mode
    if not args.doi and not args.file:
        parser.error('Please provide a DOI, a file with DOIs, or use --discover')
    
    # Collect DOIs to process
    dois = []
    if args.file:
        with open(args.file, 'r') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#'):
                    dois.append(line)
    elif args.doi:
        dois.append(args.doi)
    
    results = []
    
    # Use progress bar for multiple DOIs (output to stderr so JSON is clean)
    doi_iterator = tqdm(dois, desc="Processing DOIs", disable=len(dois) <= 1, file=sys.stderr)
    
    for doi in doi_iterator:
        if len(dois) > 1:
            doi_iterator.set_postfix_str(doi[:40] + "..." if len(doi) > 40 else doi)
        
        result, from_cache = finder.find_references(doi)
        results.append(result)
        
        # Rate limiting between papers - only if we made API calls (not from cache)
        if len(dois) > 1 and not from_cache:
            time.sleep(1)
    
    # Output results as JSON
    output = results if len(results) > 1 else results[0]
    output_json = json.dumps(output, indent=2)
    
    if args.output:
        with open(args.output, 'w') as f:
            f.write(output_json)
        print(f"Results saved to {args.output}", file=sys.stderr)
    else:
        print(output_json)


if __name__ == '__main__':
    main()
