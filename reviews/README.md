# Review answers

One file per reviewer, `<reviewer>.json`, written by

```bash
python -m src.review.run_review ... \
    --mode indirect --reviewer "your name"
```

Each records that person's answers, nested paper to dataset to what was decided
about that pair. Both review modes write to the same file, since a pair belongs
to one queue only:

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

**These are committed.** They are the one part of the pipeline that cannot be
regenerated — the classifications they check can be re-run at any time, a
person's reading of a paper cannot. They carry nothing about the model or the
prompt that produced the classification, so they stay valid across
re-classification.

Reviewers judge independently, so two people covering the same round leave two
files here; they are compared afterwards, not merged as the work is done.

See [REVIEWING.md](../REVIEWING.md).
