# Reviewing reuse classifications

The classifier answers one question about one (paper, dandiset) pair: what is
that paper's relationship to that dataset? Review checks its answers by asking a
person the same question about the same pair, so the two can be put in a
confusion matrix and the classifier's precision measured against a human read.

Review is separate from assignment. Deciding *which* pairs to prioritise, and
who takes them, is its own problem and is not solved here: this page hands you
every REUSE pair and asks about them one at a time. It has no filters, because
narrowing the pile is not the reviewer's job.

## Two queues, because there are two questions

Pairs reach the corpus by two routes, and what you are being asked turns on
which one. `--mode` picks the queue.

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

## Running a session

```bash
python -m src.analysis.build_reuse_verification_page \
    -i output/fulltext_classifications.json \
    -i output/fulltext_direct_openalex.json \
    --mode indirect --reviewer "your name"
```

`--mode direct` reviews the other queue. Both `-i` files are named either way:
the queues are cut from the two together, so leaving one out would put pairs in
the wrong one.

This serves the worksheet on `http://127.0.0.1:8000/` and opens it. `--port`
moves it, which is what a second reviewer on the same machine needs.
`--results-file` is the discovery corpus the cited paper is looked up in,
`output/all_dandiset_papers_refreshed.json` by default.

## Working through it

One pair fills the screen. Answering advances to the next.

| Key | Does |
|---|---|
| `1`–`4` | The four labels of your queue, in the order shown — and move on |
| `←` `→` | Previous, next pair |
| `u` | Jump to the next pair with no answer yet |

Pressing the label already recorded takes it back. Notes are free text and
optional; write one whenever the answer is not obvious from the paper, and
always when you answer Unsure.

Buttons are coloured against the classifier's label: green means you agreed
with it, red that you contradicted it, amber that you could not tell.

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

Both modes write to the same file. A pair belongs to one queue only, so the keys
cannot collide, and running the two modes in turn fills one answer set.

Nothing about the model, the prompt version or the time of the run is recorded.
The same pair should get the same answer from the same person no matter which
run produced the classification being checked.

**Commit these files.** They are the durable result of a review round; the
classifications they check can be regenerated, and the human reads cannot. The
toolbar shows *Saved* after each write — if it shows a failure instead, the
server is gone and the answer did not land.

Reviewers work independently. One session serves one reviewer and writes one
file, so two people reviewing the same round produce two files that are compared
afterwards rather than merged as they go.
