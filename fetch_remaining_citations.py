#!/usr/bin/env python3
"""Fetch citing papers from OpenAlex for dandisets that don't have them yet.
Saves progress every 20 dandisets."""

import json
import sys
import time
import requests
from datetime import datetime


def get_citing_papers(session, doi, created_date, max_results=999):
    """Query OpenAlex for papers citing this DOI after the dandiset was created."""
    works_url = "https://api.openalex.org/works"

    # Resolve DOI to OpenAlex work ID
    try:
        id_resp = session.get(f"https://api.openalex.org/works/doi:{doi}", timeout=15)
        if id_resp.status_code != 200:
            return []
        oa_id = id_resp.json().get("id", "")
        if not oa_id:
            return []
    except Exception:
        return []

    # Build filter
    filters = [f"cites:{oa_id}"]
    if created_date:
        try:
            dt = datetime.fromisoformat(created_date.replace('Z', '+00:00'))
            filters.append(f"from_publication_date:{dt.strftime('%Y-%m-%d')}")
        except (ValueError, TypeError):
            pass

    all_results = []
    page = 1
    per_page = 200

    while len(all_results) < max_results:
        params = {
            "filter": ",".join(filters),
            "per_page": min(per_page, max_results - len(all_results)),
            "page": page,
            "select": "doi,title,publication_date,primary_location",
        }

        try:
            resp = session.get(works_url, params=params, timeout=30)
            if resp.status_code != 200:
                break
            data = resp.json()
            results = data.get("results", [])
            if not results:
                break

            for work in results:
                doi_val = work.get("doi", "")
                if doi_val:
                    doi_val = doi_val.replace("https://doi.org/", "")
                loc = work.get("primary_location", {}) or {}
                source = loc.get("source", {}) or {}
                all_results.append({
                    "doi": doi_val,
                    "title": work.get("title", ""),
                    "publication_date": work.get("publication_date", ""),
                    "journal": source.get("display_name", ""),
                    "cited_paper_doi": doi,
                })

            if len(results) < per_page:
                break
            page += 1
            time.sleep(0.1)

        except Exception as e:
            print(f"    Error querying OpenAlex: {e}", file=sys.stderr)
            break

    return all_results


def main():
    with open("output/all_dandiset_papers.json") as f:
        data = json.load(f)

    session = requests.Session()
    session.headers.update({"User-Agent": "FindReuse/1.0 (mailto:ben.dichter@catalystneuro.com)"})

    results = data["results"]
    needs = [r for r in results if not r.get("citing_papers")]
    print(f"Need citing papers: {len(needs)}/{len(results)}", file=sys.stderr)

    updated = 0
    for r in results:
        if r.get("citing_papers"):
            continue

        all_citing = []
        created = r.get("dandiset_created", "")

        for paper in r.get("paper_relations", []):
            doi = paper.get("doi")
            if not doi:
                continue
            citing = get_citing_papers(session, doi, created)
            all_citing.extend(citing)
            if citing:
                print(f"  {r['dandiset_id']}/{doi}: {len(citing)} citing papers", file=sys.stderr)

        r["citing_papers"] = all_citing
        updated += 1

        if updated % 20 == 0:
            print(f"  Progress: {updated}/{len(needs)} done, saving...", file=sys.stderr)
            with open("output/all_dandiset_papers.json", "w") as f:
                json.dump(data, f, indent=2)

    with open("output/all_dandiset_papers.json", "w") as f:
        json.dump(data, f, indent=2)

    total = sum(len(r.get("citing_papers", [])) for r in results)
    print(f"\nDone. Updated {updated}. Total citing: {total}", file=sys.stderr)


if __name__ == "__main__":
    main()
