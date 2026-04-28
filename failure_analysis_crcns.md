# Failure Mode Analysis: CRCNS Manual Review

**Date:** 2026-04-28  
**Reviewed entries:** 30  
**Matches (LLM NEITHER treated as equivalent to human "unsure"):** 22 (73%)  
**Genuine mismatches:** 8 (27%)

---

## Overview

The CRCNS manual review compared LLM classifications against human ground-truth labels across 30 sampled entries. LLM NEITHER is treated as equivalent to human "unsure" — both indicate the classifier could not determine the correct label. Under this equivalence, 22/30 entries match (73%), leaving 8 genuine mismatches (27%).

Mismatches were categorized into six distinct failure modes, described below. Some failures originate in the LLM's reasoning; others originate upstream in the text access or extraction pipeline.

---

## Failure Modes

### 1. CAPTCHA Blocking the LLM

**What happens:** The text fetcher encounters a CAPTCHA (primarily on Elsevier and Science publisher pages) and cannot retrieve the paper. The LLM receives no text and correctly classifies the entry as NEITHER with confidence 1, reasoning "Paper text not available." The human reviewer, however, can access the paper through a browser and may be able to determine the correct classification.

**Nuance:** This is a pipeline infrastructure failure, not an LLM reasoning failure. The LLM's NEITHER call is the right response given what it received. The pipeline is correctly signaling uncertainty rather than guessing.

**Potential improvement:** Investigate CAPTCHA workarounds at the fetcher level (e.g., alternative access routes, pre-fetching via institutional proxy). In the meantime, CAPTCHA-blocked entries could be flagged separately in the review dashboard rather than surfaced as NEITHER classifications.

**Examples:**
- `10.1016/j.jml.2025.104726|ac-4` — Elsevier CAPTCHA
- `10.1016/j.jphs.2025.12.002|bf-1` — Elsevier CAPTCHA
- `10.1126/science.aaf3319|cai-1` — Science CAPTCHA

---

### 2. Institutional Access Asymmetry

**What happens:** The pipeline runs with institutional access credentials (e.g., API tokens, library proxies) that are not easily available in a standard browser session. The LLM successfully retrieves and reads a paper, producing a high-confidence classification. The human reviewer, working from a browser without the same credentials, cannot access the full text and marks the entry "unsure."

**Consequence:** Some REUSE classifications marked "unsure" during review may actually be correct. The review process is unable to confirm or deny them, creating a systematic blind spot for paywalled content that the pipeline can access but the reviewer cannot.

**Potential improvement:** Surface the cached paper text or key excerpts directly in the review dashboard so the reviewer can evaluate the LLM's evidence without needing independent full-text access.

**Examples:**
- `10.1038/s41593-018-0211-4|cai-1` — LLM=REUSE (confidence 9), Human=unsure; LLM excerpt explicitly states "All calcium imaging data of u-GCaMP6 + dLGN axons was first reported by Sun and co-workers"
- `10.1038/s41593-021-00895-5|cai-1` — LLM=REUSE (confidence 10), Human=unsure; LLM excerpt shows dataset table with explicit CRCNS download statement

---

### 3. Text Extraction Failure

**What happens:** The paper genuinely cites the primary paper, but the text extraction and chunking pipeline serves the LLM excerpts from sections that do not contain the relevant citation. The LLM, seeing no evidence of the citation, correctly classifies the entry as NEITHER. The reviewer, with access to the full paper, can see the citation is present.

**Nuance:** As with CAPTCHA failures, the LLM's reasoning is sound given its input. The failure is upstream in the text segmentation layer.

**Potential improvement:** Review the excerpt selection strategy — particularly whether reference-section-adjacent text and in-line citation contexts are being reliably extracted. The issue may be a chunking boundary cutting off citation context.

**Examples:**
- `10.1101/2023.08.03.551900|am-3` — LLM=NEITHER (confidence 10), Human=mention; LLM states "the paper does not appear to cite the primary paper at all" but reviewer notes "the citing paper does mention the original; maybe a parsing error"
- `10.1101/2020.10.07.330282|ac-4` — LLM=NEITHER (confidence 10), Human=mention; LLM excerpt shows an unrelated citation at the same reference index

---

### 4. Same-Lab False Positive with Hallucinated Evidence

