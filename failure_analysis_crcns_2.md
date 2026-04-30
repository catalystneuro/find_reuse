# Failure Mode Analysis: CRCNS Manual Review (Round 2)

**Date:** 2026-04-30
**Reviewed entries:** 30
**Matches (LLM NEITHER treated as equivalent to human "unsure"):** 19 (63%)
**Genuine mismatches:** 11 (37%)

---

## Overview

This is the second round of CRCNS manual review, comparing LLM classifications against human ground-truth labels across a fresh sample of 30 entries. As in [round 1](failure_analysis_crcns.md), LLM NEITHER is treated as equivalent to human "unsure" — both indicate the classifier could not determine the correct label. Under this equivalence, 19/30 entries match (63%), leaving 11 genuine mismatches (37%).

The match rate dropped from 73% in round 1 to 63% in round 2, driven almost entirely by an increase in **text extraction failures** (the LLM receiving only references/bibliography or only metadata). 8 of 11 mismatches in round 2 fall into the text-extraction family, compared to 2 of 8 in round 1. The same-lab hallucination and methods/protocol-paper failure modes from round 1 did not recur in this sample.

---

## Failure Modes

### 1. Text Extraction Failure — Bibliography or Metadata Only (most common)

**What happens:** The text fetcher returns only the citing paper's reference list (bibliography) or only its frontmatter metadata (title, authors, affiliations) — never the body text. The LLM correctly recognizes this as a `low_quality_text` situation and classifies as NEITHER with confidence 10. The human reviewer can open the paper in a browser and confirm the citation is a standard background mention.

**Nuance:** The LLM's NEITHER call is the right response given its input. The failure is upstream in fetching/extraction. Several of the human's notes explicitly call this out ("Not sure why the citation contexts were not retrieved", "Likely a citation extraction failure", "Not sure why the Citation Expert SERPs were not fetched properly").

**Why this is the dominant failure in round 2:** Round 2's sample reaches further into the long tail of CRCNS datasets (apl-1, evi-1, fcx-1, fcx-2, hc-10, ch-epfl-2009) where the citing papers come from a wider mix of publishers and preprint servers. A larger fraction of those venues seem to break the extraction pipeline.

**Examples:**
- `10.1101/2022.11.30.518585|apl-1` — LLM=NEITHER (conf 10), Human=mention; user notes "I have no problem accessing this text. Maybe a pipeline failure?"
- `10.1073/pnas.1718721115|cai-1` — LLM=NEITHER (conf 10), Human=mention; only metadata returned
- `10.1146/annurev-statistics-041715-033733|ch-epfl-2009` — LLM=NEITHER (conf 10), Human=mention; only bibliography returned
- `10.1038/s41528-024-00344-w|evi-1` — LLM=NEITHER (conf 10), Human=mention; only references returned
- `10.1093/sleep/zsz095|fcx-1` — LLM=NEITHER (conf 10), Human=mention; only metadata returned
- `10.1016/j.neuron.2025.03.016|hc-10` — LLM=NEITHER (conf 10), Human=mention; only references returned
- `10.1038/mp.2017.249|hc-10` — LLM=NEITHER (conf 10), Human=mention; only references returned

**Potential improvement:** Investigate the OpenAlex / fetcher pipeline for these specific publishers and preprint hosts. The high frequency suggests a systematic gap rather than individual paper-level oddities.

---

### 2. Text Extraction Failure — PDF-only DOI Page

**What happens:** A subtype of text-extraction failure where the citing paper's full text is only available as a downloadable PDF, not as HTML on the DOI landing page. The fetcher returns only the bibliography, the LLM classifies NEITHER, but the citing paper genuinely reuses the dataset.

**Why it's distinct:** Unlike the bibliography-only failures above, the human reviewer flagged this as REUSE (not just mention). Missing PDF-only papers therefore costs the pipeline genuine reuse signals, not just background citations.

**Examples:**
- `10.21203/rs.3.rs-1195514/v3|fcx-2` — LLM=NEITHER (conf 10), Human=reuse; user notes "The full text is only available as a downloadable PDF rather than on the DOI website. Likely an Open ALEXS failure."

**Potential improvement:** Add a PDF-fetch fallback when the HTML extractor returns only bibliographic content for Research Square and similar preprint platforms.

---

### 3. LLM Cannot Map Numbered Citations to the Primary Paper

