# Combined Dashboard Plan

## Context

There are two complementary analyses for discovering DANDI dataset reuse:

1. **Direct References** (`find_reuse.py --discover` → `results_dandi.json`): Searches Europe PMC for papers that directly mention DANDI datasets by DOI, URL, or text pattern. Finds 162 papers referencing 218 dandisets (315 paper-dandiset pairs). No classification yet — just raw matches.

2. **Citation-Based** (`classify_citing_papers.py` → `test_all_classifications.json`): Starts from dandisets' primary/describing papers, finds all papers that cite those primary papers, then uses an LLM to classify each as REUSE/MENTION/NEITHER. Found 181 REUSE among 2,439 pairs across 132 dandisets.

These are mostly complementary — only 8 paper-dandiset pairs overlap between direct refs and citation REUSE. Combined, they cover 287 unique dandisets.

## Goal

Create a unified dashboard that presents both sources of evidence together, organized by dandiset. The direct references need to be transformed to match the citation-based classification schema so the dashboard can treat all entries uniformly.

## Plan

### Step 1: Modify `find_reuse.py` output → classification schema

Create a new script `convert_refs_to_classifications.py` that:
- Reads `results_dandi.json` (direct dataset references)
- For each paper-dandiset pair, produces a classification entry matching the schema:
  ```json
  {
    "citing_doi": "10.1038/...",
    "cited_doi": null,
    "dandiset_id": "000130",
    "dandiset_name": "...",
    "classification": "REUSE",
    "confidence": 10,
    "reasoning": "Paper directly references dandiset via DOI/URL",
    "source_type": "direct_reference",
    "citing_title": "...",
    "citing_journal": "...",
    "citing_date": "...",
    "context_excerpts": [],
    "match_patterns": [{"pattern_type": "doi", "matched_string": "10.48324/dandi.000130"}]
  }
  ```
- All direct references are classified as REUSE (confidence 10) — a paper that explicitly references a dataset DOI/URL is using it
- Fetches dandiset names from DANDI API for any missing names
- Outputs in the same `{metadata, classifications}` wrapper format

### Step 2: Create `generate_combined_dashboard.py`

A new script that:
- Takes both classification files as input (citation-based + converted direct refs)
- Merges them, deduplicating on (citing_doi, dandiset_id) pairs
- When both sources have an entry for the same pair, keeps both with a note about multiple evidence sources
- Generates a combined dashboard HTML with:
  - **Summary tab**: Overall stats across both sources
    - Total unique reuse papers, total unique dandisets with reuse
    - Breakdown by source: "X from direct references, Y from citation analysis, Z from both"
  - **Papers tab**: All papers, with a "source" indicator (direct ref / citation / both)
    - Filter by source type in addition to existing REUSE/MENTION/NEITHER filters
    - For citation-based entries: shows existing context excerpts, reasoning, confidence
    - For direct ref entries: shows match patterns (DOI, URL, text) instead of excerpts
  - **Datasets tab**: Unified dataset view
    - Each dandiset shows papers from both sources
    - Papers grouped by source within each dandiset section
    - Sort by total reuse count (combining both sources)

### Step 3: Run the pipeline

1. Run `python convert_refs_to_classifications.py -i output/results_dandi.json -o output/direct_ref_classifications.json`
2. Run `python generate_combined_dashboard.py --refs output/direct_ref_classifications.json --citations output/test_all_classifications.json -o output/combined_dashboard.html --open`

## Key Design Decisions

- Direct references are all REUSE by definition (if a paper includes a DANDI dataset DOI/URL, it's referencing that specific dataset)
- The `source_type` field distinguishes between `"direct_reference"` and `"citation_analysis"`
- No need to run `classify_usage.py` on the direct refs — the LLM classification (PRIMARY/SECONDARY/NEITHER) is a different axis. A paper that references a dataset could be the primary author (depositing it) or a secondary user (reusing it). However, for the combined dashboard the key question is "was this dataset used/referenced by this paper?" which is true for all direct refs
- Deduplication: when a paper-dandiset pair appears in both analyses, merge the evidence (show both match patterns and citation context)
