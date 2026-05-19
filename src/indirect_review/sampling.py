"""Shared stratified-sampling logic for the review pipeline.

Used by start_review_round.py to draw the sample for a new review round.
"""

import random
from collections import defaultdict


# Fixed seed so stratified sampling is reproducible across runs and prefixes nest:
# the first 10 of a sample of 20 are exactly the same entries as a sample of 10.
SAMPLING_SEED = 0


def stratified_sample(
    entries: list[dict],
    samples_per_class: int,
    start_index: int = 0,
) -> list[dict]:
    """Stratified sample: shuffle each classification stratum once with SAMPLING_SEED,
    then take a window of `samples_per_class` entries beginning at `start_index`.
    Shuffling (rather than `random.sample`) ensures prefixes nest, so windows from
    different runs line up: `start=0,n=50` and `start=50,n=50` together give the
    same 100 entries (per class) as `start=0,n=100`.

    Mutates each sampled entry to set `sample_order` (absolute 0-indexed position
    within its class's shuffled order, so values reflect `start_index`).
    Returns the sampled entries sorted by (dandiset_id, citing_doi).
    """
    by_class = defaultdict(list)
    for entry in entries:
        by_class[entry["classification"]].append(entry)

    sampled = []
    for classification in sorted(by_class):
        group = by_class[classification]
        rng = random.Random(SAMPLING_SEED)
        rng.shuffle(group)
        window = group[start_index : start_index + samples_per_class]
        for offset, entry in enumerate(window):
            entry["sample_order"] = start_index + offset
        sampled.extend(window)
        print(
            f"  {classification}: sampled {len(window)} of {len(group)} "
            f"(window [{start_index}, {start_index + samples_per_class}))"
        )

    sampled.sort(key=lambda e: (e["dandiset_id"], e["citing_doi"]))
    return sampled
