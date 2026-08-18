#!/usr/bin/env python3
"""
Decompose the NEITHER classifications by what actually went wrong upstream.

NEITHER is meant to be a rare residual label. In the citing pipeline it is 22%
of all pairs, which makes it a symptom rather than an answer. This module reads
the existing caches and attributes each NEITHER row to one of four upstream
faults, so the question "why is NEITHER so large" becomes four smaller questions
with numbers attached:

  discovery    the dandiset's primary paper is not the dataset's paper, so every
               paper citing it is genuinely unrelated to the data
  pairing      each paper was asked about one dandiset out of the several that
               cite it, so a NEITHER can be an answer about the wrong dataset
  text         the fetch source shapes the answer; NEITHER rate varies 5x across
               sources over the same question
  classifier   the model reports "does not cite" for papers whose text contains
               the cited DOI verbatim

Read-only, and offline by construction: nothing here fetches, classifies, or
writes to the caches. `show` is the point of the module — it renders one pair
with every piece of evidence located in the text, so a row can be judged by
reading rather than by grepping a 90,000-character blob.

Usage:
    python -m src.analysis.analyze_neither summary
    python -m src.analysis.analyze_neither dandisets --min-pairs 20 --sort rate
    python -m src.analysis.analyze_neither sources
    python -m src.analysis.analyze_neither list --cause citation_in_body --limit 30
    python -m src.analysis.analyze_neither show 10.1038/s41586-023-06271-6
    python -m src.analysis.analyze_neither export --out /tmp/neither.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter, defaultdict
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterator, Optional

REPO = Path(__file__).resolve().parents[2]

CITING_CACHE = REPO / '.fulltext_classification_cache'
DIRECT_CACHE = REPO / '.fulltext_direct_cache'
PAPER_CACHE = REPO / '.paper_cache'
DISCOVERY = REPO / 'output' / 'all_dandiset_papers_refreshed.json'

MODE_CITING = 'citing'
MODE_DIRECT = 'direct'
CACHE_FOR_MODE = {MODE_CITING: CITING_CACHE, MODE_DIRECT: DIRECT_CACHE}

# A dandiset is flagged as a suspect primary-paper link when it has enough pairs
# for the rate to mean something and most of them came back NEITHER. At 20/0.40
# this selects 15 dandisets holding 892 NEITHER rows, headed by 000336, whose
# listed primary paper is a 2012 Trends in Neurosciences review. Both numbers are
# knobs: raising the rate isolates the clearest cases, lowering it pulls in
# dandisets whose primary paper is merely a poor match rather than a wrong one.
SUSPECT_MIN_PAIRS = 20
SUSPECT_MIN_NEITHER_RATE = 0.40

# Preprint registrants, for telling a dandiset whose primary paper is a preprint
# from one whose primary paper is a journal article. A citing paper generally
# cites the published version, so the preprint DOI never appears in its text.
PREPRINT_PREFIXES = ('10.1101/', '10.21203/', '10.31234/', '10.31219/', '10.64898/')

# DOI prefixes that identify a deposit rather than a paper. A dandiset whose
# paper_relations points at one of these has no primary paper at all.
NON_PAPER_PREFIXES = ('10.5281/zenodo', '10.48324/', '10.6084/', '10.5061/')

# Some paper_relations entries record the resolver URL instead of the bare DOI.
DOI_URL_PREFIX = re.compile(r'^\s*(?:https?://)?(?:dx\.)?doi\.org/', re.I)

# The phrases the classification prompt itself names as evidence of REUSE. Used
# here to ask the opposite question: does a NEITHER paper contain reuse language
# near the citation it supposedly has no relationship to?
REUSE_PHRASES = re.compile(
    r'(downloaded from'
    r'|publicly available data(set)?'
    r'|open(ly)? available data(set)?'
    r'|obtained from the'
    r'|accession (number|code)'
    r'|re-?analy[sz](ed|ing|is)'
    r'|previously published data(set)?'
    r'|we (used|analyzed|re-?analyzed) the data(set)?'
    r'|data (were|was) (obtained|acquired|taken) from)',
    re.I)

# How far from an in-body citation a reuse phrase still counts as being "near"
# it. One window is roughly a long paragraph on either side.
REUSE_PROXIMITY_CHARS = 1500

DANDISET_ID = re.compile(r'\b(0\d{5})\b')

CONTEXT_CHARS = 400


# --------------------------------------------------------------------------- #
# References heading
# --------------------------------------------------------------------------- #

# Splitting citations into "in the body" and "in the bibliography" rests entirely
# on finding where the bibliography starts, and that is the least reliable thing
# this module computes. Extractions differ: some keep a heading on its own line,
# some run it into the surrounding text, and some emit references with no heading
# at all. Each detector is tried in turn and the one that fired is reported
# alongside the offset, so a wrong split shows up in the output instead of
# quietly moving rows between buckets.

_HEADING_DETECTORS = (
    ('own_line', re.compile(
        r'(?:^|\n)[ \t]*(?:\d+[.\)]\s*)?'
        r'(REFERENCES|References|REFERENCE LIST|Reference List|BIBLIOGRAPHY'
        r'|Bibliography|LITERATURE CITED|Literature Cited|WORKS CITED|Works Cited)'
        r'[ \t]*(?:\n|$)')),
    ('inline', re.compile(
        r'(?:^|\n|\s)(REFERENCES|References|Bibliography|Literature Cited)\b[ \t]*(?=[A-Z0-9\[])')),
)

# A numbered bibliography with no heading: several "1. Author, A." style entries
# in a row. Requiring a run rather than a single match keeps numbered figure
# captions and method steps from being mistaken for the reference list.
_NUMBERED_ENTRY = re.compile(r'(?:^|\n)\s*(?:\[\s*(\d{1,3})\s*\]|(\d{1,3})[.\)])\s+[A-Z]')
_NUMBERED_RUN_LENGTH = 6


def find_references_start(text: str) -> tuple[Optional[int], str]:
    """
    Locate where the bibliography begins.

    Returns the character offset and the name of the detector that found it, or
    (None, 'none') when no detector fired. Headings are searched from the back:
    a paper that says "see References" in its introduction should not have its
    whole body classified as bibliography.
    """
    for name, pattern in _HEADING_DETECTORS:
        matches = list(pattern.finditer(text))
        if matches:
            # Prefer a heading in the last third, which is where a bibliography
            # actually sits, and fall back to the last match anywhere.
            tail = [m for m in matches if m.start() > len(text) * 0.5]
            chosen = (tail or matches)[-1]
            return chosen.start(), name

    starts = [m.start() for m in _NUMBERED_ENTRY.finditer(text)]
    for i in range(len(starts) - _NUMBERED_RUN_LENGTH + 1):
        window = starts[i:i + _NUMBERED_RUN_LENGTH]
        # Consecutive bibliography entries sit close together; numbered items
        # scattered through a Methods section do not.
        if window[-1] - window[0] < _NUMBERED_RUN_LENGTH * 600:
            return window[0], 'numbered_run'

    return None, 'none'


# --------------------------------------------------------------------------- #
# Cache loading
# --------------------------------------------------------------------------- #

def _safe_name(*parts: str) -> str:
    joined = '__'.join(parts)
    return joined.replace('/', '_').replace(':', '_').replace('\\', '_')


@lru_cache(maxsize=1)
def load_discovery() -> dict:
    """
    Index the discovery output three ways.

    Returns a dict with:
        dandisets      dandiset_id -> the discovery record
        pair           (citing_doi, dandiset_id) -> the citing_papers entry
        paper_to_ds    citing_doi -> [dandiset_id, ...] in discovery order
    """
    data = json.loads(DISCOVERY.read_text())
    dandisets: dict[str, dict] = {}
    pair: dict[tuple[str, str], dict] = {}
    paper_to_ds: dict[str, list[str]] = defaultdict(list)

    for record in data['results']:
        dandiset_id = record['dandiset_id']
        dandisets[dandiset_id] = record
        for paper in record.get('citing_papers', []):
            doi = paper.get('doi')
            if not doi:
                continue
            pair[(doi, dandiset_id)] = paper
            paper_to_ds[doi].append(dandiset_id)

    return {'dandisets': dandisets, 'pair': pair, 'paper_to_ds': dict(paper_to_ds)}


@lru_cache(maxsize=2)
def load_classifications(mode: str) -> tuple[dict, ...]:
    """Every cached classification for a mode, in filename order."""
    cache_dir = CACHE_FOR_MODE[mode]
    records = []
    for path in sorted(cache_dir.glob('*.json')):
        records.append(json.loads(path.read_text()))
    return tuple(records)


@lru_cache(maxsize=2)
def classification_index(mode: str) -> dict[tuple[str, str], dict]:
    return {(r['citing_doi'], r['dandiset_id']): r for r in load_classifications(mode)}


@lru_cache(maxsize=512)
def load_paper(doi: str) -> Optional[dict]:
    """The cached fetch for a DOI: text, source, cached_at. None if not cached."""
    path = PAPER_CACHE / f'{_safe_name(doi)}.json'
    if not path.exists():
        return None
    return json.loads(path.read_text())


def primary_relation(dandiset_id: str) -> dict:
    """The dandiset's first paper_relations entry, or an empty dict."""
    record = load_discovery()['dandisets'].get(dandiset_id) or {}
    relations = record.get('paper_relations') or []
    return (relations[0] if relations else None) or {}


