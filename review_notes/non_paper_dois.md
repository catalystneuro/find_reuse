---
dandisets: "all"
---

# Non-paper DOIs

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
