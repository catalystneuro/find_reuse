"""DANDI Archive adapter."""

import json
import re
from pathlib import Path

import requests

from .base import ArchiveAdapter

# Dandisets with "test" in the name
TEST_DANDISET_IDS = {
    "000027", "000029", "000032", "000033", "000038", "000047", "000068", "000071",
    "000112", "000116", "000118", "000120", "000123", "000124", "000126", "000135",
    "000144", "000145", "000150", "000151", "000154", "000160", "000161", "000162",
    "000164", "000171", "000241", "000299", "000335", "000346", "000349", "000400",
    "000411", "000445", "000470", "000478", "000490", "000529", "000536", "000539",
    "000543", "000544", "000545", "000567", "000712", "000730", "000733", "000881",
    "000942", "000960", "001022", "001049", "001061", "001066", "001083", "001085",
    "001133", "001175", "001698", "001758",
}

PAPER_RELATION_TYPES = {
    "dcite:IsDescribedBy", "dcite:IsPublishedIn",
    "dcite:IsSupplementTo", "dcite:Describes",
}

PAPER_RESOURCE_TYPES = {
    "dcite:JournalArticle", "dcite:Preprint", "dcite:DataPaper",
    "dcite:ConferencePaper", "dcite:ConferenceProceeding",
}

EXCLUDE_RESOURCE_TYPES = {
    "dcite:Software", "dcite:Dataset", "dcite:ComputationalNotebook",
}


