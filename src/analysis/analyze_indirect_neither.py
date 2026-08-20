#!/usr/bin/env python3
"""
Investigate the NEITHER classifications from the indirect (citing) pathway.

That pathway reaches a paper through a dandiset's primary publication: OpenAlex
records the paper as citing it, and the classifier is then asked whether the
authors reused the dataset. NEITHER is meant to be a rare residual there, and it
is 22% of all pairs -- a symptom rather than an answer.

`summary` counts how many rows carry each cause, `list` picks rows out of a
bucket, and `show` renders one pair with every piece of evidence located in the
text, so a row can be judged by reading rather than by grepping a
90,000-character blob.

Causes are not mutually exclusive and the shares sum past 100%; see
neither_common.Pipeline.

Usage:
    python -m src.analysis.analyze_indirect_neither summary
    python -m src.analysis.analyze_indirect_neither list --cause stale_extraction
    python -m src.analysis.analyze_indirect_neither list --cause citation_not_found --only
    python -m src.analysis.analyze_indirect_neither show 10.1002/adbi.202300366
"""

from __future__ import annotations

import json
import re
import sys
from functools import lru_cache
from pathlib import Path

from src.analysis.neither_common import (
    CITING_CACHE, MODE_CITING, OTHER, Pipeline, load_discovery, run,
)

CAUSE_FABRICATED_LINK = 'fabricated_paper_link'
CAUSE_WRONG_PAPER_ASKED = 'asked_about_wrong_paper'
CAUSE_NO_BODY = 'no_article_body'
CAUSE_STALE_REFS = 'stale_extraction'
CAUSE_CITATION = 'citation_not_found'
CAUSE_UNSTABLE = 'unstable_classification'


# 159 of the 365 dandisets declare no paper: no DataCite relation and no DOI in
# the description. Their primary paper was inferred by an LLM, which recorded a
# title and a DOI. Resolving those DOIs shows 96 point at a different paper and 7
# do not resolve at all -- the model knew the title and invented the identifier,
# at confidence 10. 001414 is the type specimen: its link resolves to "Old age
# variably impacts chimpanzee engagement and efficiency in stone tool use", so
# discovery fetched papers citing that and asked the classifier about an
# astrocyte-calcium dataset.
#
# The verdicts are precomputed by verify_llm_dois.py in this directory, because
# checking them needs CrossRef and this module is offline. Re-run it when
# discovery changes.
LLM_DOI_VERDICTS = Path(__file__).resolve().parent / 'llm_doi_verdicts.json'


@lru_cache(maxsize=1)
def unusable_paper_links() -> frozenset:
    """Dandisets whose LLM-supplied DOI is not the paper the LLM named."""
    verdicts = json.loads(LLM_DOI_VERDICTS.read_text())
    return frozenset(dandiset_id for dandiset_id, entry in verdicts.items()
                     if entry['verdict'] != 'matches')


def has_fabricated_paper_link(described: dict) -> bool:
    """
    The dandiset's paper link points at a different paper than it claims.

    Every pair under such a dandiset was built from papers citing an unrelated
    work, so the classifier was asked about a dataset those papers have no
    connection to. NEITHER is the correct answer and the pair should not exist.

    This is upstream of everything else here: re-fetching the text or numbering
    its references cannot help a pair that was never real.
    """
    return described['dandiset_id'] in unusable_paper_links()


def asked_about_wrong_paper(described: dict) -> bool:
    """
    The prompt named a different paper than the pair was built from.

    `build_worklist` fills primary_paper_doi from `paper_relations[0]`, but a
    dandiset can declare several papers and discovery records which one produced
    each pair. For the 37 dandisets that declare more than one, every pair built
    from a later relation was described to the model using the first.

    10.1002/exp.20250452 x 000954 is the type specimen. The pair exists because
    the paper cites "A brain-computer interface that evokes tactile sensations",
    which is reference 72 of its bibliography, DOI and full title present. The
    prompt instead named the dandiset's first relation, a 2012 Lancet paper that
    appears nowhere in the text. The model answered NEITHER, correctly, to the
    question it was asked.

    So these rows are not classifier failures and re-fetching cannot help them.
    The repair is to pass the pair's own cited_paper_doi and re-classify.
    """
    record = load_discovery()['dandisets'].get(described['dandiset_id']) or {}
    relations = record.get('paper_relations') or []
    if len(relations) < 2 or not described['cited_doi']:
        return False
    asked = ((relations[0] or {}).get('doi') or '').strip()
    return bool(asked) and asked != described['cited_doi']


