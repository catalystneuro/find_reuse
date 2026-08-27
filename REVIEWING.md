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

Writes `reuse_confirmation/reuse_candidates.json`: every REUSE pair, with
everything needed to judge it already looked up — the paper, the dataset, the
paper it cited, the model's reasoning and the passages it quoted. Both `-i`
files are named either way, since the queues are cut from the two together and
leaving one out would put pairs in the wrong one.

`--results-file` is the discovery corpus the cited paper is looked up in,
`output/all_dandiset_papers_refreshed.json` by default. `--direct-results-file`
is the direct pathway's discovery output, `output/results_dandi_openalex.json`,
which is where the titles of its papers come from — its classifications keep
only the DOI that matched.

Rerun it whenever the pipeline reruns. Pairs come out sorted, so the diff is the
pairs that were added.

The header records which run of the pipeline each input came out of — the model,
the prompt version, and the labels that run actually reached. A candidate list
is a claim about a corpus made by a particular model answering a particular
question, and changing either produces different claims about the same papers.
This is what makes two candidate lists comparable, and what says which of them a
set of answers was checking. None of it goes into the answers, where it would be
noise: which model judged a pair does not change what the right answer is.

## Assigning a round

```bash
python -m src.review.assign_reviews --dandi-hosted --lab different
```

Six flags cut the round, one per question worth asking of a pair:

| Flag | Asks |
|---|---|
| `--pathway` | which route found it, indirect or direct |
| `--dandi-source` | where the data came from: `possible`, or `evidenced` |
| `--neuro` | whether neurophysiology was among what was reused |
| `--modality` | which part of the dataset was reused, for the other six modalities |
| `--reuse-type` | what the authors did with it, from `NOVEL_ANALYSIS` to `TEACHING` |
| `--lab` | whether the group that reused the data is the one that produced it |

Naming none of them takes everything, and `--limit` caps how many new pairs a
round deals. `--dandi-source possible` keeps pairs not ruled out — the paper
named DANDI, or named no archive at all — while `evidenced` keeps only those
with something saying so.

Together they walk the funnel the project reports on:

```bash
python -m src.review.assign_reviews --neuro                          #  482 papers
python -m src.review.assign_reviews --neuro --dandi-source possible  #  245
python -m src.review.assign_reviews --neuro --dandi-source evidenced #  112
python -m src.review.assign_reviews --neuro --dandi-source evidenced \
                                    --lab different                  #   73
```

What survives is split among the reviewers in
`reuse_confirmation/reviewers.json`, one assignment each at
`<username>/<username>-assignment-<pathway>.json`. Every filename repeats the
username, so a file sent on its own still says whose it is. A username that is
not in the registry is refused rather than assigned to, so a typo cannot deal a
round to somebody who does not exist; `--reviewers` narrows to a subset of those
listed, by username.

An assignment is a queue, not a history. It holds what you still have to read,
so answering a pair takes it out, and `--limit` cuts a round down to something
you will actually finish. What you did not get to carries into the next round
rather than being forgotten, and what you answered stays on record in your
answer file.

Dealing never takes work back. A pair somebody still owes stays theirs, and a
pair somebody has answered is not dealt again, so rerunning the pipeline hands
out only what it added and nobody reviews the same pair twice. A run that
changes nothing rewrites nothing.

## Running a session

```bash
python -m src.review.run_review --reviewer rly --pathway indirect \
    --assignment reuse_confirmation/rly/rly-assignment-indirect.json
```

A session always holds every candidate in its queue. An assignment narrows what
is shown to your share of it — **Mine** and **Everyone** switch between the two
without restarting, because the pairs were loaded either way.

Drop `--assignment` to open on all of it instead:

```bash
python -m src.review.run_review --reviewer rly --pathway indirect
```

`--reviewer` and `--pathway` are always asked for. The reviewer is a registered
username and decides where the reviews are written; the pathway decides which
labels the buttons offer. Neither is inferred from a filename, and an assignment
that disagrees with either — somebody else's, or the other queue's — is refused
rather than quietly overriding what you asked for.

This serves the worksheet on `http://127.0.0.1:8000/` and opens it; `--port`
moves it, which is what a second reviewer on the same machine needs.

`--paper-cache` is the fetched paper text served behind the **Raw Text** links,
`.paper_cache` by default: the same cache the classification run read, so what
you see is what the classifier saw. It is not in the candidate list, because
what a machine has fetched is a fact about that machine.

Because the whole queue is loaded, a pair you reviewed before it was ever
assigned — or that was never assigned to you at all — is still there under
**Everyone**. Nothing about looking back at a call depends on the pair being in
your assignment.

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

**Mine / Everyone** appears when you opened on an assignment, and is a separate
question from the one above: whose the pair is, rather than whether it has been
reviewed. The two compose, so **Mine · Unreviewed** is your work still to do and
**Everyone · Reviewed** is every call made on the queue so far.

The toolbar counts two different things, and keeps them apart: **Pair 3 of 871**
sits beside the filter buttons because it is where you are inside whatever they
select, while the bar and **142 of 875 reviewed** sit off by Save because they
are the queue as a whole.

## Where the answers go

`reuse_confirmation/<you>/<you>-reviews.json`, written as you work:

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

Both queues write to the same file. A pair belongs to one of them only, so the
two cannot write over each other, and working through both assignments fills one
answer set.

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