class DANDIAdapter(ArchiveAdapter):
    name = "DANDI Archive"
    short_name = "dandi"
    search_terms = {
        "names": ["dandi", "dandiarchive"],
        "urls": ["dandiarchive.org"],
        "search_terms": ["dandiset", "DANDI Archive"],
        "doi_prefixes": ["10.48324/dandi"],
        "exclude": [
            "dandi bioscience", "dandi bio", "roberto dandi",
            "dandi march", "dandi district", "lake dandi", "meta robi",
        ],
    }
    dataset_patterns = [
        (r'10\.48324/dandi\.(\d{6})(?:/[\d.]+)?', 'doi'),
        (r'10\.80507/dandi\.(\d{6})(?:/[\d.]+)?', 'placeholder_doi'),
        (r'dandiarchive\.org/dandiset/(\d{6})', 'url'),
        (r'gui\.dandiarchive\.org/#/dandiset/(\d{6})', 'gui_url'),
        (r'DANDI:\s*(\d{6})', 'text_colon'),
        (r'DANDI\s+(\d{6})', 'text_space'),
        (r'dandiset\s+(\d{6})', 'dandiset_text'),
        (r'dandiset/(\d{6})', 'dandiset_path'),
        (r'DANDI(?:\s+archive)?(?:\s+identifier)?[,:\s]+(\d{6})', 'identifier'),
        (r'(?:dandiarchive\.org|DANDI)[,\s]+ID[:\s]*(\d{6})', 'id_format'),
        (r'DANDI\s+ID#[:\s]+(\d{6})', 'id_hash_format'),
        (r'DANDI\s+Archive\s+ID[:\s]+(\d{6})', 'archive_id'),
        (r'DANDI\s*\(dataset\s+IDs?:?\s*(\d{6})', 'dataset_ids_paren'),
        (r'DANDI\s*\([^)]*\band\s+(\d{6})\)', 'dataset_ids_paren_and'),
    ]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.session = requests.Session()
        self.api_base = "https://api.dandiarchive.org/api"

    def get_datasets(self) -> list[dict]:
        """Fetch all public, non-empty dandisets from the DANDI API."""
        datasets = []
        page = 1
        while True:
            resp = self.session.get(
                f"{self.api_base}/dandisets/?page_size=200&page={page}",
                timeout=15,
            )
            data = resp.json()
            for ds in data.get("results", []):
                draft = ds.get("draft_version", {})
                # Filter to non-empty
                if draft.get("asset_count", 0) == 0 and draft.get("size", 0) == 0:
                    continue

                datasets.append({
                    "id": ds["identifier"],
                    "name": draft.get("name", ""),
                    "description": "",  # fetched separately if needed
                    "created": ds.get("created", ""),
                    "doi": "",  # dandiset DOI fetched from version
                    "url": f"https://dandiarchive.org/dandiset/{ds['identifier']}",
                    "contributors": [ds.get("contact_person", "")],
                })
            if not data.get("next"):
                break
            page += 1

        self.log(f"Found {len(datasets)} non-empty dandisets")
        return datasets

    def get_primary_papers(self, dataset: dict) -> list[dict]:
        """Extract primary papers from dandiset relatedResource metadata."""
        did = dataset["id"]
        papers = []

        try:
            resp = self.session.get(
                f"{self.api_base}/dandisets/{did}/versions/draft/",
                timeout=10,
            )
            if resp.status_code != 200:
                return papers
            version = resp.json()
        except Exception:
            return papers

        # Extract from relatedResource
        for res in version.get("relatedResource", []):
            relation = res.get("relation", "")
            if relation not in PAPER_RELATION_TYPES:
                continue
            resource_type = res.get("resourceType", "")
            if resource_type in EXCLUDE_RESOURCE_TYPES:
                continue
            if resource_type and resource_type not in PAPER_RESOURCE_TYPES:
                continue

            doi = self._extract_doi(res)
            if doi:
                papers.append({
                    "relation": relation,
                    "url": res.get("url", ""),
                    "name": res.get("name", ""),
                    "identifier": res.get("identifier", ""),
                    "resource_type": resource_type,
                    "doi": doi,
                    "source": "relatedResource",
                })

        # Extract DOIs from description
        description = version.get("description", "") or ""
        doi_pattern = re.compile(r"10\.\d{4,9}/[^\s<>\"')\]]+")
        for match in doi_pattern.finditer(description):
            doi = match.group().rstrip(".,;:")
            if doi and not any(p["doi"].lower() == doi.lower() for p in papers):
                papers.append({
                    "relation": "dcite:IsDescribedBy",
                    "doi": doi,
                    "source": "description",
                })

        return papers

    def get_metadata(self, dataset_id: str) -> dict:
        """Fetch species, modality, size from DANDI API."""
        try:
            resp = self.session.get(
                f"{self.api_base}/dandisets/{dataset_id}/versions/draft/",
                timeout=10,
            )
            if resp.status_code != 200:
                return {}
            m = resp.json()
            summary = m.get("assetsSummary", {})

            # Species
            species_list = summary.get("species", [])
            species_raw = species_list[0].get("name", "") if species_list else ""
            if "mus musculus" in species_raw.lower() or "house mouse" in species_raw.lower():
                species = "mouse"
            elif "homo sapiens" in species_raw.lower() or "human" in species_raw.lower():
                species = "human"
            elif "rattus" in species_raw.lower() or "norway rat" in species_raw.lower():
                species = "rat"
            elif "macaca" in species_raw.lower() or "rhesus" in species_raw.lower():
                species = "nhp"
            elif species_raw:
                species = "other"
            else:
                species = "unknown"

            # Modality
            approaches = [a.get("name", "") for a in summary.get("approach", [])]
            has_ephys = any("electrophysiol" in a.lower() for a in approaches)
            has_imaging = any("microscopy" in a.lower() or "imaging" in a.lower() for a in approaches)
            if has_ephys and has_imaging:
                modality = "multimodal"
            elif has_ephys:
                modality = "ephys"
            elif has_imaging:
                modality = "imaging"
            else:
                modality = "other"

            license_list = m.get("license", [])
            license_id = license_list[0] if license_list else ""

            return {
                "species": species,
                "modality": modality,
                "size_gb": (summary.get("numberOfBytes") or 0) / 1e9,
                "n_subjects": summary.get("numberOfSubjects") or 0,
                "n_files": summary.get("numberOfFiles") or 0,
                "license": license_id,
            }
        except Exception:
            return {}

    def get_test_dataset_ids(self) -> set[str]:
        return TEST_DANDISET_IDS

    def _extract_doi(self, resource: dict) -> str:
        """Extract DOI from a relatedResource entry."""
        # Try identifier field
        identifier = resource.get("identifier", "")
        if identifier:
            doi_match = re.search(r"10\.\d{4,9}/[^\s]+", identifier)
            if doi_match:
                return doi_match.group().rstrip(".,;:")

        # Try URL field
        url = resource.get("url", "")
        if url:
            # Publisher URL patterns
            if "elifesciences.org/articles/" in url:
                article_id = url.split("/articles/")[-1].split("?")[0].split("/")[0]
                return f"10.7554/eLife.{article_id}"
            if "nature.com/articles/" in url:
                slug = url.split("/articles/")[-1].split("?")[0]
                return f"10.1038/{slug}"
            doi_match = re.search(r"10\.\d{4,9}/[^\s?#]+", url)
            if doi_match:
                return doi_match.group().rstrip(".,;:")

        return ""
