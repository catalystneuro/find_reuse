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

Citation counts include:
- Total citations (all time) from OpenAlex
- Citations after dandiset creation (potential reuse indicators)
"""

import argparse
import json
import re
import sys
import time
from typing import Optional

import requests
from tqdm import tqdm


# DANDI API base URL
DANDI_API_URL = "https://api.dandiarchive.org/api"

# Relation types that indicate a primary/describing paper relationship
# These are DataCite relation types (dcite:)
PRIMARY_PAPER_RELATIONS = {
    'dcite:IsDescribedBy',   # Most common - dataset is described by the paper
    'dcite:IsPublishedIn',   # Dataset is published in the paper
    'dcite:IsSupplementTo',  # Dataset supplements the paper (data for the paper)
}

# Additional relation types that may link to papers but aren't primary descriptors
SECONDARY_PAPER_RELATIONS = {
    'dcite:Describes',       # Reverse of IsDescribedBy (paper describes dataset)
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

    return None


def get_dandiset_creation_dates(
    session: requests.Session,
    dandiset_ids: list[str],
    show_progress: bool = True
) -> dict[str, str]:
    """
    Fetch creation dates for a list of dandisets.

    Args:
        session: requests Session object
        dandiset_ids: List of dandiset identifiers
        show_progress: Whether to show progress bar

    Returns:
        Dictionary mapping dandiset_id to creation date (ISO format string)
    """
    from datetime import datetime

    creation_dates = {}
    pbar = tqdm(dandiset_ids, desc="Fetching dandiset creation dates", disable=not show_progress)

    for ds_id in pbar:
        try:
            resp = session.get(f"{DANDI_API_URL}/dandisets/{ds_id}/", timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                created_str = data.get('created', '')
                if created_str:
                    creation_dates[ds_id] = created_str
        except requests.RequestException:
            pass
        time.sleep(0.02)

    return creation_dates


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
    from datetime import datetime

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
    - Dandiset creation dates
    - Paper publication dates and total citation counts from OpenAlex
    - Citation counts filtered to only include papers published after dandiset creation

    Args:
        results: List of results from find_dandisets_with_primary_papers
        show_progress: Whether to show progress bars
        rate_limit: Delay between API calls in seconds

    Returns:
        Updated results with citation data added to each paper relation
    """
    from datetime import datetime

    session = requests.Session()
    session.headers.update({
        'User-Agent': 'DANDIPrimaryPapers/1.0 (https://github.com/dandi; mailto:ben.dichter@catalystneuro.com)'
    })

    # Step 1: Get dandiset creation dates
    dandiset_ids = [r['dandiset_id'] for r in results]
    creation_dates = get_dandiset_creation_dates(session, dandiset_ids, show_progress)

    # Step 2: Get OpenAlex data for all unique DOIs
    all_dois = set()
    for result in results:
        for paper in result['paper_relations']:
            if paper.get('doi'):
                all_dois.add(paper['doi'])

    doi_data = {}
    pbar = tqdm(all_dois, desc="Fetching paper data from OpenAlex", disable=not show_progress)

    for doi in pbar:
        pbar.set_postfix({'doi': doi[:30]})
        data = get_openalex_paper_data(session, doi)
        if data:
            doi_data[doi] = data
        time.sleep(rate_limit)

    # Step 3: For each paper, get citations after dandiset creation
    pbar = tqdm(results, desc="Fetching citations after dandiset creation", disable=not show_progress)

    for result in pbar:
        ds_id = result['dandiset_id']
        pbar.set_postfix({'dandiset': ds_id})

        # Parse dandiset creation date
        ds_created_str = creation_dates.get(ds_id)
        ds_created = None
        if ds_created_str:
            try:
                ds_created = datetime.fromisoformat(ds_created_str.replace('Z', '+00:00'))
                result['dandiset_created'] = ds_created_str
            except ValueError:
                result['dandiset_created'] = None
        else:
            result['dandiset_created'] = None

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

            # Get citations after dandiset creation
            if ds_created and paper_info.get('openalex_id'):
                from_date = ds_created.strftime('%Y-%m-%d')
                citations_after = get_citations_after_date(
                    session, paper_info['openalex_id'], from_date
                )
                paper['citations_after_dandiset_created'] = citations_after
                if citations_after:
                    total_citations_after += citations_after
                time.sleep(rate_limit)
            else:
                paper['citations_after_dandiset_created'] = None

        result['total_citations'] = total_citations
        result['total_citations_after_created'] = total_citations_after

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
    rate_limit: float = 0.1
) -> list[dict]:
    """
    Find all dandisets that have relatedResource entries linking to primary papers.

    Args:
        include_secondary: If True, also include secondary relation types
        show_progress: Whether to show progress bars
        rate_limit: Delay between API calls in seconds

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

    # Fetch all dandisets
    dandisets = get_all_dandisets(session, show_progress)

    results = []

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
            results.append({
                'dandiset_id': ds_id,
                'dandiset_name': version_info.get('name'),
                'dandiset_version': version,
                'dandiset_url': f"https://dandiarchive.org/dandiset/{ds_id}/{version}",
                'dandiset_doi': metadata.get('doi'),
                'contact_person': ds.get('contact_person'),
                'paper_relations': paper_resources,
            })

        time.sleep(rate_limit)

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

    args = parser.parse_args()

    # Find dandisets with paper relations
    results = find_dandisets_with_primary_papers(
        include_secondary=args.all_relations,
        show_progress=not args.no_progress,
    )

    # Optionally fetch citation counts
    if args.citations:
        results = add_citation_counts(results, show_progress=not args.no_progress)

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
