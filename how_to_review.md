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

Serves the worksheet on `http://127.0.0.1:8000/`. One session covers everything:
both of your assignments open by themselves, and every candidate there is comes
aboard behind them. `--pathway indirect` only chooses which queue you start on.

## 7. Review

One pair per screen. Answering advances to the next; **Prev** and **Next** move
without answering. Notes are optional — write one when the call is not obvious,
always on **Unsure**. Your work saves itself.

Start from the model's reasoning and the passages it quoted. **If a quote is
exact and shows the authors obtained and analysed the data, that is enough —
mark it and move on.** When it is not enough, open the citing paper, the cited
paper or the dataset from the links on the card. If a paper is paywalled, **Raw
Text** is the copy we fetched.

On an indirect card the cited paper wears a chip saying how the dandiset came to
name it. A red **LLM-IDENTIFIED — VERIFY** means DANDI names no paper and a model
picked this one, and it is often wrong — one dandiset about mouse blood flow was
given a paper about chimpanzee tool use, and every pair beneath it is a
primatology paper asked about mouse data. Nothing has established that the cited
paper and the dandiset have anything to do with each other, and it is yours to
work out: open the dandiset, see what it holds and who deposited it, and decide
whether the cited paper is that work. Where it is not, the answer is **Neither**,
and say so in the note.

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

### Overview: going back over your answers

**Overview** in the toolbar lays the pairs out in groups instead of one per
screen. It is for the second pass, where the question is no longer "what is this
pair" but "what did all of this come to".

The call filter is the way back to a decision you have already made: press
**Unsure** and you have every pair you could not call, notes and all. It applies
to the worksheet too — filter to Unsure, switch back, and **Prev**/**Next** walk
exactly those.

**By Dandiset** gathers every paper that touched one dataset; **By Paper**
gathers every dataset one paper touched. Each heading tallies what its group came
to. The search box takes a dandiset id, a paper title, a DOI — commas for any of
several, so `000128, 000138, 000140` is the whole MC_Maze family at once.

Call a pair from the list with the buttons on its row, or click the row to open
it in the worksheet where the links, the reasoning and the quotes are. Notes are
written in the worksheet.

Pathway is a property of a pair, not of the session, so a dandiset's direct and
indirect pairs sit in one group and each is offered its own labels.

### Where your reviews go

`reuse_confirmation/<username>/<username>-reviews.json`, written as you work —
paper, then dataset, then the call and any note:

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

Both pathways write to this one file, and it is the only file your session
touches. A paper reviewed against four dandisets holds four entries.

Run one session at a time. A session writes this file whole, so a second one open
alongside it would overwrite whatever the first had just saved.

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
