# Pipeline Flow

Step-by-step reference for the DANDI end-to-end pipeline in [run_pipeline.py](../run_pipeline.py). The generic multi-archive driver [run_archive_pipeline.py](../run_archive_pipeline.py) follows the same logic but writes to `output/{archive}/` instead of the flat `output/` layout below, and uses the [archives/](../archives/) adapter classes to abstract the DANDI-specific parts.

For the conceptual picture of *why* the pipeline has two discovery channels, read [OVERVIEW.md](OVERVIEW.md).

## Step map

The step numbers below match the function names in [run_pipeline.py](../run_pipeline.py) (`step1_...`, `step2_...`, etc.). The comment in the script header lists 13 steps but the actual call graph collapses to 10 — I'll use the function names as the source of truth.

```
                        ┌─────────────────────────────────────┐
                        │  DANDI API, OpenAlex, Europe PMC    │
                        │  CrossRef, Unpaywall, publishers    │
                        └───────────────┬─────────────────────┘
                                        │
 Channel 1 ──────────────────────────── │ ──────────────── Channel 2
 (citation graph)                       │                  (full-text search)

  step1  dandi_primary_papers.py        │                  step5a  find_reuse.py --discover
    → output/dandi_primary_papers_results.json                → output/results_dandi.json
                │                                                    │
  step2  find_missing_papers.py (LLM)                        step5b  convert_refs_to_classifications.py
    → LLM cache, merged into step 3                            → output/direct_ref_classifications.json
                │
  step3  merge_paper_sources.py + DANDI API filter
    → output/all_dandiset_papers.json
                │
  step4  fetch_remaining_citations.py (OpenAlex)
    → updates output/all_dandiset_papers.json
                │
                └───────────────┬────────────────────────────────────┘
                                ▼
  step6  fetch_and_classify_new.py
    fetches full text (via fetch_paper.py, cached in .paper_cache/),
    extracts citation contexts, runs LLM classification,
    merges Channel 1 + Channel 2 into one file
    → output/all_classifications.json

  step6b deduplicate_preprints.py
    → updates output/all_classifications.json
                │
  step7  classify_reuse_type.py
    LLM sub-classifies each REUSE as TOOL_DEMO / NOVEL_ANALYSIS /
    AGGREGATION / BENCHMARK / CONFIRMATORY / SIMULATION / ML_TRAINING / TEACHING
    → updates output/all_classifications.json; caches per-pair in .reuse_type_cache/
                │
  step8  (inline in run_pipeline.py)
    computes pub_date − dandiset_created delay for every REUSE
    (CrossRef → OpenAlex fallback for publication dates)
    → output/dandi_reuse_delays.json
                │
  step9  andersen_gill_analysis.py
    Cox PH regression with recurrent events
    → output/andersen_gill_results.json
                │
  step10 figure regeneration
    analyze_reuse_delays.py + render_*.py
    → output/figures/*.png, output/*.png
                │
                ▼
  mirror_to_dandi_dir()
    copies canonical outputs into output/dandi/ for symmetry with
    output/crcns/, output/openneuro/, etc.
```

## Step-by-step detail

### Step 1 — Discover dandisets and primary papers

**Script:** [dandi_primary_papers.py](../dandi_primary_papers.py) (with `--citations --fetch-text`)

Walks the DANDI REST API for every dandiset, extracts the primary paper(s) from `relatedResource` metadata and description DOIs. With `--citations`, also fetches each primary paper's citing papers via OpenAlex. With `--fetch-text`, populates `.paper_cache/`.

- **Reads:** DANDI API, OpenAlex
- **Writes:** `output/dandi_primary_papers_results.json`, `.paper_cache/{doi}.json`

### Step 2 — LLM paper discovery for dandisets without metadata links

**Script:** [find_missing_papers.py](../find_missing_papers.py)

For dandisets that have no formal primary paper in their metadata, asks an LLM to guess one from the dandiset title/description.

- **Reads:** `output/dandi_primary_papers_results.json`, OpenRouter API
- **Writes:** LLM answer cache (`.missing_paper_cache.json`); results consumed by step 3

### Step 3 — Merge paper sources + filter empty dandisets

**Script:** [merge_paper_sources.py](../merge_paper_sources.py), plus an inline DANDI API filter in [run_pipeline.py](../run_pipeline.py)

Unifies formal metadata papers and LLM-discovered papers into one record per dandiset. Then hits the DANDI API to drop dandisets with zero assets/size (test or placeholder dandisets).

- **Reads:** step 1 output, step 2 LLM cache, DANDI API
- **Writes:** `output/all_dandiset_papers.json`

### Step 4 — Fetch citing papers

**Script:** [fetch_remaining_citations.py](../fetch_remaining_citations.py)

For every primary paper that doesn't already have `citing_papers` populated, queries OpenAlex for citations.

- **Reads / writes:** `output/all_dandiset_papers.json` (in place), OpenAlex

### Step 5 — Direct reference discovery (Channel 2)

**Step 5a** — [find_reuse.py](../find_reuse.py) `--discover --archives "DANDI Archive" --deduplicate`. Full-text searches Europe PMC and OpenAlex for dandiset DOIs, GUI URLs, and text mentions using the [DANDIAdapter.dataset_patterns](../archives/dandi.py) regexes.
- **Writes:** `output/results_dandi.json`

**Step 5b** — [convert_refs_to_classifications.py](../convert_refs_to_classifications.py). Transforms the raw direct-ref hits into the same schema as Channel 1 classifications. All direct refs default to `REUSE` at confidence 10 (see [PLAN.md](../PLAN.md) for the rationale).
- **Writes:** `output/direct_ref_classifications.json`

