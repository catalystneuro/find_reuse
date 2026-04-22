# Getting Started

This is the practical "what do I run" guide. For the conceptual picture, read [OVERVIEW.md](OVERVIEW.md). For the pipeline step reference, read [PIPELINE.md](PIPELINE.md).

## Why `build_reuse_review.py` fails on a fresh clone

[build_reuse_review.py](../build_reuse_review.py) is a **post-pipeline review tool**. It reads `output/all_classifications.json` and builds an HTML dashboard for manually confirming REUSE classifications. On a fresh clone there is no `output/` directory — it has to be produced by the pipeline first.

Specifically, `build_reuse_review.py` needs:

| Path | Required | Produced by |
|------|----------|-------------|
| `output/all_classifications.json` | yes — load fails without it | [fetch_and_classify_new.py](../fetch_and_classify_new.py) (step 6 of [run_pipeline.py](../run_pipeline.py)) |
| `.reuse_type_cache/` | optional — labels REUSE entries with a type if present | [classify_reuse_type.py](../classify_reuse_type.py) (step 7) |
| `.paper_cache/` | optional — used to pull "Data availability" excerpts | [fetch_paper.py](../fetch_paper.py) (populated by step 1 and step 6) |
| `.review_author_cache.json` | created on first run; OpenAlex author lookups are cached here | the script itself |

## Prerequisites

1. **Python env.** [requirements.txt](../requirements.txt) only lists `requests`, `beautifulsoup4`, `lxml`, `tqdm`. The full pipeline also needs (not pinned): `matplotlib`, `numpy`, `pandas`, `scipy`, `lifelines` (for `andersen_gill_analysis.py`), `graphviz`, `playwright` (browser-scraping fallbacks in `fetch_paper.py`), `PyMuPDF`/`pymupdf` (Unpaywall PDF extraction). Install as needed when imports fail.

2. **API keys.** Put these in a `.env` at repo root (see [README.md](../README.md#configuration)):
   - `OPENROUTER_API_KEY` — required for all LLM classification steps (Gemini 3 Flash via OpenRouter)
   - `ELSEVIER_API_KEY` — optional, improves text fetch coverage for paywalled Elsevier journals

3. **Caches.** `.paper_cache/` and `.classification_cache/` are big but reusable across runs — don't delete them unless you mean it. The hidden top-level `.crcns_*.json` and `.journal_hindex_cache.json` are pre-seeded caches that are part of the repo.

## Fast iteration with `--limit`

The full pipeline spans hundreds of dandisets and thousands of citing papers. For prototyping the review UI you almost certainly don't need all of it. [run_pipeline.py](../run_pipeline.py) accepts `--limit N`, which caps the pipeline to the first N dandisets (sorted by dandiset_id, so the subset is reproducible):

```bash
python run_pipeline.py --limit 10      # ~10 dandisets end-to-end, minutes not hours
python build_reuse_review.py            # dashboard with real data at small scale
```

The cap cascades: with `--limit 10`, step 1 only fetches primary papers for 10 dandisets and drops its per-dandiset citing-paper cap from 999 to 20 (so you fetch hundreds of citing-paper texts, not thousands), step 2 (LLM paper discovery) is skipped entirely — it enumerates the full DANDI API regardless of upstream caps, and any papers it found would be discarded by step 3's slice anyway — step 3 slices the filtered list to 10, step 5a proportionally lowers the direct-reference search cap, and downstream classification / delay / regression steps naturally shrink because they iterate the capped inputs. Outputs land at the same paths as a full run (real data, just fewer rows), so **a subsequent full run will overwrite them** — delete `output/` first if you want a clean slate.

`--limit` only applies to the fetch phase. If you've already done a limited run and want to re-run classification with the same subset:

```bash
python run_pipeline.py --skip-fetch   # re-run step 6 onward on whatever is in output/
```

## Fast path: get `build_reuse_review.py` working

The full DANDI pipeline is expensive (many API calls, LLM spend, hours of runtime). The critical path for `build_reuse_review.py` is **steps 1–6 of [run_pipeline.py](../run_pipeline.py)** — you don't strictly need steps 7–10 (reuse-type classification, delays, Cox regression, figures) to open the review dashboard. Without step 7 the "Pipeline reuse type" field will just show "not classified".

```bash
# Full pipeline (hours, costs LLM tokens)
python run_pipeline.py

# Then build the review dashboard
python build_reuse_review.py
# → writes output/reuse_review_dashboard.html
```

If a step fails part-way, you can resume with:

```bash
python run_pipeline.py --skip-fetch  # skip steps 1–5, re-run classify onward
```

## If you just want to poke at the code without running it

The pipeline writes several intermediate JSON files that are useful to look at. See [PIPELINE.md](PIPELINE.md) for the full list. The most interesting ones for understanding the data model are:

- `output/all_dandiset_papers.json` — datasets + their primary papers + their citing papers
- `output/results_dandi.json` — raw direct-reference hits (papers that mention a dandiset ID in full text)
- `output/all_classifications.json` — final merged paper↔dandiset classifications with REUSE/MENTION/NEITHER labels

Ask a colleague for a copy of `output/` from a recent run if you don't want to re-run the pipeline yet — it's the fastest way to get oriented.

## Running for a non-DANDI archive

`run_pipeline.py` is DANDI-specific. For CRCNS / OpenNeuro / SPARC, use the generic driver [run_archive_pipeline.py](../run_archive_pipeline.py):

```bash
python run_archive_pipeline.py --archive crcns
python run_archive_pipeline.py --archive crcns --step discover-datasets  # one step at a time
```

Outputs land in `output/{archive}/` instead of the DANDI-specific flat layout.