# An article body cites as it goes, so it carries in-text citation markers in
# one of three styles: bracketed, author-year, or superscripts that lost their
# markup. The `[a-z]` prefix on the superscript pattern matters -- allowing `)`
# or `.` before the digits matches statistics like "OR, 2.25" and "P = 9.03",
# which took a JAMA structured abstract from 3 markers to 59.
CITATION_MARKERS = (
    re.compile(r'\[\s*\d{1,3}\s*(?:[,–—-]\s*\d{1,3}\s*)*\]'),
    re.compile(r'\(\s*[A-Z][A-Za-zÀ-ſ\'’-]+'
               r'(?:\s+(?:et\s+al\.?|and|&)[^)]{0,20})?,?\s*(?:19|20)\d{2}[a-z]?\s*[;)]'),
    re.compile(r'[a-z]\d{1,3}(?:[,–—-]\d{1,3})*(?![\d\w])'),
)
MAX_MARKERS_WITHOUT_BODY = 10
MAX_CHARS_WITHOUT_BODY = 20_000


def citation_marker_count(text: str) -> int:
    return sum(len(pattern.findall(text)) for pattern in CITATION_MARKERS)


def has_no_article_body(described: dict) -> bool:
    """
    The fetch returned front matter, a landing page, or reference fragments.

    A conjunction because neither half works alone. Marker count alone flags
    long real papers whose markers the extraction mangled -- a 117,000-character
    document scoring 1. Length alone flags short but genuine articles, such as a
    9,510-character IEEE paper carrying 41 markers. Together they separate: the
    hand-checked failures score 3/14k, 6/11.6k, 6/14.3k and 5/12k, while real
    bodies score 41, 83, 85, 128 and 151.

    Blind-sampled at 6 rows, 5 were clearly bodyless (publisher navigation, PMC
    funding metadata, bare reference fragments, an abstract, a book chapter's
    bibliography) and 1 was borderline -- a JoVE video protocol that is
    legitimately short. Treat the count as approximate.
    """
    return (described['text_chars'] < MAX_CHARS_WITHOUT_BODY
            and citation_marker_count(described['text']) <= MAX_MARKERS_WITHOUT_BODY)


# The fetcher used to emit each CrossRef reference as a bare DOI. Today it emits
# "[300] Fast and sensitive GCaMP calcium indicators for imaging neural
# populations. Nature. 2023. 10.1038/..." -- an index, whatever metadata CrossRef
# holds, then the DOI.
#
# Testing the cache date rather than the text is the defensible form of this
# check: it answers the actionable question -- would re-fetching this paper fix
# it -- instead of guessing from content. The boundary is sharp. Share of cached
# documents carrying formatted reference entries, by month:
#
#   2026-01   935 docs    0%      2026-07   675 docs   97%
#   2026-03  6863 docs    0%      2026-08   722 docs   91%
#   2026-04  4669 docs    1%
#   2026-05    92 docs    0%
#
# Nothing was cached in June, so any cutoff in that gap gives the same answer.
REFERENCE_FORMATTING_FIX = '2026-07-01'


def has_stale_extraction(described: dict) -> bool:
    """
    The paper text was cached before the fetcher learned to number references.

    These documents carry their bibliography as an unlabelled run of bare DOI
    strings, so nothing connects a DOI to the in-text marker that cites it.

    Shown causal by intervention on two pairs, both flipping NEITHER 3/3 ->
    MENTION 3/3 with deterministic arms, via `tmp/probe_citation.py --enrich
    references`:

      10.1002/1873-3468.70268   references restored with index and title
      10.1002/adbi.202300366    index only -- 5.9 characters per reference

    The second is the informative one. Its references gained nothing but "[N] "
    prefixes, and it flipped as cleanly as the first, with the model quoting the
    in-text marker "[32,43-45]" it had previously reported as absent. So the
    index is what matters, not the recovered metadata: numbering makes the DOI
    block joinable to the markers and to the reference list, and the join cannot
    be reconstructed otherwise -- in that paper the target is the 41st DOI in the
    block but reference [44], because three cited works have no DOI deposited and
    the two sequences drift apart.

    Base rates are not evidence either way here. The defect sits in 83.8% of
    NEITHER documents but also 87.5% of MENTION and 85.4% of REUSE -- a defect
    present nearly everywhere can still be the binding constraint on the rows
    that fail, and only an intervention distinguishes those.
    """
    cached_at = described.get('cached_at')
    return bool(cached_at) and cached_at[:10] < REFERENCE_FORMATTING_FIX


