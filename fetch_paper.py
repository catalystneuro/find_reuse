#!/usr/bin/env python3
"""
fetch_paper.py - Fetch full text of scientific papers from multiple sources

This module provides the PaperFetcher class which retrieves paper text given a DOI.
It tries multiple sources in a fallback chain:
  1. Europe PMC (XML full text)
  2. NCBI PubMed Central (XML full text)
  3. CrossRef (metadata + references)
  4. Elsevier ScienceDirect API (for 10.1016/ DOIs, requires API key)
  5. Unpaywall (OA PDF)
  6. Publisher HTML (direct scraping)
  7. Playwright-based browser fetching (bioRxiv, PMC, publisher)

Text is cached locally to avoid redundant API calls.
"""

import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

# Try to import playwright for bioRxiv/medRxiv full text
try:
    from playwright.sync_api import sync_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False


# Default cache directory for storing paper full text
DEFAULT_CACHE_DIR = Path(__file__).parent / '.paper_cache'


class PaperFetcher:
    """Fetch full text of scientific papers from multiple sources."""

    def __init__(
        self,
        verbose: bool = False,
        use_cache: bool = True,
        cache_dir: str | Path | None = None,
    ):
        self.verbose = verbose
        self.use_cache = use_cache
        self.cache_dir = Path(cache_dir) if cache_dir else DEFAULT_CACHE_DIR
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'ArchiveFinder/1.0 (https://github.com/dandi; mailto:ben.dichter@catalystneuro.com)'
        })

        # Ensure cache directory exists
        if self.use_cache:
            self.cache_dir.mkdir(parents=True, exist_ok=True)

    def log(self, message: str):
        """Print message if verbose mode is enabled."""
        if self.verbose:
            print(f"[DEBUG] {message}", file=sys.stderr)

    # ------------------------------------------------------------------ #
    # Cache helpers
    # ------------------------------------------------------------------ #

    def _get_cache_path(self, doi: str) -> Path:
        """Get cache file path for a DOI."""
        safe_doi = doi.replace('/', '_').replace(':', '_').replace('\\', '_')
        return self.cache_dir / f"{safe_doi}.json"

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

    # ------------------------------------------------------------------ #
    # Utility helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def is_preprint_doi(doi: str) -> bool:
        """Check if a DOI is from a preprint server (bioRxiv/medRxiv)."""
        return doi.startswith('10.1101/')

    def get_pmcid_for_doi(self, doi: str) -> Optional[str]:
        """
        Get PMCID for a DOI using NCBI ID converter.

        Returns the PMCID if found, None otherwise.
        """
        converter_url = "https://pmc.ncbi.nlm.nih.gov/tools/idconv/api/v1/articles/"
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

    # ------------------------------------------------------------------ #
    # Source-specific fetchers
    # ------------------------------------------------------------------ #

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
                    pmcid_found = pmcid
                    self.log(f"Found PMCID: {pmcid}, fetching full text")
                    fulltext_url = f"https://www.ebi.ac.uk/europepmc/webservices/rest/{pmcid}/fullTextXML"

                    try:
                        ft_resp = self.session.get(fulltext_url, timeout=30)
                        if ft_resp.status_code == 200:
                            # Use html.parser for more complete text extraction
                            # (lxml-xml truncates table content in STAR Methods)
                            soup = BeautifulSoup(ft_resp.content, 'html.parser')
                            text = soup.get_text(separator=' ', strip=True)

                            # Also extract hyperlink URLs from ext-link elements
                            ext_links = []
                            for link in soup.find_all('ext-link'):
                                href = link.get('xlink:href', '') or link.get('href', '')
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
                                soup = BeautifulSoup(ft_resp.content, 'html.parser')
                                text = soup.get_text(separator=' ', strip=True)

                                ext_links = []
                                for link in soup.find_all('ext-link'):
                                    href = link.get('xlink:href', '') or link.get('href', '')
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

        converter_url = "https://pmc.ncbi.nlm.nih.gov/tools/idconv/api/v1/articles/"
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
                page.wait_for_timeout(5000)

                title = page.title()
                if 'not found' in title.lower() or '404' in title or 'error' in title.lower():
                    self.log(f"Page not found for {pmcid}")
                    browser.close()
                    return None

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

                for server in ['biorxiv', 'medrxiv']:
                    url = f'https://www.{server}.org/content/{doi}v1.full'
                    self.log(f"Navigating to: {url}")

                    try:
                        page.goto(url, wait_until='domcontentloaded', timeout=30000)
                        page.wait_for_timeout(10000)

                        title = page.title()
                        if 'not found' in title.lower() or '404' in title:
                            self.log(f"Page not found on {server}")
                            continue

                        article = page.query_selector('article')
                        if article:
                            text = article.inner_text()
                        else:
                            text = page.inner_text('body')

                        if text and len(text) > 1000:
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

                doi_url = f'https://doi.org/{doi}'
                self.log(f"Navigating to: {doi_url}")

                page.goto(doi_url, wait_until='domcontentloaded', timeout=30000)
                page.wait_for_timeout(5000)

                title = page.title()
                if 'not found' in title.lower() or '404' in title or 'error' in title.lower():
                    self.log(f"Page not found for {doi}")
                    browser.close()
                    return None

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

        doi_url = f"https://doi.org/{doi}"

        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.5',
            }

            resp = self.session.get(doi_url, headers=headers, timeout=30, allow_redirects=True)
            resp.raise_for_status()

            content_type = resp.headers.get('content-type', '')
            if 'text/html' not in content_type:
                self.log(f"Not HTML content: {content_type}")
                return None

            soup = BeautifulSoup(resp.content, 'html.parser')

            for element in soup(['script', 'style', 'nav', 'header', 'footer']):
                element.decompose()

            article_content = None
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
                text = soup.get_text(separator=' ', strip=True)

            if len(text) > 1000:
                self.log(f"Got {len(text)} chars from publisher HTML")
                return text
            else:
                self.log(f"Insufficient content from HTML ({len(text)} chars)")
                self.log("Trying Playwright fallback for insufficient HTML content")
                return self.get_text_from_publisher_playwright(doi)

        except Exception as e:
            self.log(f"Publisher HTML error: {e}")
            self.log("Trying Playwright fallback for publisher HTML")
            return self.get_text_from_publisher_playwright(doi)

        return None

    def extract_text_from_pdf_url(self, url: str) -> Optional[str]:
        """Download a PDF from a URL and extract text using PyMuPDF."""
        import tempfile
        try:
            resp = self.session.get(url, timeout=60, stream=True)
            if resp.status_code != 200:
                self.log(f"PDF download failed: HTTP {resp.status_code}")
                return None
            content_type = resp.headers.get('content-type', '')
            if 'pdf' not in content_type and not url.endswith('.pdf'):
                self.log(f"Not a PDF: {content_type}")
                return None
            with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as tmp:
                for chunk in resp.iter_content(chunk_size=65536):
                    tmp.write(chunk)
                tmp_path = tmp.name
            import fitz  # PyMuPDF
            pages = []
            with fitz.open(tmp_path) as doc:
                for page in doc:
                    pages.append(page.get_text())
            Path(tmp_path).unlink(missing_ok=True)
            text = '\n'.join(pages).strip()
            if len(text) > 500:
                return text
            self.log(f"PDF text too short ({len(text)} chars)")
            return None
        except Exception as e:
            self.log(f"PDF extraction error: {e}")
            return None

    def get_text_from_elsevier(self, doi: str) -> Optional[str]:
        """Get paper full text via Elsevier ScienceDirect API."""
        api_key = os.environ.get('ELSEVIER_API_KEY')
        if not api_key:
            # Try loading from .env
            env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')
            if os.path.exists(env_path):
                with open(env_path) as f:
                    for line in f:
                        line = line.strip()
                        if line.startswith('ELSEVIER_API_KEY='):
                            api_key = line.split('=', 1)[1].strip().strip('"').strip("'")
                            break
        if not api_key:
            return None

        # Only try for Elsevier DOIs
        if not doi.startswith('10.1016/'):
            return None

        self.log(f"Trying Elsevier API for DOI: {doi}")
        try:
            resp = self.session.get(
                f"https://api.elsevier.com/content/article/doi/{doi}",
                headers={
                    'X-ELS-APIKey': api_key,
                    'Accept': 'text/plain',
                },
                timeout=30,
            )
            if resp.status_code == 200:
                text = resp.text.strip()
                if len(text) > 500:
                    self.log(f"Got text from Elsevier API ({len(text)} chars)")
                    return text
            elif resp.status_code == 401:
                self.log("Elsevier API: unauthorized (check API key)")
            elif resp.status_code == 403:
                self.log("Elsevier API: forbidden (subscription required)")
            else:
                self.log(f"Elsevier API: status {resp.status_code}")
        except Exception as e:
            self.log(f"Elsevier API error: {e}")
        return None

    def get_text_from_unpaywall(self, doi: str) -> Optional[str]:
        """Get paper text via Unpaywall OA PDF lookup."""
        self.log(f"Trying Unpaywall for DOI: {doi}")
        try:
            resp = self.session.get(
                f"https://api.unpaywall.org/v2/{doi}",
                params={'email': 'ben.dichter@catalystneuro.com'},
                timeout=15,
            )
            if resp.status_code != 200:
                return None
            data = resp.json()
            if not data.get('is_oa'):
                self.log("Unpaywall: not OA")
                return None
            for loc in data.get('oa_locations', []):
                pdf_url = loc.get('url_for_pdf')
                if not pdf_url:
                    continue
                # Skip PMC PDFs — they return HTML redirects; we use PMC Playwright instead
                if 'pmc.ncbi.nlm.nih.gov' in pdf_url:
                    continue
                self.log(f"Unpaywall PDF: {pdf_url[:80]}")
                text = self.extract_text_from_pdf_url(pdf_url)
                if text:
                    return text
        except Exception as e:
            self.log(f"Unpaywall error: {e}")
        return None

    # ------------------------------------------------------------------ #
    # Main orchestrator
    # ------------------------------------------------------------------ #

    def get_paper_text(self, doi: str) -> tuple[Optional[str], str, bool]:
        """
        Get paper text from multiple sources with a fallback chain.

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

        # For bioRxiv/medRxiv preprints (10.1101/...), use dedicated method first
        if self.is_preprint_doi(doi):
            self.log(f"Preprint DOI detected, trying bioRxiv/medRxiv Playwright first: {doi}")
            playwright_text = self.get_text_from_biorxiv_playwright(doi)
            if playwright_text and len(playwright_text) > 1000:
                self.log(f"Got text from bioRxiv Playwright ({len(playwright_text)} chars)")
                text_parts.append(playwright_text)
                sources_used.append('playwright_biorxiv')

            # Also try CrossRef for references
            crossref_text = self.get_text_from_crossref(doi)
            if crossref_text and len(crossref_text) > 100:
                self.log(f"Got text from crossref ({len(crossref_text)} chars)")
                text_parts.append(crossref_text)
                if 'crossref' not in sources_used:
                    sources_used.append('crossref')

            # If bioRxiv Playwright failed, try Europe PMC (some preprints are indexed there)
            if not sources_used or sources_used == ['crossref']:
                text, europe_pmc_pmcid = self.get_text_from_europe_pmc(doi)
                if text and len(text) > 100:
                    self.log(f"Got text from europe_pmc ({len(text)} chars)")
                    text_parts.insert(0, text)
                    sources_used.insert(0, 'europe_pmc')
        else:
            # For non-preprint DOIs, try Europe PMC first
            text, europe_pmc_pmcid = self.get_text_from_europe_pmc(doi)
            if europe_pmc_pmcid:
                pmcid = europe_pmc_pmcid
            if text and len(text) > 100:
                self.log(f"Got text from europe_pmc ({len(text)} chars)")
                text_parts.append(text)
                sources_used.append('europe_pmc')
            else:
                time.sleep(0.5)
                # Try NCBI PMC
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

            # If PMC text is short, try Playwright for more complete content
            MIN_PMC_TEXT_FOR_COMPLETENESS = 15000
            pmc_text_length = len(text_parts[0]) if text_parts and sources_used and sources_used[0] in ('europe_pmc', 'ncbi_pmc') else 0

            if pmc_text_length > 0 and pmc_text_length < MIN_PMC_TEXT_FOR_COMPLETENESS:
                if not pmcid:
                    pmcid = self.get_pmcid_for_doi(doi)
                if pmcid:
                    self.log(f"PMC text seems short ({pmc_text_length} chars), trying Playwright for {pmcid}")
                    playwright_text = self.get_text_from_pmc_playwright(pmcid)
                    if playwright_text and len(playwright_text) > pmc_text_length:
                        self.log(f"Got better text from PMC Playwright ({len(playwright_text)} chars vs {pmc_text_length})")
                        text_parts[0] = playwright_text
                        sources_used[0] = 'pmc_playwright'

            # If we don't have PMC full text, try other sources
            if not sources_used or sources_used == ['crossref']:
                # Try Elsevier API (for 10.1016/ DOIs)
                elsevier_text = self.get_text_from_elsevier(doi)
                if elsevier_text and len(elsevier_text) > 1000:
                    self.log(f"Got text from Elsevier API ({len(elsevier_text)} chars)")
                    text_parts.append(elsevier_text)
                    sources_used.append('elsevier')

            if not sources_used or sources_used == ['crossref']:
                # Try Unpaywall for OA PDF
                unpaywall_text = self.get_text_from_unpaywall(doi)
                if unpaywall_text and len(unpaywall_text) > 1000:
                    self.log(f"Got text from Unpaywall ({len(unpaywall_text)} chars)")
                    text_parts.append(unpaywall_text)
                    sources_used.append('unpaywall')

            if not sources_used or sources_used == ['crossref']:
                # Try scraping publisher HTML as fallback
                publisher_text = self.get_text_from_publisher_html(doi)
                if publisher_text and len(publisher_text) > 1000:
                    self.log(f"Got text from publisher ({len(publisher_text)} chars)")
                    text_parts.append(publisher_text)
                    sources_used.append('publisher_html')

                # If publisher HTML failed but we have a PMCID, try PMC Playwright
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
            # Only cache if we have more than just crossref metadata
            if sources_used != ['crossref']:
                self._cache_text(doi, combined_text, source_str)
            else:
                self.log(f"Skipping cache for crossref-only result: {doi}")
            return combined_text, source_str, False

        return None, '', False
