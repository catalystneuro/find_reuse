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
python -m src.review.run_review \
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
`output/all_dandiset_papers_refreshed.json` by default. `--direct-results-file`
is the direct pathway's discovery output, `output/results_dandi_openalex.json`,
which is where the titles of its papers come from — its classifications keep
only the DOI that matched. `--paper-cache` is the fetched paper text served
behind the **Raw Text** links, `.paper_cache` by default: the same cache the
classification run read, so what you see is what the classifier saw.

## Working through it

One pair fills the screen: the citing paper, the paper it cited where there is
one, and the cited dataset, each linked and sized to be read. The buttons sit
directly under them. What the classifier said is below that — useful, but not
what the answer is read off.

**Raw Text** beside a DOI opens the text we fetched for that paper, so a
paywall is not where the reading stops. It is what the classifier was given, not
the published article — the export mangles citations, figure captions and line
numbers, which is why a quote can be sound and still not match character for
character. The passages quoted for the pair are marked in it where they stand
verbatim, and the page opens on the first of them. The link is there only for
papers the cache holds.

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
  "reviews": {
    "10.1002/acn3.70285": {
      "000768": {"call": "reuse"},
      "000026": {"call": "mention", "note": "Cited for the method, not the data."}
    }
  }
}
```

The record nests paper, then dataset, then the call and the note made about that
pair — the pair, not the paper, is what was answered, so a paper reusing four
datasets holds four records under its DOI.

Both modes write to the same file. A pair belongs to one queue only, so the two
cannot write over each other, and running the modes in turn fills one answer
set.

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
