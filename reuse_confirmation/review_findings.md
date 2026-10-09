# What the Manual Reviews Say About the Classifier

Every pair in `reuse_candidates.json` is one the classifier called REUSE, so a
reviewer who called a pair anything else found an error. This document reads the
two reviewers' calls and notes against the candidate list, sorts the errors by
cause, and proposes pipeline changes in order of how many errors each would
remove.

The numbers come from Paul's reviews on `main` (364 candidate pairs) and Ryan's
round 2 reviews from #65 (370 candidate pairs), which overlap on 4 pairs and
agree on all 4. Together they cover 730 of the 1,838 candidate pairs, and the
reviewers added 22 pairs the pipeline had not proposed. The candidate list is
the one generated on 2026-09-25. The tables are produced by

```bash
python -m src.review.analyze_reviews
```

which reads every reviews file under `reuse_confirmation/`. Until #65 is merged,
pass Ryan's file from that branch with `--reviews`. A few counts below are
marked as one-off checks; those used local caches the script does not read.

## Overall Agreement

Reviewers confirmed 468 of the 715 pairs they reached a decision on (65%). The
direct pathway, where the paper names a dandiset identifier, is much more
reliable than the indirect pathway, where the paper cites a dandiset's paper.

| | pairs | reuse | ambiguous reuse | neither | mention | primary | unsure | confirmed |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| direct | 128 | 106 | 0 | 19 | 0 | 3 | 0 | 83% |
| indirect | 602 | 362 | 45 | 164 | 16 | 0 | 15 | 62% |

All 22 rejected direct pairs fall under three mechanical causes described below
(15 on a DOI that is not a paper, 4 on a preprint whose published version is
also listed, 3 where the citing paper deposited the data). Without the first two
the direct pathway is at 106 of 109 (97%).

## Dandisets That Share a Paper

The strongest predictor of a rejected pair is whether other dandisets name the
same paper.

| other dandisets naming the paper | pairs | reuse | ambiguous reuse | rejected | unsure | confirmed |
|---|---:|---:|---:|---:|---:|---:|
| none | 477 | 403 | 6 | 62 | 6 | 86% |
| 1 | 84 | 35 | 13 | 32 | 4 | 44% |
| 2 or more | 169 | 30 | 26 | 108 | 5 | 18% |

The indirect pathway builds one pair for every dandiset that names the cited
paper and asks the classifier about each pair separately. A paper that reused
the Churchland data is then called REUSE for every MC_Maze dandiset, and at most
one of those calls is right. The five MC_Maze dandisets (000070, 000128, 000138,
000139, 000140) account for 163 judged pairs, of which 30 were confirmed (18%).
Every other dandiset together is at 438 of 552 (79%).

This affects 803 of the 1,838 candidate pairs, so it is the largest source of
error in the unreviewed part of the list too.

## Causes of Rejected Pairs

Each of the 202 rejected pairs (neither, mention, or primary) is assigned one
cause by rule, from the pair itself where possible and from the reviewer's note
otherwise. The rules are in `src/review/analyze_reviews.py`.

| cause | pairs | papers |
|---|---:|---:|
| reuse of a dandiset sharing the paper | 61 | 29 |
| reuse of another dandiset | 36 | 19 |
| not a paper | 25 | 14 |
| data taken from elsewhere | 23 | 20 |
| preprint of a paper also listed | 19 | 7 |
| cited without reuse | 18 | 13 |
| citing paper is the source | 10 | 7 |
| no rule | 10 | 6 |

Reuse of a dandiset sharing the paper. The paper did reuse data described by the
cited paper, and the reviewer credited a different dandiset that names the same
paper. Reviewers decided which one from a dandiset identifier or URL in the data
availability statement, from the dataset's name (MC_Maze Small), from neuron and
trial counts that match only one variant, or from recording conditions (Scala
000008 at room temperature and 000035 at physiological temperature).

Reuse of another dandiset. The same failure where the two dandisets name
different papers: IBL behavior (000045) and the IBL Brain Wide Map (000409),
000574 and 000575, and the MC_Maze variants against the original 000070.

Not a paper. The DOI belongs to an eLife assessment, review, or author response
(suffix `.sa0` to `.sa3`), or to an OSF registration. 32 of the 1,838 candidate
pairs are on such a DOI.

Data taken from elsewhere. The paper reused the cited paper's data and says it
came from Zenodo, Dryad, Figshare, G-Node, or CRCNS. In most of these the
dandiset holds only part of that data. MC_RTT (000129) is one session of the
Sabes lab Zenodo deposit, and the paper used sessions that are not in it.

