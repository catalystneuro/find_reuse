"""CRCNS (Collaborative Research in Computational Neuroscience) adapter."""

import json
import re
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

from .base import ArchiveAdapter


# DOIs that appear on many CRCNS about pages but are not dataset-specific primary papers
CRCNS_INFRASTRUCTURE_DOIS = {
    "10.1007/s12021-008-9009-y",  # "Data Sharing for Computational Neuroscience" (Teeters et al. 2008)
}


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
        self._doi_to_code_cache_path = Path(".crcns_doi_to_code.json")

    def get_datasets(self) -> list[dict]:
        """Fetch all CRCNS datasets from DataCite API (DOI prefix 10.6080).

        Resolves DOIs to CRCNS dataset codes (e.g., hc-3, pvc-11) by following
        DOI redirects to crcns.org URLs. Results are cached.
        """
        # Fetch from DataCite
        datacite_records = []
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
                dates = attrs.get("dates", [])
                created = ""
                for d in dates:
                    if d.get("dateType") == "Created":
                        created = d.get("date", "")
                        break
                if not created and year:
                    created = f"{year}-01-01"

                datacite_records.append({
                    "doi": doi,
                    "title": title,
                    "description": self._get_description(attrs),
                    "creators": creators,
                    "year": year,
                    "created": created,
                })

            total = data.get("meta", {}).get("total", 0)
            if page * page_size >= total:
                break
            page += 1
            time.sleep(0.5)

        self.log(f"Found {len(datacite_records)} records in DataCite")

        # Resolve DOIs to CRCNS codes
        doi_to_code = self._resolve_doi_codes([r["doi"] for r in datacite_records])

        # Build dataset list
        datasets = []
        for rec in datacite_records:
            code = doi_to_code.get(rec["doi"], "")
            dataset_id = code or rec["doi"]
            datasets.append({
                "id": dataset_id,
                "name": rec["title"],
                "description": rec["description"],
                "created": rec["created"],
                "doi": rec["doi"],
                "url": f"https://crcns.org/data-sets/{code.split('-')[0]}/{code}" if code else f"https://doi.org/{rec['doi']}",
                "contributors": rec["creators"],
                "publication_year": rec["year"],
            })

        self._datacite_cache = datasets
        n_with_code = sum(1 for d in datasets if "-" in d["id"])
        self.log(f"Resolved {n_with_code}/{len(datasets)} to CRCNS codes")
        return datasets

    def _resolve_doi_codes(self, dois: list[str]) -> dict[str, str]:
        """Resolve DataCite DOIs to CRCNS dataset codes via HTTP redirect.

        Caches results to avoid repeated lookups.
        """
        # Load cache
        if self._doi_to_code_cache_path.exists():
            with open(self._doi_to_code_cache_path) as f:
                cache = json.load(f)
        else:
            cache = {}

        need_resolve = [d for d in dois if d not in cache]
        if need_resolve:
            self.log(f"Resolving {len(need_resolve)} DOIs to CRCNS codes...")
            for i, doi in enumerate(need_resolve):
                try:
                    resp = self.session.head(
                        f"https://doi.org/{doi}", allow_redirects=True, timeout=15
                    )
                    match = re.search(r"crcns\.org/(?:data-sets/\w+|[\w]+)/([\w]+-\d+)", resp.url)
                    if match:
                        cache[doi] = match.group(1)
                    else:
                        cache[doi] = ""  # mark as attempted
                except Exception:
                    cache[doi] = ""
                if (i + 1) % 30 == 0:
                    self.log(f"  {i + 1}/{len(need_resolve)}")
                time.sleep(0.3)

            with open(self._doi_to_code_cache_path, "w") as f:
                json.dump(cache, f, indent=2)

        return {doi: code for doi, code in cache.items() if code}

    def get_primary_papers(self, dataset: dict) -> list[dict]:
        """Find primary papers for a CRCNS dataset.

        Scrapes the dataset's 'about' page for paper DOIs. Uses the actual
        scraped URL from .crcns_codes.json (since URL category paths don't
        always match dataset code prefixes, e.g. pvc-5 is under /vc/ not /pvc/).
        """
        papers = []
        dataset_id = dataset["id"]

        # Get the actual URL for this dataset
        base_url = self._get_dataset_url(dataset_id)
        if not base_url:
            return papers

        for suffix in ["/about", f"/about-{dataset_id}", ""]:
            try:
                url = base_url.rstrip("/") + suffix
                resp = self.session.get(url, timeout=10)
                if resp.status_code != 200:
                    continue
                dois = re.findall(r"10\.\d{4,}/[^\s<\"&]+", resp.text)
                dois = [d.rstrip(".,;:/") for d in dois]
                paper_dois = [d for d in dois
                              if not d.startswith("10.6080/")
                              and d.lower() not in CRCNS_INFRASTRUCTURE_DOIS]
                for doi in paper_dois[:3]:
                    if not any(p["doi"] == doi for p in papers):
                        papers.append({
                            "relation": "linked",
                            "doi": doi,
                            "source": "about_page",
                        })
                if papers:
                    break
            except Exception:
                pass

        return papers

    def _get_dataset_url(self, dataset_id: str) -> str:
        """Get the actual CRCNS URL for a dataset code using scraped mapping."""
        codes_path = Path(".crcns_codes.json")
        if codes_path.exists():
            with open(codes_path) as f:
                codes = json.load(f)
            if dataset_id in codes:
                return codes[dataset_id]
        return ""

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
