"""SPARC (Stimulating Peripheral Activity to Relieve Conditions) adapter.

Uses the Pennsieve Discover API to access SPARC datasets.
API docs: https://docs.sparc.science/docs/sparc-apis-and-open-access-code
"""

import json
import re
import time
from pathlib import Path

import requests

from .base import ArchiveAdapter


class SPARCAdapter(ArchiveAdapter):
    """SPARC data repository adapter via Pennsieve Discover API."""

    name = "SPARC"
    short_name = "sparc"
    search_terms = {
        "names": ["sparc"],
        "urls": ["sparc.science", "pennsieve.io"],
        "search_terms": ["SPARC"],
        "doi_prefixes": ["10.26275"],
    }

    API_BASE = "https://api.pennsieve.io/discover"

    # Test/embargo datasets to exclude
    TEST_TAGS = {"test", "embargo", "testing"}

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "FindReuse/1.0"})

    def get_datasets(self) -> list[dict]:
        """Fetch all SPARC datasets from Pennsieve Discover API."""
        datasets = []
        offset = 0
        limit = 100

        while True:
            resp = self.session.get(
                f"{self.API_BASE}/datasets",
                params={
                    "limit": limit,
                    "offset": offset,
                    "orderBy": "date",
                    "orderDirection": "asc",
                    "organizationId": 367,  # SPARC organization
                },
                timeout=30,
            )
            if resp.status_code != 200:
                self.log(f"API error: {resp.status_code}")
                break

            data = resp.json()
            total = data.get("totalCount", 0)

            for ds in data.get("datasets", []):
                # Skip test datasets
                tags = set(t.lower() for t in ds.get("tags", []))
                if tags & self.TEST_TAGS:
                    continue

                contributors = []
                for c in ds.get("contributors", []):
                    name = f"{c.get('lastName', '')}, {c.get('firstName', '')}"
                    contributors.append(name.strip(", "))

                datasets.append({
                    "id": str(ds["id"]),
                    "name": ds.get("name", ""),
                    "description": ds.get("description", ""),
                    "created": ds.get("firstPublishedAt", ds.get("createdAt", "")),
                    "doi": ds.get("doi", ""),
                    "url": f"https://sparc.science/datasets/{ds['id']}",
                    "contributors": contributors,
                    "size_bytes": ds.get("size", 0),
                    "n_files": ds.get("fileCount", 0),
                    "n_subjects": sum(
                        m.get("count", 0)
                        for m in ds.get("modelCount", [])
                        if m.get("modelName") in ("animal_subject", "subject")
                    ),
                    "tags": list(tags),
                    "license": ds.get("license", ""),
                })

            offset += limit
            if offset >= total:
                break
            time.sleep(0.2)

        self.log(f"Found {len(datasets)} SPARC datasets")
        return datasets

    def get_primary_papers(self, dataset: dict) -> list[dict]:
        """Extract primary papers from externalPublications field."""
        papers = []
        did = dataset["id"]

        try:
            resp = self.session.get(f"{self.API_BASE}/datasets/{did}", timeout=15)
            if resp.status_code == 200:
                ds = resp.json()
                for pub in ds.get("externalPublications", []):
                    doi = pub.get("doi", "")
                    rel = pub.get("relationshipType", "")
                    if doi and rel in ("IsDescribedBy", "IsPublishedIn",
                                       "IsSupplementTo", "Describes", "References"):
                        # Filter out protocol DOIs
                        if "protocols.io" in doi:
                            continue
                        papers.append({
                            "relation": rel,
                            "doi": doi,
                            "source": "external_publications",
                        })
        except Exception:
            pass

        return papers

    def get_metadata(self, dataset_id: str) -> dict:
        """Return metadata for Andersen-Gill regression."""
        try:
            resp = self.session.get(f"{self.API_BASE}/datasets/{dataset_id}", timeout=15)
            if resp.status_code == 200:
                ds = resp.json()
                tags = set(t.lower() for t in ds.get("tags", []))
                description = (ds.get("description", "") + " " + ds.get("name", "")).lower()

                # Species detection
                species = "unknown"
                for s, kw in [("mouse", ["mouse", "mice", "murine"]),
                              ("rat", ["rat ", "rats "]),
                              ("human", ["human", "patient"]),
                              ("pig", ["pig", "porcine", "swine"]),
                              ("cat", ["cat ", "feline"])]:
                    if any(k in description for k in kw):
                        species = s
                        break

                n_subjects = sum(
                    m.get("count", 0)
                    for m in ds.get("modelCount", [])
                    if m.get("modelName") in ("animal_subject", "subject")
                )

                return {
                    "species": species,
                    "size_gb": ds.get("size", 0) / 1e9,
                    "n_subjects": n_subjects,
                    "n_files": ds.get("fileCount", 0),
                    "license": ds.get("license", ""),
                }
        except Exception:
            pass
        return {}

    def get_test_dataset_ids(self) -> set[str]:
        return set()
