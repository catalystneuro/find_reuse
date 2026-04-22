# Scripts Reference

One-line-ish descriptions of every Python file in the repo, grouped by role. Paths are clickable. For the end-to-end flow, read [PIPELINE.md](PIPELINE.md) first.

## Drivers

- [run_pipeline.py](../run_pipeline.py) — DANDI end-to-end driver. Flags: `--skip-fetch`, `--figures-only`.
- [run_archive_pipeline.py](../run_archive_pipeline.py) — Generic multi-archive driver (uses [archives/](../archives/) adapters). Flags: `--archive {dandi,crcns,openneuro,sparc}`, `--step {discover-datasets,fetch-citations,direct-refs,classify}`.

## Archive adapters — [archives/](../archives/)

- [archives/base.py](../archives/base.py) — `ArchiveAdapter` base class. Required overrides: `get_datasets()`, `get_primary_papers()`, `get_metadata()`. Required class attrs: `name`, `short_name`, `search_terms`, `dataset_patterns`.
- [archives/dandi.py](../archives/dandi.py) — DANDI REST API (`api.dandiarchive.org`). 14 dataset patterns including DOI `10.48324/dandi.XXXXXX` and GUI URLs.
- [archives/crcns.py](../archives/crcns.py) — DataCite API + web scraping of CRCNS `/about` pages. 5 patterns including DOI `10.6080/KXXXXX` and dataset codes like `hc-3`.
- [archives/openneuro.py](../archives/openneuro.py) — GraphQL at `openneuro.org/crn/graphql`. 5 patterns including DOI `10.18112/openneuro.dsXXXXXX`.
- [archives/sparc.py](../archives/sparc.py) — Pennsieve Discover API. 3 patterns for SPARC DOIs.
- [archives/__init__.py](../archives/__init__.py) — Adapter registry.

## Analysis modules — [analysis/](../analysis/) (imported, not CLI)

- [analysis/combined_plot.py](../analysis/combined_plot.py) — 6-panel overview figure (source archives, journals, MCF, reuse rate).
- [analysis/reuse_modeling.py](../analysis/reuse_modeling.py) — MCF fitting (Richards, saturating exp), reuse rate with Poisson CIs, power-law growth, projections. 2×2 figure.
- [analysis/reuse_distribution.py](../analysis/reuse_distribution.py) — Reuse count distribution with NB fit + dispersion stats.
- [analysis/render_flowcharts.py](../analysis/render_flowcharts.py) — Generic Graphviz flowcharts per archive. CLI: `python -m analysis.render_flowcharts --archive {name}`.

## Channel 2 — direct reference discovery

- [find_reuse.py](../find_reuse.py) — Main script. Full-text searches Europe PMC / OpenAlex for archive mentions, extracts dataset IDs via adapter regexes. Flags: `--discover`, `--archives {name}`, `--deduplicate`, `-o {file}`, `-v`. Also supports single-DOI mode: `python find_reuse.py 10.xxxx/...`.
- [convert_refs_to_classifications.py](../convert_refs_to_classifications.py) — Transforms `results_*.json` into the Channel 1 classification schema (all direct refs → REUSE at confidence 10).

## Channel 1 — citation pipeline stages

- [dandi_primary_papers.py](../dandi_primary_papers.py) — Discovers dandisets + primary papers from DANDI API. With `--citations` and `--fetch-text`, also fetches citing papers and their full text. Step 1 of [run_pipeline.py](../run_pipeline.py).
- [find_missing_papers.py](../find_missing_papers.py) — LLM-guess primary papers for dandisets that lack metadata links. Step 2.
- [merge_paper_sources.py](../merge_paper_sources.py) — Unify formal metadata and LLM-discovered papers. Step 3.
- [fetch_remaining_citations.py](../fetch_remaining_citations.py) — Populate `citing_papers` via OpenAlex for any primary paper missing them. Step 4.

## Text fetching

- [fetch_paper.py](../fetch_paper.py) — Multi-source full-text fetcher with fallback chain (Europe PMC → NCBI PMC → CrossRef → Unpaywall → publisher HTML → Playwright). Cached in `.paper_cache/`. See [paper_fetching_flow.md](../paper_fetching_flow.md) for the flowchart.
- [citation_context.py](../citation_context.py) — (library) Extracts sentence windows around DOI/reference citations in paper text.
- [extract_citation_contexts.py](../extract_citation_contexts.py) — Standalone wrapper around `citation_context.py` that pre-computes contexts for an entire results file.

## LLM classification

- [llm_utils.py](../llm_utils.py) — OpenRouter API wrapper + JSON response parsing. Uses `OPENROUTER_API_KEY`; default model is Gemini 3 Flash Preview.
- [classify_usage.py](../classify_usage.py) — Big script: extracts dataset mentions + LLM-classifies PRIMARY / SECONDARY / NEITHER. Core library for the others.
- [classify_citing_papers.py](../classify_citing_papers.py) — REUSE / MENTION / NEITHER for each (citing paper, primary paper) pair.
- [classify_reuse_type.py](../classify_reuse_type.py) — Sub-classify REUSE into 8 types (TOOL_DEMO, NOVEL_ANALYSIS, AGGREGATION, BENCHMARK, CONFIRMATORY, SIMULATION, ML_TRAINING, TEACHING). Step 7.
- [classify_source_archive.py](../classify_source_archive.py) — Normalize and LLM-resolve the source archive (DANDI / Allen / IBL / CRCNS / Figshare / …) for each REUSE.
- [fetch_and_classify_new.py](../fetch_and_classify_new.py) — Step 6 orchestrator: text fetch → context extraction → classification → merge Channel 1+2.
- [fetch_and_classify_crcns.py](../fetch_and_classify_crcns.py) — CRCNS-specific version of the same pipeline.
- [filter_patchseq_genetic.py](../filter_patchseq_genetic.py) — LLM filter: demotes Patch-seq papers that only reuse transcriptomics (not ephys) from REUSE → MENTION.

