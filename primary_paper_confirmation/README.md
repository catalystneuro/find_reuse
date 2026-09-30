# Confirming primary papers

Where DANDI names no paper for a dandiset, a model was asked to pick one
(`relation: llm_identified`). Every paper citing a dandiset's primary paper
becomes a candidate reuse of it, so a wrong pick fills the reuse queue with
papers citing something else. Here a person confirms which papers really
describe each of those dandisets, and rediscovery searches only from those.

```
primary_paper_confirmation/
  primary_paper_candidates.json   every dandiset to confirm, and the papers put forward
  confirmed_primary_papers.json   what the reviewer called each paper
```

## Steps

```bash
python -m src.review.build_primary_candidates    # writes primary_paper_candidates.json
python -m src.review.run_primary_review          # the review page; saves as you go
python scripts/rediscover_citing_papers.py       # searches from the confirmed papers
```

The candidates are built from a corpus that still holds the model's picks. A
corpus rediscovered after review has had them replaced, so the builder refuses
it.

## `primary_paper_candidates.json`

One card per dandiset: its name, contact, description, species and techniques,
and every candidate paper. For each paper:

- `llm_identified`: the model's pick, with the title the model gave it
  (`claimed_name`) and whether that is the title the DOI resolves to
  (`name_matches`)
- `direct_primary`: the direct pathway classified the paper PRIMARY, with the
  passages naming the dandiset as the authors' own deposit

Every paper also carries `title_in_dandiset`: whether the title its DOI resolves
to appears word for word, case and punctuation aside, in the dandiset's title or
description.

## `confirmed_primary_papers.json`

```json
{"dandisets": {
  "000020": {"calls": {"10.1016/j.cell.2020.09.057": "primary"},
             "notes": {"10.1016/j.cell.2020.09.057":
                       "Named in the data availability statement."}}}}
```

Each call is `primary`, `not_primary` or `unsure`, and each paper can carry a
note. A dandiset can have several
primary papers. A paper added by DOI during review is called `primary`.

`scripts/rediscover_citing_papers.py` applies the file through
`apply_confirmed_primary_papers` in `src/review/primary_papers.py`:

- every `llm_identified` relation is removed;
- each paper called `primary` is added as `relation: confirmed`;
- a dandiset left with no paper is dropped from the corpus.

A dandiset nobody has reviewed is therefore not searched at all.

## Clearing stale classifications

Cached classifications are keyed by citing paper and dandiset, not by the paper
the pair cites. A pair that rediscovery reaches through a different paper would
be answered from the cache with the old paper in its prompt. Once, on each
machine that holds a classification cache, copy the corpus before rediscovering,
then delete those entries:

```bash
cp output/all_dandiset_papers_refreshed.json output/all_dandiset_papers_refreshed.before.json
python scripts/rediscover_citing_papers.py
python -m src.review.clear_stale_classifications \
    --old output/all_dandiset_papers_refreshed.before.json \
    --new output/all_dandiset_papers_refreshed.json
```
