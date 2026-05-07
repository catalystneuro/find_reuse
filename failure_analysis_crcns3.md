# Failure Mode Analysis: CRCNS Manual Review (Round 3)

**Date:** 2026-05-07
**Reviewed entries:** 100 REUSE/MENTION classifications (NEITHER calls were not reviewed this round)
**Genuine mismatches:** 12

Round 3 was run on a fresh sample after a round of prompt and pipeline optimization done in response to [round 1](failure_analysis_crcns.md) and [round 2](failure_analysis_crcns_2.md).

---

## Failure Modes

### 1. Wrong-Dataset Attribution Among Sibling Works (new in round 3)

**What happens:** The citing paper genuinely reuses *some* CRCNS data, but from a different dataset than the one being evaluated. The LLM correctly recognizes reuse signals (deposit statements, "we used data from…", explicit references) and confidently classifies REUSE — but the data being reused is from a sibling dataset by the same lab, or a precursor paper by the same author that was not the deposit primary.

**Why it's hard:** The reuse signals are real and strong. Disambiguating which CRCNS dataset is being reused requires checking the dataset *identity* (region, species, modality, depositor) against what the citing paper actually used, not just whether a primary-paper citation appears.

**Examples:**
- `10.1016/j.cortex.2021.01.016|fcx-2` — LLM=REUSE (conf 10), Human=mention. LLM cites Excerpt 11 stating data is deposited to CRCNS and references "Johnson et al., 2018 [38]" (the FCX-2 primary). User notes the paper actually reuses **Johnson et al. 2017** data, which corresponds to the **PFC-5** dataset, not FCX-2.
- `10.1101/027102|pfc-2` — LLM=REUSE (conf 10), Human=mention. LLM correctly identifies a "Peyrache et al. 2009 [7]" citation and matches it to the PFC-2 primary author. User notes the paper reuses an earlier Peyrache 2009 work that was not the PFC-2 deposit.
- `10.1371/journal.pcbi.1006359|pfc-2` — LLM=REUSE (conf 10), Human=mention. LLM's own reasoning **flags the contradiction** ("the text describes the data as being from macaque V1 (which contradicts the DANDI dataset description of rat PFC/CA1)") but classifies REUSE anyway because reference [104] was resolved to the primary. User confirms: the paper reuses the **PVC-5** dataset, not PFC-2.

**Potential improvement:** When the LLM detects a contradiction between the dataset's metadata (species, region, modality) and the data the citing paper describes using, that contradiction should override a positive citation match rather than being noted and ignored. More structurally, the resolver could be made stricter when an author has multiple CRCNS deposits — at least flag the ambiguity to the LLM.

---

### 2. Numbered-Citation Reference Resolution (recurring + amplified)

