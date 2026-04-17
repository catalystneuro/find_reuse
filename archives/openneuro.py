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

        OpenNeuro datasets link to papers via the BIDS dataset_description.json
        References field, or through DataCite relatedIdentifiers.
        """
        papers = []

        # Check DataCite for relatedIdentifiers
        doi = dataset.get("doi", "")
        if doi:
            try:
                resp = self.session.get(
                    f"https://api.datacite.org/dois/{doi}",
                    timeout=10,
                )
                if resp.status_code == 200:
                    attrs = resp.json().get("data", {}).get("attributes", {})
                    for rel in attrs.get("relatedIdentifiers", []):
                        if rel.get("relationType") in ("IsDescribedBy", "IsSupplementTo", "References"):
                            rel_id = rel.get("relatedIdentifier", "")
                            if rel_id and "10." in rel_id:
                                papers.append({
                                    "relation": rel["relationType"],
                                    "doi": rel_id,
                                    "source": "datacite",
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
