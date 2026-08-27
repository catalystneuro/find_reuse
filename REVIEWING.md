# Reviewing reuse classifications

The classifier answers one question about one (paper, dandiset) pair: what is
that paper's relationship to that dataset? Review checks its answers by asking a
person the same question about the same pair, so the two can be put in a
confusion matrix and the classifier's precision measured against a human read.

Getting there takes three steps, and only the last one is reviewing. First
every REUSE pair the classifiers produced is collected into a candidate list.
Then a round is cut from it and split among the reviewers. Only then does anyone
sit down in front of a worksheet, which has no filters and no choices about what
to work on: narrowing the pile already happened, and it is not the reviewer's
job.

## Two queues, because there are two questions

Pairs reach the corpus by two routes, and what you are being asked turns on
which one. Each route has its own queue, and an assignment covers one of them.

**Indirect** — the paper cited a dandiset's publication. It might have reused
that dataset, or it might just be citing the work. The card leads with the paper
that was cited, since deciding usually means reading it.

| Label | When |
|---|---|
| **Reuse** | The paper obtained the dataset and analysed it. |
| **Mention** | The paper refers to the work but never touches the data. |
| **Neither** | Neither holds — the citation is about something else. |
| **Unsure** | You cannot tell from the text. Say why in the note. |

**Direct** — the paper printed a dandiset identifier in its own text. It might
have reused that dataset, or it might be the paper that deposited it. There is
no cited paper here, so the card shows only the paper and the dataset.

| Label | When |
|---|---|
| **Reuse** | The paper obtained the dataset and analysed it. |
| **Primary** | The paper *is* the one that deposited the dataset. |
| **Neither** | Neither holds — the identifier is there for another reason. |
| **Unsure** | You cannot tell from the text. Say why in the note. |

Each queue offers only the labels its own classifier could have produced. A
label off that scale cannot go in the confusion matrix, which is the point of
reviewing at all.

Every pair the classifier called REUSE is in one queue or the other, never both.
A pair reached by both routes is still one pair asking one question, so it is
reviewed once, in the direct queue — the only one that can answer that these
authors deposited the data.

The classifier's own reasoning and the passage it quoted are on screen. Check
the quote against the paper before trusting it: a quote marked **not in paper**
does not appear there at all, so a claim resting only on those is unsupported.

## Collecting the candidates

```bash
python -m src.review.build_candidates \
    -i output/fulltext_classifications.json \
    -i output/fulltext_direct_openalex.json
```

Writes `reviews/reuse_candidates.json`: every REUSE pair, with everything needed
to judge it already looked up — the paper, the dataset, the paper it cited, the
model's reasoning and the passages it quoted. Both `-i` files are named either
way, since the queues are cut from the two together and leaving one out would
put pairs in the wrong one.

Rerun it whenever the pipeline reruns. Pairs come out sorted, so the diff is the
pairs that were added.

## Assigning a round

```bash
python -m src.review.assign_reviews --dandi-hosted --lab different
```

The flags cut the round: `--pathway`, `--dandi-hosted`, `--neuro`, `--modality`,
`--archive`, `--reuse-type`, `--lab`, `--min-confidence`,
`--exclude-unverifiable-quotes`, and `--limit` to cap its size. Each has a
`--no-` form where it makes sense, and naming none of them takes everything.

What survives is split among the reviewers in `reviews/reviewers.json`, one
`reviews/assignments/<reviewer>.<pathway>.json` each. A name that is not in that
file is refused rather than assigned to, so a typo cannot deal a round to
somebody who does not exist; `--reviewers` narrows to a subset of those listed.

Dealing never takes work back. A pair somebody already holds stays with them,
and a pair somebody has already answered goes to them, so rerunning the pipeline
assigns only what it added and nobody reviews the same pair twice. A run that
deals nothing rewrites nothing.

## Running a session

```bash
python -m src.review.run_review \
    --assignment reviews/assignments/rly.indirect.json
```

The assignment says whose session it is and which queue it covers, so there is
nothing else to get right. This serves the worksheet on `http://127.0.0.1:8000/`
and opens it; `--port` moves it, which is what a second reviewer on the same
machine needs.

## Working through it

One pair fills the screen: the citing paper, the paper it cited where there is
one, and the cited dataset, each linked and sized to be read. The buttons sit
directly under them. What the classifier said is below that — useful, but not
what the answer is read off.

Answering advances to the next pair; **Prev** and **Next** move without
answering. Pressing the label already recorded takes it back. Notes are free
text and optional; write one whenever the answer is not obvious from the paper,
and always when you answer Unsure.

Each label carries its own colour from the start — Reuse green, Mention blue,
Primary purple, Neither red, Unsure amber — so an answer is recognised by colour
rather than read off its text. The one you pick fills in and turns bold.

**All / Unreviewed / Reviewed** picks what the queue holds, for the two ways of
working. A session opens on **Unreviewed**, the working pass: a pair leaves the
queue as you answer it and the next one takes its place, so the queue empties as
you go. **Reviewed** is the looking-back pass over answers already given, where
changing one is a matter of clicking a different label.

The toolbar counts two different things, and keeps them apart: **Pair 3 of 871**
sits beside the filter buttons because it is where you are inside whichever
queue they select, while the bar and **142 of 1017 reviewed** sit off by Save
because they are the round as a whole.

## Where the answers go

`reviews/<your-name>.json`, written as you work:

```json
{
  "reviewer": "your name",
  "calls": {"10.1002/acn3.70285\t000768": "reuse"},
  "notes": {}
}
```

A call is keyed by the DOI and the dandiset, tab-separated — the pair, not the
paper, because a paper reusing four datasets gets four independent answers.

Both queues write to the same file. A pair belongs to one of them only, so the
keys cannot collide, and working through both assignments fills one answer set.

Nothing about the model, the prompt version or the time of the run is recorded.
The same pair should get the same answer from the same person no matter which
run produced the classification being checked.

**Commit these files.** They are the durable result of a review round; the
classifications they check can be regenerated, and the human reads cannot. The
toolbar shows *Auto-saved* after each write of its own — if it shows a failure
instead, the server is gone and the answer did not land. **Save** writes
immediately rather than waiting for the moment's pause the automatic write
allows, and reads back *Saved* so the two are told apart; nothing is lost
without it, but it is there to be sure before closing the tab.

Reviewers work independently. One session serves one reviewer and writes one
file, so two people reviewing the same round produce two files that are compared
afterwards rather than merged as they go.