### Step 6 — Fetch text, extract contexts, classify, merge

**Script:** [fetch_and_classify_new.py](../fetch_and_classify_new.py)

The biggest step. For every citing paper from Channel 1:
1. Fetches full text via [fetch_paper.py](../fetch_paper.py) (multi-source fallback — see [paper_fetching_flow.md](../paper_fetching_flow.md))
2. Extracts citation contexts using [citation_context.py](../citation_context.py) (sentence windows around the primary-paper reference)
3. Calls the LLM to classify REUSE / MENTION / NEITHER with confidence and reasoning
4. Merges Channel 1 classifications with Channel 2's `direct_ref_classifications.json`

- **Reads:** `output/all_dandiset_papers.json`, `output/direct_ref_classifications.json`, `.paper_cache/`
- **Writes:** `output/all_classifications.json`, `.classification_cache/{doi_pair}.json`

### Step 6b — Deduplicate preprint/published pairs

**Script:** [deduplicate_preprints.py](../deduplicate_preprints.py)

Detects bioRxiv/medRxiv ↔ journal duplicates by fuzzy title matching and merges them, keeping the journal version.

- **Reads / writes:** `output/all_classifications.json` (in place)

### Step 7 — Classify reuse type

**Script:** [classify_reuse_type.py](../classify_reuse_type.py)

LLM sub-classification of REUSE entries into 8 categories (TOOL_DEMO, NOVEL_ANALYSIS, AGGREGATION, BENCHMARK, CONFIRMATORY, SIMULATION, ML_TRAINING, TEACHING).

- **Reads / writes:** `output/all_classifications.json` (adds `reuse_type` field), `.reuse_type_cache/{pair}.json`

### Step 8 — Update delay data (inline)

Inline logic inside [run_pipeline.py](../run_pipeline.py) — no dedicated script. Computes `delay_days = publication_date − dandiset_created` for every REUSE pair, using CrossRef for pub dates with OpenAlex as fallback. Also syncs the `source_archive` field from classifications into delays.

- **Reads:** `output/all_classifications.json`, `output/all_dandiset_papers.json`, CrossRef, OpenAlex
- **Writes:** `output/dandi_reuse_delays.json`

### Step 9 — Andersen–Gill regression

**Script:** [andersen_gill_analysis.py](../andersen_gill_analysis.py)

Cox proportional hazards regression for recurrent reuse events. Pulls feature metadata (species, modality, size, n_subjects) from the DANDI adapter.

- **Reads:** `output/dandi_reuse_delays.json`, DANDI metadata via adapter
- **Writes:** `output/andersen_gill_results.json`, `output/figures/andersen_gill_forest.png`

### Step 10 — Regenerate figures

Runs a battery of plotting scripts:
- [analyze_reuse_delays.py](../analyze_reuse_delays.py) — delay histograms, Kaplan–Meier curves
- [render_phase2_flow.py](../render_phase2_flow.py), [render_dandiset_coverage_flow.py](../render_dandiset_coverage_flow.py), [render_reference_flow.py](../render_reference_flow.py), [render_flow.py](../render_flow.py) — Graphviz flowcharts
- `andersen_gill_analysis.py --plot-only` — forest plot from cached results

- **Writes:** `output/figures/*.png`, `output/*.png`

### Mirror

At the end, [run_pipeline.py](../run_pipeline.py) copies the canonical outputs into `output/dandi/` so downstream tools that expect the multi-archive layout (`datasets.json`, `classifications.json`, `direct_refs.json`, `delays.json`) work uniformly across archives.

## Key intermediate files at a glance

| File | Produced by | Consumed by |
|------|-------------|-------------|
| `output/dandi_primary_papers_results.json` | step 1 | steps 2, 3 |
| `output/all_dandiset_papers.json` | step 3 (+ step 4 in place) | steps 5, 6, 8 |
| `output/results_dandi.json` | step 5a | step 5b |
| `output/direct_ref_classifications.json` | step 5b | step 6 (merge) |
| `output/all_classifications.json` | step 6 (+ 6b, 7 in place) | steps 8, 9, 10, [build_reuse_review.py](../build_reuse_review.py), [generate_combined_dashboard.py](../generate_combined_dashboard.py) |
| `output/dandi_reuse_delays.json` | step 8 | step 9, [analyze_reuse_delays.py](../analyze_reuse_delays.py) |
| `output/andersen_gill_results.json` | step 9 | figure regeneration, paper |
| `.paper_cache/{doi}.json` | steps 1, 6 (via [fetch_paper.py](../fetch_paper.py)) | steps 6, 7, [build_reuse_review.py](../build_reuse_review.py) |
| `.classification_cache/{pair}.json` | step 6 | step 6 (skips re-running LLM) |
| `.reuse_type_cache/{pair}.json` | step 7 | step 7, [build_reuse_review.py](../build_reuse_review.py) |

## Resume / partial runs

- `python run_pipeline.py --skip-fetch` — skips steps 1–5, starts at step 6. Use this after fixing a classification bug without re-hitting every API.
- `python run_pipeline.py --figures-only` — runs only step 10. Use after tweaking a plot.
- For individual steps, run the underlying script directly; most take `-o` / `-i` for file paths and can be invoked in isolation.

## Multi-archive driver

[run_archive_pipeline.py](../run_archive_pipeline.py) collapses this down to four adapter-based steps: `discover-datasets`, `fetch-citations`, `direct-refs`, `classify`. It writes to `output/{archive}/` with a standardized layout (see [README.md](../README.md#output-structure)). Use it for CRCNS / OpenNeuro / SPARC; [run_pipeline.py](../run_pipeline.py) remains the DANDI-specific driver with the richer step 8–10 post-processing.