def normalize_primary_doi(doi: Optional[str]) -> str:
    """Strip the packaging some paper_relations entries carry: surrounding
    whitespace, a trailing tab, a doi.org URL wrapper."""
    if not doi:
        return ''
    return DOI_URL_PREFIX.sub('', doi.strip()).strip()


def primary_doi_damage(doi: Optional[str]) -> str:
    """
    How the recorded DOI string is malformed, separately from what it points at.

    Kept apart from the kind because the two are independent failures and one
    masks the other: 000129 records a Zenodo DOI *and* a trailing tab, and
    reporting only the tab would hide that there is no paper behind it.
    """
    if not doi:
        return 'missing'
    if doi != doi.strip():
        return 'whitespace'
    if DOI_URL_PREFIX.match(doi):
        return 'url_form'
    return 'clean'


def primary_doi_kind(doi: Optional[str]) -> str:
    """What kind of thing a dandiset's primary-paper DOI points at."""
    normalized = normalize_primary_doi(doi)
    if not normalized:
        return 'missing'
    if normalized.startswith(NON_PAPER_PREFIXES):
        return 'non_paper'
    if normalized.startswith(PREPRINT_PREFIXES):
        return 'preprint'
    return 'journal'


@lru_cache(maxsize=2)
def dandiset_label_counts(mode: str) -> dict[str, Counter]:
    """Per-dandiset label counts, the input to the suspect-primary flag."""
    counts: dict[str, Counter] = defaultdict(Counter)
    for record in load_classifications(mode):
        counts[record['dandiset_id']][record['classification']] += 1
    return dict(counts)


