# Running the DANDI reuse pipeline

This measures how much data reuse the DANDI Archive generates. It finds papers
that either cite a dandiset's primary publication or name a dandiset identifier
outright, retrieves their full text, and asks a model whether the authors reused
data, which part of the dataset they used, and where they say they got it.

## Setup

Two repositories:

```bash
git clone https://github.com/catalystneuro/find_reuse.git
git clone https://github.com/catalystneuro/paper-text-fetcher.git

cd find_reuse
pip install -e ../paper-text-fetcher[all]
pip install -r requirements.txt
python -m playwright install chromium
```

Playwright is used for bioRxiv, medRxiv and publisher pages that render their
content with JavaScript. Without it the pipeline still runs, and coverage drops.

Create `.env` in `find_reuse` with your own keys:

```
OPENROUTER_API_KEY=sk-or-v1-...
```

The classifier reads `OPENROUTER_API_KEY` for the pinned model slug, and
`DEEPSEEK_API_KEY` if you point it at DeepSeek's own API instead. Nothing in the
repository contains a key.

## Restoring the shared data

The code will rebuild everything from scratch, and doing so takes days and puts
significant load on Europe PMC, NCBI, Unpaywall and publisher sites. If you have
the shared archives, unpack them into the repository root first:

```bash
cd find_reuse
tar xzf paper_cache.tar.gz             # ~6 GB unpacked, 75,000 papers
tar xzf classification_caches.tar.gz   # per-paper classifications
tar xzf results.tar.gz                 # pipeline outputs
```

`paper_cache.tar.gz` is the one worth having. It holds the retrieved text for
every paper, keyed by DOI, and everything downstream reads through it.

The classification caches mean a rerun costs nothing for papers already
classified. They are keyed by (paper, dandiset) and carry a `prompt_version`, so
changing a question invalidates only what that change affects.

## What the pipeline does

**Retrieval** distinguishes three outcomes rather than two. `full_text` means an
article body was retrieved; `metadata_only` means a title, abstract and
references but no body, which is common and is not usable for judging reuse;
`unavailable` means nothing came back. Sources are tried in a chain and
validated individually, because a publisher returning a paywall page with HTTP
200 looks like success.

**Classification** sends the whole paper in one call and requires the model to
quote the passage it judged from. Every quote is checked against the paper
before the result is accepted, so a claim can be verified rather than trusted.
About 3% of quotes cannot be located and are flagged.

**Two pathways** answer different questions. The indirect pathway searches
papers citing a dandiset's primary publication and labels them REUSE, MENTION or
NEITHER. The direct pathway searches papers naming a dandiset identifier and
labels them PRIMARY, REUSE or NEITHER, since there the question is whether these
authors published the dataset or reused it.

## Running it

Discovery for the indirect pathway, which re-queries OpenAlex for citing papers:

```bash
python scripts/rediscover_citing_papers.py --workers 8
```

OpenAlex enforces a daily quota and answers a spent one with a `Retry-After`
measured in hours. The script checks before starting and aborts rather than
produce a silently truncated result.

Discovery for the direct pathway:

```bash
python -m src.direct_pipeline.find_reuse --discover \
    --max-results 1000 --archives "DANDI Archive" --deduplicate \
    -o output/results_dandi_openalex.json
```

Classification, indirect then direct:

```bash
python -m src.shared.run_fulltext_classification \
    --results-file output/all_dandiset_papers_refreshed.json \
    --limit 40000 --workers 96

python -m src.shared.run_fulltext_classification --mode direct \
    --results-file output/results_dandi_openalex.json \
    --cache-dir .fulltext_direct_cache \
    -o output/fulltext_direct_openalex.json --limit 2000 --workers 24
```

`--limit` counts (paper, dandiset) pairs, and the corpus currently holds about
28,000 of them, so keep the limit above that: the output file is written from
the selected worklist, and a limit below the pair count silently drops cached
rows from it. A full indirect pass costs about $110 and takes a few hours at
96 workers. The work is network-bound, so worker counts far above the core
count are appropriate. Add `--retry-errors` to re-run only the failures.

Reports:

```bash
python scripts/build_reuse_report_docx.py -o ~/Desktop/dandi_reuse_report.docx
python -m src.analysis.build_reuse_verification_page \
    -i output/fulltext_classifications.json \
    -i output/fulltext_direct_openalex.json \
    --neuro-only --lab different --dandi-evidenced -o /tmp/review.html
```

Both recompute their figures from the classification outputs, so they cannot
drift from the data.

## Corrections you should know about

`config/primary_paper_overrides.json` corrects the primary paper attributed to
four dandisets. Every paper citing a dandiset's primary publication is treated
as a candidate reuse of that dandiset, so a wrong primary paper adds every paper
citing an unrelated work. Those four accounted for 917 spurious links. Each
entry records the reasoning, so they can be revisited when DANDI's own metadata
changes.

`src/indirect_pipeline/validate_description_dois.py` reads a dandiset's
description and decides whether a DOI scraped from it describes the dataset or
is cited for some other reason. Only the former is admitted. Verdicts are cached
and a few carry `reviewed_by_human`, which the model must not overturn.

## Known gaps

The downstream analysis scripts under `src/analysis/` still read the older
pipeline's outputs (`all_classifications.json`, `direct_ref_classifications.json`),
which predate this work. They have not been migrated, and the schemas differ:
the newer records carry modality, provenance and quote fields, and lack the
publication dates the survival analyses need. Migrating them means joining the
new classifications back to the corpus for dates.

Fifty primary papers sourced from dandiset descriptions were accepted by the
validation step but have not been read individually. They carry 826 citing
papers between them.