Preprint of a paper also listed. A one-off check against
`.preprint_doi_map.json` finds 137 candidate pairs on 100 preprints whose
published version is also in the candidate list. `build_candidates` collapses
version suffixes and does not apply that map.

Cited without reuse. The classifier took a background citation, a replotted
figure, or the reuse of a different cited paper's data as reuse of this one.

Citing paper is the source. The citing paper deposited or assembled the data:
benchmark papers such as FALCON, a paper that harmonized several datasets into a
new dandiset, and a dandiset's own paper citing an earlier paper from the same
project. The indirect prompt has no PRIMARY label to give these. A one-off check
finds 25 indirect candidate pairs on 16 papers where the citing paper is itself
listed as some dandiset's paper, 8 of them for the same dandiset.

## Pairs the Pipeline Missed

Reviewers added 22 pairs on 17 papers. All 22 are on a paper the pipeline had
proposed for a different dandiset, so the paper was read and the wrong dataset
was credited. Seven name a dandiset that has no candidate pair at all, and seven
are the Allen Visual Coding Neuropixels dandisets (000021, 000022). In a one-off
check, the dandiset identifier appears in the fetched text for 1 of the 22, so a
wider identifier search would not have found them.

## Proposed Changes

The first two are filters with no judgment involved. The third is the one that
matters most.

1. Drop DOIs that are not papers before classification. A pattern on the DOI
   (`\.sa\d+$` and `10.17605/osf.io/`) removes 25 of the 202 rejections and 32
   candidate pairs.

2. Collapse a preprint onto its published version when building pairs, using
   the preprint map the pipeline already keeps. This removes 19 rejections and
   about 137 candidate pairs, and it settles the question from the October 8
   call about which version counts.

3. Decide which dandiset a reuse belongs to in a separate step. Classify once
   per citing paper and cited paper. When several dandisets name the cited
   paper, ask a second question that lists those dandisets with their names and
   descriptions and asks which ones the text identifies, with "cannot tell" as
   an allowed answer. Reuse that cannot be attributed would be counted once for
   the group in the analyses and would reach reviewers as one card, not one card
   per dandiset. This addresses the 61 rejections in the first row of the table,
   most of the 45 ambiguous calls, and the 803 candidate pairs that have
   siblings. It also needs fewer classification calls, one per cited paper in
   place of one per dandiset.

4. Give the indirect prompt the related dandisets as well. For a dandiset whose
   project has others (IBL, the Boran working memory sets, MC_Maze and 000070),
   name them in the prompt and instruct the model to answer REUSE only for the
   dandiset whose contents match what the paper used. This addresses the 36
   rejections in the second row and the missed pairs that were reuse of a
   neighboring dandiset, which is about 10 of the 22.

5. Add PRIMARY to the indirect labels, and skip pairs where the citing paper is
   the dandiset's own paper. The second part is a lookup against the paper
   relations the pipeline already holds.

6. Check the reference behind the evidence quote. #67 resolves the numbered
   citation in a quote to its bibliography entry. Where that entry is a paper
   other than the dandiset's, the pair can be flagged or sent back to the
   classifier. This targets the 18 pairs cited without reuse.

If changes 1 to 4 removed every rejection in their categories, the confirmed
share on the reviewed pairs would rise from 65% to about 82%. That is an upper
bound, since the attribution step will make its own errors.

## Things That Did Not Matter

Paper published before the dandiset. In a one-off check, 3 of the 1,355
candidate pairs with a known paper date have a paper older than the dandiset.
The MC_Maze false positives raised on the call come from shared papers, and a
date rule would remove almost none of them.

The archive the classifier recorded. Among indirect pairs, those the classifier
said came from DANDI were confirmed less often (52 of 106, 49%) than those it
said came from another archive (174 of 233, 75%). The first group is dominated
by Neural Latents Benchmark papers, which `classify_source_archive` maps to
DANDI and which hit the shared-paper problem.

## Differences Between Reviewers

On pairs whose paper belongs to one dandiset, Paul confirmed 216 of 248 (87%)
and Ryan 187 of 229 (82%). The gap Ryan raised on the call is in the pairs with
shared papers, where Paul called 48 of 116 neither and 17 ambiguous, and Ryan
called 82 of 141 neither and 22 ambiguous. Ryan was also dealt more of those
pairs. The two reviewers treat data taken from another repository differently:
Paul marked IBL data downloaded from Figshare as ambiguous reuse, and Ryan
marked Sabes lab data downloaded from Zenodo as neither. A shared rule for that
case would remove most of the remaining difference.
