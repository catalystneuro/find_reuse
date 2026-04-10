#!/usr/bin/env python3
"""
find_missing_papers.py - Use LLM to find associated papers for dandisets
that lack paper links in their metadata.

For each dandiset without known paper associations, sends its metadata
(name, description, contributors) to an LLM to identify the likely
associated publication.

Results are cached in .missing_paper_cache.json with timestamps and
draft_modified checks to avoid redundant lookups.

Usage:
    python find_missing_papers.py                  # Check all missing dandisets
    python find_missing_papers.py --max 20         # Check up to 20
    python find_missing_papers.py --recheck        # Recheck dandisets whose metadata changed
"""

import argparse
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

from llm_utils import call_openrouter_api, get_api_key

DANDI_API_URL = "https://api.dandiarchive.org/api"
CACHE_FILE = Path(".missing_paper_cache.json")
RESULTS_FILE = Path("output/dandi_primary_papers_results.json")


def load_cache() -> dict:
    """Load the cache of previous LLM checks."""
    if CACHE_FILE.exists():
        with open(CACHE_FILE) as f:
            return json.load(f)
    return {}


def save_cache(cache: dict):
    """Save the cache."""
    with open(CACHE_FILE, "w") as f:
        json.dump(cache, f, indent=2)


def get_all_dandiset_ids(session: requests.Session) -> list[dict]:
    """Fetch all dandisets with their draft_modified timestamps."""
    dandisets = []
    url = f"{DANDI_API_URL}/dandisets/"
    params = {"page_size": 200}

    while url:
        resp = session.get(url, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        for ds in data["results"]:
            draft = ds.get("draft_version", {})
            dandisets.append({
                "id": ds["identifier"],
                "draft_modified": draft.get("modified"),
                "embargo_status": ds.get("embargo_status"),
            })
        url = data.get("next")
        params = {}  # next URL includes params

    return dandisets


def get_dandiset_metadata(session: requests.Session, dandiset_id: str) -> dict:
    """Fetch draft version metadata for a dandiset."""
    resp = session.get(
        f"{DANDI_API_URL}/dandisets/{dandiset_id}/versions/draft/",
        timeout=15,
    )
    if resp.status_code != 200:
        return {}
    return resp.json()


def build_prompt(dandiset_id: str, meta: dict) -> str:
    """Build an LLM prompt to identify the associated paper."""
    name = meta.get("name", "")
    description = meta.get("description", "")[:2000]
    contributors = meta.get("contributor", [])

    # Format contributor names
    author_names = []
    for c in contributors[:15]:
        if isinstance(c, dict):
            last = c.get("lastName", c.get("name", ""))
            first = c.get("firstName", "")
            if last:
                author_names.append(f"{last}, {first}".strip(", "))

    authors_str = "; ".join(author_names) if author_names else "Not listed"

    # Check for any related resources that might give hints
    related = meta.get("relatedResource", [])
    related_str = ""
    if related:
        related_items = []
        for r in related[:5]:
            rel = r.get("relation", "")
            url = r.get("url", "")
            ident = r.get("identifier", "")
            name_r = r.get("name", "")
            related_items.append(f"  {rel}: {ident or url} {name_r}")
        related_str = "\nRelated resources:\n" + "\n".join(related_items)

    return f"""You are identifying the primary scientific publication associated with a neuroscience dataset on the DANDI Archive.

DANDI Dataset ID: {dandiset_id}
Name: {name}
Description: {description}
Contributors: {authors_str}{related_str}

Based on the dataset name, description, and contributor list, identify the most likely primary publication (journal article or preprint) that describes or is associated with this dataset.

Look for:
1. Paper titles or author names referenced in the dataset name/description
2. Well-known datasets you recognize from the contributor names and description
3. DOIs or paper references embedded in the text

Respond with a JSON object:
{{"found": true/false, "doi": "10.xxxx/..." or null, "title": "paper title" or null, "authors": "First Author et al." or null, "journal": "journal name" or null, "year": YYYY or null, "confidence": 1-10, "reasoning": "brief explanation"}}

If this appears to be a test dataset, workshop exercise, or has no identifiable associated paper, set found=false.
Only set found=true if you can identify a specific paper with reasonable confidence (>=6).
If you know the DOI, always include it. If you only know the title/authors, include those."""


def classify_dandiset(
    dandiset_id: str,
    meta: dict,
    api_key: str,
) -> dict:
    """Use LLM to identify the associated paper for a dandiset."""
    prompt = build_prompt(dandiset_id, meta)

    response = call_openrouter_api(
        prompt, api_key, return_raw=True, max_tokens=400, timeout=60,
    )

    if not response:
        return {"found": False, "error": "no_response"}

    # Parse JSON from response
    try:
        text = response.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
        result = json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r'\{[^{}]*"found"[^{}]*\}', response, re.DOTALL)
        if m:
            try:
                result = json.loads(m.group(0))
            except json.JSONDecodeError:
                return {"found": False, "error": "parse_error", "raw": response[:200]}
        else:
            return {"found": False, "error": "parse_error", "raw": response[:200]}

    return result


