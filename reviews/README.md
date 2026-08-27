# Review answers

One file per reviewer, `<reviewer>.json`, written by

```bash
python -m src.analysis.build_reuse_verification_page ... --reviewer "your name"
```

Each records that person's answers for (citing paper, dandiset) pairs, keyed by
DOI and dandiset id joined with a tab:

```json
{
  "reviewer": "your name",
  "calls": {"10.1002/acn3.70285\t000768": "reuse"},
  "notes": {}
}
```

**These are committed.** They are the one part of the pipeline that cannot be
regenerated — the classifications they check can be re-run at any time, a
person's reading of a paper cannot. They carry nothing about the model or the
prompt that produced the classification, so they stay valid across
re-classification.

Reviewers judge independently, so two people covering the same round leave two
files here; they are compared afterwards, not merged as the work is done.

See [REVIEWING.md](../REVIEWING.md).