@lru_cache(maxsize=2)
def suspect_dandisets(mode: str) -> frozenset:
    flagged = set()
    for dandiset_id, counts in dandiset_label_counts(mode).items():
        total = sum(counts.values())
        if total >= SUSPECT_MIN_PAIRS and counts['NEITHER'] / total >= SUSPECT_MIN_NEITHER_RATE:
            flagged.add(dandiset_id)
    return frozenset(flagged)


# --------------------------------------------------------------------------- #
# Per-pair description
# --------------------------------------------------------------------------- #

def _occurrences(haystack_lower: str, needle: str) -> list[int]:
    if not needle:
        return []
    needle = needle.lower()
    found = []
    start = haystack_lower.find(needle)
    while start != -1:
        found.append(start)
        start = haystack_lower.find(needle, start + 1)
    return found


def describe_pair(citing_doi: str, dandiset_id: str, mode: str = MODE_CITING) -> dict:
    """
    Everything known about one (paper, dandiset) pair, from the caches alone.

    The classification record says what the model concluded; the rest says what
    the model was looking at when it concluded it. Keeping both in one record is
    what lets a NEITHER be attributed to a fault rather than merely counted.
    """
    record = classification_index(mode).get((citing_doi, dandiset_id))
    discovery = load_discovery()
    paper_entry = discovery['pair'].get((citing_doi, dandiset_id)) or {}
    relation = primary_relation(dandiset_id)
    dandiset_record = discovery['dandisets'].get(dandiset_id) or {}
    counts = dandiset_label_counts(mode).get(dandiset_id, Counter())
    dandiset_total = sum(counts.values())

    cited_doi = (paper_entry.get('cited_paper_doi') or relation.get('doi') or '').strip()
    cached = load_paper(citing_doi)
    text = (cached or {}).get('text') or ''
    lowered = text.lower()

    references_start, references_detector = find_references_start(text) if text else (None, 'none')

    citation_offsets = _occurrences(lowered, cited_doi)
    if references_start is None:
        body_offsets, bibliography_offsets = citation_offsets, []
    else:
        body_offsets = [o for o in citation_offsets if o < references_start]
        bibliography_offsets = [o for o in citation_offsets if o >= references_start]

    dandi_offsets = _occurrences(lowered, 'dandi')
    dandiset_id_offsets = _occurrences(lowered, dandiset_id) if dandiset_id else []
    other_dandiset_ids = sorted(
        {m.group(1) for m in DANDISET_ID.finditer(text)} - {dandiset_id}
    ) if dandi_offsets else []

    reuse_offsets = [m.start() for m in REUSE_PHRASES.finditer(text)]
    reuse_near_citation = any(
        abs(reuse - body) <= REUSE_PROXIMITY_CHARS
        for reuse in reuse_offsets for body in body_offsets)

    linked_dandisets = discovery['paper_to_ds'].get(citing_doi, [])
    index = classification_index(mode)
    unasked = [d for d in linked_dandisets if (citing_doi, d) not in index]

    return {
        'citing_doi': citing_doi,
        'dandiset_id': dandiset_id,
        'mode': mode,
        'title': (record or {}).get('title') or paper_entry.get('title', ''),

        'classification': (record or {}).get('classification'),
        'confidence': (record or {}).get('confidence'),
        'reasoning': (record or {}).get('reasoning'),
        'evidence_quotes': (record or {}).get('evidence_quotes') or [],
        'hallucinated_quote_count': (record or {}).get('hallucinated_quote_count'),
        'input_chars': (record or {}).get('input_chars'),
        'truncation': (record or {}).get('truncation'),

        'text_missing': cached is None,
        'text_chars': len(text),
        # The source of the text that was actually classified. Discovery records
        # its own text_source, kept separately: when the two disagree the paper
        # was refetched from somewhere else after discovery ran.
        'text_source': (cached or {}).get('source'),
        'discovery_text_source': paper_entry.get('text_source'),
        'text_drift': (
            None if (cached is None or not record)
            else len(text) - record.get('input_chars', 0)),

        'cited_doi': cited_doi,
        'cited_doi_kind': primary_doi_kind(cited_doi or None),
        'citation_count': len(citation_offsets),
        'citation_offsets': citation_offsets,
        'citation_in_body_offsets': body_offsets,
        'citation_in_bibliography_offsets': bibliography_offsets,

        'references_start': references_start,
        'references_detector': references_detector,

        'dandi_mentions': len(dandi_offsets),
        'dandi_offsets': dandi_offsets,
        'dandiset_id_offsets': dandiset_id_offsets,
        'other_dandiset_ids': other_dandiset_ids,

        'reuse_phrase_offsets': reuse_offsets,
        'reuse_near_citation': reuse_near_citation,

        'dandiset_name': dandiset_record.get('dandiset_name', ''),
        'primary_doi': (relation.get('doi') or ''),
        'primary_doi_kind': primary_doi_kind(relation.get('doi')),
        'primary_doi_damage': primary_doi_damage(relation.get('doi')),
        'primary_title': relation.get('name', ''),
        'dandiset_pairs': dandiset_total,
        'dandiset_neither': counts.get('NEITHER', 0),
        'dandiset_neither_rate': (counts.get('NEITHER', 0) / dandiset_total) if dandiset_total else 0.0,
        'dandiset_suspect': dandiset_id in suspect_dandisets(mode),

        'linked_dandisets': linked_dandisets,
        'linked_dandiset_count': len(linked_dandisets),
        'unasked_dandisets': unasked,

        'text': text,
    }