def validate_doi(doi: str) -> bool:
    """Check if a DOI resolves via CrossRef or OpenAlex."""
    s = requests.Session()
    s.headers.update({"User-Agent": "FindMissingPapers/1.0"})
    try:
        resp = s.get(f"https://api.crossref.org/works/{doi}", timeout=15)
        if resp.status_code == 200:
            return True
    except Exception:
        pass
    try:
        resp = s.get(f"https://api.openalex.org/works/doi:{doi}", timeout=15)
        if resp.status_code == 200 and resp.json().get("title"):
            return True
    except Exception:
        pass
    return False


def recover_doi(title: str) -> tuple[str | None, str | None]:
    """Try to find a DOI by searching title in Europe PMC, OpenAlex, CrossRef.

    Returns (doi, source) or (None, None).
    """
    s = requests.Session()
    s.headers.update({"User-Agent": "FindMissingPapers/1.0"})

    query = title.replace('"', '').strip()[:200]

    # Europe PMC
    try:
        resp = s.get(
            "https://www.ebi.ac.uk/europepmc/webservices/rest/search",
            params={"query": f'TITLE:"{query}"', "format": "json", "pageSize": 3},
            timeout=15,
        )
        if resp.status_code == 200:
            for r in resp.json().get("resultList", {}).get("result", []):
                if r.get("doi"):
                    return r["doi"], "europepmc"
    except Exception:
        pass

    # OpenAlex
    try:
        resp = s.get(
            "https://api.openalex.org/works",
            params={"filter": f"title.search:{title[:100]}", "per_page": 3},
            timeout=15,
        )
        if resp.status_code == 200:
            for r in resp.json().get("results", []):
                doi = r.get("doi", "")
                if doi:
                    return doi.replace("https://doi.org/", ""), "openalex"
    except Exception:
        pass

    # CrossRef
    try:
        resp = s.get(
            "https://api.crossref.org/works",
            params={"query.title": title[:200], "rows": 3},
            timeout=15,
        )
        if resp.status_code == 200:
            for item in resp.json().get("message", {}).get("items", []):
                cr_title = item.get("title", [""])[0].lower()
                if len(set(title.lower().split()) & set(cr_title.split())) > len(title.split()) * 0.5:
                    if item.get("DOI"):
                        return item["DOI"], "crossref"
    except Exception:
        pass

    return None, None


