# How to review

Everyone does step 1. **Steps 2–4 and 9 belong to whoever cuts and closes the
round — if you have been assigned one, go from step 1 to [step 5](#5-set-up).**

## 1. Get the archives

Both come from the Google Drive folder Ben shares. Unpack them into the
repository root.

```bash
tar xzf paper_cache.tar.gz    # 6 GB, the fetched text of every paper
tar xzf results.tar.gz        # the latest classification results
```

Get the paper cache before you start reviewing. Many of these papers are
paywalled, and the **Raw Text** link on each card is the copy we already
fetched — without it every link says the text is not cached.

`results.tar.gz` is only needed to rebuild the candidate list in step 2.

## 2. Build the candidate list

Only when the pipeline has been re-run; otherwise it is already committed.

```bash
python -m src.review.build_candidates \
    -i output/fulltext_classifications.json \
    -i output/fulltext_direct_openalex.json
```

The list leaves out pairs on a DOI that is not a paper (an eLife review or
assessment, an OSF registration) and pairs on a preprint whose published version
was classified for the same dandiset. Preprints are matched to their published
versions through bioRxiv and OpenAlex, cached in `.preprint_doi_map.json`.

## 3. Assign a round

A new reviewer goes into `reuse_confirmation/reviewers.json` first, or they
cannot be dealt to. `username` is their GitHub handle and names their files.

```json
{"username": "rly", "name": "Ryan Ly"}
```

```bash
python -m src.review.assign_reviews --neuro --dandi-source evidenced --lab different
```

That is the bottom row of Ben's funnel: pairs where neurophysiology was reused,
where something in the paper says the data came from DANDI, and where the
reusing group is not the one that produced it — 73 papers, 97 pairs. Drop
filters to widen it; see `--help` for the rest.

Papers are dealt whole: every dataset a paper reused goes to the same person,
so a pair can be judged against the others from the same paper and a dataset the
pipeline should have found shows up as a gap in the set of somebody reading that
paper anyway.

A pair already assigned or already reviewed is never dealt again, so a narrower
filter deals nothing once a wider round is out. `--reassign` discards the
existing queues and deals from scratch, which is also what gathers a paper an
earlier round split between two people.

`--paper-link llm_identified` cuts a different kind of round: the indirect pairs
whose dandiset names no paper, so a model picked one. What needs checking there
is the pairing rather than the reuse. `--paper-link declared` is the other side,
the pairs DANDI's own metadata stands behind.

## 4. Open a PR with the round

## 5. Set up

```bash
git clone https://github.com/catalystneuro/find_reuse.git
git clone https://github.com/catalystneuro/paper-text-fetcher.git

cd find_reuse
pip install -e ../paper-text-fetcher[all]
pip install -r requirements.txt
```

## 6. Open your assignment

```bash
git pull

python -m src.review.run_review --reviewer rly
```

Serves the worksheet on `http://127.0.0.1:8000/`. That is the whole command:
one session covers everything, both of your assignments open by themselves, and
every candidate there is comes aboard behind them. Which pathway you are looking
at, and whether you are looking past your own queue, are buttons in the toolbar.

## 7. Review

Do the first pass yourself, without Claude or other AI tools. These reviews are
the ground truth the classifier is measured against. AI tools are fine for the
re-review once the reviews are merged (section 9).

If a quoted passage matches the paper exactly and clearly shows the authors
analysed this dataset, call it **Reuse** and move on. No further digging is
needed. When the quotes do not settle it, read the paper.

**Reuse** means the data was reused, wherever it was downloaded from. A paper
that got the data from the AllenSDK, the IBL ONE API, Zenodo, Figshare or a
lab's own server is still **Reuse**. When the paper says it got the data
somewhere other than DANDI, write the note in this form, so data obtained from
DANDI can be counted separately:

```text
Obtained from AllenSDK, not DANDI. DANDI:000021 mirrors this data.
```

**Indirect**: the paper cited a dandiset's publication.

| Label | When |
|---|---|
| **Reuse** | The paper obtained the dataset and analysed it. |
| **Ambiguous Reuse** | The paper reused DANDI data; which dandiset is unclear. Say what you could tell in the note. |
| **Mention** | The paper refers to the work but never touches the data. |
| **Neither** | A pipeline error: the pair should not have been proposed. See below. |
| **Unsure** | You cannot tell from the text. Say why in the note. |

**Direct**: the paper printed a dandiset identifier in its own text.

| Label | When |
|---|---|
| **Reuse** | The paper obtained the dataset and analysed it. |
| **Ambiguous Reuse** | The paper reused DANDI data; which dandiset is unclear. Say what you could tell in the note. |
| **Primary** | The paper *is* the one that deposited the dataset. |
| **Neither** | A pipeline error: the pair should not have been proposed. See below. |
| **Unsure** | You cannot tell from the text. Say why in the note. |

**Mention** and **Neither** both mean the paper did not reuse this dataset, and
they record two kinds of error. **Mention** is a model error: the paper cites
the work behind this dataset, as background or for a method, and the model took
that for reuse. **Neither** is a pipeline error, a pair the model could not have
called correctly from the text it was given:

- The citing paper is a preprint of a paper that is reviewed under its own DOI.
- Several dandisets share the cited publication, and the paper shows which one
  it used. The others are **Neither**.
- Only the abstract was fetched, so the text cannot settle the call.
- A model linked a paper to this dandiset that has nothing to do with it.

### Adding a dandiset the pipeline missed

If you notice that a paper reused a dandiset the pipeline did not pair it with,
add it with **+ Add Reused Dandiset** on the worksheet. You do not need to go
looking for these. Add only dandisets the paper reused, since mentions and the
paper's own deposits are not tracked.

### Where your reviews go

Every call saves as you make it, to
`reuse_confirmation/<username>/<username>-reviews.json`, filed by paper, then
dataset:

```json
{
  "reviewer": "rly",
  "reviews": {
    "10.1002/acn3.70285": {
      "000768": {"call": "reuse"},
      "000026": {"call": "mention", "note": "Cited for the method."}
    }
  }
}
```

Pairs you added go under `added`, with the name DANDI gave the dataset:

```json
  "added": {"10.1002/acn3.70285": {"000128": {"dandiset_name": "MC_Maze: macaque primary motor …"}}}
```

Run one session at a time. A session writes this file whole, so a second one
open alongside it would overwrite what the first had saved.

### Review notes

Some cases need knowledge the card does not give, such as which study sets of a
benchmark are on DANDI, or what to call a DOI that is a peer review and not a
paper. These are kept in [review_notes/](review_notes/), one note per case,
each listing the dandisets it applies to. Check them when a dandiset confuses
you, and point the AI to them in the AI-assisted re-review. When you settle a
case the next reviewer would also have to work out, write a new note.

## 8. Open a PR with your reviews

## 9. Merge the reviews

```bash
python -m src.review.merge_reviews
```

`all_reviews.json` is every pair anybody has judged and what each of them called
it. `confirmed_reuse.json` is the ones that came out reuse — one reviewer is
enough by default, `--min-reviewers 2` once pairs have been read twice. Both go
in `reuse_confirmation/`, and pairs the reviewers disagreed about are named on
the console.

Pairs somebody added go in too, marked `"source": "reviewer"` so they can be
differentiated from the ones the classifier proposed. They are confirmed on the same
terms: adding one is a reviewer saying the paper reused that dataset, so it
already carries their call and one reviewer confirms it. `--min-reviewers 2`
would ask a second to agree, and nothing yet puts one reviewer's finds on
another's screen. The console says how many of them there were, and counts the
classifier's precision over its own pairs alone.

Once the reviews are merged, an AI-assisted pass over them can help settle
**Ambiguous Reuse** and **Unsure** pairs and catch calls that disagree with
their notes or with each other. Point it to [review_notes/](review_notes/).
Importantly, we must be careful about not
taking anything the AI claims to be true without verifying it ourselves.
Check every fact it offers against the paper, the
code or the dataset before it changes a call or goes into a note. A call that
changes is changed by the reviewer who made it, in their own reviews file.
