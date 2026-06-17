#!/usr/bin/env python3
"""
deduplicate_preprints.py — Remove preprint/published duplicate entries from classifications.

When a paper exists as both a preprint (bioRxiv/medRxiv/arXiv) and a published journal
article, both may appear as separate citing papers. This script uses preprint server
metadata (bioRxiv/medRxiv API, OpenAlex) to find the published DOI, then removes the
preprint entry if the journal version is already in the classifications.

Usage:
    python deduplicate_preprints.py
    python deduplicate_preprints.py --dry-run
    python deduplicate_preprints.py --input output/crcns/classifications.json
"""

import argparse
import json
import re
import sys
import time
from collections import defaultdict
from pathlib import Path

import requests

CACHE_FILE = Path(".preprint_doi_map.json")


def is_preprint_doi(doi):
    """Check if a DOI is from a preprint server."""
    doi_lower = doi.lower()
    return ("10.1101/" in doi_lower or "arxiv" in doi_lower
            or "10.21203/" in doi_lower or "10.2139/" in doi_lower)


def _load_doi_cache():
    """Load cached preprint -> published DOI mapping."""
    if CACHE_FILE.exists():
        with open(CACHE_FILE) as f:
            return json.load(f)
    return {}


def _save_doi_cache(cache):
    """Save preprint -> published DOI mapping."""
    with open(CACHE_FILE, "w") as f:
        json.dump(cache, f, indent=2)


