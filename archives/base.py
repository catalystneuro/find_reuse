"""Base class for archive adapters."""

from abc import ABC, abstractmethod
from pathlib import Path


class ArchiveAdapter(ABC):
    """Base class for data archive adapters.

    Each archive (DANDI, CRCNS, OpenNeuro, etc.) implements this interface
    to provide dataset metadata, primary paper links, and search configuration
    for the reuse analysis pipeline.
    """

    # Subclasses must set these
    name: str = ""  # e.g., "DANDI Archive", "CRCNS"
    short_name: str = ""  # e.g., "dandi", "crcns"

    # Search configuration for find_reuse.py direct reference discovery
    search_terms: dict = {}  # ARCHIVE_SEARCH_TERMS entry

    # Regex patterns for extracting dataset IDs from paper text
    # List of (regex_pattern, pattern_type_name) tuples
    # The first capture group should be the dataset ID
    dataset_patterns: list = []  # ARCHIVE_PATTERNS entry

    def __init__(self, output_dir: str | Path | None = None, verbose: bool = False):
        self.verbose = verbose
        if output_dir:
            self.output_dir = Path(output_dir)
        else:
            self.output_dir = Path("output") / self.short_name
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def log(self, msg: str):
        if self.verbose:
            import sys
            print(f"[{self.short_name}] {msg}", file=sys.stderr)

    @abstractmethod
    def get_datasets(self) -> list[dict]:
        """Return all datasets in the archive.

        Each dataset dict should have at minimum:
            - id: str (archive-specific identifier, e.g., "000017" or "hc-3")
            - name: str
            - description: str
            - created: str (ISO date)
            - doi: str (dataset DOI, if any)
            - url: str (dataset landing page URL)
            - contributors: list[str] (contributor names)
        """

    @abstractmethod
    def get_primary_papers(self, dataset: dict) -> list[dict]:
        """Return primary papers linked to a dataset.

        Each paper dict should have:
            - doi: str
            - title: str (optional)
            - relation: str (e.g., "dcite:IsDescribedBy", "linked", "llm")
            - source: str (how the link was discovered)
        """

    @abstractmethod
    def get_metadata(self, dataset_id: str) -> dict:
        """Return metadata for Andersen-Gill regression covariates.

        Should return dict with any available fields:
            - species: str ("mouse", "human", "nhp", "rat", "other", "unknown")
            - modality: str ("ephys", "imaging", "multimodal", "other")
            - size_gb: float
            - n_subjects: int
            - n_files: int
            - license: str
        """

    def get_test_dataset_ids(self) -> set[str]:
        """Return IDs of test/placeholder datasets to exclude.

        Override in subclass if the archive has test datasets.
        """
        return set()

    def build_datasets_json(self) -> dict:
        """Build the unified datasets JSON file.

        Calls get_datasets() and get_primary_papers() for each dataset.
        Returns the data structure and saves to output_dir/datasets.json.
        """
        import json

        self.log("Fetching dataset catalog...")
        datasets = self.get_datasets()
        self.log(f"Found {len(datasets)} datasets")

        test_ids = self.get_test_dataset_ids()
        datasets = [d for d in datasets if d["id"] not in test_ids]
        self.log(f"After excluding {len(test_ids)} test datasets: {len(datasets)}")

        results = []
        for i, ds in enumerate(datasets):
            if (i + 1) % 50 == 0:
                self.log(f"  Processing {i + 1}/{len(datasets)}...")

            papers = self.get_primary_papers(ds)

            results.append({
                "dandiset_id": ds["id"],  # keep field name for compatibility
                "dandiset_name": ds["name"],
                "dandiset_url": ds.get("url", ""),
                "dandiset_doi": ds.get("doi", ""),
                "dandiset_created": ds.get("created", ""),
                "data_accessible": ds.get("data_accessible", ds.get("created", "")),
                "contact_person": ds.get("contributors", [""])[0] if ds.get("contributors") else "",
                "paper_relations": papers,
                "total_citations": 0,
                "total_citations_after_created": 0,
                "citing_papers": [],
            })

        data = {"count": len(results), "results": results}

        out_path = self.output_dir / "datasets.json"
        with open(out_path, "w") as f:
            json.dump(data, f, indent=2)
        self.log(f"Saved {out_path}")

        return data
