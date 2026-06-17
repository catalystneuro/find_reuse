"""OpenAlex / Crossref / bioRxiv helpers for citing-paper discovery and full-text fetch.

Extracted from `dandi_primary_papers.py` for the minimal pipeline. Only the
library functions used by `run_minimal_pipeline.py` and
`extract_citation_contexts.py` are included.
"""

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import requests
from tqdm import tqdm

PREPRINT_CACHE_DIR = Path('.preprint_cache')
ALTERNATE_DOI_CACHE_FILE = Path('.alternate_doi_cache.json')


def get_openalex_paper_data(session: requests.Session, doi: str) -> Optional[dict]:
    doi_clean = doi.strip()
    if doi_clean.startswith('doi:') or doi_clean.startswith('DOI:'):
        doi_clean = doi_clean[4:]

    url = f"https://api.openalex.org/works/doi:{doi_clean}"

    try:
        resp = session.get(url, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            return {
                'publication_date': data.get('publication_date'),
                'cited_by_count': data.get('cited_by_count', 0),
                'openalex_id': data.get('id'),
            }
    except requests.RequestException:
        pass

    return None


def _get_preprint_cache_path(doi: str) -> Path:
    safe_doi = doi.replace('/', '_').replace(':', '_').replace('\\', '_')
    return PREPRINT_CACHE_DIR / f"{safe_doi}.json"


def _load_alternate_doi_cache() -> dict:
    if ALTERNATE_DOI_CACHE_FILE.exists():
        try:
            with open(ALTERNATE_DOI_CACHE_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save_alternate_doi_cache(cache: dict) -> None:
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
    """Look up the alternate version of a DOI (preprint↔published)."""
    doi_clean = doi.strip().lower()

    if doi_clean.startswith('10.1101/'):
        return _lookup_published_version(session, doi_clean)
    else:
        return _lookup_preprint_version(session, doi_clean, alt_cache)


def _lookup_published_version(session: requests.Session, preprint_doi: str) -> Optional[str]:
    PREPRINT_CACHE_DIR.mkdir(exist_ok=True)
    cache_path = _get_preprint_cache_path(preprint_doi)

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
    if alt_cache is None:
        alt_cache = _load_alternate_doi_cache()

    if published_doi in alt_cache:
        return alt_cache[published_doi] or None

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
                alt_cache[published_doi] = work_doi
                _save_alternate_doi_cache(alt_cache)
                return work_doi

    except requests.RequestException:
        pass

    alt_cache[published_doi] = ""
    _save_alternate_doi_cache(alt_cache)
    return None


def get_citing_papers(
    session: requests.Session,
    openalex_id: str,
    after_date: str,
    max_results: int = 10,
) -> list[dict]:
    if '/' in openalex_id:
        openalex_id = openalex_id.split('/')[-1]

    per_page = min(max_results, 200)
    base_url = (
        f"https://api.openalex.org/works?filter=cites:{openalex_id},"
        f"publication_date:>{after_date}&per_page={per_page}"
        f"&mailto=ben.dichter@catalystneuro.com"
    )

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
                    doi = doi.replace('https://doi.org/', '')

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

            meta = data.get('meta', {})
            cursor = meta.get('next_cursor')

        except requests.RequestException:
            break

    return citing_papers


def _get_semantic_scholar_api_key() -> Optional[str]:
    """Read SEMANTIC_SCHOLAR_API_KEY from env or .env (optional; raises rate limits)."""
    key = os.environ.get('SEMANTIC_SCHOLAR_API_KEY')
    if not key:
        env_file = Path('.env')
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                if line.startswith('SEMANTIC_SCHOLAR_API_KEY='):
                    key = line.split('=', 1)[1].strip().strip('"').strip("'")
                    break
    return key or None


def get_semantic_scholar_citing_papers(
    session: requests.Session,
    doi: str,
    after_date: str,
    max_results: int = 10,
    api_key: Optional[str] = None,
    rate_limit: float = 1.0,
) -> list[dict]:
    """Find papers citing `doi` via the Semantic Scholar Graph API.

    Complements OpenAlex citation discovery. Returns dicts in the same shape as
    get_citing_papers (openalex_id is None). Papers published on or before
    after_date are skipped when a publication date is available; entries without
    a date are kept (the classifier sorts them out). Returns [] if the paper is
    not in Semantic Scholar (404) or on persistent errors — never raises.
    """
    headers = {'x-api-key': api_key} if api_key else {}
    citing_papers: list[dict] = []
    offset = 0
    page = min(1000, max(max_results, 100))
    retries = 0

    while len(citing_papers) < max_results:
        try:
            resp = session.get(
                f"https://api.semanticscholar.org/graph/v1/paper/DOI:{doi}/citations",
                params={'fields': 'externalIds,title,publicationDate,venue',
                        'limit': page, 'offset': offset},
                headers=headers, timeout=30,
            )
        except requests.RequestException:
            break

        if resp.status_code == 404:
            break  # primary paper not indexed by Semantic Scholar
        if resp.status_code == 429 and retries < 5:
            retries += 1
            time.sleep(2 * retries)
            continue
        if resp.status_code != 200:
            break
        retries = 0

        data = resp.json()
        items = data.get('data', [])
        if not items:
            break

        for item in items:
            cp = item.get('citingPaper') or {}
            ext = cp.get('externalIds') or {}
            c_doi = (ext.get('DOI') or '').replace('https://doi.org/', '').lower()
            if not c_doi:
                continue
            pub_date = cp.get('publicationDate')
            if after_date and pub_date and pub_date <= after_date:
                continue
            citing_papers.append({
                'doi': c_doi,
                'title': cp.get('title', ''),
                'publication_date': pub_date,
                'journal': cp.get('venue'),
                'openalex_id': None,
                'source': 'semantic_scholar',
            })
            if len(citing_papers) >= max_results:
                break

        next_offset = data.get('next')
        if next_offset and items:
            offset = next_offset
            time.sleep(rate_limit)
        else:
            break

    return citing_papers


def _make_openalex_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({
        'User-Agent': 'DANDIPrimaryPapers/1.0 (https://github.com/dandi; mailto:ben.dichter@catalystneuro.com)'
    })
    return session


def find_citing_papers(
    result: dict,
    session: requests.Session,
    max_citing_papers_per_dandiset: int,
    rate_limit: float = 0.1,
    use_semantic_scholar: bool = True,
) -> list[dict]:
    """Populate `result['citing_papers']` with citing-paper metadata.

    Mutates `result` in place and returns the populated list. Does not fetch
    full text. Looks up alternate (preprint↔published) DOI versions for each
    primary paper and queries OpenAlex for papers citing them after the
    dataset creation date. When `use_semantic_scholar` is True, supplements
    OpenAlex with Semantic Scholar citation discovery (deduped by DOI), which
    also runs for primary papers OpenAlex has no record of.
    """
    ds_created_str = result.get('dandiset_created')
    if not ds_created_str:
        result['citing_papers'] = []
        return result['citing_papers']

    try:
        ds_created = datetime.fromisoformat(ds_created_str.replace('Z', '+00:00'))
        from_date = ds_created.strftime('%Y-%m-%d')
    except ValueError:
        result['citing_papers'] = []
        return result['citing_papers']

    all_citing = []
    seen_citing_dois = set()
    s2_api_key = _get_semantic_scholar_api_key() if use_semantic_scholar else None

    def _add(citing, cited_doi):
        """Merge citing-paper dicts into all_citing, deduped by lowercased DOI."""
        for c in citing:
            c_doi = (c.get('doi') or '').lower()
            if c_doi and c_doi not in seen_citing_dois:
                c.setdefault('source', 'openalex')
                c['cited_paper_doi'] = cited_doi
                all_citing.append(c)
                seen_citing_dois.add(c_doi)

    for paper in result['paper_relations']:
        doi = paper.get('doi')
        if not doi:
            continue

        # Collect this paper's DOI versions (primary + preprint/published alt)
        version_dois = [doi]
        openalex_ids = []

        openalex_data = get_openalex_paper_data(session, doi)
        if openalex_data and openalex_data.get('openalex_id'):
            openalex_ids.append((openalex_data['openalex_id'], doi))

        alt_doi = get_alternate_doi(session, doi)
        if alt_doi:
            version_dois.append(alt_doi)
            alt_data = get_openalex_paper_data(session, alt_doi)
            if alt_data and alt_data.get('openalex_id'):
                openalex_ids.append((alt_data['openalex_id'], alt_doi))

        # OpenAlex citation discovery
        for oa_id, version_doi in openalex_ids:
            citing = get_citing_papers(
                session, oa_id, from_date,
                max_results=max_citing_papers_per_dandiset,
            )
            _add(citing, doi)
            time.sleep(rate_limit)

        # Semantic Scholar supplement — runs even when OpenAlex has no record,
        # which is where it adds the most (sparse-OpenAlex primary papers).
        if use_semantic_scholar and len(all_citing) < max_citing_papers_per_dandiset:
            for version_doi in version_dois:
                if len(all_citing) >= max_citing_papers_per_dandiset:
                    break
                s2_citing = get_semantic_scholar_citing_papers(
                    session, version_doi, from_date,
                    max_results=max_citing_papers_per_dandiset, api_key=s2_api_key,
                )
                _add(s2_citing, doi)

        if len(all_citing) >= max_citing_papers_per_dandiset:
            break

    result['citing_papers'] = all_citing[:max_citing_papers_per_dandiset]
    return result['citing_papers']


def fetch_citing_paper_texts(
    results: list[dict],
    max_citing_papers_per_dandiset: int = 10,
    max_total_papers: int | None = None,
    show_progress: bool = True,
    verbose: bool = False,
    rate_limit: float = 0.1,
    cache_dir: str = "/Volumes/microsd64/data/",
) -> list[dict]:
    """Fetch full text for papers that cite the primary papers."""
    from src.direct_pipeline.find_reuse import ArchiveFinder

    finder = ArchiveFinder(verbose=verbose, use_cache=True, cache_dir=cache_dir)

    session = _make_openalex_session()

    pbar = tqdm(results, desc="Fetching citing papers from OpenAlex", disable=not show_progress)

    for result in pbar:
        ds_id = result['dandiset_id']
        pbar.set_postfix({'dandiset': ds_id})

        if result.get('citing_papers'):
            continue

        find_citing_papers(
            result, session,
            max_citing_papers_per_dandiset=max_citing_papers_per_dandiset,
            rate_limit=rate_limit,
        )

    all_citing_dois = []
    seen_dois = set()
    for result in results:
        for citing in result.get('citing_papers', []):
            doi = citing.get('doi')
            if doi and doi not in seen_dois:
                all_citing_dois.append(doi)
                seen_dois.add(doi)

    if max_total_papers is not None and len(all_citing_dois) > max_total_papers:
        print(f"Found {len(all_citing_dois)} unique citing papers, limiting to {max_total_papers}", file=sys.stderr)
        all_citing_dois = all_citing_dois[:max_total_papers]
    else:
        print(f"Found {len(all_citing_dois)} unique citing papers to fetch", file=sys.stderr)

    doi_texts = {}
    n_workers = 8

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

    if uncached_dois:
        import concurrent.futures
        import threading

        thread_local = threading.local()

        def get_finder():
            if not hasattr(thread_local, 'finder'):
                thread_local.finder = ArchiveFinder(
                    verbose=verbose, use_cache=True, cache_dir=cache_dir,
                )
            return thread_local.finder

        def fetch_one(doi):
            f = get_finder()
            text, source, from_cache = f.get_paper_text(doi)
            if not from_cache:
                time.sleep(0.2)
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

    for result in results:
        for citing in result.get('citing_papers', []):
            doi = citing.get('doi')
            if doi and doi in doi_texts:
                text_info = doi_texts[doi]
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
