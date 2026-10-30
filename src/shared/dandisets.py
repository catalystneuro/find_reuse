"""
Which dandisets DANDI holds data for.

A paper can only have reused a dandiset that has data in it. Several stages of
the pipeline choose which dandisets to work on, and they share this one test so
that none of them mines, classifies or lists a dandiset another has left out.
"""

from __future__ import annotations

import time

import requests
from tqdm import tqdm

DANDI_API = 'https://api.dandiarchive.org/api'


def get_all_dandisets(session: requests.Session, show_progress: bool = True) -> list[dict]:
    """
    Fetch all dandisets from the DANDI API.

    Args:
        session: requests Session object
        show_progress: Whether to show a progress bar

    Returns:
        List of dandiset metadata dictionaries
    """
    url = f"{DANDI_API}/dandisets/"
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


def holds_data(record: dict) -> bool:
    """
    Whether a dandiset record from DANDI's API has any data in it.

    A dandiset whose draft and most recent published version both have no
    assets and no size names a paper but holds nothing that can have been
    reused. This selects the same dandisets as the API's own empty=false filter.
    """
    return any((record.get(key) or {}).get('asset_count')
               or (record.get(key) or {}).get('size')
               for key in ('draft_version', 'most_recent_published_version'))


def dandisets_with_data(session: requests.Session | None = None,
                        show_progress: bool = False) -> set[str]:
    """
    The identifiers of every publicly listed dandiset that holds data.

    DANDI leaves embargoed dandisets out of the listing it gives an anonymous
    request (and answers 401 for one asked about directly), so neither they nor
    identifiers it never issued are in this set. Embargoed data is not available
    to reuse, and a dandiset joins the listing when its embargo lifts.
    """
    session = session or requests.Session()
    return {ds['identifier'] for ds in get_all_dandisets(session, show_progress)
            if holds_data(ds)}
