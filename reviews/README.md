# Reviews

Everything a round of manual review needs, and everything it produces. See
[REVIEWING.md](../REVIEWING.md) for how the three steps fit together.

## `reviewers.json`

Who reviews. `name` decides the filenames below, so it is the one field that
must not change casually.

```json
[{"name": "rly", "github": "rly"}]
```

Assigning to a name that is not listed here is refused rather than obeyed — a
typo would otherwise deal pairs to somebody who does not exist, taking them out
of circulation and giving them to nobody.

## `reuse_candidates.json`

Every (paper, dandiset) pair the classifier called REUSE, with everything needed
to judge it: the paper, the dataset, the paper it cited, the model's reasoning
and the passages it quoted. Written by `src.review.build_candidates`, sorted by
pair, so rerunning the pipeline shows up as the pairs it added.

Its header says which run of the pipeline produced it — the model, the prompt
version, and the labels that run reached, per input:

```json
{"path": "output/fulltext_classifications.json",
 "models": ["openai/gpt-5.6-luna"], "prompt_versions": [5],
 "labels": ["MENTION", "NEITHER", "REUSE"]}
```

That is the version of a candidate list. Two of them are comparable only if this
matches, and it is what identifies the run a set of answers was checking.

## `assignments/<reviewer>.<pathway>.json`

Which pairs are whose, nested paper to dataset the way the answers are. Pairs
only — the records are in the candidate list, and storing them twice would only
let the two disagree.

```json
{"reviewer": "rly", "pathway": "indirect",
 "pairs": {"10.1002/acn3.70285": ["000768"]}}
```

A queue, not a history: only what that person still has to read. Answering a
pair takes it out, so these files stay short and a finished round leaves an
empty one. What a round did not get through carries into the next.

Regenerable in principle, but not in practice: who was asked to read what is a
decision, and dealing again from scratch would not necessarily reach the same
one. Written by `src.review.assign_reviews`.

## `<reviewer>.json`

One person's answers, written by the dashboard as they work, nested paper to
dataset to what was decided about that pair.

```json
{
  "reviewer": "your name",
  "reviews": {
    "10.1002/acn3.70285": {
      "000768": {"call": "reuse"},
      "000026": {"call": "mention", "note": "Cited for the method."}
    }
  }
}
```

A paper reusing four datasets stands in four separate relationships, so it holds
four records under its DOI, each with its own call and its own optional note.
Both queues write to the same file, since a pair belongs to one of them only.
This is the file that accumulates across rounds, and a session reads it alongside
the assignment so that past calls stay on screen under **Reviewed**.

**This is the one file here that cannot be regenerated.** The classifications it
checks can be re-run at any time; a person's reading of a paper cannot. It
carries nothing about the model or the prompt that produced the classification,
so it stays valid across re-classification.

Reviewers judge independently, so two people covering the same round leave two
files here; they are compared afterwards, not merged as the work is done.

## All of it is committed

The answers because they are irreplaceable, the assignments because they record
a decision, and the candidate list because it is what the assignments point into
— together they let somebody clone the repository and start reviewing.