# --------------------------------------------------------------------------- #
# Cause attribution
# --------------------------------------------------------------------------- #

# One cause per row, first match wins, so the histogram sums to the NEITHER
# count with nothing left over. The order puts upstream faults ahead of the
# symptoms they produce: a paper cited under a wrong primary paper is explained
# by that link regardless of what its text looks like, so asking whether the
# model found the citation would only describe the consequence.
#
# The two pipelines get different ladders because they ask different questions.
# Citing mode reaches a paper through a dandiset's primary publication, so its
# faults are about that link and about whether the model found the citation.
# Direct mode reaches a paper because the dandiset identifier is already in its
# text, so the only live question is where in the text the identifier sits.
CAUSE_ORDER_CITING = (
    'no_cached_text',
    'bad_primary',
    'suspect_primary',
    'dandi_evidence',
    'citation_in_body',
    'citation_in_bibliography',
    'citation_absent',
)

CAUSE_ORDER_DIRECT = (
    'no_cached_text',
    'catalog_listing',
    'identifier_in_bibliography',
    'identifier_in_body',
)

CAUSE_ORDER_FOR_MODE = {
    MODE_CITING: CAUSE_ORDER_CITING,
    MODE_DIRECT: CAUSE_ORDER_DIRECT,
}

# How many other dandiset-shaped identifiers have to appear before the text is
# read as a catalog of datasets rather than a paper discussing one. Across the
# direct NEITHER rows the counts are 0, 2, 2, 10, 14, then 71 for each of the 65
# rows belonging to the NWB ecosystem paper, whose Appendix 6 tabulates the
# archive. The threshold sits in that gap; lowering it pulls in papers that
# merely name several dandisets rather than enumerating the archive.
CATALOG_MIN_OTHER_IDS = 20

CAUSE_NOTES = {
    'no_cached_text': 'classified earlier; the paper text is no longer in .paper_cache',
    'bad_primary': "dandiset's primary DOI is a deposit, malformed, or missing",
    'suspect_primary': f'dandiset has >={SUSPECT_MIN_PAIRS} pairs and '
                       f'>={SUSPECT_MIN_NEITHER_RATE:.0%} NEITHER',
    'dandi_evidence': 'text names DANDI or a dandiset ID; possible missed REUSE or PRIMARY',
    'citation_in_body': 'cited DOI appears before the bibliography',
    'citation_in_bibliography': 'cited DOI present, bibliography only; likely a mislabeled MENTION',
    'citation_absent': 'cited DOI not in text; spurious edge or preprint/published mismatch',
    'catalog_listing': f'text lists >={CATALOG_MIN_OTHER_IDS} other dandiset IDs; the match is a '
                       'catalog entry, not a reference to this dataset',
    'identifier_in_bibliography': 'dandiset ID appears only in the bibliography',
    'identifier_in_body': 'dandiset ID appears in the body without either relationship',
}


