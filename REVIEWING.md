# Reviewing reuse classifications

The classifier answers one question about one (paper, dandiset) pair: what is
that paper's relationship to that dataset? Review checks its answers by asking a
person the same question about the same pair, so the two can go in a confusion
matrix and the classifier's precision can be measured against a human read.

This is the walkthrough, start to finish. **Steps 1–3 happen once, by whoever
cuts the round. If somebody has already assigned you a round, skip to
[step 4](#4-set-yourself-up).**

---

## 1. Build the candidate list

Only needed when the pipeline has been re-run. Otherwise
`reuse_confirmation/reuse_candidates.json` is already committed and current.

This is the one step that reads the pipeline's output, so it is the one step
that needs the archives Ben shares on Google Drive. Unpack `results.tar.gz` into
the repository root, then:

```bash
python -m src.review.build_candidates \
    -i output/fulltext_classifications.json \
    -i output/fulltext_direct_openalex.json
```

Every pair the classifier called REUSE goes in, with everything needed to judge
it already resolved: the paper, the dataset, the paper it cited, the model's
reasoning and the passages it quoted. Roughly 1,000 pairs across both pathways.

The file is committed, which is what lets everyone else skip this step. Pairs
come out sorted, so a re-run diffs as the pairs it added. If nothing changed it
says `Unchanged` and rewrites nothing.

## 2. Cut a round and deal it

```bash
python -m src.review.assign_reviews --neuro --dandi-source evidenced
```

The flags narrow the candidate list to the round you want reviewed; what
survives is split evenly among the reviewers listed in
`reuse_confirmation/reviewers.json`.

| Flag | Keeps pairs where |
|---|---|
| `--pathway {indirect,direct}` | only that queue |
| `--dandi-source possible` | the paper named DANDI, or named no archive at all |
| `--dandi-source evidenced` | something positively says the data came from DANDI |
| `--neuro` / `--no-neuro` | neurophysiology was (or was not) among what was reused |
| `--modality NAME` | that modality was reused; repeatable |
| `--reuse-type TYPE` | the authors did that with it; repeatable |
| `--lab {same,different}` | the reusing group is (or is not) the one that produced the data |
| `--limit N` | at most N *new* pairs are dealt |

Naming none of them takes everything. `--limit` is how you keep a first round to
a size somebody will actually finish.

These compose into the funnel the project reports on:

```bash
--neuro                                     # 482 papers
--neuro --dandi-source evidenced            # 112
--neuro --dandi-source evidenced --lab different   # 73
```

**Dealing never takes work back.** A pair somebody still owes stays theirs, and
a pair somebody has already reviewed is never dealt again — so re-running after
the pipeline adds pairs hands out only what is new, and nobody reviews the same
pair twice.

The corollary catches people out: **once a wide round is out, a narrower filter
deals nothing**, because every pair it selects is already in somebody's queue.
`--reassign` throws the existing queues away and deals from scratch:

```bash
python -m src.review.assign_reviews --reassign \
    --neuro --dandi-source evidenced --lab different
```

That discards assignments only. Reviews already given are in their own files and
are never touched, and their pairs stay out of the deal either way.

## 3. Commit the round

```bash
git add reuse_confirmation/
git commit -m "Assign the DANDI-evidenced round"
git push
```

This is the handoff. Reviewers get their assignments by pulling.

---

## 4. Set yourself up

*You are here if somebody assigned you a round.*

Two repositories, because the review page serves the paper text the classifier
was given, and that reader lives in the other one:

```bash
git clone https://github.com/catalystneuro/find_reuse.git
git clone https://github.com/catalystneuro/paper-text-fetcher.git

cd find_reuse
pip install -e ../paper-text-fetcher[all]
pip install -r requirements.txt
```

That is the whole setup. **You do not need anything from Google Drive to
review** — the candidate list and your assignment are both committed, and
nothing in the review path reads the pipeline's output.

One optional extra. Each paper's card has a **Raw Text** link that opens the
text we fetched for it, which is how you read a paper that turns out to be
paywalled. That text lives in a 6 GB cache that is *not* in the repository. If
you have not unpacked `paper_cache.tar.gz` from Google Drive, every one of those
links will say *"No text for this paper is in the cache."* That is expected, not
broken — but the cache is worth having, since the alternative is bouncing off
publisher paywalls.

Then pull, so you have the round:

```bash
git pull
ls reuse_confirmation/<your-username>/
```

## 5. Open your assignment

```bash
python -m src.review.run_review --reviewer rly --pathway indirect \
    --assignment reuse_confirmation/rly/rly-assignment-indirect.json
```

This serves the worksheet on `http://127.0.0.1:8000/` and opens it. `--port`
moves it, which is what a second reviewer on the same machine needs.

You will usually have two assignments, one per pathway, and they are separate
sessions — the two queues ask different questions and offer different buttons.
Run the command once per file.

`--reviewer` is your registered username and decides which file your reviews are
written to. `--pathway` decides which buttons the cards offer. Neither is
inferred from the filename, and an assignment that disagrees with either —
somebody else's, or the other queue's — is refused rather than quietly
overriding what you asked for.

Dropping `--assignment` opens the whole queue instead of your share of it.

## 6. Review

One pair fills the screen: the citing paper, the paper it cited where there is
one, and the dataset — each linked and sized to be read. The buttons sit
directly under them. What the classifier said is below that: useful, but not
what you read the answer off.

**Which buttons you get depends on the queue**, because the two pathways ask
different questions.

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
no cited paper, so the card shows the paper and the dataset alone.

| Label | When |
|---|---|
| **Reuse** | The paper obtained the dataset and analysed it. |
| **Primary** | The paper *is* the one that deposited the dataset. |
| **Neither** | Neither holds — the identifier is there for another reason. |
| **Unsure** | You cannot tell from the text. Say why in the note. |

Each queue offers only the labels its own classifier could have produced. A
label off that scale cannot go in the confusion matrix, which is the point of
reviewing at all.

Answering advances to the next pair. **Prev** and **Next** move without
answering, and pressing the label already recorded takes it back. Notes are
optional; write one whenever the answer is not obvious from the paper, and
always when you answer **Unsure**.

Each label carries its own colour before you press it — Reuse green, Mention
blue, Primary purple, Neither red, Unsure amber — so an answer is recognised by
colour rather than read off its text. The one you pick fills in and turns bold.

Your work saves itself as you go. The toolbar reads *Auto-saved* after each
write; **Save** forces one immediately and reads back *Saved*, so the two are
told apart. If it ever shows a failure instead, the server is gone and the
answer did not land.

### The two filter rows

**All / Unreviewed / Reviewed** picks what the queue holds. A session opens on
**Unreviewed**, the working pass: a pair leaves as you answer it and the next
takes its place, so the queue empties as you go. **Reviewed** is the
looking-back pass, where changing a call is a matter of clicking a different
label.

**Assigned Only / All** appears when you opened on an assignment, and asks a
different question: whose the pair is, rather than whether it has been reviewed.
The two are independent, so **Assigned Only · Unreviewed** is your work still to
do and **All · Reviewed** is every call made on the queue.

They sit at opposite ends of the toolbar because each belongs to the readout it
moves. **Pair 3 of 871** follows the review state; the progress bar and
**142 of 437 reviewed** follow **Assigned Only / All**. The bar deliberately
ignores the review state — switching to **Reviewed** should not read as having
finished.

## 7. Commit your reviews

```bash
git add reuse_confirmation/<your-username>/
git commit -m "Review the DANDI-evidenced round"
git push
```

Commit as you go rather than at the end. Reviews are the one artifact here that
cannot be regenerated: the classifications they check can be re-run any time, a
person's reading of a paper cannot.

Two reviewers touch different files, so pushing does not conflict. They are
compared afterwards, not merged as the work is done.

---

## Things worth knowing

**A quote marked *not in paper* is not there at all.** The classifier is made to
quote the passage it judged from, and every quote is checked against the paper
afterwards. About 3% cannot be found. A claim resting only on those has nothing
behind it — which is often the fastest way to spot a wrong REUSE call.

**Raw Text is not the published article.** It is what the classifier was given,
so it carries the export's own mangling of citations, captions and line numbers.
That is why a quote can be sound and still not match character for character.
Passages quoted for the pair are marked where they stand verbatim, and the page
opens on the first of them.

**The cited paper is sometimes a stand-in.** The indirect queue shows the paper
this work actually cited where discovery recorded it, and the dandiset's own
declared paper where it did not. The card says which — **Cited Paper** or
**Dataset Paper** — so a stand-in is not mistaken for the real citation.

**A paper reusing four datasets gets four independent reviews.** The pair is the
unit, not the paper. The same paper can be Reuse against one dandiset and
Mention against another.

**Every pair is in one queue only.** A pair both pathways reached is still one
pair asking one question, so it is reviewed once, in the direct queue — the only
one that can answer that these authors deposited the data.

**Your past reviews stay visible.** A session loads the whole queue, so a pair
you reviewed before it was ever assigned to you — or that was never assigned to
you at all — is still there under **All**.

**Reviews outlive re-classification.** Nothing about the model, the prompt, or
the run is recorded with your answer. The same pair should get the same answer
from the same person no matter which run produced the classification.

See [reuse_confirmation/README.md](reuse_confirmation/README.md) for what each
file in the tree is, and [RUNNING.md](RUNNING.md) for the pipeline that produces
the classifications being checked.
