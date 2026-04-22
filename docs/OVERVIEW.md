# Project Overview

## What this repo is

A research pipeline that measures **data reuse** across open neuroscience repositories — DANDI, CRCNS, OpenNeuro, SPARC, and a few others. It answers the question "when a lab publishes a dataset, how often does someone else use it in a later paper?" It produces:

- Per-dataset catalogs of primary and reusing papers
- REUSE/MENTION/NEITHER classifications per paper–dataset pair (LLM-assisted)
- Survival analysis of time-to-first-reuse
- Andersen–Gill Cox regression on features that predict reuse
- HTML dashboards for browsing the results and for manual review
- Figures + a paper draft ([paper_draft.md](../paper_draft.md))

## The two discovery channels

Finding "paper X reuses dataset Y" is hard because there is no consistent citation convention. The pipeline hedges by running **two independent discovery channels** and merging them:

### Channel 1 — Citation pipeline

Start from the dataset's **primary paper** (the paper that describes the dataset). Walk the citation graph: every paper that cites the primary paper is a candidate reuser. Then fetch each citing paper's full text and ask an LLM "does this paper actually use the dataset, or just mention it?"

```
dataset → primary paper(s) → OpenAlex citations → full text → LLM classify
                                                              ├─ REUSE
                                                              ├─ MENTION
                                                              └─ NEITHER
```

High recall when the primary paper is known and cited. Misses papers that use the dataset without citing the primary paper (surprisingly common — ~93% of DANDI reuse papers cite only the primary paper, and some cite neither).

### Channel 2 — Direct reference discovery

Skip the citation graph. Search Europe PMC / OpenAlex full-text for papers that mention the archive by name or contain an archive-specific dataset identifier (DANDI DOI pattern `10.48324/dandi.XXXXXX`, CRCNS code `hc-3`, OpenNeuro ID `ds001234`, etc.). Every such paper is a candidate reuser.

```
archive search terms → Europe PMC / OpenAlex full-text → regex-extract dataset IDs
                                                         → LLM classify (PRIMARY/REUSE/NEITHER)
```

Catches papers that reuse without citing the primary paper. Requires archive-specific regex patterns (one of the reasons the `archives/` adapter layer exists).

### Merging

The two channels overlap only partially — for DANDI, only 8 paper–dandiset pairs overlapped between the two in an early run. The pipeline merges them into a single `all_classifications.json` with a `source_type` field (`"citation_analysis"` or `"direct_reference"`), deduplicates preprint/published pairs, and then runs downstream post-processing (reuse-type sub-classification, source-archive normalization, delay computation, Cox regression).

[PLAN.md](../PLAN.md) at the repo root is the original design note for this merging step — useful background, but the code has since evolved past exactly what's described there.

## The archive adapter layer

Early versions of the code were DANDI-specific. It has been refactored so that each archive implements an [ArchiveAdapter](../archives/base.py) with three core methods:

- `get_datasets()` — list every dataset in the archive
- `get_primary_papers(dataset)` — find the paper(s) that describe each dataset
- `get_metadata(dataset_id)` — species / modality / size / subjects (for Cox regression features)

Plus two class attributes that drive Channel 2:

- `search_terms` — keywords fed to Europe PMC / OpenAlex full-text search
- `dataset_patterns` — regexes for extracting dataset IDs from text

Adapters live in [archives/](../archives/). To add a new archive, subclass `ArchiveAdapter` and register it in [archives/__init__.py](../archives/__init__.py); [README.md](../README.md#adding-a-new-archive) has the checklist.

Note: [run_pipeline.py](../run_pipeline.py) (DANDI-only) pre-dates this refactor and still hard-codes DANDI specifics. [run_archive_pipeline.py](../run_archive_pipeline.py) is the newer, adapter-based driver. The two coexist; for non-DANDI work use the generic driver.

## Directory map

```
find_reuse/
├── archives/            # Archive adapters (ArchiveAdapter + 4 concrete classes)
├── analysis/            # Shared plotting + modeling (combined_plot, reuse_modeling, etc.)
├── assets/              # Logos for dashboards and presentations
├── output/              # All pipeline outputs (git-ignored, created at runtime)
├── .paper_cache/        # Cached full text per DOI (git-ignored)
├── .classification_cache/   # Cached LLM classifications (git-ignored)
├── .reuse_type_cache/   # Cached reuse-type classifications (git-ignored)
├── run_pipeline.py              # DANDI end-to-end driver
├── run_archive_pipeline.py      # Generic multi-archive driver
├── find_reuse.py                # Channel 2: direct reference discovery
├── fetch_paper.py               # Multi-source full-text fetcher
├── classify_*.py                # LLM classification stages
├── analyze_*.py, andersen_gill_analysis.py, predict_reuse.py  # Statistical analysis
├── build_*.py, generate_*.py    # Dashboards / viewers / validation sets
├── render_*.py                  # Flowchart PNGs (Graphviz)
├── create_presentation.py, create_talk.py  # Slide generation
└── paper_draft.md, references.bib, *.png   # Manuscript artifacts
```

See [SCRIPTS.md](SCRIPTS.md) for a one-liner on every script, and [PIPELINE.md](PIPELINE.md) for the full step-by-step flow with the JSON files that pass between stages.
