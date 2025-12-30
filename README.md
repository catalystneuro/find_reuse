# find_reuse

Find dataset references in scientific papers.

This tool extracts text from papers (given a DOI) and identifies references to datasets on multiple archives:
- [DANDI Archive](https://dandiarchive.org/)
- [OpenNeuro](https://openneuro.org/)
- [Figshare](https://figshare.com/)
- [PhysioNet](https://physionet.org/)

## Installation

```bash
pip install -r requirements.txt
```

## Usage

### Single DOI

```bash
python find_reuse.py 10.1038/s41593-024-01783-4
```

### Multiple DOIs from file

```bash
python find_reuse.py --file dois.txt
```

When processing multiple DOIs, a progress bar shows the current status.

### Verbose mode

```bash
python find_reuse.py -v 10.1038/s41593-024-01783-4
```

## Output Format

Output is always JSON:

```json
{
  "doi": "10.1038/s41593-024-01783-4",
  "archives": {
    "DANDI Archive": {
      "dataset_ids": ["000130"],
      "matches": [
        {
          "id": "000130",
          "pattern_type": "doi",
          "matched_string": "10.48324/dandi.000130"
        }
      ]
    }
  },
  "source": "europe_pmc+crossref",
  "error": null
}
```

## Detected Patterns

### DANDI Archive
- `10.48324/dandi.{id}` - DANDI DOI format
- `dandiarchive.org/dandiset/{id}` - URL format
- `gui.dandiarchive.org/#/dandiset/{id}` - GUI URL format
- `DANDI: {id}` or `DANDI {id}` - Text mentions
- `dandiset/{id}` - Generic dandiset reference

### OpenNeuro
- `10.18112/openneuro.{id}` - OpenNeuro DOI format
- `openneuro.org/datasets/{id}` - URL format
- `OpenNeuro: {id}` or `OpenNeuro {id}` - Text mentions
- `ds{6 digits}` - Dataset ID pattern

### Figshare
- `10.6084/m9.figshare.{id}` - Figshare DOI format (with optional version)
- `figshare.com/articles/{name}/{id}` - URL format
- `figshare.com/ndownloader/files/{id}` - Download URL format

### PhysioNet
- `10.13026/{id}` - PhysioNet DOI format (e.g., `10.13026/C2KX0P`)
- `physionet.org/content/{id}` - URL format
- `physionet.org/physiobank/database/{id}` - PhysioBank URL format

## Data Sources

The tool queries multiple sources to maximize coverage:

1. **Europe PMC** - Full text for open access articles (requires PMCID)
2. **NCBI PubMed Central** - Full text for open access articles
3. **CrossRef** - References section (always checked, often contains dataset DOIs)
4. **Publisher HTML** - Direct scraping of open access article pages (Nature, Springer, Cell, etc.)

## API Usage

The tool can also be used programmatically:

```python
from find_reuse import ArchiveFinder

finder = ArchiveFinder(verbose=True)
result = finder.find_references("10.1038/s41593-024-01783-4")

print(result['archives'])  # {'DANDI Archive': {'dataset_ids': ['000130'], ...}}
print(result['source'])    # 'europe_pmc+crossref'