ABSENCE_CLAIM = re.compile(
    r'(does not|no|never|without|lacks?|absent)[^.]{0,40}'
    r'(mention|cit|referenc|refer to|discuss)'
    r'|not (mentioned|cited|referenced|discussed)', re.I)


def reports_citation_not_found(described: dict) -> bool:
    """
    The model says the citation is absent, contradicting the OpenAlex edge.

    Every pair on the citing worklist exists because OpenAlex recorded the paper
    as citing the dataset's primary publication, so a reasoning that reports the
    citation absent disagrees with the source that created the pair, and one of
    the two is wrong. It is almost always the model: of the rows reaching this
    test, the cited DOI is literally in the text in 77% of them, and of the rest
    77% carry two or more of the cited paper's author surnames. Only about 2%
    show no trace of the cited paper at all, which is where a spurious OpenAlex
    edge would sit.

    This is nearly definitional for NEITHER rather than a slice of it: 3,067 of
    3,085 rows assert absence in some form. The 18 that do not are cases where
    the model found the citation and judged it anyway.

    What it does NOT establish is why the model missed it. Five document
    properties -- length, whether the cited author is named in the body, topical
    distance, where the DOI sits, and reference formatting -- all fail to
    separate these rows from MENTION.
    """
    return bool(ABSENCE_CLAIM.search(described['reasoning'] or ''))


# Re-running production's own prompt three times over the 130 rows that no other
# cause explained: 37% reproduce NEITHER every time, 35% are mixed, and 28% never
# return NEITHER at all. Of 171 flips, 170 went to MENTION and one to REUSE -- the
# instability sits entirely on the MENTION/NEITHER boundary, and essentially no
# reuse is hiding in these rows.
#
# Measured by measure_volatility.py in this directory, which costs money and needs
# the network. Only the rows it has actually re-run carry a verdict; everything
# else is unmeasured rather than stable, so this test is silent about them.
VOLATILITY_VERDICTS = Path(__file__).resolve().parent / 'volatility_verdicts.json'


@lru_cache(maxsize=1)
def unstable_pairs() -> frozenset:
    """Pairs that failed to reproduce NEITHER on at least one re-run."""
    if not VOLATILITY_VERDICTS.exists():
        return frozenset()
    verdicts = json.loads(VOLATILITY_VERDICTS.read_text())
    return frozenset((entry['citing_doi'], entry['dandiset_id'])
                     for entry in verdicts.values()
                     if entry['labels']
                     and entry['labels'].count('NEITHER') < len(entry['labels']))


def has_unstable_classification(described: dict) -> bool:
    """
    Re-running the same prompt does not reproduce NEITHER.

    These rows are not a defect in any input. The pipeline asked a well-formed
    question about an intact paper, and the answer simply is not stable -- the
    cached NEITHER is one draw from a distribution that mostly says MENTION.

    Only rows that have been re-run carry a verdict, so a False here can mean
    "stable" or "never measured".
    """
    return (described['citing_doi'], described['dandiset_id']) in unstable_pairs()


PIPELINE = Pipeline(
    name='indirect',
    module='analyze_indirect_neither',
    mode=MODE_CITING,
    cache_dir=CITING_CACHE,
    cause_tests=(
        (CAUSE_FABRICATED_LINK, has_fabricated_paper_link),
        (CAUSE_WRONG_PAPER_ASKED, asked_about_wrong_paper),
        (CAUSE_NO_BODY, has_no_article_body),
        (CAUSE_STALE_REFS, has_stale_extraction),
        (CAUSE_CITATION, reports_citation_not_found),
        (CAUSE_UNSTABLE, has_unstable_classification),
    ),
    cause_notes={
        # One line each. The evidence, thresholds and caveats live in each
        # predicate's docstring, which is where a reader who needs them is.
        CAUSE_FABRICATED_LINK: "the dandiset's LLM-supplied paper DOI is a different paper",
        CAUSE_WRONG_PAPER_ASKED: 'the prompt named the dandiset\'s first paper, not the one '
                                 'this pair was built from',
        CAUSE_NO_BODY: 'the fetch returned no article body',
        CAUSE_STALE_REFS: f'cached before {REFERENCE_FORMATTING_FIX}, when references were '
                          'unnumbered bare DOIs',
        CAUSE_CITATION: 'the model reports it could not find the citation',
        CAUSE_UNSTABLE: 're-running the same prompt does not reproduce NEITHER',
        OTHER: 'none of the above',
    },
    description=__doc__,
)


if __name__ == '__main__':
    sys.exit(run(PIPELINE))
