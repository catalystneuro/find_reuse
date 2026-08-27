# Confirming reuse

Everything the manual check of the classifier's REUSE calls takes and produces.
Candidates go in at the top, one reviewer's work sits in the directory named
after them, and confirmed reuse comes out. See [REVIEWING.md](../REVIEWING.md)
for how the three steps fit together.

```
reuse_confirmation/
  reviewers.json                              who reviews
  reuse_candidates.json                       every pair still to be checked
  pauladkisson/
    pauladkisson-assignment-indirect.json     what they still have to read
    pauladkisson-assignment-direct.json
    pauladkisson-reviews.json                 what they decided
```

The top of the tree is what everybody shares; a directory below it is one
person's.

Every filename repeats the username its directory already carries. These files
get sent to people one at a time, and two of them side by side in a chat window
should not be distinguishable only by which folder they came out of. The
redundancy is the point: which directory a file sits in stops mattering.

## `reviewers.json`

Who reviews. Two names, because they do two different jobs.

```json
[{"username": "rly", "name": "Ryan Ly"}]
```

`username` names the directory below and every file in it, so it has to be
stable and usable as a filename — a GitHub handle already is, which is why it is
the obvious thing to use. Changing someone's username means renaming their
directory and its files to match; nothing does that for you.

`name` is who that is, for whoever opens this file to decide who should take a
round. Nothing reads it. That is fine here in a way it would not be in a
generated file: this one is kept by hand and meant to be read by a person.

Assigning to a username that is not listed is refused rather than obeyed — a
typo would otherwise deal pairs to somebody who does not exist, taking them out
of circulation and giving them to nobody. So is a username that could not name a
file, since one quietly rewritten into a usable form would come back as somebody
else, holding none of their own work.

## `reuse_candidates.json`

Every (paper, dandiset) pair the classifier called REUSE, with everything needed
to judge it: the paper, the dataset, the paper it cited, the model's reasoning
and the passages it quoted. Alongside those, the fields a round is cut on,
including `dandi_reason` — why this pair counts as DANDI data, which is a
different question from whether DANDI hosts the modality it reused. Written by
`src.review.build_candidates`, sorted by pair, so rerunning the pipeline shows
up as the pairs it added.

Its header says which run of the pipeline produced it — the model, the prompt
version, and the labels that run reached, per input:

```json
{"path": "output/fulltext_classifications.json",
 "models": ["openai/gpt-5.6-luna"], "prompt_versions": [5],
 "labels": ["MENTION", "NEITHER", "REUSE"]}
```

That is the version of a candidate list. Two of them are comparable only if this
matches, and it is what identifies the run a set of reviews was checking.

## `<username>/<username>-assignment-<pathway>.json`

Which pairs are whose, nested paper to dataset the way the reviews are. Pairs
only — the records are in the candidate list, and storing them twice would only
let the two disagree.

```json
{"reviewer": "rly", "pathway": "indirect",
 "pairs": {"10.1002/acn3.70285": ["000768"]}}
```

A queue, not a history: only what that person still has to read. Reviewing a
pair takes it out, so these files stay short and a finished round leaves an
empty one. What a round did not get through carries into the next.

Regenerable in principle, but not in practice: who was asked to read what is a
decision, and dealing again from scratch would not necessarily reach the same
one. Written by `src.review.assign_reviews`.

## `<username>/<username>-reviews.json`

One person's reviews, written by the dashboard as they work, nested paper to
dataset to what was decided about that pair.

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

A review is one person's read of one pair: a `call`, and a `note` saying why
where the call is not obvious. A paper reusing four datasets stands in four
separate relationships, so it holds four reviews under its DOI. Both queues
write to this one file, since a pair belongs to one of them only. This is the
file that accumulates across rounds, and a session reads it alongside the
assignment so that past calls stay on screen under **Reviewed**.

**This is the one file here that cannot be regenerated.** The classifications it
checks can be re-run at any time; a person's reading of a paper cannot. It
carries nothing about the model or the prompt that produced the classification,
so it stays valid across re-classification.

Reviewers judge independently, so two people covering the same round leave two
files; they are compared afterwards, not merged as the work is done.

## All of it is committed

The reviews because they are irreplaceable, the assignments because they record
a decision, and the candidate list because it is what the assignments point into
— together they let somebody clone the repository and start reviewing.
