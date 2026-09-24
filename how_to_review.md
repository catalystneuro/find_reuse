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

Serves the worksheet on `http://127.0.0.1:8000/`. That is the whole command:
one session covers everything, both of your assignments open by themselves, and
every candidate there is comes aboard behind them. Which pathway you are looking
at, and whether you are looking past your own queue, are buttons in the toolbar.

## 7. Review

One pair per screen. Answering advances to the next; **Prev** and **Next** move
without answering. Notes are optional — write one when the call is not obvious,
always on **Unsure**. Your work saves itself.

**Undo** takes back the last call and goes back to the pair it was made on —
⌘Z, or Ctrl+Z, does the same. It works in either view, which is what the
overview needs: a pair answered there leaves the list at once.

Start from the model's reasoning and the passages it quoted. **If a quote is
exact and shows the authors obtained and analysed the data, that is enough —
mark it and move on.** When it is not enough, open the citing paper, the cited
paper or the dataset from the links on the card. If a paper is paywalled, **Raw
Text** is the copy we fetched.

Review without AI assistance. Read the card, the paper, its code and the
dataset yourself, and write the note from what you found there. These reviews
are the ground truth the classifier is measured against, so every call rests on
a person's reading of the sources.

The question is whether the paper reused the dataset, not which copy of it the
authors downloaded. Many datasets on DANDI are also served by the group that
produced them (the AllenSDK, the IBL ONE API, a lab's own databank). A paper that
analysed the same data through one of those is **Reuse**. Where the paper says
it got the data somewhere other than DANDI, the note names the source and the
dandiset that mirrors it in this form, so a count of data obtained from DANDI can
leave these out:

```text
Obtained from AllenSDK, not DANDI. DANDI:000021 mirrors this data.
```

A paper that does not say where it got the data needs no such note.

On an indirect card the cited paper wears a chip saying how the dandiset came to
name it. A red **LLM-IDENTIFIED — VERIFY** means DANDI names no paper and a model
picked this one, and it is often wrong — one dandiset about mouse blood flow was
given a paper about chimpanzee tool use, and every pair beneath it is a
primatology paper asked about mouse data. Nothing has established that the cited
paper and the dandiset have anything to do with each other, and it is yours to
work out: open the dandiset, see what it holds and who deposited it, and decide
whether the cited paper is that work. Where it is not, the answer is **Neither**,
and say so in the note.

Some citing DOIs are not papers but documents published alongside one. eLife
gives each version of a paper its own DOI (`10.7554/eLife.85786.1`, `.2`, `.3`)
and each document about that version one more, ending in `.saN`:

| Title starts with | Document |
|---|---|
| eLife Assessment | The editors' summary judgement of the paper |
| Reviewer #N (Public review) | One reviewer's report |
| Author response | The authors' reply to the reviews |

The number after `sa` does not say which document it is (the assessment is
`sa0` on one version and `sa3` on another, and `sa2` can be Reviewer #1), so go
by the title on the card. The eLife Assessment is written by the editors, not by
a reviewer, but it is the same case as the others: none of these documents
obtained any data, even when the paper they are about did, so every pair on one
is **Neither**. The note says which document it is and gives the DOI of the
version of the paper it is about, which is the DOI with the `.saN` removed:

```text
Not a paper. This is the eLife Assessment of doi: 10.7554/eLife.109538.1.
Not a paper. This is a peer review of doi: 10.7554/eLife.89421.1.
Not a paper. This is the author response to the reviews of doi: 10.7554/eLife.85069.1.
```

Review DOIs from other publishers are the same case, and Crossref lists them
all, eLife's included, with type `peer-review`.

MC_Maze ([000128](https://dandiarchive.org/dandiset/000128)) and its Large,
Medium and Small variants ([000138](https://dandiarchive.org/dandiset/000138),
[000139](https://dandiarchive.org/dandiset/000139),
[000140](https://dandiarchive.org/dandiset/000140)) share primary papers, and
papers reanalysing them cite those papers or the Neural Latents Benchmark (NLB)
rather than an identifier. The citation does not say which dandiset was used,
but the rest of the paper usually does. Look for:

* **Counts.** Each variant is a different recording session, so a reported
  neuron, condition or trial count picks one out. These are from the NLB paper
  ([Pei et al. 2021](https://arxiv.org/abs/2109.04463), Table 4).

  | Dandiset | Variant | Neurons | Held-in | Held-out | Conditions | Training trials | Test trials |
  |---|---|---|---|---|---|---|---|
  | 000128 | MC_Maze | 182 | 137 | 45 | 108 | 2295 | 574 |
  | 000138 | MC_Maze_Large | 162 | 122 | 40 | 27 | 500 | 100 |
  | 000139 | MC_Maze_Medium | 152 | 114 | 38 | 27 | 250 | 100 |
  | 000140 | MC_Maze_Small | 142 | 107 | 35 | 27 | 100 | 100 |

  Table 4 lists the held-in and held-out units separately and gives no total.
  **Neurons** is the two added together, verified independently of the paper by
  counting the neurons in the sole training file each of these dandisets holds.

* **The NLB leaderboard.** A paper that reports NLB results was usually
  submitted to the [leaderboard](https://eval.ai/web/challenges/challenge-page/1256/leaderboard).
  The "MC_Maze" leaderboards score 000128 only. The variants are scored only on
  the "MC_Maze Scaling" leaderboards, in columns headed [500], [250] and [100].
  A method listed on MC_Maze and on no MC_Maze Scaling leaderboard used 000128.
  The leaderboards list public submissions only.
* **Code and data availability.** Dataset names such as `mc_maze_large`, a
  dandiset URL, or a download script in the paper's repository.

Where the evidence identifies one dandiset, call that pair **Reuse** and its
siblings on the same paper **Neither**, and put the evidence in the note on
each, so whoever reads any one of them sees why. **Ambiguous Reuse** is for when
none of this settles it: the data was reused, and nothing in the paper, its code
or the leaderboard says which dandiset it came from. Say in the note what you
checked.

An amber **SHARED WITH N DANDISETS** chip on the dataset means other dandisets
name the same paper, and the evidence box lists them with their own chips. The
MC_Maze family is the case to have in mind: one paper describes four dandisets,
so a work citing it has said nothing about which of the four it actually
obtained. Where nothing in the paper identifies the dataset on the card, the
reuse is not attributable to it, and that belongs in the note. A sibling wearing
the red verify chip is the weaker case — a model picked that paper for it, so the
sharing may be nothing more than a bad guess.

**Indirect** — the paper cited a dandiset's publication.

| Label | When |
|---|---|
| **Reuse** | The paper obtained the dataset and analysed it. |
| **Ambiguous Reuse** | The paper reused DANDI data; which dandiset is unclear. Say what you could tell in the note. |
| **Mention** | The paper refers to the work but never touches the data. |
| **Neither** | Neither holds — the citation is about something else. |
| **Unsure** | You cannot tell from the text. Say why in the note. |

**Direct** — the paper printed a dandiset identifier in its own text.

| Label | When |
|---|---|
| **Reuse** | The paper obtained the dataset and analysed it. |
| **Ambiguous Reuse** | The paper reused DANDI data; which dandiset is unclear. Say what you could tell in the note. |
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

**By Dandiset** gathers every paper that touched one dataset; **By Citing
Paper** gathers every dataset one paper touched — the paper doing the citing,
never the one the dandiset names. Each heading tallies what its group came to.
The search box takes a dandiset id, a paper title, a DOI — commas for any of
several, so `000128, 000138, 000140` is the whole MC_Maze family at once.

Click a heading to fold that group shut; it keeps its tally, so a folded group
still says what it came to. **Collapse All** puts them all away, which is how you
open one dataset and read it on its own.

Call a pair from the list with the buttons on its row, and write the note in the
box under it. Click the row anywhere else to open it in the worksheet, where the
links, the reasoning and the quotes are.

Pathway is a property of a pair, not of the session, so a dandiset's direct and
indirect pairs sit in one group and each is offered its own labels.

### Adding a dandiset the pipeline missed

As you review the paper or quotes from it, you might notice dandisets that the
paper reused that the pipeline never proposed. Click the **+ Add Reused Dandiset**
button at the right of the call row on the worksheet to open that paper on its own
and see every dataset it was paired with, including those not assigned for review, 
the call and notes on each, and a box at the bottom that takes an identifier.

Enter into that box a six-digit DANDI identifier for a dandiset the paper **reused**.
A paper that merely mentions a dandiset, or names one it deposited itself, is not
currently tracked. 

To undo an added dataset, click **×** on the row or **× Remove Pair** from the worksheet.

When you clicked the "+ Add Reused Dandiset" button, the view filters were changed to show
all datasets associated with the paper (All, not Indirect/Direct/Added; All, not Reviewed/Unreviewed; no call
filter; All Candidates; By Citing Paper; and the search box filled in with the paper DOI).
Click "Return to Worksheet" in the top left of the page to return to your previous 
Worksheet state with your previous filters.

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

Every pathway writes to this one file, and it is the only file your session
touches. A paper reviewed against four dandisets holds four entries. Pairs you
added go in beside them under `added`, with the name DANDI gave the dataset:

```json
  "added": {"10.1002/acn3.70285": {"000128": {"dandiset_name": "MC_Maze: macaque primary motor …"}}}
```

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

Pairs somebody added go in too, marked `"source": "reviewer"` so they can be
differentiated from the ones the classifier proposed. They are confirmed on the same
terms: adding one is a reviewer saying the paper reused that dataset, so it
already carries their call and one reviewer confirms it. `--min-reviewers 2`
would ask a second to agree, and nothing yet puts one reviewer's finds on
another's screen. The console says how many of them there were, and counts the
classifier's precision over its own pairs alone.

Once the reviews are merged, an AI-assisted pass over them can help settle
**Ambiguous Reuse** and **Unsure** pairs and catch calls that disagree with
their notes or with each other. Whatever it turns up enters the ground truth if
it is accepted, and a model states an invented quote, count or table number as
confidently as a real one. Check every fact it offers against the paper, the
code or the dataset before it changes a call or goes into a note. A call that
changes is changed by the reviewer who made it, in their own reviews file.
