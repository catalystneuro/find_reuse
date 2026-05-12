"""Shared stratified-sampling logic for the review pipeline.

Used by start_review_round.py to draw the sample for a new review round.
"""

import random
from collections import defaultdict


# Fixed seed so stratified sampling is reproducible across runs and prefixes nest:
# the first 10 of a sample of 20 are exactly the same entries as a sample of 10.
SAMPLING_SEED = 0


def stratified_sample(entries: list[dict], samples_per_class: int) -> list[dict]:
    """Stratified sample: shuffle each classification stratum once with SAMPLING_SEED,
    then take the first `samples_per_class` entries. Shuffling (rather than `random.sample`)
    ensures prefixes nest, so a sample of 20 contains the same first-10 as a sample of 10.

    Mutates each sampled entry to set `sample_order` (0-indexed within its class).
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
        take = min(samples_per_class, len(group))
        for index, entry in enumerate(group[:take]):
            entry["sample_order"] = index
        sampled.extend(group[:take])
        print(f"  {classification}: sampled {take} of {len(group)}")

    sampled.sort(key=lambda e: (e["dandiset_id"], e["citing_doi"]))
    return sampled