def resolve_preprint_doi(preprint_doi, session, cache):
    """Resolve a preprint DOI to its published journal DOI.

    Uses bioRxiv/medRxiv API first, falls back to OpenAlex.
    Returns the published DOI or None.
    """
    # Only trust a positive (resolved) cache entry. A falsy entry means we
    # previously found no published version — but the preprint may have been
    # published since, so re-resolve rather than trusting a stale negative.
    if cache.get(preprint_doi):
        return cache[preprint_doi]

    published_doi = None
    doi_lower = preprint_doi.lower()

    # bioRxiv / medRxiv API
    if "10.1101/" in doi_lower:
        for server in ["biorxiv", "medrxiv"]:
            try:
                resp = session.get(
                    f"https://api.biorxiv.org/details/{server}/{preprint_doi}",
                    timeout=10,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    for entry in data.get("collection", []):
                        pub = entry.get("published", "")
                        if pub and pub != "NA":
                            published_doi = pub
                            break
                if published_doi:
                    break
            except Exception:
                pass

    # Fallback: OpenAlex
    if not published_doi:
        try:
            resp = session.get(
                f"https://api.openalex.org/works/doi:{preprint_doi}",
                timeout=10,
            )
            if resp.status_code == 200:
                work = resp.json()
                # Check locations for a non-preprint version
                for loc in work.get("locations", []):
                    source = loc.get("source") or {}
                    if source.get("type") == "journal":
                        loc_doi = loc.get("doi", "")
                        if loc_doi and loc_doi != preprint_doi:
                            published_doi = loc_doi.replace("https://doi.org/", "")
                            break
        except Exception:
            pass

    # Only cache positive resolutions. Caching None would pin an unpublished
    # preprint permanently, so it would never be re-checked once it is published.
    if published_doi:
        cache[preprint_doi] = published_doi
    return published_doi


def deduplicate(classifications, dry_run=False):
    """Remove preprint duplicates using preprint server metadata."""
    # Find all preprint DOIs
    preprint_dois = set()
    for c in classifications:
        doi = c.get("citing_doi", "")
        if is_preprint_doi(doi):
            preprint_dois.add(doi)

    if not preprint_dois:
        return classifications

    # Build set of all DOIs in classifications
    all_dois = set(c.get("citing_doi", "") for c in classifications)

    # Resolve preprint -> published DOI
    cache = _load_doi_cache()
    session = requests.Session()
    session.headers.update({"User-Agent": "FindReuse/1.0"})

    doi_map = {}  # preprint_doi -> published_doi
    n_resolved = 0
    n_cached = 0
    for i, doi in enumerate(sorted(preprint_dois)):
        # Trust only positive cache hits; re-resolve falsy (previously
        # unpublished) entries in case the preprint has since been published.
        if cache.get(doi):
            n_cached += 1
            doi_map[doi] = cache[doi]
            continue

        published = resolve_preprint_doi(doi, session, cache)
        if published:
            doi_map[doi] = published
            n_resolved += 1
        time.sleep(0.05)

        if (i + 1) % 50 == 0:
            _save_doi_cache(cache)
            print(f"  Resolved {i+1}/{len(preprint_dois)}...", file=sys.stderr)

    _save_doi_cache(cache)
    print(f"  Preprints: {len(preprint_dois)}, resolved: {n_resolved}, cached: {n_cached}, "
          f"with published version in data: {sum(1 for v in doi_map.values() if v.lower() in {d.lower() for d in all_dois})}",
          file=sys.stderr)

    # Find entries to remove: preprint entries where the published version
    # is also present for the same dandiset_id
    published_keys = set()
    for c in classifications:
        doi = c.get("citing_doi", "")
        if not is_preprint_doi(doi):
            did = c.get("dandiset_id", "")
            published_keys.add((doi.lower(), did))

    remove_indices = set()
    for i, c in enumerate(classifications):
        doi = c.get("citing_doi", "")
        if doi not in doi_map:
            continue
        published_doi = doi_map[doi]
        did = c.get("dandiset_id", "")
        if (published_doi.lower(), did) in published_keys:
            remove_indices.add(i)

    if dry_run:
        print(f"Would remove {len(remove_indices)} duplicate entries:")
        by_cls = defaultdict(int)
        for i in remove_indices:
            by_cls[classifications[i]["classification"]] += 1
        for cls_name, n in sorted(by_cls.items()):
            print(f"  {cls_name}: {n}")
        print(f"\nExamples:")
        for i in sorted(remove_indices)[:10]:
            c = classifications[i]
            pub = doi_map.get(c["citing_doi"], "?")
            print(f"  {c['citing_doi']} -> {pub} ({c.get('dandiset_id', '')} {c['classification']})")
        return classifications

    kept = [c for i, c in enumerate(classifications) if i not in remove_indices]
    return kept


# eLife publishes a paper under a base DOI (10.7554/eLife.NNNNN, the version of
# record) plus per-version DOIs (…NNNNN.1, .2, .3). These are the same work and
# should not be counted separately.
_ELIFE_RE = re.compile(r"^(10\.7554/elife\.\d+)(?:\.(\d+))?$", re.IGNORECASE)

# Preference order when collapsing duplicate versions of one work: keep the
# entry carrying the strongest evidence so a REUSE is never dropped in favor of
# a MENTION on another version.
_CLASS_RANK = {"REUSE": 4, "PRIMARY": 3, "MENTION": 2, "NEITHER": 1}


def _elife_base(doi):
    """Return the version-stripped eLife base DOI, or None if not an eLife DOI."""
    m = _ELIFE_RE.match((doi or "").strip())
    return m.group(1).lower() if m else None


def _keep_rank(c):
    """Sort key for choosing which entry of a versioned group to keep."""
    return (
        _CLASS_RANK.get(c.get("classification"), 0),
        c.get("text_length") or 0,
        c.get("confidence") or 0,
    )


def collapse_elife_versions(classifications, dry_run=False):
    """Collapse eLife versioned DOIs (…NNNNN.1/.2/.3 and base) to one entry.

    Versions are grouped per (base DOI, dandiset_id) — a paper may legitimately
    reuse several dandisets — and the entry with the strongest classification is
    kept (ties broken by text length, then confidence).
    """
    groups = defaultdict(list)
    for i, c in enumerate(classifications):
        base = _elife_base(c.get("citing_doi", ""))
        if base:
            groups[(base, c.get("dandiset_id", ""))].append(i)

    remove = set()
    for indices in groups.values():
        if len(indices) <= 1:
            continue
        keep = max(indices, key=lambda i: _keep_rank(classifications[i]))
        remove.update(i for i in indices if i != keep)

    if dry_run:
        print(f"Would collapse {len(remove)} eLife version duplicates", file=sys.stderr)
        return classifications

    return [c for i, c in enumerate(classifications) if i not in remove]


def main():
    parser = argparse.ArgumentParser(description="Deduplicate preprint/published pairs")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be removed")
    parser.add_argument("--input", type=str, default=None,
                        help="Input classifications file (default: output/all_classifications.json)")
    args = parser.parse_args()

    input_file = Path(args.input) if args.input else Path("output/all_classifications.json")
    with open(input_file) as f:
        data = json.load(f)

    before = len(data["classifications"])
    data["classifications"] = deduplicate(data["classifications"], dry_run=args.dry_run)
    data["classifications"] = collapse_elife_versions(data["classifications"], dry_run=args.dry_run)
    after = len(data["classifications"])

    if args.dry_run:
        return

    data["count"] = after
    with open(input_file, "w") as f:
        json.dump(data, f, indent=2)

    print(f"Deduplicated: {before} -> {after} ({before - after} removed)", file=sys.stderr)


if __name__ == "__main__":
    main()