def assign_cause(described: dict) -> str:
    if described['text_missing']:
        return 'no_cached_text'

    if described['mode'] == MODE_DIRECT:
        if len(described['other_dandiset_ids']) >= CATALOG_MIN_OTHER_IDS:
            return 'catalog_listing'
        references_start = described['references_start']
        offsets = described['dandiset_id_offsets']
        if references_start is not None and offsets and all(o >= references_start for o in offsets):
            return 'identifier_in_bibliography'
        return 'identifier_in_body'

    if (described['primary_doi_kind'] in ('non_paper', 'missing')
            or described['primary_doi_damage'] != 'clean'):
        return 'bad_primary'
    if described['dandiset_suspect']:
        return 'suspect_primary'
    if described['dandi_mentions'] or described['dandiset_id_offsets']:
        return 'dandi_evidence'
    if described['citation_in_body_offsets']:
        return 'citation_in_body'
    if described['citation_offsets']:
        return 'citation_in_bibliography'
    return 'citation_absent'


def described_rows(mode: str, label: Optional[str] = 'NEITHER') -> Iterator[dict]:
    """Describe every cached pair, optionally restricted to one label."""
    for record in load_classifications(mode):
        if label and record['classification'] != label:
            continue
        described = describe_pair(record['citing_doi'], record['dandiset_id'], mode)
        described['cause'] = assign_cause(described)
        yield described


# --------------------------------------------------------------------------- #
# Rendering helpers
# --------------------------------------------------------------------------- #

def _table(headers: list[str], rows: list[list[Any]], aligns: str = '') -> str:
    aligns = aligns or 'l' * len(headers)
    cells = [[str(c) for c in row] for row in rows]
    widths = [max(len(headers[i]), *(len(r[i]) for r in cells)) if cells else len(headers[i])
              for i in range(len(headers))]
    out = []
    for row in [headers] + cells:
        out.append('  '.join(
            (cell.rjust(widths[i]) if aligns[i] == 'r' else cell.ljust(widths[i]))
            for i, cell in enumerate(row)).rstrip())
        if row is headers:
            out.append('  '.join('-' * w for w in widths))
    return '\n'.join(out)


def _window(text: str, offset: int, references_start: Optional[int]) -> str:
    start = max(0, offset - CONTEXT_CHARS)
    end = min(len(text), offset + CONTEXT_CHARS)
    where = 'body' if (references_start is None or offset < references_start) else 'bibliography'
    snippet = ' '.join(text[start:end].split())
    return f'    @{offset} ({where})\n      ...{snippet}...'


def _print_windows(text: str, offsets: list, references_start: Optional[int],
                   limit: int = 6) -> None:
    """
    Print context windows, collapsing hits that would show the same passage.

    Extractions repeat themselves — a hyperlink block can name the same archive
    five times inside one paragraph — and printing a window per hit buries the
    distinct evidence under copies of itself.
    """
    shown = 0
    last_end = None
    for offset in offsets:
        if last_end is not None and offset < last_end:
            continue
        print(_window(text, offset, references_start))
        last_end = offset + CONTEXT_CHARS
        shown += 1
        if shown >= limit:
            remaining = sum(1 for o in offsets if o >= last_end)
            if remaining:
                print(f'    ... {remaining} further hit(s) not shown')
            return


# --------------------------------------------------------------------------- #
# Subcommands
# --------------------------------------------------------------------------- #

def command_summary(args) -> int:
    for mode in ([args.mode] if args.mode else [MODE_CITING, MODE_DIRECT]):
        records = load_classifications(mode)
        labels = Counter(r['classification'] for r in records)
        total = len(records)
        print(f'\n=== {mode} pipeline: {total} classified pairs')
        print(_table(['label', 'n', 'share'],
                     [[k, v, f'{v/total:.1%}'] for k, v in labels.most_common()], 'lrr'))

        neither = [r for r in described_rows(mode)]
        if not neither:
            continue

        print(f'\n--- cause of {len(neither)} NEITHER')
        causes = Counter(r['cause'] for r in neither)
        print(_table(['cause', 'n', 'share', 'note'],
                     [[c, causes[c], f'{causes[c]/len(neither):.1%}', CAUSE_NOTES[c]]
                      for c in CAUSE_ORDER_FOR_MODE[mode] if causes[c]], 'lrrl'))

        print('\n--- concentration by dandiset')
        by_dandiset = Counter(r['dandiset_id'] for r in neither)
        ranked = by_dandiset.most_common()
        running = 0
        rows = []
        for i, (_, n) in enumerate(ranked, 1):
            running += n
            if i in (5, 10, 20, 50, 100):
                rows.append([f'top {i}', running, f'{running/len(neither):.0%}'])
        print(_table(['dandisets', 'NEITHER', 'share'], rows, 'lrr'))
        print(f'{len(ranked)} distinct dandisets carry NEITHER rows')

        by_paper = Counter(r['citing_doi'] for r in neither).most_common(5)
        # Citing mode asks about each paper once, so this is flat by construction
        # and says nothing. Direct mode asks per identifier, where one paper can
        # dominate the whole bucket.
        if by_paper and by_paper[0][1] > 2:
            print('\n--- most concentrated single papers')
            titles = {r['citing_doi']: r['title'] for r in neither}
            print(_table(['n', 'citing DOI', 'title'],
                         [[n, d, titles[d][:60]] for d, n in by_paper], 'rll'))

        if mode == MODE_CITING:
            _print_primary_health()
            _print_pairing_coverage(mode)
    return 0


