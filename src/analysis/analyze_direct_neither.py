#!/usr/bin/env python3
"""
Investigate the NEITHER classifications from the direct pathway.

That pathway reaches a paper because a dandiset identifier is already in its
text, so the live question is not whether the dataset is relevant but where in
the text the identifier sits. NEITHER is small and well behaved here: 70 of 424
pairs, and 65 of those are one paper.

Usage:
    python -m src.analysis.analyze_direct_neither summary
    python -m src.analysis.analyze_direct_neither list --cause catalog_listing
    python -m src.analysis.analyze_direct_neither list --cause other
    python -m src.analysis.analyze_direct_neither show 10.7554/elife.78362 --dandiset 000003
"""

from __future__ import annotations

import sys

from src.analysis.neither_common import (
    DIRECT_CACHE, MODE_DIRECT, OTHER, Pipeline, run,
)

CAUSE_CATALOG = 'catalog_listing'


# How many other dandiset-shaped identifiers make the text a catalog of datasets
# rather than a paper discussing one. Across the direct NEITHER rows the counts
# are 0, 2, 2, 10, 14, then 71 for each of the 65 rows belonging to the NWB
# ecosystem paper, whose Appendix 6 tabulates the archive. The threshold sits in
# that gap.
CATALOG_MIN_OTHER_IDS = 20


def is_catalog_listing(described: dict) -> bool:
    """The identifier is one row of a dataset catalog, not a reference to it."""
    return len(described['other_dandiset_ids']) >= CATALOG_MIN_OTHER_IDS


PIPELINE = Pipeline(
    name='direct',
    module='analyze_direct_neither',
    mode=MODE_DIRECT,
    cache_dir=DIRECT_CACHE,
    cause_tests=(
        (CAUSE_CATALOG, is_catalog_listing),
    ),
    cause_notes={
        CAUSE_CATALOG: f'one row of a catalog listing >={CATALOG_MIN_OTHER_IDS} dandisets',
        OTHER: 'none of the above',
    },
    description=__doc__,
)


if __name__ == '__main__':
    sys.exit(run(PIPELINE))
