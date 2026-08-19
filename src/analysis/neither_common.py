#!/usr/bin/env python3
"""
Shared machinery for the NEITHER analysis scripts.

Not a CLI. `analyze_indirect_neither.py` and `analyze_direct_neither.py` each
define their own causes and hand a Pipeline to `run()`; everything that does not
differ between the two pathways lives here: reading the caches, describing a
(paper, dataset) pair, and rendering the summary/list/show commands.

Read-only, and offline by construction: nothing here fetches, classifies, or
writes to the caches.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import textwrap
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


class Pipeline:
    """What differs between the two pathways."""

    def __init__(self, name, module, mode, cache_dir, cause_tests, cause_notes, description):
        self.name = name                  # 'indirect' / 'direct', for messages
        self.module = module              # module name, for the hint printed by `list`
        self.mode = mode                  # MODE_CITING / MODE_DIRECT, for prompts and display
        self.cache_dir = cache_dir
        self.cause_tests = cause_tests    # ordered ((cause, predicate), ...)
        self.cause_notes = cause_notes
        self.description = description    # the script's module docstring

    @property
    def causes(self):
        return tuple(cause for cause, _ in self.cause_tests) + (OTHER,)

    def assign_causes(self, described):
        """Every cause matching this row, in display order; (OTHER,) if none do."""
        matched = tuple(cause for cause, test in self.cause_tests if test(described))
        return matched or (OTHER,)


OTHER = 'other'

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
def load_classifications(cache_dir: Path) -> tuple[dict, ...]:
    """Every cached classification in a directory, in filename order."""
    records = []
    for path in sorted(cache_dir.glob('*.json')):
        records.append(json.loads(path.read_text()))
    return tuple(records)


@lru_cache(maxsize=2)
def classification_index(cache_dir: Path) -> dict[tuple[str, str], dict]:
    return {(r['citing_doi'], r['dandiset_id']): r for r in load_classifications(cache_dir)}


@lru_cache(maxsize=512)
def load_paper(doi: str) -> Optional[dict]:
    """The cached fetch for a DOI: text, source, cached_at. None if not cached."""
    for filename in (cache_filename(doi), legacy_cache_filename(doi)):
        path = PAPER_CACHE / filename
        if path.exists():
            return json.loads(path.read_text())
    return None


def primary_relation(dandiset_id: str, cited_doi: str = '') -> dict:
    """
    The paper_relations entry a pair was built from, or an empty dict.

    A dandiset can declare several papers -- 37 of them do -- and discovery
    records which one produced each pair. Taking the first entry regardless
    would name a different paper than the row is actually about, which it does
    for 598 of the NEITHER rows.
    """
    record = load_discovery()['dandisets'].get(dandiset_id) or {}
    relations = [r for r in (record.get('paper_relations') or []) if r]
    if cited_doi:
        for relation in relations:
            if (relation.get('doi') or '').strip() == cited_doi:
                return relation
    return relations[0] if relations else {}


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
def dandiset_label_counts(cache_dir: Path) -> dict[str, Counter]:
    """Per-dandiset label counts, for the context line in `show`."""
    counts: dict[str, Counter] = defaultdict(Counter)
    for record in load_classifications(cache_dir):
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


def describe_pair(pipeline, citing_doi: str, dandiset_id: str) -> dict:
    """
    Everything known about one (paper, dandiset) pair, from the caches alone.

    The classification record says what the model concluded; the rest says what
    the model was looking at when it concluded it. Keeping both in one record is
    what lets a NEITHER be attributed to a fault rather than merely counted.
    """
    record = classification_index(pipeline.cache_dir).get((citing_doi, dandiset_id))
    discovery = load_discovery()
    paper_entry = discovery['pair'].get((citing_doi, dandiset_id)) or {}
    dandiset_record = discovery['dandisets'].get(dandiset_id) or {}
    counts = dandiset_label_counts(pipeline.cache_dir).get(dandiset_id, Counter())
    dandiset_total = sum(counts.values())

    cited_doi = (paper_entry.get('cited_paper_doi') or '').strip()
    relation = primary_relation(dandiset_id, cited_doi)
    cited_doi = cited_doi or (relation.get('doi') or '').strip()
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
    index = classification_index(pipeline.cache_dir)
    unasked = [d for d in linked_dandisets if (citing_doi, d) not in index]

    return {
        'citing_doi': citing_doi,
        'dandiset_id': dandiset_id,
        'mode': pipeline.mode,
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
        'cached_at': (cached or {}).get('cached_at'),
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
        'dandiset_relation_count': len([r for r in
                                        (dandiset_record.get('paper_relations') or []) if r]),
        'primary_relation_source': relation.get('source'),
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
def described_rows(pipeline, label: Optional[str] = 'NEITHER') -> Iterator[dict]:
    """Describe every cached pair, optionally restricted to one label."""
    for record in load_classifications(pipeline.cache_dir):
        # Filter before describing. describe_pair reads the paper text off disk
        # and runs every regex over it, so describing all 13,956 rows to keep
        # 3,085 costs several times what the caller asked for.
        if label and record['classification'] != label:
            continue
        described = describe_pair(pipeline, record['citing_doi'], record['dandiset_id'])
        described['causes'] = pipeline.assign_causes(described)
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

def command_summary(pipeline, args) -> int:
    records = load_classifications(pipeline.cache_dir)
    labels = Counter(r['classification'] for r in records)
    total = len(records)
    print(f'\n=== {pipeline.name} pipeline: {total} classified pairs')
    print(_table(['label', 'n', 'share'],
                 [[k, v, f'{v/total:.1%}'] for k, v in labels.most_common()], 'lrr'))

    neither = list(described_rows(pipeline))
    if not neither:
        return 0

    print(f'\n--- cause of {len(neither)} NEITHER')
    causes = Counter(c for r in neither for c in r['causes'])
    print(_table(['cause', 'n', 'share', 'note'],
                 [[c, causes[c], f'{causes[c]/len(neither):.1%}', pipeline.cause_notes[c]]
                  for c in pipeline.causes if causes[c]], 'lrrl'))
    multiple = sum(1 for r in neither if len(r['causes']) > 1)
    print(f'shares overlap and do not sum to 100%: {multiple} rows '
          f'({multiple/len(neither):.0%}) match more than one cause')
    return 0


def command_list(pipeline, args) -> int:
    rows = []
    for described in described_rows(pipeline, label=args.label):
        if args.cause and args.cause not in described['causes']:
            continue
        # --only isolates the rows a cause does not share with any other, which
        # is where an unexplained residual lives: in the indirect pipeline 396
        # rows carry citation_not_found while the paper is intact and its
        # references are numbered, so no known defect accounts for them.
        if args.only and len(described['causes']) > 1:
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
        ['citing DOI', 'cited DOI', 'dandiset', 'causes'],
        [[r['citing_doi'], r['cited_doi'] or '-', r['dandiset_id'], ' + '.join(r['causes'])]
         for r in rows],
        'llll'))
    print(f'\n{len(rows)} rows. Read one with:')
    print(f'  python -m src.analysis.{pipeline.module} show <citing DOI> --dandiset <id>')
    print(f'  python -m src.analysis.{pipeline.module} show <citing DOI> --cited-doi <DOI>')
    return 0


def command_show(pipeline, args) -> int:
    index = classification_index(pipeline.cache_dir)
    candidates = [key for key in index if key[0] == args.doi]
    if args.dandiset:
        candidates = [key for key in candidates if key[1] == args.dandiset]

    described_pairs = [describe_pair(pipeline, citing_doi, dandiset_id)
                       for citing_doi, dandiset_id in sorted(candidates)]
    if args.cited_doi:
        # The cited DOI is derived from the pair rather than stored on it, so
        # this filters after describing rather than on the cache key.
        wanted = args.cited_doi.strip().lower()
        described_pairs = [d for d in described_pairs
                           if (d['cited_doi'] or '').lower() == wanted]

    if not described_pairs:
        asked = ' / '.join(filter(None, [args.doi, args.dandiset, args.cited_doi]))
        print(f'No {pipeline.name} classification cached for {asked}', file=sys.stderr)
        return 1

    for described in described_pairs:
        described['causes'] = pipeline.assign_causes(described)
        _render_pair(pipeline, described)
    return 0


def _render_pair(pipeline, d: dict) -> None:
    text = d['text']
    references_start = d['references_start']

    print('=' * 100)
    print(f'{d["citing_doi"]}  x  dandiset {d["dandiset_id"]}')
    print(f'  {d["title"]}')
    print('=' * 100)

    print(f'\nVERDICT   {d["classification"]}  confidence {d["confidence"]}'
          f'   causes: {" + ".join(d["causes"])}')
    for cause in d['causes']:
        print(f'  {cause}: {pipeline.cause_notes.get(cause, "")}')
    print(f'\nREASONING\n  {(d["reasoning"] or "").strip()}')

    print(f'\nDANDISET  {d["dandiset_id"]}  {d["dandiset_name"]}')
    print(f'  primary paper   {d["primary_doi"] or "-"}  '
          f'[{d["primary_doi_kind"]}, string {d["primary_doi_damage"]}'
          + (f', via {d["primary_relation_source"]}' if d['primary_relation_source'] else '')
          + ']')
    print(f'                  {d["primary_title"]}')
    if d['dandiset_relation_count'] > 1:
        print(f'  NOTE            this dandiset declares {d["dandiset_relation_count"]} papers; '
              'the one shown is the one this pair was built from')
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

    heading = ('CITED PRIMARY DOI' if pipeline.mode == MODE_CITING
               else "CITED PRIMARY DOI (context only; the direct pathway asks about the identifier)")
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




# --------------------------------------------------------------------------- #
# Plot
# --------------------------------------------------------------------------- #

# Causes overlap, so a pie or a stacked bar of them would double-count. They also
# nest rather than cross -- in the indirect pipeline citation_not_found covers
# 99.4% of rows and 301 of the 306 no_article_body rows sit inside
# stale_extraction -- so a Venn would draw seven regions when only four exist and
# imply a symmetry that is not there. An Euler diagram draws the regions that
# exist and sizes them to the data.
#
# The figure has two panels because the causes alone do not convey scope. The bar
# puts NEITHER back in the context of every pair the pipeline classified, and the
# circles below break that slice open.
#
# Distinct hues rather than shades of one colour: the circles are nested, so
# neighbouring areas touch along a shared edge and a sequential ramp makes the
# boundaries hard to see.
CAUSE_COLOURS = ('#264653', '#E76F51', '#E9C46A', '#8AB17D')
OTHER_COLOUR = '#B0BEC5'
# Where the OTHER blob sits: outside every cause circle, because that is what
# matching no cause means. Far enough from the outer circle's centre to stay
# disjoint from it at any plausible size.
OTHER_CENTRE = (-1.12, -0.82)
# Below this the blob is invisible at print size, so it is floored. The floor
# breaks area-proportionality for that one shape, which the caption states.
MIN_VISIBLE_RADIUS = 0.035


def _share(count: int, total: int) -> str:
    """A share that never rounds a real category down to 0%."""
    fraction = count / total
    return '<1%' if 0 < fraction < 0.005 else f'{fraction:.0%}'
NEITHER_COLOUR = '#264653'
OTHER_LABEL_COLOUR = '#CFD8DC'
NESTING_OFFSET = 0.10   # fraction of the outer radius, per level


def command_plot(pipeline, args) -> int:
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D
    from matplotlib.patches import ConnectionPatch

    records = load_classifications(pipeline.cache_dir)
    label_counts = Counter(r['classification'] for r in records)
    classified = len(records)

    rows = list(described_rows(pipeline))
    counts = Counter(c for r in rows for c in r['causes'])
    total = len(rows)

    # OTHER is the absence of a cause, not one of them, so it gets no circle.
    # Largest first, so each circle is drawn inside the one before it.
    ordered = [(cause, counts[cause]) for cause in pipeline.causes
               if counts[cause] and cause != OTHER]
    ordered.sort(key=lambda pair: -pair[1])

    figure, (bar_axes, euler_axes) = plt.subplots(
        2, 1, figsize=(7.6, 9.2), gridspec_kw={'height_ratios': [1, 6]})

    # ---- panel 1: every classified pair, by label -------------------------
    left = 0.0
    neither_span = (0.0, 0.0)
    for label, count in label_counts.most_common():
        width = count / classified
        is_neither = label == 'NEITHER'
        bar_axes.barh(0, width, left=left, height=0.55,
                      color=NEITHER_COLOUR if is_neither else OTHER_LABEL_COLOUR,
                      edgecolor='white', linewidth=1.5)
        caption = f'{label}\n{count:,}  ({_share(count, classified)})'
        if width >= 0.13:
            bar_axes.text(left + width / 2, 0, caption, ha='center', va='center',
                          fontsize=9.5, color='white' if is_neither else '#37474F',
                          fontweight='bold' if is_neither else 'normal')
        else:
            # Too narrow to hold text. Put it above the bar rather than let it
            # overrun the neighbouring segment.
            bar_axes.text(left + width / 2, 0.42, caption, ha='center', va='bottom',
                          fontsize=8.5, color='#37474F', linespacing=1.2)
        if is_neither:
            neither_span = (left, left + width)
        left += width

    bar_axes.set_xlim(0, 1)
    bar_axes.set_ylim(-0.6, 0.6)
    bar_axes.axis('off')
    # Extra padding so a label pushed above a narrow segment clears the title.
    bar_axes.set_title(f'{pipeline.name} pipeline: {classified:,} classified pairs',
                       fontsize=13, pad=34)

    # ---- panel 2: what is wrong with the NEITHER rows ---------------------
    outer = 1.0
    handles, labels = [], []
    for level, (cause, count) in enumerate(ordered):
        radius = outer * (count / total) ** 0.5
        # Walk each successive circle toward the same edge, so the crescent of
        # "outer cause only" opens on one side instead of closing to a hairline.
        centre_x = (outer - radius) * (1 - NESTING_OFFSET * level)
        colour = CAUSE_COLOURS[level % len(CAUSE_COLOURS)]
        euler_axes.add_patch(plt.Circle((centre_x, 0.0), radius, zorder=level + 1,
                                        facecolor=colour, edgecolor='white',
                                        linewidth=2.5))
        handles.append(Line2D([], [], marker='o', linestyle='none', markersize=13,
                              markerfacecolor=colour, markeredgecolor='white'))
        labels.append(f'{cause}   {count:,}  ({_share(count, total)} of NEITHER)')

    # OTHER is the absence of a cause, so it is drawn disjoint from the nest
    # rather than left off. At 2 of 3,085 rows it is a speck, which is honest.
    if counts[OTHER]:
        radius = outer * (counts[OTHER] / total) ** 0.5
        euler_axes.add_patch(plt.Circle(OTHER_CENTRE, max(radius, MIN_VISIBLE_RADIUS),
                                        zorder=len(ordered) + 1,
                                        facecolor=OTHER_COLOUR, edgecolor='white',
                                        linewidth=1.5))
        handles.append(Line2D([], [], marker='o', linestyle='none', markersize=13,
                              markerfacecolor=OTHER_COLOUR, markeredgecolor='white'))
        labels.append(f'{OTHER}   {counts[OTHER]:,}  '
                      f'({_share(counts[OTHER], total)} of NEITHER)')

    euler_axes.set_xlim(-1.30, 1.16)
    euler_axes.set_ylim(-1.15, 1.15)
    euler_axes.set_aspect('equal')
    euler_axes.axis('off')

    # Tie the two panels together: the circles are the NEITHER bar segment,
    # opened up. The lines land on the outer circle's upper shoulders rather
    # than on the panel corners, which are empty space.
    if ordered:
        widest_radius = outer * (ordered[0][1] / total) ** 0.5
        widest_centre = outer - widest_radius
        shoulder = widest_radius * 0.7071
        for fraction, side in zip(neither_span, (-1.0, 1.0)):
            figure.add_artist(ConnectionPatch(
                xyA=(fraction, -0.30), coordsA=bar_axes.transData,
                xyB=(widest_centre + side * shoulder, shoulder),
                coordsB=euler_axes.transData,
                color='#B0BEC5', linewidth=1.0, linestyle=(0, (4, 3)), zorder=0))

    legend = euler_axes.legend(handles, labels, loc='upper center',
                               bbox_to_anchor=(0.5, -0.02), ncol=1, frameon=False,
                               fontsize=10.5, handletextpad=0.9, labelspacing=0.8)
    for text in legend.get_texts():
        text.set_va('center')

    multiple = sum(1 for r in rows if len(r['causes']) > 1)
    note = 'Circle area is proportional to row count.'
    if multiple:
        note += (f' {multiple:,} of {total:,} rows carry more than one cause, so the areas '
                 'overlap and do not sum to the total.')
    if counts[OTHER]:
        note += (' Rows matching no cause are drawn outside the nest, at a minimum '
                 'size so they stay visible.')
    # Anchored to the axes, below the legend. Figure-level text would be placed
    # before bbox_inches='tight' recomputes the bounding box, and would land on
    # top of the legend.
    euler_axes.text(0.5, -0.10 - 0.068 * len(handles), textwrap.fill(note, 80),
                    transform=euler_axes.transAxes, ha='center', va='top',
                    fontsize=8.5, color='#5C6F7C')

    out = Path(args.out)
    figure.savefig(out, dpi=200, bbox_inches='tight', facecolor='white',
                   pad_inches=0.35)
    print(f'wrote {out}')
    return 0


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def run(pipeline) -> int:
    """Parse arguments for a pipeline and dispatch. The scripts' whole main()."""
    parser = argparse.ArgumentParser(
        description=pipeline.description,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)

    p_summary = sub.add_parser(
        'summary', help='label counts and how many NEITHER rows match each cause')
    p_summary.set_defaults(func=command_summary)

    p_list = sub.add_parser('list', help='pick rows to read')
    p_list.add_argument('--cause', choices=sorted(pipeline.causes))
    p_list.add_argument('--only', action='store_true',
                        help='keep only rows where --cause is the sole matching cause')
    p_list.add_argument('--dandiset')
    p_list.add_argument('--label', default='NEITHER',
                        help='restrict to a label, or "" for every classification')
    p_list.add_argument('--limit', type=int, default=25)
    p_list.set_defaults(func=command_list)

    p_show = sub.add_parser('show', help='render one pair with all evidence located in the text')
    p_show.add_argument('doi', help='the citing paper')
    p_show.add_argument('--dandiset')
    p_show.add_argument('--cited-doi',
                        help='select the pair by the paper that was cited, as an alternative '
                             'to --dandiset')
    p_show.set_defaults(func=command_show)

    p_plot = sub.add_parser('plot', help='Euler diagram of the causes, as a PNG')
    p_plot.add_argument('--out', default=None,
                        help=f'output path (default: {{pipeline.module}}.png)')
    p_plot.set_defaults(func=command_plot)

    args = parser.parse_args()
    if getattr(args, 'out', None) is None and args.func is command_plot:
        args.out = f'{pipeline.module}.png'
    return args.func(pipeline, args)