def _print_primary_health() -> None:
    discovery = load_discovery()
    kinds, pairs_by_kind = Counter(), Counter()
    damage, pairs_by_damage = Counter(), Counter()
    for dandiset_id, record in discovery['dandisets'].items():
        doi = primary_relation(dandiset_id).get('doi')
        pairs = len(record.get('citing_papers', []))
        kinds[primary_doi_kind(doi)] += 1
        pairs_by_kind[primary_doi_kind(doi)] += pairs
        damage[primary_doi_damage(doi)] += 1
        pairs_by_damage[primary_doi_damage(doi)] += pairs

    print('\n--- primary-paper health across all dandisets')
    print(_table(['what the primary DOI points at', 'dandisets', 'discovery pairs'],
                 [[k, kinds[k], pairs_by_kind[k]]
                  for k in ('journal', 'preprint', 'non_paper', 'missing') if kinds[k]], 'lrr'))
    print()
    print(_table(['how the DOI string is written', 'dandisets', 'discovery pairs'],
                 [[k, damage[k], pairs_by_damage[k]]
                  for k in ('clean', 'whitespace', 'url_form', 'missing') if damage[k]], 'lrr'))


def _print_pairing_coverage(mode: str) -> None:
    discovery = load_discovery()
    index = classification_index(mode)
    all_pairs = sum(len(v) for v in discovery['paper_to_ds'].values())
    print('\n--- pairing coverage')
    print(f'{len(discovery["paper_to_ds"])} distinct citing papers, '
          f'{all_pairs} (paper, dandiset) pairs in discovery')
    print(f'{len(index)} pairs classified; {all_pairs - len(index)} never asked')
    multi = Counter(len(v) for v in discovery['paper_to_ds'].values())
    print(_table(['dandisets per paper', 'papers'],
                 [[k, multi[k]] for k in sorted(multi)][:8], 'rr'))


def command_dandisets(args) -> int:
    counts = dandiset_label_counts(args.mode)
    rows = []
    for dandiset_id, label_counts in counts.items():
        total = sum(label_counts.values())
        if total < args.min_pairs:
            continue
        neither = label_counts.get('NEITHER', 0)
        relation = primary_relation(dandiset_id)
        record = load_discovery()['dandisets'].get(dandiset_id) or {}
        rows.append({
            'dandiset_id': dandiset_id,
            'neither': neither,
            'total': total,
            'rate': neither / total,
            'primary_doi': (relation.get('doi') or '').strip() or '-',
            'kind': primary_doi_kind(relation.get('doi')),
            'damage': primary_doi_damage(relation.get('doi')),
            'primary_title': (relation.get('name') or '')[:44],
            'dandiset_name': (record.get('dandiset_name') or '')[:38],
        })
    key = (lambda r: r['rate']) if args.sort == 'rate' else (lambda r: r['neither'])
    rows.sort(key=key, reverse=True)
    rows = rows[:args.limit]

    print(f'dandisets with >={args.min_pairs} classified pairs, sorted by {args.sort}')
    print(f'(flagged as suspect_primary at >={SUSPECT_MIN_NEITHER_RATE:.0%})\n')
    print(_table(
        ['dandiset', 'NEITHER', 'pairs', 'rate', '!', 'primary DOI', 'kind', 'string',
         'primary paper title'],
        [[r['dandiset_id'], r['neither'], r['total'], f'{r["rate"]:.0%}',
          '*' if r['rate'] >= SUSPECT_MIN_NEITHER_RATE else ' ',
          r['primary_doi'], r['kind'], r['damage'], r['primary_title']] for r in rows],
        'lrrrllllll'))
    print('\nRows marked * are candidates for a primary-paper override. Confirm each by hand:')
    print('  python -m src.analysis.analyze_neither list --dandiset <id> --limit 5')
    return 0


def command_sources(args) -> int:
    by_source: dict[str, Counter] = defaultdict(Counter)
    for record in load_classifications(args.mode):
        cached = load_paper(record['citing_doi'])
        source = (cached or {}).get('source') or '(no cached text)'
        by_source[source][record['classification']] += 1

    extra: dict[str, Counter] = defaultdict(Counter)
    for described in described_rows(args.mode):
        source = described['text_source'] or '(no cached text)'
        extra[source]['neither'] += 1
        extra[source]['refs_found'] += bool(described['references_detector'] != 'none')
        extra[source]['cited_doi_found'] += bool(described['citation_offsets'])
        extra[source]['citing_preprint'] += described['citing_doi'].startswith(PREPRINT_PREFIXES)
        extra[source]['cited_preprint'] += described['cited_doi'].startswith(PREPRINT_PREFIXES)

    rows = []
    for source, labels in sorted(by_source.items(), key=lambda kv: -sum(kv[1].values())):
        total = sum(labels.values())
        if total < args.min_pairs:
            continue
        n = extra[source]['neither'] or 1
        rows.append([
            source, total, f'{labels["NEITHER"]/total:.0%}',
            f'{extra[source]["refs_found"]/n:.0%}',
            f'{extra[source]["cited_doi_found"]/n:.0%}',
            f'{extra[source]["citing_preprint"]/n:.0%}',
            f'{extra[source]["cited_preprint"]/n:.0%}',
        ])
    print('NEITHER rate by fetch source. The last four columns describe the NEITHER')
    print('rows only: did the extraction keep a bibliography, was the cited DOI')
    print('anywhere in the text, and were the two papers preprints.\n')
    print(_table(
        ['fetch source', 'pairs', 'NEITHER', 'refs', 'cited DOI', 'citing pp', 'cited pp'],
        rows, 'lrrrrrr'))
    return 0


