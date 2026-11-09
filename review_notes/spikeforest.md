---
dandisets: ["000618"]
---

# SpikeForest

SpikeForest ([000618](https://dandiarchive.org/dandiset/000618)) is a benchmark
of 13 ground-truth study sets
([Magland et al. 2020](https://doi.org/10.7554/eLife.55167), Table 2), and only
four of them are on DANDI, all in 000618. Papers usually say they took data
"from SpikeForest" and name a study set or one of its studies, so that name
decides the call. A paper that names any of these reused 000618 and is
**Reuse**:

| Study set | Studies | Recordings | Folder in 000618 |
|---|---|---|---|
| PAIRED_BOYDEN | `paired_boyden32c` | 19 | `sub-paired-boyden` |
| PAIRED_ENGLISH | `paired_english` | 29 | `sub-paired-english` |
| PAIRED_KAMPFF | `paired_kampff` | 15 | `sub-paired-kampff` |
| SYNTH_JANELIA, later renamed HYBRID_JANELIA | `hybrid_drift_siprobe`, `hybrid_static_siprobe`, `hybrid_drift_tetrode`, `hybrid_static_tetrode` | 60 | `sub-hybrid-janelia` |

A paper that names only the other nine is **Neither**, since none of them is on
DANDI under any dandiset: PAIRED_CRCNS_HC1, PAIRED_MEA64C_YGER,
PAIRED_MONOTRODE, SYNTH_BIONET, SYNTH_MAGLAND, SYNTH_MEAREC_NEURONEXUS,
SYNTH_MEAREC_TETRODE, SYNTH_MONOTRODE, SYNTH_VISAPY and MANUAL_FRANKLAB.
SYNTH_MONOTRODE's studies are named `neurocube_*`, and its two `neurocube_sim2`
studies are the 95 Pedreira et al. 2012 simulations. PAIRED_MONOTRODE's studies, such as
`paired_monotrode_boyden32c` and `paired_monotrode_kampff`, are single channels
cut from the Boyden, Kampff, CRCNS and Yger recordings, and are not in 000618.
Every study is listed under Recordings on the
[SpikeForest site](https://spikeforest.flatironinstitute.org/), reached
through "Original website".
Put the set or study the paper named in the note. A paper that says it used
SpikeForest but names no study set or study is **Unsure**.
