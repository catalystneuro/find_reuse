# How to review

Steps 1–3 are done once, by whoever cuts the round. **If you have been assigned
one, start at [step 4](#4-set-up).**

## 1. Build the candidate list

Only when the pipeline has been re-run; otherwise it is already committed. Needs
`results.tar.gz` from Google Drive unpacked into the repository root.

```bash
python -m src.review.build_candidates \
    -i output/fulltext_classifications.json \
    -i output/fulltext_direct_openalex.json
```

## 2. Assign a round

A new reviewer goes into `reuse_confirmation/reviewers.json` first, or they
cannot be dealt to. `username` is their GitHub handle and names their files.

```json
{"username": "rly", "name": "Ryan Ly"}
```

```bash
python -m src.review.assign_reviews --neuro --dandi-source evidenced --limit 100
```

Filters narrow the round; see `--help`.

A pair already assigned or already reviewed is never dealt again, so a narrower
filter deals nothing once a wider round is out. `--reassign` discards the
existing queues and deals from scratch.

## 3. Push the round

```bash
git add reuse_confirmation/
git commit -m "Assign the DANDI-evidenced round"
git push
```

## 4. Set up

```bash
git clone https://github.com/catalystneuro/find_reuse.git
git clone https://github.com/catalystneuro/paper-text-fetcher.git

cd find_reuse
pip install -e ../paper-text-fetcher[all]
pip install -r requirements.txt
tar xzf paper_cache.tar.gz    # 6 GB, from Google Drive
```

Get the cache. Many of these papers are paywalled, and the **Raw Text** link on
each card is the copy we already fetched — without it every link says the text
is not cached.

## 5. Open your assignment

```bash
git pull

python -m src.review.run_review --reviewer rly --pathway indirect \
    --assignment reuse_confirmation/rly/rly-assignment-indirect.json
```

Serves the worksheet on `http://127.0.0.1:8000/`. One session per pathway, so
run it again with `--pathway direct` and that assignment.

## 6. Review

One pair per screen. Answering advances to the next; **Prev** and **Next** move
without answering. Notes are optional — write one when the call is not obvious,
always on **Unsure**. Your work saves itself.

**Indirect** — the paper cited a dandiset's publication.

| Label | When |
|---|---|
| **Reuse** | The paper obtained the dataset and analysed it. |
| **Mention** | The paper refers to the work but never touches the data. |
| **Neither** | Neither holds — the citation is about something else. |
| **Unsure** | You cannot tell from the text. Say why in the note. |

**Direct** — the paper printed a dandiset identifier in its own text.

| Label | When |
|---|---|
| **Reuse** | The paper obtained the dataset and analysed it. |
| **Primary** | The paper *is* the one that deposited the dataset. |
| **Neither** | Neither holds — the identifier is there for another reason. |
| **Unsure** | You cannot tell from the text. Say why in the note. |

## 7. Push your reviews

```bash
git add reuse_confirmation/<your-username>/
git commit -m "Review the DANDI-evidenced round"
git push
```

Commit as you go. Reviews are the one thing here that cannot be regenerated.