def command_list(args) -> int:
    rows = []
    for described in described_rows(args.mode, label=args.label):
        if args.cause and described['cause'] != args.cause:
            continue
        if args.dandiset and described['dandiset_id'] != args.dandiset:
            continue
        if args.source and (described['text_source'] or '') != args.source:
            continue
        rows.append(described)
        if len(rows) >= args.limit:
            break

    print(_table(
        ['citing DOI', 'dandiset', 'cause', 'cites', 'body', 'DANDI', 'source', 'title'],
        [[r['citing_doi'], r['dandiset_id'], r['cause'], r['citation_count'],
          len(r['citation_in_body_offsets']), r['dandi_mentions'],
          (r['text_source'] or '-')[:24], (r['title'] or '')[:44]] for r in rows],
        'llrrrrll'))
    print(f'\n{len(rows)} rows. Read one with:')
    print('  python -m src.analysis.analyze_neither show <citing DOI> --dandiset <id>')
    return 0


def command_show(args) -> int:
    index = classification_index(args.mode)
    candidates = [key for key in index if key[0] == args.doi]
    if args.dandiset:
        candidates = [key for key in candidates if key[1] == args.dandiset]
    if not candidates:
        print(f'No {args.mode} classification cached for {args.doi}'
              + (f' / {args.dandiset}' if args.dandiset else ''), file=sys.stderr)
        return 1

    for citing_doi, dandiset_id in sorted(candidates):
        described = describe_pair(citing_doi, dandiset_id, args.mode)
        described['cause'] = assign_cause(described)
        _render_pair(described)
    return 0


def _render_pair(d: dict) -> None:
    text = d['text']
    references_start = d['references_start']

    print('=' * 100)
    print(f'{d["citing_doi"]}  x  dandiset {d["dandiset_id"]}')
    print(f'  {d["title"]}')
    print('=' * 100)

    print(f'\nVERDICT   {d["classification"]}  confidence {d["confidence"]}'
          f'   cause: {d["cause"]}')
    print(f'  {CAUSE_NOTES.get(d["cause"], "")}')
    print(f'\nREASONING\n  {(d["reasoning"] or "").strip()}')

    print(f'\nDANDISET  {d["dandiset_id"]}  {d["dandiset_name"]}')
    print(f'  primary paper   {d["primary_doi"] or "-"}  '
          f'[{d["primary_doi_kind"]}, string {d["primary_doi_damage"]}]')
    print(f'                  {d["primary_title"]}')
    print(f'  this dandiset   {d["dandiset_neither"]}/{d["dandiset_pairs"]} NEITHER '
          f'({d["dandiset_neither_rate"]:.0%})'
          + ('   FLAGGED as a suspect primary-paper link' if d['dandiset_suspect'] else ''))

    print(f'\nPAPER TEXT  {d["text_chars"]:,} chars   source {d["text_source"] or "-"}')
    if d['text_missing']:
        print('  NOT IN .paper_cache — the classification cannot be re-examined against its input')
    if d['text_drift']:
        print(f'  DRIFT: cache is {d["text_drift"]:+,} chars from the {d["input_chars"]:,} '
              'the classifier saw')
    if d['truncation']:
        print(f'  truncated: {d["truncation"]}')
    print(f'  bibliography starts at {references_start if references_start is not None else "not found"}'
          f'  (detector: {d["references_detector"]})')

    heading = ('CITED PRIMARY DOI' if d['mode'] == MODE_CITING
               else "CITED PRIMARY DOI (context only; direct mode asks about the identifier)")
    print(f'\n{heading}  {d["cited_doi"] or "-"}  [{d["cited_doi_kind"]}]')
    print(f'  {d["citation_count"]} occurrence(s): '
          f'{len(d["citation_in_body_offsets"])} in body, '
          f'{len(d["citation_in_bibliography_offsets"])} in bibliography')
    _print_windows(text, d['citation_offsets'], references_start)
    if not d['citation_offsets'] and text:
        print('    this DOI does not appear in the text at all')

    print(f'\nDANDI SIGNALS  {d["dandi_mentions"]} mention(s) of "dandi"'
          f'   this dandiset ID appears {len(d["dandiset_id_offsets"])} time(s)')
    if d['other_dandiset_ids']:
        print(f'  other dandiset-shaped IDs in the text: {", ".join(d["other_dandiset_ids"][:20])}')
    _print_windows(text, d['dandi_offsets'], references_start)

    print(f'\nREUSE PHRASES  {len(d["reuse_phrase_offsets"])} hit(s)'
          f'   near an in-body citation: {d["reuse_near_citation"]}')
    _print_windows(text, d['reuse_phrase_offsets'], references_start, limit=4)

    print(f'\nPAIRING  this paper is cited by {d["linked_dandiset_count"]} dandiset(s): '
          f'{", ".join(d["linked_dandisets"])}')
    if d['unasked_dandisets']:
        print(f'  never classified against: {", ".join(d["unasked_dandisets"])}')

    quotes = d['evidence_quotes']
    print(f'\nEVIDENCE QUOTES  {len(quotes)}   hallucinated: {d["hallucinated_quote_count"]}')
    for quote in quotes:
        offset = quote.get('offset')
        where = ('body' if (references_start is None or (offset is not None and offset < references_start))
                 else 'bibliography')
        print(f'  [{quote.get("match_type")}] @{offset} ({where})')
        print(f'    {quote.get("quote", "")[:400]}')
    print()


