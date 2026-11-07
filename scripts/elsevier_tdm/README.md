# Elsevier full text under an institutional subscription

About 1,640 Elsevier articles and book chapters (1,335 journal articles) that cite
DANDI or CRCNS datasets' primary papers could not be read: Elsevier's API returns subscription articles only to
requests from a subscribing institution. This folder fetches and classifies them
on such a network, so that only classification results leave it.

1. On the institution's network or VPN, from a `find_reuse` checkout with
   `paper-text-fetcher` installed:

   ```bash
   export ELSEVIER_API_KEY=...     # your own key from https://dev.elsevier.com
   export OPENROUTER_API_KEY=...   # the key provided for this run
   python scripts/elsevier_tdm/run_elsevier_tdm.py --check
   python scripts/elsevier_tdm/run_elsevier_tdm.py
   ```

2. Send back `work/dandi_classifications.json` and `work/crcns_classifications.json`.
   Keep `work/texts/`, the fetched articles, on your machine.

Fetching takes about one second per article (30 minutes in all). Classification
covers about 2,400 paper–dataset pairs, costs roughly US$30 on the OpenRouter key
and takes 10 to 20 minutes.

Elsevier's text and data mining policy allows researchers at subscribing
institutions to mine articles for non-commercial research through this API with
their own key: https://www.elsevier.com/about/policies-and-standards/text-and-data-mining
