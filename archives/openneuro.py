"""OpenNeuro adapter (stub for future implementation)."""

from .base import ArchiveAdapter


class OpenNeuroAdapter(ArchiveAdapter):
    """OpenNeuro data repository adapter.

    OpenNeuro hosts BIDS-formatted neuroimaging data (MRI, EEG, MEG, iEEG).
    Uses a GraphQL API at https://openneuro.org/crn/graphql.
    Datasets have IDs like ds000001, ds004930.
    DOI prefix: 10.18112/openneuro
    """

    name = "OpenNeuro"
    short_name = "openneuro"
    search_terms = {
        "names": ["openneuro"],
        "urls": ["openneuro.org"],
        "search_terms": ["OpenNeuro"],
        "doi_prefixes": ["10.18112/openneuro"],
    }

    GRAPHQL_URL = "https://openneuro.org/crn/graphql"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        import requests
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "FindReuse/1.0"})

    def get_datasets(self) -> list[dict]:
        """Fetch all datasets from OpenNeuro GraphQL API."""
        datasets = []
        cursor = None
        page_size = 100

        while True:
            after_clause = f', after: "{cursor}"' if cursor else ""
            query = f"""
            {{
              datasets(first: {page_size}{after_clause}) {{
                edges {{
                  cursor
                  node {{
                    id
                    name
                    created
                    latestSnapshot {{
                      description {{
                        Name
                        License
                        Authors
                        DatasetDOI
                      }}
                      size
                      summary {{
                        subjects
                        modalities
                        totalFiles
                      }}
                    }}
                  }}
                }}
                pageInfo {{
                  hasNextPage
                  endCursor
                }}
              }}
            }}
            """

            try:
                resp = self.session.post(
                    self.GRAPHQL_URL,
                    json={"query": query},
                    timeout=30,
                )
                data = resp.json()
                edges = data.get("data", {}).get("datasets", {}).get("edges", [])
                page_info = data.get("data", {}).get("datasets", {}).get("pageInfo", {})

                for edge in edges:
                    node = edge["node"]
                    snapshot = node.get("latestSnapshot") or {}
                    desc = snapshot.get("description") or {}
                    summary = snapshot.get("summary") or {}

                    authors = desc.get("Authors", []) or []
                    doi = desc.get("DatasetDOI", "") or ""
                    if doi:
                        doi = doi.replace("doi:", "").replace("https://doi.org/", "").strip()

                    modalities = summary.get("modalities", []) or []

                    datasets.append({
                        "id": node["id"],
                        "name": desc.get("Name", node.get("name", "")),
                        "description": "",
                        "created": node.get("created", ""),
                        "doi": doi,
                        "url": f"https://openneuro.org/datasets/{node['id']}",
                        "contributors": authors[:10] if isinstance(authors, list) else [],
                        "modalities": modalities,
                        "size_bytes": snapshot.get("size", 0),
                        "n_subjects": len(summary.get("subjects", []) or []),
                        "n_files": summary.get("totalFiles", 0),
                        "license": desc.get("License", ""),
                    })

                if not page_info.get("hasNextPage"):
                    break
                cursor = page_info.get("endCursor")

            except Exception as e:
                self.log(f"GraphQL error: {e}")
                break

        self.log(f"Found {len(datasets)} OpenNeuro datasets")
        return datasets

    def get_primary_papers(self, dataset: dict) -> list[dict]:
        """Extract primary papers from OpenNeuro dataset metadata.

        Queries the GraphQL API for ReferencesAndLinks from the BIDS
        dataset_description.json, then extracts DOIs via regex.
        """
        import re
        papers = []
        did = dataset.get("id", "")

        # Query GraphQL for ReferencesAndLinks
        query = f"""
        {{
          dataset(id: "{did}") {{
            latestSnapshot {{
              description {{
                ReferencesAndLinks
              }}
            }}
          }}
        }}
        """
        try:
            resp = self.session.post(self.GRAPHQL_URL, json={"query": query}, timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                desc = (data.get("data", {}).get("dataset", {})
                        .get("latestSnapshot", {}).get("description", {})) or {}
                refs = desc.get("ReferencesAndLinks", []) or []

                for ref in refs:
                    if not isinstance(ref, str):
                        continue
                    # Extract DOIs from reference text
                    dois = re.findall(r"10\.\d{4,}/[^\s<>\"')\]]+", ref)
                    for doi in dois:
                        doi = doi.rstrip(".,;:")
                        if not any(p["doi"] == doi for p in papers):
                            papers.append({
                                "relation": "linked",
                                "doi": doi,
                                "source": "references_and_links",
                            })
        except Exception:
            pass

        return papers

    def get_metadata(self, dataset_id: str) -> dict:
        """Return metadata for Andersen-Gill regression covariates."""
        # Metadata was collected during get_datasets
        return {}

    def get_test_dataset_ids(self) -> set[str]:
        return set()