EXPORT_FIELDS = (
    'citing_doi', 'dandiset_id', 'cause', 'classification', 'confidence',
    'title', 'dandiset_name',
    'text_missing', 'text_chars', 'text_source', 'text_drift',
    'cited_doi', 'cited_doi_kind', 'citation_count',
    'references_start', 'references_detector',
    'dandi_mentions', 'other_dandiset_ids',
    'reuse_near_citation',
    'primary_doi', 'primary_doi_kind', 'primary_doi_damage', 'primary_title',
    'dandiset_pairs', 'dandiset_neither', 'dandiset_neither_rate', 'dandiset_suspect',
    'linked_dandiset_count', 'unasked_dandisets',
    'hallucinated_quote_count', 'input_chars',
)


def command_export(args) -> int:
    out = Path(args.out)
    with out.open('w', newline='') as handle:
        writer = csv.writer(handle)
        writer.writerow(list(EXPORT_FIELDS) + [
            'citation_in_body', 'citation_in_bibliography', 'reuse_phrase_hits'])
        count = 0
        for described in described_rows(args.mode, label=args.label):
            row = []
            for field in EXPORT_FIELDS:
                value = described[field]
                row.append(','.join(value) if isinstance(value, list) else value)
            row += [len(described['citation_in_body_offsets']),
                    len(described['citation_in_bibliography_offsets']),
                    len(described['reuse_phrase_offsets'])]
            writer.writerow(row)
            count += 1
    print(f'wrote {count} rows to {out}')
    return 0


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)

    def add_mode(p, default=MODE_CITING):
        p.add_argument('--mode', choices=[MODE_CITING, MODE_DIRECT], default=default)

    p_summary = sub.add_parser('summary', help='label counts, causes, and the four faults')
    p_summary.add_argument('--mode', choices=[MODE_CITING, MODE_DIRECT], default=None,
                           help='default: both pipelines')
    p_summary.set_defaults(func=command_summary)

    p_dandisets = sub.add_parser('dandisets', help='per-dandiset NEITHER rate and primary paper')
    add_mode(p_dandisets)
    p_dandisets.add_argument('--min-pairs', type=int, default=SUSPECT_MIN_PAIRS)
    p_dandisets.add_argument('--sort', choices=['rate', 'count'], default='rate')
    p_dandisets.add_argument('--limit', type=int, default=40)
    p_dandisets.set_defaults(func=command_dandisets)

    p_sources = sub.add_parser('sources', help='NEITHER rate by paper-fetch source')
    add_mode(p_sources)
    p_sources.add_argument('--min-pairs', type=int, default=30)
    p_sources.set_defaults(func=command_sources)

    p_list = sub.add_parser('list', help='pick rows to read')
    add_mode(p_list)
    p_list.add_argument('--cause', choices=sorted(set(CAUSE_ORDER_CITING + CAUSE_ORDER_DIRECT)))
    p_list.add_argument('--dandiset')
    p_list.add_argument('--source')
    p_list.add_argument('--label', default='NEITHER',
                        help='restrict to a label, or "" for every classification')
    p_list.add_argument('--limit', type=int, default=25)
    p_list.set_defaults(func=command_list)

    p_show = sub.add_parser('show', help='render one pair with all evidence located in the text')
    add_mode(p_show)
    p_show.add_argument('doi')
    p_show.add_argument('--dandiset')
    p_show.set_defaults(func=command_show)

    p_export = sub.add_parser('export', help='one CSV row per pair, all features flat')
    add_mode(p_export)
    p_export.add_argument('--out', required=True)
    p_export.add_argument('--label', default='NEITHER')
    p_export.set_defaults(func=command_export)

    args = parser.parse_args()
    return args.func(args)


if __name__ == '__main__':
    sys.exit(main())
