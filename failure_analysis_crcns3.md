# Failure Mode Analysis: CRCNS Manual Review (Round 3)

**Date:** 2026-05-07
**Reviewed entries:** 100 REUSE/MENTION classifications (NEITHER calls were not reviewed this round)
**Genuine mismatches:** 12

Round 3 was run on a fresh sample after a round of prompt and pipeline optimization done in response to [round 1](failure_analysis_crcns.md) and [round 2](failure_analysis_crcns_2.md).

---

## Breakdown of the 12 Mismatches

| Group | Count | Priority |
|---|---|---|
| LLM=REUSE, Human=mention (false positives) | 8 | High — the focus of this analysis |
| LLM=MENTION, Human=reuse (recall miss) | 1 | Medium |
| LLM=REUSE, Human=unsure (unverifiable) | 3 | Low |

---

## The 8 REUSE→Mention False Positives

The eight false-positive REUSE calls split cleanly into three causes.

### A. LLM intelligence / prompting (2)

The LLM treats something that is clearly *not* data reuse as reuse. These are either model errors or rubric-definition gaps.

1. **`10.1101/029124|cai-1`** — LLM=REUSE (conf 9). The citing paper "derived parameters (alpha and A) for their fluorescence waveforms from experimental results in [3]" — a *fitted parameter* from the primary paper, not the underlying calcium-imaging data. User: "This is a modeling paper that just uses a parameter from the primary paper, which shouldn't count as data reuse."

2. **`10.1038/s41593-018-0284-0|pvc-8`** — LLM=REUSE (conf 9). The "citing paper" is a Coen-Cagli review/perspective written by the dataset's primary author, *discussing* their own data rather than reusing it. User: "This is a review and not data reuse."

**Fix:** Tighten the rubric in the prompt to explicitly exclude (a) parameter-only borrowing in modeling papers and (b) review/perspective articles, even when shared authorship is present.

---

### B. Citation-numbering pipeline issues (2)

The reference number that the pipeline associates with the primary paper does not match the reference number the LLM sees evidence about. The LLM ends up reasoning about a different paper than the one it thinks it's reasoning about.

3. **`10.1101/2022.06.30.497612|fly-1`** — LLM=REUSE (conf 9). LLM quotes "examined data from our functional screen of sparse Gal4 and split Gal4 driver lines [49]" and treats [49] as the primary. User: the primary (Aimon et al.) is **citation #52**; #49 is **Chen et al. 2022**, which is what the paper actually reuses.

4. **`10.1101/2022.09.25.509420|hc-10`** — LLM=REUSE (conf 9). LLM quotes "performed as part of a previously published study by Fernandez-Ruiz et al., [19]." User: in the published paper, **#19 is the primary (Sang-Hun Lee)** and **#8 is Fernandez-Ruiz** — i.e., the citation numbers in the extracted excerpts do not match the citation numbers in the published paper.

**Fix:** Investigate where the numbering goes out of sync. Candidates: (a) the resolver matches the primary's DOI to a wrong number, (b) the OpenAlex citation list and the body-text citation numbers disagree (e.g., preprint vs. published version), (c) the excerpt extractor rewrites or renumbers citations during chunking. A targeted audit comparing resolver output to a hand-checked bibliography on N papers would tell us which.

---

### C. Tricky cases — wrong source or over-indexed signal (4)

The paper genuinely reuses data, but not the data this primary/dataset is supposed to represent. The LLM detects strong reuse signals (shared authorship, "we used this dataset", deposit statements) and stops short of validating *which* data is actually being reused. These are the hardest cases — the surface features look identical to a true positive.

5. **`10.1016/j.cortex.2021.01.016|fcx-2`** — LLM=REUSE (conf 10). LLM cites Excerpt 11 stating data is deposited to CRCNS and references "Johnson et al., 2018 [38]" (the FCX-2 primary). User: the paper actually reuses **Johnson et al. 2017** data, which corresponds to the **PFC-5** dataset, not FCX-2. *(Wrong CRCNS dataset, same depositor.)*

6. **`10.1101/027102|pfc-2`** — LLM=REUSE (conf 10). LLM identifies "Peyrache et al. 2009 [7]" and matches it to the PFC-2 primary author. User: the paper reuses an earlier Peyrache 2009 work that was not the PFC-2 deposit. *(Wrong primary paper, same author.)*

7. **`10.1371/journal.pcbi.1006359|pfc-2`** — LLM=REUSE (conf 10). LLM's own reasoning flags the contradiction ("the text describes the data as being from macaque V1 (which contradicts the DANDI dataset description of rat PFC/CA1)") but classifies REUSE anyway because reference [104] was resolved to the primary. User: the paper reuses the **PVC-5** dataset, not PFC-2. *(Wrong CRCNS dataset; the LLM literally noticed and ignored the species/region mismatch.)*

8. **`10.1101/2020.05.12.091215|ssc-3`** — LLM=REUSE (conf 9). Heavy author overlap (Timme, Ito, Myroshnychenko, Beggs) and the LLM concluded "a direct re-analysis/extension of the dataset described in the primary paper." User: "this paper describes its own data collection of electrophysiology data and does not provide sufficient evidence for data reuse." *(Over-indexing on shared authorship.)*

**Fix:** Require the LLM to validate dataset identity (species, region, modality, depositor) against what the citing paper describes using, rather than concluding REUSE from a primary-paper citation alone. The `1006359/pfc-2` case is especially telling — the prompt structure currently allows the LLM to acknowledge a contradiction and still classify REUSE; the rubric should make a flagged contradiction disqualifying.

---

## The 1 Recall Miss

**`10.1101/2022.03.19.484958|pvc-11`** — LLM=MENTION (conf 9), Human=reuse. The LLM's reasoning explicitly catches a numbering discrepancy: "ref [53] in the text, though the bibliography list shows [53] as Collins et al. and [54] as Smith & Kohn." The paper genuinely reuses Smith & Kohn (2008) PVC-11 data, but because the resolver fed the LLM the wrong reference number, the LLM saw no in-text evidence and called MENTION. User: "This may indicate an issue with the reference renumbering."

This is the same root cause as bucket B — citation-numbering pipeline issues — just producing a false negative instead of a false positive. Fixing the resolver / renumbering chain should close this case alongside the bucket B cases.

---

## The 3 Unsure Cases (low priority)

These are LLM=REUSE / Human=unsure, where the human couldn't validate the call and so it could not be confirmed as either a mismatch or a match.

- **`10.14232/phd.11093|hdr-1`** — PhD thesis. User: "This is a thesis, not a real journal article or preprint."
- **`10.1184/r1/6720272|pvc-11`** — PhD thesis. User: "This is a PhD thesis and a little bit questionable as a paper that's supposed to demonstrate data reuse."
- **`10.1080/01621459.2015.1116988|pfc-2`** — Paywalled, no reviewer access. User: "I don't have access to the full text."

The thesis cases are sample-selection issues (DOI prefixes like `10.14232/phd.*` and `10.1184/r1/*` could be filtered upstream). The paywall case is the recurring institutional access asymmetry from rounds 1 and 2 — surfacing the cached LLM excerpts in the review dashboard would let the reviewer evaluate without independent full-text access.

