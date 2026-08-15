Subject: Reuse detection: switching to whole-paper classification, and what it changes

Hi all,

I have reworked how we decide whether a paper reused data, and the results
differ enough from what we had that I want to lay out the approach before we
build anything else on top of it.

## What Changed

Until now we extracted small windows of text around each citation and asked the
model to judge from those excerpts. That made the decision depend on the
citation resolution machinery being correct, and on the evidence happening to
fall inside one of the windows. Data availability statements, which are the
single most reliable indicator of reuse, usually sit at the end of a paper and
far from any citation, so they were often outside the window entirely.

We now send the entire paper in one call and ask the model to find the evidence
itself. The change is affordable because of the model: DeepSeek V4 Flash at
$0.14 per million input tokens. A full pass over 11,578 papers with retrievable
full text cost $47.48 and ran in under three hours. The direct pathway, which
covers papers naming a dandiset identifier outright, added $2.12. At that price
we can re-run the whole corpus whenever the question changes, which we have now
done three times.

## Requiring a Quote

The classifier must return the passage it judged from, quoted character for
character, and we check every quote against the paper before accepting it. A
label on its own has to be taken on trust. A quote either appears in the text or
it does not.

About 5% of quotes do not appear, which is the fabrication rate we should assume
for anything this model tells us. Of the reuse verdicts, 733 of 740 carry at
least one quote we could locate, so the fabrications are concentrated in cases
where a real quote also exists rather than propping up verdicts on their own.

Matching needed to be layered to be useful. Text extractors emit curly quotes,
non breaking spaces and en dashes that the model straightens when transcribing,
and a model quoting from mid sentence drops the lead in and capitalises the new
first word. A strict substring test flagged almost every run as fabricated. Each
tier still requires every non whitespace character in the original order, and we
record which tier matched, so a byte perfect quote stays distinguishable from
one that survived normalisation.

We also ask separately for the passage establishing where the data came from.
That matters because a reuse claim and a provenance claim fail independently,
and previously only the first was checkable.

## Modality, Which Turned Out To Matter Most

A large share of DANDI holdings are Patch-seq, and those components live in
different places. DANDI holds the neurophysiology and the behaviour recorded
alongside it, while morphological reconstructions sit in NeuroMorpho or the
Allen Cell Types Database and transcriptomics sits on GEO, CELLxGENE or NeMO. A
paper that cites a Patch-seq study and analyses only its gene expression has not
reused DANDI data, though at the citation level it looks identical to one that
did.

The classifier now reports which parts of a dataset a paper actually analysed.
Across the citing pathway, 270 of 740 reuse papers, roughly a third, are not
neurophysiology at all. The largest single group is 189 papers reusing
transcriptomics alone. Without that distinction our reuse count was inflated by
more than a third.

## Where The Numbers Land

After deduplicating versioned DOIs, which I discuss below, we have 752 papers
reusing something from a DANDI linked dataset and 483 reusing neurophysiology
specifically.

The provenance question narrows it further. Only 101 of those 483 carry textual
evidence that the data came from DANDI rather than somewhere else. Another 139
never state a source, so DANDI cannot be ruled out, and 243 name a different
archive, most often the Allen Institute or CRCNS. This is a real result rather
than a measurement problem: the data behind a dandiset is frequently
distributed through several channels at once, and a paper can legitimately reuse
it without ever touching DANDI.

Splitting by whether the reusing group produced the data, 371 of the 483 are
different labs and 100 are the same lab. Combining both filters, the figure I
would defend is 61 papers where an outside lab reused neurophysiology data with
evidence pointing at DANDI, or 73 counting papers that reuse several datasets
with a mix of both. If we resolve the 139 papers with no stated source, the
upper bound is 200.

## Two Things I Got Wrong Along The Way

I had assumed DANDI stores morphology, which it does not, and had missed that it
stores behaviour, which it does. That assumption is also baked into
filter_patchseq_genetic.py, which keeps morphology only reuse as DANDI reuse and
has no concept of behaviour. That script should be retired now that this runs
inline on full text.

Ben spotted that eLife DOIs were being double counted. Their reviewed preprint
model mints .1, .2 and .3 for each revision alongside the base DOI, so one paper
can appear five times. There were 557 redundant DOIs across 240 groups, 233 of
them eLife, with the rest from Research Square, F1000Research, Authorea and
Qeios. Every figure above is post deduplication and roughly 5% lower than what I
had reported before.

That mistake had one useful side effect. Because the versions were classified
independently, they act as a consistency check on near identical text. Of 407
version groups, 385 agreed and 22 disagreed, which gives us a 94.6%
self consistency rate. That is the most direct reliability estimate we have, and
it means roughly one judgement in twenty is sensitive to small textual
differences.

## What I Would Like From You

The 61 papers are in a review page with the quoted evidence and the provenance
passage for each, so they can be checked individually rather than taken on
faith. I would rather several people spot check them than have me confirm my own
output. The cases worth attacking first are the ones qualifying on a quoted
mention of DANDI, since a passing reference in a paper that actually downloaded
from CRCNS would be a false positive of exactly the kind this cohort is
vulnerable to.

The other open question is whether we treat the 139 papers with no stated source
as candidates or as excluded. That single decision moves the headline from 61 to
somewhere near 200, and it is a judgement about what claim we want to make
rather than something the data settles.

Ben