**What happens:** When the citing paper uses numbered citations (e.g., `[42, 49, 51]`) instead of author-year (`Okubo et al., 2015`), the pipeline correctly resolves which reference number corresponds to the primary paper ([citation_context.py:108](citation_context.py#L108)) and surfaces excerpts where that number appears. **However, the resolved reference number is never told to the LLM** — see [classify_citing_papers.py:98-103](classify_citing_papers.py#L98-L103), which formats each excerpt only with its index and detection method, dropping the `reference_number` field stored on the context. The LLM sees the cited paper's *DOI* in the prompt header and a bag of excerpts containing numbered citations, but it has no anchor telling it which number in the excerpt corresponds to the primary paper.

**Consequence:** When an excerpt contains a numbered citation like `[42, 49, 51]`, the LLM has to guess which one is the primary paper. It guesses from semantic context — picking whichever number sits next to the most topically relevant phrase — and then writes that guess into its reasoning ("ref 49 is cited as the source of in vitro recordings used for validation"). With author-year citations the LLM does not have this problem, because the author names appear right next to the claim.

**Why this is dangerous:** The LLM produces a high-confidence REUSE call backed by what looks like real, in-text evidence. The error is invisible to the LLM and only catchable by a human who manually checks reference numbering against the citing paper's bibliography.

**Examples:**
- `10.1038/s41467-019-12572-0|ch-epfl-2009` — LLM=REUSE (conf 9), Human=mention; user notes "Reference 49, which is used in the reasoning of the LLM, does not point to the primary paper. This paper does reuse data, but not from the paired data set." The LLM's reasoning latched onto `[49]` in an excerpt about in vitro/in vivo validation, but ref 49 in that paper is a different work — the LLM had no way to know which number was the primary paper. Round 1's similar-looking case (`10.1101/2020.10.07.330282|ac-4`) defaulted to NEITHER; this round-2 case produces a confident **REUSE** false positive — strictly more harmful.

**Potential improvement:** Pass the resolved `reference_number` into the prompt explicitly, e.g., a header line stating "The primary paper is reference [49] in this paper's bibliography. Only treat citations of [49] (or ranges/lists containing 49) as citations of the primary paper." This grounds the LLM's reasoning in the pipeline's resolution rather than letting it guess.

---

### 4. Institutional Access Asymmetry

**What happens:** The pipeline accesses paywalled full text the human reviewer cannot reach in a browser. The LLM produces a confident classification; the human marks "unsure" because they cannot verify.

**Consequence:** Same as round 1 — a systematic blind spot where the pipeline's classifications cannot be validated against ground truth. Some of these may be correct and some may be wrong; we cannot tell.

**Examples:**
- `10.1038/s41587-021-01074-4|cai-2` — LLM=MENTION (conf 10), Human=unsure (no access)
- `10.1109/jmems.2021.3092230|evi-1` — LLM=MENTION (conf 9), Human=unsure (no access)

**Potential improvement:** (Same as round 1.) Surface cached LLM excerpts in the review dashboard so the reviewer can evaluate evidence without independent full-text access.

---

### 5. Excerpt Truncation in the Review Dashboard

**What happens:** The LLM is given many citation contexts when classifying, and may quote a high-numbered excerpt as evidence (e.g., "Excerpt 8 explicitly states that Panels A and B of Figure 3 are from a dataset of cortical excitatory cells attributed to Watson et al 2016"). However, only the **first 5** contexts are persisted to `context_excerpts` and rendered in the review HTML — see [classify_citing_papers.py:297](classify_citing_papers.py#L297) (`for context in contexts_with_text[:5]`). The reviewer cannot see Excerpt 8 because it was never saved.

**Why this is distinct from #4:** This is a review-tooling/persistence bug, not access asymmetry. Even if the reviewer had the full paper PDF, they would still be unable to *trace* the LLM's cited evidence back through the dashboard, because the relevant excerpt is missing from the saved record. Originally classified as "possibly hallucinated evidence" — but the LLM almost certainly *did* see Excerpt 8; the pipeline simply never wrote it to disk.

**Examples:**
- `10.1016/j.conb.2017.02.013|fcx-1` — LLM=REUSE (conf 9, references Excerpt 8), Human=unsure; user notes "the excerpts that get shown don't have the conclusive evidence that is used in the pipeline reasoning." The LLM's reasoning quotes Excerpt 8 but the dashboard only shows excerpts 1–5.

**Interaction with failure mode #4:** Access asymmetry (#4) is amplified by this truncation. When the LLM references a high-numbered excerpt *and* the human cannot access the paper, the reviewer has no path to verification at all.

**Potential improvement:** Persist all excerpts the LLM was shown (not just the first 5), or at minimum the specific excerpts the LLM cites in its reasoning. The change is a one-line fix at [classify_citing_papers.py:297](classify_citing_papers.py#L297) — drop the `[:5]` slice (or raise it substantially). The dashboard already paginates excerpts via `slice(0, 5)` in [build_minimal_review.py:181](build_minimal_review.py#L181); that should also be lifted.

---

## Summary

| Failure Mode | Origin | LLM Fault? | Round 2 Entries | Round 1 Entries |
|---|---|---|---|---|
| Text extraction — bibliography/metadata only | Text pipeline | No | 7 | 2 |
| Text extraction — PDF-only DOI page | Text pipeline | No | 1 | 0 |
| LLM cannot map numbered citations to primary paper | Prompt construction (resolved ref number not passed) | Yes (LLM guesses) | 1 | 0 |
| Institutional access asymmetry | Review methodology | No | 2 | 3 |
| Excerpt truncation in review dashboard | Review tooling (pipeline persistence) | No | 1 | 0 |
| CAPTCHA blocking | Pipeline access | No | 0 | 4 |
| Methods/protocol paper false positive | LLM reasoning | Yes | 0 | 1 |
| Transitive reuse / nested citation chain | Review methodology | Unclear | 0 | 1 |

---

## Cross-Round Observations

1. **Text extraction has overtaken access blocking as the dominant failure source.** Round 1 was dominated by CAPTCHA-blocked papers (4 entries) and access asymmetry (3). Round 2 has zero CAPTCHA failures and is dominated by bibliography-only / metadata-only extraction failures (7 entries plus 1 PDF-only and 1 wrong-index variant). This is consistent with round 2 sampling further into the long tail of dataset codes (apl-1, evi-1, fcx-1/2, hc-10, ch-epfl-2009) where citing papers come from a more diverse set of publishers and preprint hosts.

2. **A new high-confidence false-positive mode emerged: numbered-citation guessing.** Round 1's hard-to-classify failures all defaulted to NEITHER. Round 2 surfaced a case where the LLM, faced with numbered citations like `[42, 49, 51]` and no information about which number is the primary paper, guessed from semantic context and produced a confident REUSE call. The pipeline already resolves the correct reference number — it just doesn't tell the LLM. This is a more dangerous mode than NEITHER-by-default failures and is plausibly cheap to fix.

3. **Match rate is 63% (round 2) vs. 73% (round 1).** The drop is entirely attributable to the increased rate of bibliography-only extraction failures, not to LLM reasoning quality. If the text-extraction issues were resolved, the round-2 match rate would jump to ~90%.

---

## Open Questions (Updated)

1. **What is causing the bibliography-only extractions?** This single failure pattern is the largest source of round-2 mismatches. Is it OpenAlex returning truncated content for these venues? A specific HTML extractor failing on certain templates? Worth a focused investigation before the next round.

2. **Should we fall back to PDF parsing for Research Square / preprint hosts where the DOI landing page lacks full text?** One genuine REUSE was lost to this in round 2.

3. **Pass the resolved reference number into the prompt.** The pipeline already resolves the citing paper's bibliography number for the primary paper, but that information is dropped before the LLM sees it. Passing it explicitly would let the LLM disambiguate numbered citations like `[42, 49, 51]` instead of guessing from semantic context. (A separate, currently unobserved failure mode is the resolver itself being wrong; we'll catch that if it surfaces in a future review.)

4. **Excerpt persistence:** The 5-excerpt cap at [classify_citing_papers.py:297](classify_citing_papers.py#L297) was likely set for storage/display economy, but the LLM is regularly quoting beyond that window. Concrete fix candidates: persist all excerpts the LLM saw, or persist the union of (first 5, excerpts the LLM cited by number in its reasoning).

5. **Open questions from round 1 still apply:** evidence grounding (require quoted spans), reviewer access to cached excerpts, and the transitive-reuse policy question.