**What happens:** The pipeline links a reference number in the citing paper's bibliography to the primary paper's DOI. When that link is wrong — or when the LLM has to guess which number is the primary in the absence of an explicit anchor (the [round 2 issue](failure_analysis_crcns_2.md#3-llm-cannot-map-numbered-citations-to-the-primary-paper)) — the classification rides on a faulty premise. Round 3 surfaces three variants:

- **LLM guesses the wrong number from semantic context** (round 2 mode recurring):
  - `10.1101/2022.06.30.497612|fly-1` — LLM=REUSE (conf 9), Human=mention. LLM quotes "examined data from our functional screen of sparse Gal4 and split Gal4 driver lines [49]" and treats [49] as the primary. User notes the primary (Aimon et al.) is **citation #52**; #49 is **Chen et al. 2022**, which is what the paper actually reuses. The LLM additionally leaned on a same-lab prior ("'our' and the shared authorship … confirms this is a reuse"), compounding the error.

- **The resolver itself produced a wrong reference number, the LLM noticed, and downgraded to MENTION (false negative)**:
  - `10.1101/2022.03.19.484958|pvc-11` — LLM=MENTION (conf 9), Human=reuse. LLM's reasoning explicitly catches the discrepancy: "ref [53] in the text, though the bibliography list shows [53] as Collins et al. and [54] as Smith & Kohn." The paper genuinely reuses Smith & Kohn (2008) PVC-11 data — but because the resolver fed the LLM the wrong reference number, the LLM saw no in-text evidence and called MENTION. User: "This may indicate an issue with the reference renumbering."

- **Citation-extraction mismatch between the excerpt text and the actual paper's reference list**:
  - `10.1101/2022.09.25.509420|hc-10` — LLM=REUSE (conf 9), Human=mention. LLM quotes Excerpt 6/7: "performed as part of a previously published study by Fernandez-Ruiz et al., [19]." User notes that in the actual paper, citation **#19 is the primary (Sang-Hun Lee)**, while **#8 is Fernandez-Ruiz** — i.e., the citation numbers in the extracted excerpts do not match the citation numbers in the published paper. Either the extractor pulled from a preprint version with different numbering, or the renumbering step introduced a collision.

**Why this cluster matters:** The round-2 proposal — pass the resolved reference number into the LLM's prompt as an explicit anchor — was implemented before round 3, and round 3 still surfaced three cases. It plausibly closed the round-2 `ch-epfl-2009` mode (LLM guessing from semantic context) for some fraction of cases, but `fly-1` shows the LLM can still get pulled off the anchor when other reuse signals (shared lab, "our data") are strong, and `pvc-11` / `hc-10` show that when the resolver itself is wrong, anchoring the LLM to that wrong number locks the error in. Any further fix needs to address resolver correctness, not just prompt grounding.

**Potential improvement:**
- Validate the resolver against an independent extraction of the citing paper's bibliography (catch wrong-author / wrong-year matches).
- When the resolver and the LLM-visible excerpt disagree on which paper a number refers to, surface the disagreement rather than picking one.
- Explicitly version-stamp citation numbers (preprint vs. published) so the excerpt extractor and the resolver are working from the same numbering scheme.

---

### 3. Same-Author / Same-Lab False Positive (recurring)

**What happens:** When the citing paper shares authors with the primary paper, the LLM treats this as a strong reuse prior and classifies REUSE even when the actual contribution of the citation is something other than data reuse — a parameter borrowed for a model, a review/perspective paper discussing the work, or the citing paper describing its own (newly collected) data while citing a related earlier paper from the same lab.

**Examples:**
- `10.1101/029124|cai-1` — LLM=REUSE (conf 9), Human=mention. The citing paper "derived parameters (alpha and A) for their fluorescence waveforms from experimental results in [3]" — i.e., used a *fitted parameter*, not the underlying calcium-imaging data. User: "This is a modeling paper that just uses a parameter from the primary paper, which shouldn't count as data reuse."
- `10.1038/s41593-018-0284-0|pvc-8` — LLM=REUSE (conf 9), Human=mention. The "citing paper" is a Coen-Cagli review/perspective written by the dataset's primary author, *discussing* their own data rather than reusing it. User: "This is a review and not data reuse."
- `10.1101/2020.05.12.091215|ssc-3` — LLM=REUSE (conf 9), Human=mention. Strong shared-author signal (Timme, Ito, Myroshnychenko, Beggs all overlap), but the user notes "this paper describes its own data collection of electrophysiology data and does not provide sufficient evidence for data reuse."

**Potential improvement:** Add explicit definitions in the prompt for the boundary cases — modeling papers that borrow a parameter, review/perspective articles, and papers describing newly collected data while citing a same-lab predecessor. None of these should count as REUSE under the current rubric. Round 1 already recommended requiring the LLM to distinguish between citing related work, using a parameter from related work, and using the actual deposited data, with quoted evidence at each step; that recommendation still applies.

---

### 4. Theses / Non-Standard Documents in the Sample

**What happens:** Some entries in the sample are PhD theses or repository deposits rather than journal articles or preprints. The LLM treats them like ordinary papers and classifies normally; the human reviewer marks them `unsure` because they do not fit the population we are trying to characterize.

**Examples:**
- `10.14232/phd.11093|hdr-1` — LLM=REUSE (conf 9), Human=unsure. User: "This is a thesis, not a real journal article or preprint."
- `10.1184/r1/6720272|pvc-11` — LLM=REUSE (conf 5), Human=unsure. User: "This is a PhD thesis and a little bit questionable as a paper that's supposed to demonstrate data reuse."

**Nuance:** These are not classifier failures per se — the LLM may even be correctly identifying reuse — but they are sample-selection issues that pollute the population statistics. They show up as mismatches because the human can't validate without treating the document differently.

**Potential improvement:** Filter the citation sample to exclude DOIs from thesis repositories (CMU `r1/`, Hungarian `phd.*`, etc.) before classification, or tag them so review and downstream metrics can opt them in/out.

---

### 5. Institutional Access Asymmetry (recurring from rounds 1 & 2)

**What happens:** The pipeline reads paywalled full text the human reviewer cannot reach in a browser; the LLM produces a confident classification, the human marks `unsure`.

**Examples:**
- `10.1080/01621459.2015.1116988|pfc-2` — LLM=REUSE (conf 9), Human=unsure. User: "I don't have access to the full text."

**This mode appeared 3× in round 1, 2× in round 2, and 1× in round 3.** The mitigation proposed in earlier rounds — surfacing the cached LLM excerpts in the review dashboard — would close it; round 2 also identified a related persistence bug (only the first 5 excerpts saved) which would need to be fixed first for the mitigation to work end-to-end.

---

## Summary

| Failure Mode | Origin | LLM Fault? | Round 3 count | Status vs. earlier rounds |
|---|---|---|---|---|
| Wrong-dataset attribution among sibling works | LLM reasoning + resolver | Yes (partial) | 3 | New |
| Numbered-citation reference resolution | Prompt + resolver + extractor | Mixed | 3 | Recurring (round 2: 1; round 1: 0\*) |
| Same-author / same-lab false positive | LLM reasoning | Yes | 3 | Recurring (round 1: 1) |
| Theses / non-standard documents in sample | Sample selection | No | 2 | New |
| Institutional access asymmetry | Review methodology | No | 1 | Recurring (round 2: 2; round 1: 3) |

\* Round 1 had an analogue (`10.1101/2020.10.07.330282|ac-4` — wrong-index excerpt) that defaulted to NEITHER rather than producing a confident wrong call.

---

## Open Questions

1. **What is the resolver's actual accuracy on numbered citations?** Round 3 surfaces two cases (`pvc-11/484958`, `hc-10`) where the resolver-LLM-extractor stack disagrees with the published bibliography. A targeted audit — sample N papers with numbered citations and check resolver output against ground truth — would tell us whether to prioritize prompt grounding or resolver correctness.

2. **Should the prompt require dataset-identity validation, not just citation matching?** Three round-3 mismatches involve the LLM correctly detecting reuse but attributing it to the wrong CRCNS dataset. Prompting the LLM to check species/region/modality consistency might catch these, but at the cost of additional prompt complexity.

3. **What is the policy for same-lab borderline cases?** Modeling papers borrowing a parameter, review articles, and papers describing their own data collection while citing same-lab predecessors all currently produce false-positive REUSE calls. The classification rubric should explicitly address each.

4. **Should theses and other non-standard documents be filtered upstream?** A simple DOI-prefix exclude list (`10.14232/phd.*`, `10.1184/r1/*`, etc.) would remove two of twelve round-3 mismatches with no classifier change.

5. **Open questions from earlier rounds still apply:** evidence grounding (require quoted spans), surfacing cached excerpts in the review dashboard, persisting all excerpts the LLM was shown, and sample bias toward paywall-accessible content.