**What happens:** The LLM detects shared authorship between the citing paper and the primary dataset paper and uses this as a strong prior toward REUSE. When the actual text evidence is thin or absent, the LLM fabricates specific dataset details — unit counts, epoch counts, methodology — as if they appeared in the paper. It then reports these invented details as supporting evidence, producing a high-confidence REUSE classification with no real grounding.

**This is the most serious failure mode identified.** A high-confidence call backed by fabricated evidence is harder to catch than a borderline low-confidence call, and it can mislead both automated metrics and reviewers who trust the LLM's reasoning.

**Potential improvement:** Require the LLM to quote exact text spans as evidence rather than describe what it "found." Any dataset-specific claim (unit counts, electrode counts, subject numbers) should be traceable to a quoted excerpt.

**Examples:**
- `10.1016/j.cub.2021.02.055|bf-3` — LLM=REUSE (confidence 10), Human=mention; LLM claimed "same authors, 423 single units, 54 epochs, k-means clustering" — none of these details were present in the paper text

---

### 5. Methods/Protocol Paper False Positive

**What happens:** A CRCNS dataset's primary paper is a methods or protocol paper rather than a primary data paper. The citing paper references this methods paper for methodological background. The LLM interprets citation of the methods paper as evidence that the citing paper reused the underlying dataset.

**Why it's hard:** The methods paper and the dataset are legitimately associated, so surface-level signals (shared citation, shared lab) look identical to genuine reuse. The distinction requires understanding whether the citing paper is using the method or the data.

**Potential improvement:** Flag datasets whose primary papers are protocol/methods papers explicitly in the pipeline, and apply a stricter evidence threshold for classifying citations to those papers as REUSE.

**Examples:**
- `10.1038/s41467-020-18732-x|am-4` — LLM=REUSE (confidence 10), Human=mention; the am-4 primary paper is a data collection protocol paper; the citing paper cites it for methodology, not data reuse

---

### 6. Transitive Data Reuse (Nested Citation Chain)

**What happens:** A citing paper reuses data from an intermediary reanalysis paper, which itself reanalyzed data from the original tracked dataset. The citation chain runs:

> citing paper → reanalysis paper (Lynch et al. 2016) → original dataset (Okubo et al. 2015 / am-3)

The citing paper does not explicitly cite the original dataset paper; it cites only the intermediary. During review, this entry was marked as a mention because the link to am-3 was not visible without manually following the Lynch et al. citation chain.

**Nuance:** The LLM's REUSE classification may have been correct. The failure is in the review methodology — a human reviewer cannot reasonably be expected to follow all transitive citation chains during a first-pass review. This case also raises an open policy question: **should transitive reuse count as reuse of the original dataset?**

**Examples:**
- `10.1101/2023.01.23.525213|am-3` — LLM=REUSE (confidence 9), Human=mention (later revised); citing paper reanalyzes Lynch et al. 2016 data, which is itself a reanalysis of Okubo et al. 2015 (am-3)

---

## Summary

| Failure Mode | Origin | LLM Fault? | Entries | Genuine Mismatches |
|---|---|---|---|---|
| CAPTCHA blocking | Pipeline access | No | 4 | 0 (NEITHER ≈ unsure) |
| Institutional access asymmetry | Review methodology | No | 3 | 3 |
| Text extraction failure | Text pipeline | No | 2 | 2 |
| Same-lab false positive with hallucinated evidence | LLM reasoning | Yes | 1 | 1 |
| Methods/protocol paper false positive | LLM reasoning | Yes | 1 | 1 |
| Transitive reuse / nested citation chain | Review methodology | Unclear | 1 | 1 |

---

## Open Questions

1. **Transitive reuse policy:** Should the pipeline attempt to detect reuse through intermediary reanalysis papers? This would require multi-hop citation graph traversal and a policy decision about what counts as reuse.

2. **Evidence grounding:** Should the LLM be required to quote exact text spans rather than describe its reasoning in prose? This would make hallucinated evidence detectable during review.

3. **CAPTCHA entries in metrics:** Should CAPTCHA-blocked NEITHER classifications be excluded from precision/recall calculations, since the classification failure is infrastructural rather than reasoning-based?

4. **Reviewer access:** Can the review dashboard be updated to display the cached excerpts the LLM used, so the reviewer's evaluation is not blocked by paywall access differences?