## Post-processing

- [deduplicate_preprints.py](../deduplicate_preprints.py) — Fuzzy-title dedup of bioRxiv/medRxiv vs journal versions. Step 6b.

## Statistical analysis

- [analyze_reuse_delays.py](../analyze_reuse_delays.py) — Time-to-first-reuse histograms and Kaplan–Meier curves. Excludes the last 6 months to avoid indexing lag bias.
- [analyze_time_to_reuse.py](../analyze_time_to_reuse.py) — Delay histograms broken down by REUSE vs MENTION; two anchors (dataset creation, primary paper).
- [andersen_gill_analysis.py](../andersen_gill_analysis.py) — Cox PH regression with recurrent events. Pulls feature metadata via the DANDI adapter. Writes `output/andersen_gill_results.json`. Flag: `--plot-only`.
- [predict_reuse.py](../predict_reuse.py) — MCF + power-law dataset growth → projected cumulative reuse; separates same-lab vs different-lab.
- [analyze_downloads_vs_reuse.py](../analyze_downloads_vs_reuse.py) — Correlates DANDI download volume with citation-based reuse. Needs `../access-summaries/` external repo.
- [analyze_crcns.py](../analyze_crcns.py) — CRCNS-specific figure regeneration (mirrors the DANDI end-of-pipeline).
- [analyze_openneuro.py](../analyze_openneuro.py) — OpenNeuro-specific figure regeneration (currently direct-refs only).

## Validation & manual review

- [build_validation_set.py](../build_validation_set.py) — Samples 100 paper–dandiset pairs (balanced REUSE vs non-REUSE) for manual annotation.
- [build_reuse_review.py](../build_reuse_review.py) — HTML review dashboard over `output/all_classifications.json`. Lets you label confirmed / not-reuse / unsure, reuse type, source archive. Save/load state as JSON. **Requires `output/all_classifications.json` — see [GETTING_STARTED.md](GETTING_STARTED.md).**

## Dashboards / viewers

- [generate_viewer.py](../generate_viewer.py) — Self-contained searchable HTML dashboard over classifications.
- [generate_reuse_viewer.py](../generate_reuse_viewer.py) — Two-tab dashboard (Datasets with expandable papers, Papers searchable).
- [generate_combined_dashboard.py](../generate_combined_dashboard.py) — Merges Channel 1 + Channel 2 into a single unified HTML dashboard; see [PLAN.md](../PLAN.md) for the design note.

## Flowchart renderers (Graphviz PNGs)

- [render_flow.py](../render_flow.py) — Paper-fetching flowchart (preprint vs published paths).
- [render_phase2_flow.py](../render_phase2_flow.py) — Channel 1 citation pipeline.
- [render_citation_pipeline_flow.py](../render_citation_pipeline_flow.py) — Similar to above; kept for the paper figures.
- [render_dandiset_coverage_flow.py](../render_dandiset_coverage_flow.py) — Phase 1 dataset→paper linkage coverage.
- [render_direct_ref_flow.py](../render_direct_ref_flow.py) — Channel 2 direct reference flow.
- [render_reference_flow.py](../render_reference_flow.py) — How papers reference datasets (citation only / direct only / both).
- [render_search_flow.py](../render_search_flow.py) — Search engine discovery flow.

Note: some `render_*.py` scripts are DANDI-specific one-offs while [analysis/render_flowcharts.py](../analysis/render_flowcharts.py) is the generic multi-archive version. New archives should use the generic one.

## Manuscript / slide generation

- [plot_dandi_citations.py](../plot_dandi_citations.py) — Quarterly DANDI citation count plot.
- [create_presentation.py](../create_presentation.py) — CatalystNeuro-branded PowerPoint deck.
- [create_talk.py](../create_talk.py) — Talk slides.

## Manuscript artifacts (not code)

- [README.md](../README.md) — High-level readme (audience: external).
- [PLAN.md](../PLAN.md) — Original design note for the combined dashboard merge step.
- [paper_draft.md](../paper_draft.md) — Manuscript draft.
- [references.bib](../references.bib) — BibTeX library.
- [data reuse vignettes.md](../data%20reuse%20vignettes.md) — Narrative examples for the paper.
- [paper_fetching_flow.md](../paper_fetching_flow.md) — Mermaid diagram of [fetch_paper.py](../fetch_paper.py)'s fallback chain.
- `known_dandi_papers.txt`, `test_dois.txt`, `test_all_dois.txt` — Seed DOI lists.
- `.crcns_catalog.json`, `.crcns_code_to_doi.json`, `.crcns_doi_to_code.json`, `.crcns_codes.json` — Pre-seeded CRCNS caches (committed to repo).
- `.journal_hindex_cache.json` — Pre-seeded journal h-index lookup.
- `dandi_citations_quarterly.png` — Standalone figure.
