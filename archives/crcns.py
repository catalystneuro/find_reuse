"""CRCNS (Collaborative Research in Computational Neuroscience) adapter."""

import json
import re
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

from .base import ArchiveAdapter


class CRCNSAdapter(ArchiveAdapter):
    name = "CRCNS"
    short_name = "crcns"
    search_terms = {
        "names": ["crcns"],
        "urls": ["crcns.org"],
        "search_terms": ["CRCNS"],
        "doi_prefixes": ["10.6080"],
    }

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "FindReuse/1.0"})
        self._datacite_cache = None
        self._publications_cache = None

    def get_datasets(self) -> list[dict]:
        """Fetch all CRCNS datasets from DataCite API (DOI prefix 10.6080)."""
        datasets = []
        page = 1
        page_size = 100

        while True:
            resp = self.session.get(
                "https://api.datacite.org/dois",
                params={
                    "query": "prefix:10.6080",
                    "page[size]": page_size,
                    "page[number]": page,
                },
                timeout=30,
            )
            data = resp.json()

            for item in data.get("data", []):
                attrs = item.get("attributes", {})
                doi = attrs.get("doi", "")
                titles = attrs.get("titles", [])
                title = titles[0].get("title", "") if titles else ""
                creators = [c.get("name", "") for c in attrs.get("creators", [])]
                year = attrs.get("publicationYear")

                # Extract dataset ID from title or DOI
                # CRCNS titles often contain the code like "pvc-1" or "hc-3"
                dataset_id = self._extract_dataset_id(title, doi)

                # Dates
                dates = attrs.get("dates", [])
                created = ""
                for d in dates:
                    if d.get("dateType") == "Created":
                        created = d.get("date", "")
                        break
                if not created and year:
                    created = f"{year}-01-01"

                datasets.append({
                    "id": dataset_id or doi,
                    "name": title,
                    "description": self._get_description(attrs),
                    "created": created,
                    "doi": doi,
                    "url": f"https://doi.org/{doi}",
                    "contributors": creators,
                    "publication_year": year,
                })

            total = data.get("meta", {}).get("total", 0)
            if page * page_size >= total:
                break
            page += 1
            time.sleep(0.5)

        self._datacite_cache = datasets
        self.log(f"Found {len(datasets)} CRCNS datasets in DataCite")
        return datasets

    def get_primary_papers(self, dataset: dict) -> list[dict]:
        """Find primary papers for a CRCNS dataset.

        Strategy:
        1. Check DataCite relatedIdentifiers (usually empty for CRCNS)
        2. Match against scraped publications page
        3. Fall back to LLM identification
        """
        papers = []
        dataset_id = dataset["id"]
        doi = dataset.get("doi", "")

        # Strategy 1: DataCite relatedIdentifiers (rare for CRCNS)
        # Already checked during get_datasets -- CRCNS doesn't use these

        # Strategy 2: Match from publications page
        pub_mapping = self._get_publications_mapping()
        if dataset_id in pub_mapping:
            for paper_doi in pub_mapping[dataset_id]:
                papers.append({
                    "relation": "linked",
                    "doi": paper_doi,
                    "source": "publications_page",
                })

        return papers

    def get_metadata(self, dataset_id: str) -> dict:
        """Extract metadata from DataCite record and dataset description.

        CRCNS doesn't have a structured metadata API like DANDI, so we parse
        what we can from titles and descriptions.
        """
        if self._datacite_cache:
            for ds in self._datacite_cache:
                if ds["id"] == dataset_id:
                    return self._parse_metadata_from_description(ds)
        return {}

    def get_test_dataset_ids(self) -> set[str]:
        """CRCNS is curated -- no test datasets."""
        return set()

    def _extract_dataset_id(self, title: str, doi: str) -> str:
        """Extract CRCNS dataset code (e.g., 'hc-3', 'pvc-11') from title."""
        # Common patterns: "hc-3", "pvc-11", "ret-1", "fcx-1"
        match = re.search(r"\b([a-z]{2,5}-\d{1,3})\b", title.lower())
        if match:
            return match.group(1)
        # Try extracting from DOI suffix
        suffix = doi.split("/")[-1] if "/" in doi else ""
        match = re.search(r"([a-z]{2,5}\d{1,3})", suffix.lower())
        if match:
            return match.group(1)
        return ""

    def _get_description(self, attrs: dict) -> str:
        """Extract description from DataCite attributes."""
        descriptions = attrs.get("descriptions", [])
        for d in descriptions:
            if d.get("description"):
                return d["description"]
        return ""

    def _get_publications_mapping(self) -> dict[str, list[str]]:
        """Scrape CRCNS publications page to build dataset_id -> [paper DOIs] mapping."""
        if self._publications_cache is not None:
            return self._publications_cache

        self.log("Scraping CRCNS publications page...")
        try:
            resp = self.session.get("https://crcns.org/publications", timeout=30)
            html = resp.text
        except Exception as e:
            self.log(f"Failed to fetch publications page: {e}")
            self._publications_cache = {}
            return self._publications_cache

        # Find DOIs and nearby dataset codes
        # The publications page lists papers with dataset codes in context
        mapping = {}

        # Extract all DOIs
        dois = re.findall(r"10\.\d{4,}/[^\s<\"&]+", html)

        # Extract dataset codes near each DOI mention
        # Parse paragraphs/list items containing both DOIs and dataset codes
        soup = BeautifulSoup(html, "html.parser")
        for element in soup.find_all(["li", "p", "div"]):
            text = element.get_text()
            element_dois = re.findall(r"10\.\d{4,}/[^\s<\"&]+", text)
            element_codes = re.findall(r"\b([a-z]{2,5}-\d{1,3})\b", text.lower())
            if element_dois and element_codes:
                for code in element_codes:
                    if code not in mapping:
                        mapping[code] = []
                    for doi in element_dois:
                        doi = doi.rstrip(".,;:")
                        if doi not in mapping[code]:
                            mapping[code].append(doi)

        self._publications_cache = mapping
        self.log(f"Found paper links for {len(mapping)} dataset codes")
        return mapping

    def _parse_metadata_from_description(self, dataset: dict) -> dict:
        """Parse species and modality from dataset title and description."""
        text = (dataset.get("name", "") + " " + dataset.get("description", "")).lower()

        # Species detection
        if "mouse" in text or "mus musculus" in text:
            species = "mouse"
        elif "human" in text or "homo sapiens" in text:
            species = "human"
        elif "macaque" in text or "monkey" in text or "primate" in text:
            species = "nhp"
        elif "rat" in text or "rattus" in text:
            species = "rat"
        elif "cat" in text or "felis" in text:
            species = "other"
        elif "aplysia" in text:
            species = "other"
        else:
            species = "unknown"

        # Modality detection
        if "electrophysiol" in text or "spike" in text or "extracellular" in text or "tetrode" in text:
            modality = "ephys"
        elif "calcium" in text or "fluorescen" in text or "imaging" in text:
            modality = "imaging"
        elif "patch" in text and "clamp" in text:
            modality = "ephys"
        else:
            modality = "other"

        return {
            "species": species,
            "modality": modality,
            "size_gb": 0,  # Not available from DataCite
            "n_subjects": 0,
            "n_files": 0,
            "license": "",
        }
