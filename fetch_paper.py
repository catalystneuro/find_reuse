#!/usr/bin/env python3
"""
fetch_paper.py - Project-local wrapper around the `paper-text-fetcher` library.

The fetching logic lives in `paper_text_fetcher`, which is domain independent
and installable on its own. This module supplies the settings specific to this
project (the cache location and the contact details we identify ourselves with
to NCBI, CrossRef, and Unpaywall) and re-exports the public names so existing
imports such as `from fetch_paper import PaperFetcher` keep working.

See the library's README for the retrieval sources and for how full text is
distinguished from metadata.
"""

from pathlib import Path

from paper_text_fetcher import (  # noqa: F401  (re-exported for callers)
    FULL_TEXT_SOURCES,
    METADATA_SOURCES,
    MIN_FULL_TEXT_CHARS,
    PLAYWRIGHT_AVAILABLE,
    TextCache,
    format_crossref_reference,
    has_full_text_source,
    is_full_text,
    looks_like_paywall_or_landing_page,
    xml_has_body,
)
from paper_text_fetcher import PaperFetcher as _BasePaperFetcher

# Default cache directory for storing paper full text
DEFAULT_CACHE_DIR = Path(__file__).parent / '.paper_cache'

# Sent to NCBI, CrossRef, and Unpaywall so they can reach us about our traffic.
CONTACT_EMAIL = 'ben.dichter@catalystneuro.com'
TOOL_NAME = 'dandi_finder'
USER_AGENT = (
    'ArchiveFinder/1.0 (https://github.com/dandi; '
    f'mailto:{CONTACT_EMAIL})'
)


class PaperFetcher(_BasePaperFetcher):
    """
    `paper_text_fetcher.PaperFetcher` with this project's defaults applied.

    Keeps the original keyword-argument signature, in which `cache_dir` is
    optional, so existing call sites are unaffected.
    """

    def __init__(
        self,
        verbose: bool = False,
        use_cache: bool = True,
        cache_dir: str | Path | None = None,
        **kwargs,
    ):
        kwargs.setdefault('contact_email', CONTACT_EMAIL)
        kwargs.setdefault('tool_name', TOOL_NAME)
        kwargs.setdefault('user_agent', USER_AGENT)
        super().__init__(
            cache_dir=Path(cache_dir) if cache_dir else DEFAULT_CACHE_DIR,
            verbose=verbose,
            use_cache=use_cache,
            **kwargs,
        )


__all__ = [
    'PaperFetcher',
    'TextCache',
    'format_crossref_reference',
    'is_full_text',
    'has_full_text_source',
    'looks_like_paywall_or_landing_page',
    'xml_has_body',
    'FULL_TEXT_SOURCES',
    'METADATA_SOURCES',
    'MIN_FULL_TEXT_CHARS',
    'PLAYWRIGHT_AVAILABLE',
    'DEFAULT_CACHE_DIR',
    'CONTACT_EMAIL',
]
