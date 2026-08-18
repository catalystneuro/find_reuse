#!/usr/bin/env python3
"""
Investigate the NEITHER classifications one cause at a time.

NEITHER is meant to be a rare residual label. In the citing pipeline it is 22%
of all pairs, which makes it a symptom rather than an answer: the label says the
paper has no relationship to the dataset, but not why the pipeline ever put the
two together.

`summary` measures how many NEITHER rows match the one cause currently under
investigation, `list` picks rows out of that bucket, and `show` renders a single
pair with every piece of evidence located in the text, so a row can be judged by
reading rather than by grepping a 90,000-character blob.

Read-only, and offline by construction: nothing here fetches, classifies, or
writes to the caches.

Usage:
    python -m src.analysis.analyze_neither summary
    python -m src.analysis.analyze_neither list --cause citation_in_bibliography --limit 30
    python -m src.analysis.analyze_neither show 10.1038/s41586-023-06271-6
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterator, Optional

# .paper_cache holds two filename schemes and both are live: the fetcher moved
# from replacing '/', ':' and '\' with '_' to percent-encoding the lowercased
# DOI, and it reads either, so entries written before and after the change sit
# side by side -- currently about 69,900 legacy and 6,100 encoded. Both are
# imported rather than reimplemented so the two cannot drift apart.
from paper_text_fetcher.cache import cache_filename, legacy_cache_filename

REPO = Path(__file__).resolve().parents[2]

CITING_CACHE = REPO / '.fulltext_classification_cache'
DIRECT_CACHE = REPO / '.fulltext_direct_cache'
PAPER_CACHE = REPO / '.paper_cache'
DISCOVERY = REPO / 'output' / 'all_dandiset_papers_refreshed.json'

MODE_CITING = 'citing'
MODE_DIRECT = 'direct'
CACHE_FOR_MODE = {MODE_CITING: CITING_CACHE, MODE_DIRECT: DIRECT_CACHE}

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
    for filename in (cache_filename(doi), legacy_cache_filename(doi)):
        path = PAPER_CACHE / filename
        if path.exists():
            return json.loads(path.read_text())
    return None


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
        'text_source': (cached or {}).get('source'),
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
# Investigating one cause at a time. Only the test below runs; every row that
# does not match falls to OTHER. The suppressed causes are recoverable from the
# raw features that `export` carries, and the next one to look at replaces this
# one rather than joining it.
CAUSE_NO_BODY = 'no_article_body'
CAUSE_CITING = 'citation_not_found'
CAUSE_DIRECT = 'catalog_listing'
OTHER = 'other'

# no_article_body sits above citation_not_found: when the fetch never produced a
# paper, the model's failure to find the citation is a consequence of that and
# says nothing about its retrieval. Rows are counted under the upstream fault.
CAUSE_ORDER_FOR_MODE = {
    MODE_CITING: (CAUSE_NO_BODY, CAUSE_CITING, OTHER),
    MODE_DIRECT: (CAUSE_DIRECT, OTHER),
}

# How many other dandiset-shaped identifiers have to appear before the text is
# read as a catalog of datasets rather than a paper discussing one. Across the
# direct NEITHER rows the counts are 0, 2, 2, 10, 14, then 71 for each of the 65
# rows belonging to the NWB ecosystem paper, whose Appendix 6 tabulates the
# archive. The threshold sits in that gap; lowering it pulls in papers that
# merely name several dandisets rather than enumerating the archive.
CATALOG_MIN_OTHER_IDS = 20

CAUSE_NOTES = {
    CAUSE_NO_BODY: 'the fetch returned no article body, so there was nothing to find '
                   'the citation in',
    CAUSE_CITING: "the model reports it could not find the citation, contradicting the "
                  'OpenAlex edge that put the pair on the worklist',
    CAUSE_DIRECT: f'text lists >={CATALOG_MIN_OTHER_IDS} other dandiset IDs; the match is a '
                  'catalog entry, not a reference to this dataset',
    OTHER: 'not this cause',
}


# Every pair on the citing worklist is there because OpenAlex recorded the paper
# as citing the dataset's primary publication. So a reasoning that reports the
# citation is absent contradicts the source that created the pair, and one of the
# two is wrong.
#
# This is the whole bucket, not a slice of it: 3,067 of the 3,085 NEITHER rows
# assert absence, and the 18 that do not are cases where the model found the
# citation and judged it anyway. The split is therefore between a retrieval
# failure and a judgement, and the retrieval side is 99.4% of NEITHER.
#
# What this test does NOT establish is whether the citation was findable. That
# needs resolving the in-text marker through the reference list, which is the
# adjudicator, not this.
# An article body cites as it goes, so it carries in-text citation markers in one
# of three styles. Front matter, landing pages, reference-only fragments and
# reviewer reports carry almost none.
#
# Neither half of this test works alone, which is why it is a conjunction. Marker
# count alone flags long real papers whose markers the extraction mangled -- a
# 117,000-character document scoring 1. Length alone flags short but genuine
# articles, such as a 9,510-character IEEE paper carrying 41 markers. Together
# they separate cleanly: the known failures score 3/14k, 6/11.6k, 6/14.3k and
# 5/12k, while real bodies score 41, 83, 85, 128 and 151.
#
# The `[a-z]` prefix on the superscript pattern matters. Allowing `)` or `.`
# before the digits matches statistics like "OR, 2.25" and "P = 9.03", which took
# a JAMA structured abstract from 3 markers to 59 and destroyed the separation.
CITATION_MARKERS = (
    re.compile(r'\[\s*\d{1,3}\s*(?:[,\u2013\u2014-]\s*\d{1,3}\s*)*\]'),
    re.compile(r'\(\s*[A-Z][A-Za-z\u00C0-\u017F\'\u2019-]+'
               r'(?:\s+(?:et\s+al\.?|and|&)[^)]{0,20})?,?\s*(?:19|20)\d{2}[a-z]?\s*[;)]'),
    re.compile(r'[a-z]\d{1,3}(?:[,\u2013\u2014-]\d{1,3})*(?![\d\w])'),
)
MAX_MARKERS_WITHOUT_BODY = 10
MAX_CHARS_WITHOUT_BODY = 20_000


def citation_marker_count(text: str) -> int:
    return sum(len(pattern.findall(text)) for pattern in CITATION_MARKERS)


ABSENCE_CLAIM = re.compile(
    r'(does not|no|never|without|lacks?|absent)[^.]{0,40}'
    r'(mention|cit|referenc|refer to|discuss)'
    r'|not (mentioned|cited|referenced|discussed)', re.I)


def assign_cause(described: dict) -> str:
    if described['mode'] == MODE_DIRECT:
        if len(described['other_dandiset_ids']) >= CATALOG_MIN_OTHER_IDS:
            return CAUSE_DIRECT
        return OTHER

    if (described['text_chars'] < MAX_CHARS_WITHOUT_BODY
            and citation_marker_count(described['text']) <= MAX_MARKERS_WITHOUT_BODY):
        return CAUSE_NO_BODY
    if ABSENCE_CLAIM.search(described['reasoning'] or ''):
        return CAUSE_CITING
    return OTHER


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

        neither = list(described_rows(mode))
        if not neither:
            continue

        print(f'\n--- cause of {len(neither)} NEITHER')
        causes = Counter(r['cause'] for r in neither)
        print(_table(['cause', 'n', 'share', 'note'],
                     [[c, causes[c], f'{causes[c]/len(neither):.1%}', CAUSE_NOTES[c]]
                      for c in CAUSE_ORDER_FOR_MODE[mode] if causes[c]], 'lrrl'))
    return 0


def command_list(args) -> int:
    rows = []
    for described in described_rows(args.mode, label=args.label):
        if args.cause and described['cause'] != args.cause:
            continue
        if args.dandiset and described['dandiset_id'] != args.dandiset:
            continue
        rows.append(described)
        if len(rows) >= args.limit:
            break

    # Both DOIs, because the row is a pair and investigating one means opening
    # both papers: the citing paper is what was read, the cited paper is what it
    # was asked about.
    print(_table(
        ['citing DOI', 'cited DOI', 'dandiset', 'cause'],
        [[r['citing_doi'], r['cited_doi'] or '-', r['dandiset_id'], r['cause']]
         for r in rows],
        'llll'))
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
          f'({d["dandiset_neither_rate"]:.0%})')

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


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)

    def add_mode(p, default=MODE_CITING):
        p.add_argument('--mode', choices=[MODE_CITING, MODE_DIRECT], default=default)

    p_summary = sub.add_parser('summary', help='label counts and how many match the cause under investigation')
    p_summary.add_argument('--mode', choices=[MODE_CITING, MODE_DIRECT], default=None,
                           help='default: both pipelines')
    p_summary.set_defaults(func=command_summary)

    p_list = sub.add_parser('list', help='pick rows to read')
    add_mode(p_list)
    p_list.add_argument('--cause', choices=sorted({CAUSE_NO_BODY, CAUSE_CITING, CAUSE_DIRECT, OTHER}))
    p_list.add_argument('--dandiset')
    p_list.add_argument('--label', default='NEITHER',
                        help='restrict to a label, or "" for every classification')
    p_list.add_argument('--limit', type=int, default=25)
    p_list.set_defaults(func=command_list)

    p_show = sub.add_parser('show', help='render one pair with all evidence located in the text')
    add_mode(p_show)
    p_show.add_argument('doi')
    p_show.add_argument('--dandiset')
    p_show.set_defaults(func=command_show)

    args = parser.parse_args()
    return args.func(args)


if __name__ == '__main__':
    sys.exit(main())
