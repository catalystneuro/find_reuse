#!/usr/bin/env python3
"""
Investigate the NEITHER classifications from the direct pathway.

That pathway reaches a paper because a dandiset identifier is already in its
text, so the live question is not whether the dataset is relevant but where in
the text the identifier sits. NEITHER is small and well behaved here: 70 of 424
pairs, and 65 of those are one paper.

Usage:
    python -m src.analysis.analyze_direct_neither summary
    python -m src.analysis.analyze_direct_neither list --cause reasoned_as_mention
    python -m src.analysis.analyze_direct_neither list --cause other
    python -m src.analysis.analyze_direct_neither show 10.7554/elife.78362 --dandiset 000003
"""

from __future__ import annotations

import re
import sys

from src.analysis.neither_common import (
    DIRECT_CACHE, MODE_DIRECT, OTHER, Pipeline, run,
)

CAUSE_MENTION = 'reasoned_as_mention'


# The direct pathway's label set is PRIMARY | REUSE | NEITHER -- there is no
# MENTION. Its prompt therefore defines NEITHER to absorb one:
#
#   "The identifier appears without either relationship. Use this when the
#    reference is a bibliography entry for another study, a passing mention of
#    the archive, a mention of the dataset as related work the authors did not
#    touch, or a parsing artifact where the matched string is not really this
#    dataset."
#
# So direct NEITHER means "no relationship OR a mention", while indirect NEITHER
# means "no relationship" alone. The two share a name and are not the same thing.
MENTION_LANGUAGE = re.compile(
    r'passing mention'
    r'|(only|merely|simply)\s+(as\s+)?(an?\s+)?(example|entry|listed|lists?|mention|appears?|cited)'
    r'|appears?\s+(only|just|merely)'
    r'|(in|from)\s+(an?\s+)?(appendix|reference list|bibliograph|table)'
    r'|as\s+(an?\s+)?(example|background|related work)'
    r'|not\s+.{0,40}(obtain|download|analyz|re-?process|generat|deposit|collect)',
    re.I)


def reasoned_as_mention(described: dict) -> bool:
    """
    The model's reasoning describes a mention, which its labels cannot express.

    Matches 70 of 70 direct NEITHER rows. Not one is described as a parsing
    artifact -- the only thing the label would still cover if MENTION existed --
    so the whole bucket is a label-set gap rather than a pipeline fault.

    This tests what the model said, not what the paper is. That is deliberate:
    the claim is about the label set, and the evidence is that the model reasons
    its way to MENTION and has nowhere to put the answer.

    Confirmed by intervention on one pair. Adding a MENTION bullet, narrowing
    NEITHER to the parsing-artifact clause, and adding MENTION to the JSON label
    union took 10.1038/s41586-025-09708-2 x 000026 from NEITHER 3/3 to MENTION
    3/3, deterministic in both arms. The two arms produced the same reasoning and
    quoted the same sentence verbatim -- "As ex vivo datasets with tens of scans
    become available 30, 39, https://dandiarchive.org/dandiset/000026, our tool
    has great potential..." -- so nothing about the judgement changed, only the
    box available to record it in. Run it with tmp/probe_direct_mention.py.
    """
    return bool(MENTION_LANGUAGE.search(described.get('reasoning') or ''))


PIPELINE = Pipeline(
    name='direct',
    module='analyze_direct_neither',
    mode=MODE_DIRECT,
    cache_dir=DIRECT_CACHE,
    cause_tests=(
        (CAUSE_MENTION, reasoned_as_mention),
    ),
    cause_notes={
        CAUSE_MENTION: 'the reasoning describes a mention, which direct mode cannot label',
        OTHER: 'none of the above',
    },
    description=__doc__,
)


if __name__ == '__main__':
    sys.exit(run(PIPELINE))
