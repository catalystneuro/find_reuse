---
dandisets: ["000021", "000022", "000039", "000049", "000050", "000711", "000713", "000728"]
---

# Allen Brain Observatory

Allen Brain Observatory papers are cited by the hundreds, often together, and
most citing papers got the data through the AllenSDK or the Allen portal. Those
serve the same sessions as DANDI, so a paper that used them reused the dandiset
("Obtained from AllenSDK, not DANDI. DANDI:000728 mirrors this data."). What the
paper says it analysed tells which dandiset it used:

| The paper says it used | Dandiset | Allen project |
|---|---|---|
| Two-photon or calcium imaging data from the Allen Brain Observatory or Visual Coding ("Visual Coding 2P", "Brain Observatory Cache", observatory.brain-map.org/visualcoding, "three sessions", an ophys experiment ID) | [000728](https://dandiarchive.org/dandiset/000728) | Visual Coding - 2P Ca+ Imaging |
| Neuropixels or electrophysiology data from Visual Coding, with the "Brain Observatory 1.1" stimulus set or 32 mice | [000021](https://dandiarchive.org/dandiset/000021) | Visual Coding - Neuropixels |
| The same, with the "Functional Connectivity" stimulus set or 26 mice | [000022](https://dandiarchive.org/dandiset/000022) | Visual Coding - Neuropixels |
| Visual Coding Neuropixels with no stimulus set or count that settles it | 000021 and 000022, **Ambiguous Reuse** | Visual Coding - Neuropixels |
| Two-photon or calcium imaging data from Visual Behavior | [000711](https://dandiarchive.org/dandiset/000711) | Visual Behavior - 2P |
| Neuropixels or electrophysiology data from Visual Behavior | [000713](https://dandiarchive.org/dandiset/000713) | Visual Behavior - Neuropixels |
| Visual Behavior or the change detection task, with no modality | 000711 and 000713, **Ambiguous Reuse** | Visual Behavior |

A paper that says only "Visual Coding" or "Allen Brain Observatory", with no
modality, is **Ambiguous Reuse** across 000728, 000021 and 000022. de Vries et al.
2020 is the 2P survey's paper and Siegle et al. 2021 the Neuropixels one, but
Neuropixels papers often cite de Vries as well, so the citation alone does not
settle it. 000039, 000049 and 000050 were recorded on the same 2P rig but are
not in the AllenSDK, so a paper is on them only if it describes their
experiments (contrast tuning, TF x SF tuning, run tuning) or names the dandiset.