def validate_and_recover_dois(cache: dict, workers: int = 8):
    """Validate DOIs for all found entries; attempt recovery for invalid ones."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    to_validate = {
        k: v for k, v in cache.items()
        if v.get("found") and v.get("confidence", 0) >= 6
        and v.get("doi") and v.get("doi_validated") is None
    }

    if not to_validate:
        return

    print(f"  Validating {len(to_validate)} DOIs...", file=sys.stderr)

    def _validate(item):
        did, entry = item
        return did, validate_doi(entry["doi"].strip())

    valid = 0
    invalid_entries = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        for did, is_valid in executor.map(lambda x: _validate(x), to_validate.items()):
            if is_valid:
                cache[did]["doi_validated"] = True
                valid += 1
            else:
                cache[did]["doi_validated"] = False
                invalid_entries.append(did)

    print(f"  Validated: {valid}, Invalid: {len(invalid_entries)}", file=sys.stderr)

    if not invalid_entries:
        return

    # Attempt recovery for invalid DOIs
    print(f"  Recovering DOIs for {len(invalid_entries)} entries...", file=sys.stderr)
    recovered = 0

    def _recover(did):
        title = cache[did].get("title", "")
        if not title:
            return did, None, None
        return (did, *recover_doi(title))

    with ThreadPoolExecutor(max_workers=workers) as executor:
        for did, new_doi, source in executor.map(_recover, invalid_entries):
            if new_doi:
                cache[did]["doi"] = new_doi
                cache[did]["doi_validated"] = True
                cache[did]["doi_recovery_source"] = source
                recovered += 1

    print(f"  Recovered: {recovered}, Still invalid: {len(invalid_entries) - recovered}", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(
        description="Find associated papers for dandisets using LLM"
    )
    parser.add_argument(
        "--max", type=int, default=None,
        help="Maximum number of dandisets to check",
    )
    parser.add_argument(
        "--recheck", action="store_true",
        help="Recheck dandisets whose metadata has changed since last check",
    )
    parser.add_argument(
        "--min-confidence", type=int, default=6,
        help="Minimum confidence to report a found paper (default: 6)",
    )
    parser.add_argument(
        "--workers", type=int, default=8,
        help="Number of parallel workers (default: 8)",
    )
    args = parser.parse_args()

    api_key = get_api_key()
    cache = load_cache()

    session = requests.Session()
    session.headers.update({"User-Agent": "FindMissingPapers/1.0"})

    # Load existing results to know which dandisets already have papers
    known_ids = set()
    if RESULTS_FILE.exists():
        with open(RESULTS_FILE) as f:
            results_data = json.load(f)
        known_ids = set(r["dandiset_id"] for r in results_data["results"])

    # Get all dandisets
    print("Fetching dandiset list...", file=sys.stderr)
    all_dandisets = get_all_dandiset_ids(session)
    print(f"  Total dandisets: {len(all_dandisets)}", file=sys.stderr)
    print(f"  Already have papers: {len(known_ids)}", file=sys.stderr)

    # Filter to dandisets without known papers
    to_check = []
    skipped_cache = 0
    for ds in all_dandisets:
        did = ds["id"]
        if did in known_ids:
            continue

        # Check cache
        if did in cache and not args.recheck:
            cached = cache[did]
            # Skip if we've checked and metadata hasn't changed
            cached_modified = cached.get("draft_modified")
            if cached_modified and cached_modified == ds["draft_modified"]:
                skipped_cache += 1
                continue

        to_check.append(ds)

    print(f"  To check: {len(to_check)} (skipped {skipped_cache} cached)", file=sys.stderr)

    if args.max:
        to_check = to_check[:args.max]
        print(f"  Limited to: {len(to_check)}", file=sys.stderr)

    # Process dandisets in parallel
    from concurrent.futures import ThreadPoolExecutor, as_completed
    import threading

    found_count = 0
    checked = 0
    lock = threading.Lock()

    def process_one(ds):
        """Fetch metadata and classify a single dandiset."""
        did = ds["id"]
        # Each thread needs its own session
        s = requests.Session()
        s.headers.update({"User-Agent": "FindMissingPapers/1.0"})

        meta = get_dandiset_metadata(s, did)
        if not meta:
            return did, {
                "found": False,
                "error": "no_metadata",
                "checked_at": datetime.now().isoformat(),
                "draft_modified": ds["draft_modified"],
            }

        name = meta.get("name", "")

        # Skip obvious test/placeholder dandisets
        if name.lower() in ("test", "asdf", "abc", "bla", "zzz") or len(name) < 5:
            return did, {
                "found": False,
                "reason": "test_dandiset",
                "checked_at": datetime.now().isoformat(),
                "draft_modified": ds["draft_modified"],
            }

        result = classify_dandiset(did, meta, api_key)
        result["checked_at"] = datetime.now().isoformat()
        result["draft_modified"] = ds["draft_modified"]
        result["dandiset_name"] = name
        return did, result

    min_confidence = args.min_confidence
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {executor.submit(process_one, ds): ds for ds in to_check}

        for future in as_completed(futures):
            did, result = future.result()
            with lock:
                cache[did] = result
                checked += 1

                if result.get("found") and result.get("confidence", 0) >= min_confidence:
                    found_count += 1
                    doi = result.get("doi", "")
                    title = result.get("title", "")[:60]
                    conf = result.get("confidence", "?")
                    print(f"  FOUND {did}: {doi or title} (confidence={conf})", file=sys.stderr)

                if checked % 20 == 0:
                    print(f"  Progress: {checked}/{len(to_check)} checked, {found_count} found", file=sys.stderr)
                    save_cache(cache)

    save_cache(cache)

    # Validate DOIs and recover invalid ones
    validate_and_recover_dois(cache, workers=args.workers)
    save_cache(cache)

    # Summary
    all_found = [
        (did, entry) for did, entry in cache.items()
        if entry.get("found") and entry.get("confidence", 0) >= args.min_confidence
        and entry.get("doi_validated") is True
    ]

    print(f"\n{'='*60}", file=sys.stderr)
    print(f"Checked: {checked}", file=sys.stderr)
    print(f"Total found (confidence >= {args.min_confidence}): {len(all_found)}", file=sys.stderr)

    # Print found papers as JSON to stdout
    output = []
    for did, entry in sorted(all_found):
        output.append({
            "dandiset_id": did,
            "dandiset_name": entry.get("dandiset_name", ""),
            "doi": entry.get("doi"),
            "title": entry.get("title"),
            "authors": entry.get("authors"),
            "journal": entry.get("journal"),
            "year": entry.get("year"),
            "confidence": entry.get("confidence"),
            "reasoning": entry.get("reasoning"),
        })

    json.dump(output, sys.stdout, indent=2)
    print(file=sys.stdout)


if __name__ == "__main__":
    main()
