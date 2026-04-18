# find_reuse

Automated pipeline for measuring data reuse across open neuroscience repositories. Identifies papers that reuse datasets from DANDI, CRCNS, OpenNeuro, SPARC, and other archives by combining citation graph traversal, full-text search, and LLM-based classification.

## Supported Archives

| Archive | Adapter | Datasets | Primary papers | Direct refs | Reuse identified |
|---------|---------|----------|----------------|-------------|------------------|
| [DANDI Archive](https://dandiarchive.org/) | `DANDIAdapter` | 556 | 359 (65%) | 231 papers | 1,065 events |
| [CRCNS](https://crcns.org/) | `CRCNSAdapter` | 147 | 73 (50%) | 114 papers | 390 events |
| [OpenNeuro](https://openneuro.org/) | `OpenNeuroAdapter` | 1,174 | 292 (25%) | 1,990 papers | 1,562+ events |
| [SPARC](https://sparc.science/) | `SPARCAdapter` | 571 | ~40% | In progress | -- |

Additional archives supported for direct reference detection (no adapter): EBRAINS, Figshare, PhysioNet.

## Architecture

```
archives/                    # Archive adapters (single source of truth)
    base.py                  # ArchiveAdapter base class
    dandi.py                 # DANDI Archive (REST API)
    crcns.py                 # CRCNS (DataCite + web scraping)
    openneuro.py             # OpenNeuro (GraphQL API)
    sparc.py                 # SPARC (Pennsieve Discover API)

analysis/                    # Shared analysis modules
    combined_plot.py         # 4-panel overview figure
    reuse_modeling.py        # MCF fitting, projections, 2x2 model figure
    reuse_distribution.py    # Reuse count distribution with NB fit
    render_flowcharts.py     # Pipeline flowcharts

find_reuse.py                # Direct reference discovery (full-text search)
fetch_paper.py               # Multi-source paper text retrieval
classify_citing_papers.py    # LLM reuse classification (REUSE/MENTION/NEITHER)
classify_reuse_type.py       # LLM reuse type classification (8 categories)
classify_source_archive.py   # Source archive normalization
deduplicate_preprints.py     # Preprint/published pair deduplication
convert_refs_to_classifications.py  # Direct refs -> classification format

run_pipeline.py              # DANDI end-to-end pipeline
run_archive_pipeline.py      # Generic multi-archive pipeline
```

## Pipeline

The analysis proceeds in two parallel discovery channels:

### Channel 1: Citation Pipeline
1. **Link datasets to primary papers** via metadata (API, DOI extraction, LLM identification)
2. **Find citing papers** via OpenAlex citation graph
3. **Fetch full text** via Europe PMC, NCBI PMC, CrossRef, Elsevier API, Unpaywall, Playwright
4. **Extract citation contexts** around references to the primary paper
5. **Classify** each citing paper as REUSE / MENTION / NEITHER using Gemini 3 Flash

### Channel 2: Direct Reference Discovery
1. **Full-text search** Europe PMC + OpenAlex for papers mentioning the archive
2. **Extract dataset IDs** from paper text using archive-specific regex patterns
3. **Classify** each paper-dataset pair as REUSE / PRIMARY / NEITHER

### Post-processing
- Deduplicate preprint/published pairs
- Normalize source archives
- Filter false positives (e.g., Patch-seq transcriptomics-only reuse for DANDI)
- Classify reuse type (TOOL_DEMO, NOVEL_ANALYSIS, AGGREGATION, BENCHMARK, CONFIRMATORY, SIMULATION, ML_TRAINING, TEACHING)

## Usage

### Run for a specific archive

```bash
# Full pipeline
python run_archive_pipeline.py --archive dandi
python run_archive_pipeline.py --archive crcns

# Single step
python run_archive_pipeline.py --archive crcns --step discover-datasets
python run_archive_pipeline.py --archive crcns --step fetch-citations
python run_archive_pipeline.py --archive crcns --step direct-refs
```

### Direct reference discovery

```bash
# Search for papers referencing datasets from a specific archive
python find_reuse.py --discover --archives "DANDI Archive" -o output/results.json -v

# Search all archives
python find_reuse.py --discover -o output/results.json -v

# Single DOI
python find_reuse.py 10.1038/s41593-024-01783-4
```

### Generate figures

```bash
# DANDI (uses existing output/)
python run_pipeline.py --figures-only

# CRCNS
python analyze_crcns.py

# OpenNeuro
python analyze_openneuro.py

# Flowcharts for any archive
python -m analysis.render_flowcharts --archive crcns
```

## Output Structure

Each archive produces the same directory structure:

```
output/{archive}/
    datasets.json                    # Dataset catalog with primary papers
    classifications.json             # All reuse classifications
    direct_refs.json                 # Direct reference discovery results
    direct_ref_classifications.json  # Classifications for direct refs
    andersen_gill_results.json       # Cox PH regression results
    figures/
        combined_all_labs.png        # 4-panel overview
        reuse_rate_model.png         # MCF + projections (2x2)
        reuse_distribution.png       # Count distribution with NB fit
        reuse_type.png               # Reuse type breakdown
        top_datasets.png             # Most reused datasets
        andersen_gill_forest.png     # Predictor forest plot
        phase1_coverage.png          # Dataset-to-paper linkage flow
        phase2_citation_flow.png     # Citation analysis pipeline flow
        reference_flow.png           # How papers reference datasets
    reuse_distribution_stats.json    # NB distribution fit statistics
```

## Adding a New Archive

1. Create `archives/{name}.py` with a class inheriting from `ArchiveAdapter`
2. Implement: `get_datasets()`, `get_primary_papers()`, `get_metadata()`
3. Set class attributes: `name`, `short_name`, `search_terms`, `dataset_patterns`
4. Register in `archives/__init__.py`
5. Run: `python run_archive_pipeline.py --archive {name}`

## Configuration

- **LLM**: Gemini 3 Flash Preview via OpenRouter (set `OPENROUTER_API_KEY` in `.env`)
- **Elsevier API**: Optional, set `ELSEVIER_API_KEY` in `.env` for paywalled text
- **Paper cache**: `.paper_cache/` (shared across archives)
- **Classification cache**: `.classification_cache/` (per DOI pair)

## Key Findings

- **DANDI**: 1,065 reuse events from 796 papers. Citations (HR=4.1) and Allen Institute provenance (HR=3.4) are strongest predictors. 93% of reuse papers cite only the primary paper, not the dataset.
- **CRCNS**: 390 reuse events from 147 datasets over 17 years. MCF saturates at K=3.6 reuse papers per dataset. 53% of datasets will never be reused (NB model).
- **OpenNeuro**: 1,562+ reuse events from direct references alone. MCF follows quadratic growth (no saturation). Dataset citations (HR=12.3) dominate.

## Citation

If you use this pipeline, please cite:

> Dichter, B. et al. (2026). Measuring Data Reuse in Open Neuroscience: A Systematic Analysis of the DANDI Archive. *In preparation*.

## License

BSD-3-Clause
