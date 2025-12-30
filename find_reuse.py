#!/usr/bin/env python3
"""
find_reuse.py - Find dataset references in scientific papers

This module extracts text from papers (given a DOI) and identifies references
to datasets on multiple archives (DANDI Archive, OpenNeuro, Figshare, PhysioNet).

Usage:
    python find_reuse.py <DOI>
    python find_reuse.py --file dois.txt
    python find_reuse.py --discover -o results.json
    
Output is always JSON.
"""

import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
from typing import Optional
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup
from tqdm import tqdm


# Cache directory for storing paper full text
CACHE_DIR = Path(__file__).parent / '.paper_cache'


# Archive reference patterns - dictionary of archive name to list of (pattern, pattern_type) tuples
ARCHIVE_PATTERNS = {
    'DANDI Archive': [
        # DOI format: 10.48324/dandi.000130 or 10.48324/dandi.000130/0.210914.1539
        (r'10\.48324/dandi\.(\d{6})', 'doi'),
        # URL formats
        (r'dandiarchive\.org/dandiset/(\d{6})', 'url'),
        (r'gui\.dandiarchive\.org/#/dandiset/(\d{6})', 'gui_url'),
        # Direct text mentions
        (r'DANDI:\s*(\d{6})', 'text_colon'),
        (r'DANDI\s+(\d{6})', 'text_space'),
        (r'dandiset\s+(\d{6})', 'dandiset_text'),
        (r'dandiset/(\d{6})', 'dandiset_path'),
        # DANDI archive identifier pattern
        (r'DANDI(?:\s+archive)?(?:\s+identifier)?[:\s]+(\d{6})', 'identifier'),
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
}


class ArchiveFinder:
    """Find dataset references from multiple archives in papers."""
    
    def __init__(self, verbose: bool = False, use_cache: bool = True):
        self.verbose = verbose
        self.use_cache = use_cache
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'ArchiveFinder/1.0 (https://github.com/dandi; mailto:info@dandiarchive.org)'
        })
        
        # Ensure cache directory exists
        if self.use_cache:
            CACHE_DIR.mkdir(exist_ok=True)
    
    def _get_cache_path(self, doi: str) -> Path:
        """Get cache file path for a DOI."""
        # Use hash of DOI for filename to avoid filesystem issues
        doi_hash = hashlib.md5(doi.encode()).hexdigest()
        return CACHE_DIR / f"{doi_hash}.json"
    
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
    
    def log(self, message: str):
        """Print message if verbose mode is enabled."""
        if self.verbose:
            print(f"[DEBUG] {message}", file=sys.stderr)
    
    def find_archive_ids(self, text: str, archive_name: str) -> list[dict]:
        """
        Find all dataset IDs for a specific archive in the given text.
        
        Returns list of matches with id, pattern type, and matched string.
        """
        matches = []
        seen_ids = set()
        
        patterns = ARCHIVE_PATTERNS.get(archive_name, [])
        for pattern, pattern_type in patterns:
            for match in re.finditer(pattern, text, re.IGNORECASE):
                dataset_id = match.group(1)
                matched_str = match.group(0)
                
                if dataset_id not in seen_ids:
                    matches.append({
                        'id': dataset_id,
                        'pattern_type': pattern_type,
                        'matched_string': matched_str
                    })
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
    
    def get_text_from_europe_pmc(self, doi: str) -> Optional[str]:
        """
        Get full text from Europe PMC.
        
        Only returns text if full text is available (PMCID exists).
        Abstract-only results are skipped since DANDI refs are rarely in abstracts.
        """
        self.log(f"Trying Europe PMC for DOI: {doi}")
        
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
                
                # Only proceed if we have full text access via PMCID
                if pmcid:
                    self.log(f"Found PMCID: {pmcid}, fetching full text")
                    fulltext_url = f"https://www.ebi.ac.uk/europepmc/webservices/rest/{pmcid}/fullTextXML"
                    
                    try:
                        ft_resp = self.session.get(fulltext_url, timeout=30)
                        if ft_resp.status_code == 200:
                            soup = BeautifulSoup(ft_resp.content, 'lxml-xml')
                            # Extract all text content
                            return soup.get_text(separator=' ', strip=True)
                    except Exception as e:
                        self.log(f"Error fetching full text: {e}")
                else:
                    self.log("No PMCID available, skipping abstract-only result")
                    
        except Exception as e:
            self.log(f"Europe PMC error: {e}")
        
        return None
    
    def get_text_from_pmc(self, doi: str) -> Optional[str]:
        """
        Get full text from NCBI PubMed Central.
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
                    return soup.get_text(separator=' ', strip=True)
                    
        except Exception as e:
            self.log(f"NCBI PMC error: {e}")
        
        return None
    
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
    
    def get_text_from_publisher_html(self, doi: str) -> Optional[str]:
        """
        Scrape full text from publisher's open access HTML page.
        
        Works with Nature, Springer, Cell, Elsevier, and other open access papers.
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
                
        except Exception as e:
            self.log(f"Publisher HTML error: {e}")
        
        return None
    
    def get_paper_text(self, doi: str) -> tuple[Optional[str], str]:
        """
        Get paper text from multiple sources.
        
        Returns tuple of (text, source_name) or (None, '') if not found.
        Combines text from multiple sources to maximize coverage.
        """
        # Check cache first
        cached = self._get_cached_text(doi)
        if cached and cached[0]:
            return cached
        
        text_parts = []
        sources_used = []
        
        # Try full-text sources first
        fulltext_sources = [
            ('europe_pmc', self.get_text_from_europe_pmc),
            ('ncbi_pmc', self.get_text_from_pmc),
        ]
        
        for source_name, fetch_func in fulltext_sources:
            text = fetch_func(doi)
            if text and len(text) > 100:
                self.log(f"Got text from {source_name} ({len(text)} chars)")
                text_parts.append(text)
                sources_used.append(source_name)
                # Only use first successful full-text source
                break
            time.sleep(0.5)
        
        # Always try CrossRef for references (they often contain DANDI DOIs)
        crossref_text = self.get_text_from_crossref(doi)
        if crossref_text and len(crossref_text) > 100:
            self.log(f"Got text from crossref ({len(crossref_text)} chars)")
            text_parts.append(crossref_text)
            if 'crossref' not in sources_used:
                sources_used.append('crossref')
        time.sleep(0.5)
        
        # If we don't have PMC full text, try scraping publisher HTML
        if not sources_used or sources_used == ['crossref']:
            publisher_text = self.get_text_from_publisher_html(doi)
            if publisher_text and len(publisher_text) > 1000:
                self.log(f"Got text from publisher ({len(publisher_text)} chars)")
                text_parts.append(publisher_text)
                sources_used.append('publisher_html')
        
        if text_parts:
            combined_text = '\n\n'.join(text_parts)
            source_str = '+'.join(sources_used)
            # Cache the result
            self._cache_text(doi, combined_text, source_str)
            return combined_text, source_str
        
        return None, ''
    
    def find_references(self, doi: str) -> dict:
        """
        Find dataset references from all archives in a paper given its DOI.
        
        Returns dict with DOI, found dataset IDs by archive, source, and match details.
        """
        result = {
            'doi': doi,
            'archives': {},
            'source': '',
            'error': None
        }
        
        # Get paper text
        text, source = self.get_paper_text(doi)
        
        if not text:
            result['error'] = 'Could not retrieve paper text'
            return result
        
        result['source'] = source
        
        # Find references from all archives
        archive_matches = self.find_all_archive_references(text)
        
        for archive_name, matches in archive_matches.items():
            result['archives'][archive_name] = {
                'dataset_ids': list(set(m['id'] for m in matches)),
                'matches': matches
            }
        
        return result
    
    def search_pubmed(self, query: str, max_results: int = 1000) -> list[dict]:
        """
        Search PubMed for papers matching a query.
        
        Returns list of papers with PMID, DOI, and title.
        """
        self.log(f"Searching PubMed: {query}")
        
        # Search PubMed
        esearch_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
        params = {
            'db': 'pubmed',
            'term': query,
            'retmax': max_results,
            'retmode': 'json',
            'tool': 'archive_finder',
            'email': 'info@dandiarchive.org'
        }
        
        try:
            resp = self.session.get(esearch_url, params=params, timeout=60)
            resp.raise_for_status()
            data = resp.json()
            
            pmids = data.get('esearchresult', {}).get('idlist', [])
            total_count = int(data.get('esearchresult', {}).get('count', 0))
            self.log(f"Found {total_count} total results, got {len(pmids)} PMIDs")
            
            if not pmids:
                return []
            
            # Fetch details for these PMIDs in batches
            papers = []
            batch_size = 200
            
            for i in range(0, len(pmids), batch_size):
                batch = pmids[i:i + batch_size]
                self.log(f"Fetching details for PMIDs {i+1}-{i+len(batch)}")
                
                efetch_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
                params = {
                    'db': 'pubmed',
                    'id': ','.join(batch),
                    'rettype': 'xml',
                    'retmode': 'xml',
                    'tool': 'archive_finder',
                    'email': 'info@dandiarchive.org'
                }
                
                fetch_resp = self.session.get(efetch_url, params=params, timeout=60)
                if fetch_resp.status_code == 200:
                    soup = BeautifulSoup(fetch_resp.content, 'lxml-xml')
                    
                    for article in soup.find_all('PubmedArticle'):
                        paper = self._parse_pubmed_article(article)
                        if paper and paper.get('doi'):
                            papers.append(paper)
                
                time.sleep(0.5)  # Rate limiting
            
            return papers
            
        except Exception as e:
            self.log(f"PubMed search error: {e}")
            return []
    
    def _parse_pubmed_article(self, article) -> Optional[dict]:
        """Parse a PubMed article XML element."""
        try:
            pmid_elem = article.find('PMID')
            pmid = pmid_elem.get_text() if pmid_elem else None
            
            title_elem = article.find('ArticleTitle')
            title = title_elem.get_text() if title_elem else None
            
            # Find DOI
            doi = None
            for article_id in article.find_all('ArticleId'):
                if article_id.get('IdType') == 'doi':
                    doi = article_id.get_text()
                    break
            
            # Also check ELocationID
            if not doi:
                for eloc in article.find_all('ELocationID'):
                    if eloc.get('EIdType') == 'doi':
                        doi = eloc.get_text()
                        break
            
            return {
                'pmid': pmid,
                'doi': doi,
                'title': title
            }
        except Exception:
            return None
    
    def discover_papers(self, max_results: int = 1000) -> dict:
        """
        Discover papers that reference datasets from any supported archive.
        
        Searches PubMed with a combined query for all archives, then analyzes
        each paper to extract specific dataset references.
        
        Returns dict with query metadata and results by DOI.
        """
        # Build combined query for all archives
        query_parts = [
            # DANDI
            '("dandi"[All Fields] OR "dandiarchive"[All Fields] OR "10.48324/dandi"[All Fields])',
            # OpenNeuro
            '("openneuro"[All Fields] OR "10.18112/openneuro"[All Fields])',
            # Figshare (too generic, use DOI pattern only)
            '("10.6084/m9.figshare"[All Fields])',
            # PhysioNet
            '("physionet"[All Fields] OR "10.13026"[All Fields])',
        ]
        combined_query = ' OR '.join(query_parts)
        
        self.log(f"Combined query: {combined_query}")
        
        # Search PubMed
        papers = self.search_pubmed(combined_query, max_results)
        
        # Prepare results
        result = {
            'query_metadata': {
                'query': combined_query,
                'timestamp': datetime.now().isoformat(),
                'total_pubmed_results': len(papers),
                'max_results_requested': max_results,
            },
            'results': [],
            'papers_without_datasets': []
        }
        
        if not papers:
            self.log("No papers found")
            return result
        
        # Process each paper
        self.log(f"Processing {len(papers)} papers...")
        papers_with_datasets = 0
        
        paper_iterator = tqdm(papers, desc="Analyzing papers", file=sys.stderr)
        
        for paper in paper_iterator:
            doi = paper.get('doi')
            if not doi:
                continue
            
            paper_iterator.set_postfix_str(doi[:40] + "..." if len(doi) > 40 else doi)
            
            # Find dataset references
            paper_result = self.find_references(doi)
            
            # Add paper metadata
            paper_result['pmid'] = paper.get('pmid')
            paper_result['title'] = paper.get('title')
            
            # Track papers with and without dataset references
            if paper_result.get('archives'):
                result['results'].append(paper_result)
                papers_with_datasets += 1
            else:
                # Store DOI of papers without datasets
                result['papers_without_datasets'].append({
                    'doi': doi,
                    'pmid': paper.get('pmid'),
                    'title': paper.get('title'),
                    'error': paper_result.get('error')
                })
            
            # Rate limiting
            time.sleep(1)
        
        result['query_metadata']['papers_with_datasets'] = papers_with_datasets
        
        return result


def main():
    parser = argparse.ArgumentParser(
        description='Find dataset references (DANDI Archive, OpenNeuro, Figshare, PhysioNet) in scientific papers'
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
        help='Discover papers from PubMed that reference datasets'
    )
    parser.add_argument(
        '--max-results', '-n',
        type=int,
        default=100,
        help='Maximum number of PubMed results to process (default: 100)'
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
    
    args = parser.parse_args()
    
    finder = ArchiveFinder(verbose=args.verbose)
    
    # Discovery mode
    if args.discover:
        result = finder.discover_papers(max_results=args.max_results)
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
        
        result = finder.find_references(doi)
        results.append(result)
        
        # Rate limiting between papers
        if len(dois) > 1:
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
