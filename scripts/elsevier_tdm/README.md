# Elsevier articles, classified where they are licensed

About 1,640 Elsevier articles and book chapters cite the primary papers of DANDI
or CRCNS datasets, and we could not read them. Elsevier's API returns
subscription articles only to requests from a subscribing institution, and its
text and data mining terms do not allow the texts to be passed on. So the texts
are fetched and classified on the institution's side. What comes back is only the
decisions, with evidence snippets of up to 200 characters, the limit Elsevier's
TDM terms set for output.

## At the institution (needs Python 3 and `requests`)

On the campus network or VPN:

```bash
export ELSEVIER_API_KEY=...        # your own key from https://dev.elsevier.com
python fetch_elsevier.py --check   # confirms this network is entitled
python fetch_elsevier.py           # about 30 minutes; writes elsevier_texts/; safe to rerun

export OPENROUTER_API_KEY=...      # the key Ben sent (spending is capped)
python classify_local.py --dry-run # counts the pairs to classify
python classify_local.py           # about 2,400 paper-dataset pairs, an hour or two; safe to rerun
```

Share `decisions.json` on Drive. Keep `elsevier_texts/` where it is.

`classify_local.py` runs the same classifier as the rest of the study
(`classify_fulltext_reuse.py`, prompt version 6, copied unchanged; its SHA-256
is recorded in `decisions.json`). For each pair, `decisions.json` holds:

- the label and the model's structured answers (purpose, modalities, source archive, same lab);
- the evidence quotes, cut to 200 characters, with whether each was found in the article;
- the model's reasoning, with any verbatim run of 120 or more characters from the article replaced by `[…]`;
- the article's length and any DANDI identifiers it names;
- the fetch status of every DOI.

It holds no article text beyond those snippets. The text does go to the
language model through OpenRouter for classification.

## Back here

```bash
python scripts/elsevier_tdm/ingest_decisions.py decisions.json
```

This merges the decisions into both corpora's classification outputs, backing up
each file first. Then rebuild the candidates and the paper.

`make_jobs.py` regenerates `jobs.json` and `dois.txt` from the current corpora.
