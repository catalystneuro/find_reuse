# Elsevier full text from a subscribing institution

About 1,640 Elsevier articles and book chapters (1,335 journal articles) that cite
DANDI or CRCNS datasets' primary papers could not be read: Elsevier's API returns
subscription articles only to requests from a subscribing institution.

On the institution's network or VPN (needs Python 3 and `requests` only):

```bash
export ELSEVIER_API_KEY=...        # your own key from https://dev.elsevier.com
python fetch_elsevier.py --check   # confirms this network is entitled
python fetch_elsevier.py           # about 30 minutes; safe to rerun
```

This writes `elsevier_texts.zip`. Share it on Drive.

Then, in this repository:

```bash
python scripts/elsevier_tdm/ingest_elsevier_zip.py elsevier_texts.zip
```

and rerun the classification for both corpora.
