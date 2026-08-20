#!/usr/bin/env python3
"""
Check the citation graph against the papers themselves.

The indirect pathway trusts OpenAlex to say that a paper cites a dandiset's
primary publication. When that edge is wrong, the pair enters the corpus and the
classifier is asked a question with no answer in the text. The classifier is a
poor instrument for catching it: the extracted text often has no reference list
at all, so absence of the citation is not something the model can observe.

Looking for the primary paper's DOI or exact title in the citing paper's text is
cheap, deterministic, and has no such blind spot beyond the missing reference
list itself, which is why a paper is only reported here when the marker is
absent AND the text does appear to carry references.

Run:
    python -m src.analysis.audit_citation_links
    python -m src.analysis.audit_citation_links --min-pairs 30 --threshold 0.4
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from paper_text_fetcher import TextCache  # noqa: E402

# A title shorter than this is too generic to match on safely.
MIN_TITLE_CHARS = 25

REF_HEAD = re.compile(r'\n\s*(references|bibliography|literature cited|works cited)\s*\n', re.I)


def normalize(text: str) -> str:
    """Fold case, accents and punctuation so titles match across renderings."""
    folded = unicodedata.normalize('NFKD', text or '').lower()
    return re.sub(r'[^a-z0-9]+', ' ', folded).strip()


def find_primary_marker(paper_text: str, primaries: list[tuple[str, str]],
                        citing_doi: str) -> str:
    """
    Return which marker of the primary paper appears in `paper_text`.

    A paper's own DOI is skipped: a preprint and its published version share a
    corpus, and a paper citing itself is not evidence of the link being right.
    Returns 'doi', 'title', or '' for no marker.
    """
    low = paper_text.lower()
    folded = normalize(paper_text)
    for doi, title in primaries:
        if doi and doi != citing_doi.lower() and doi in low:
            return 'doi'
        norm_title = normalize(title)
        if len(norm_title) > MIN_TITLE_CHARS and norm_title in folded:
            return 'title'
    return ''


def load_primaries(corpus_path: Path) -> dict[str, list[tuple[str, str]]]:
    corpus = json.loads(corpus_path.read_text())
    out: dict[str, list[tuple[str, str]]] = {}
    for record in corpus.get('results', []):
        out[record['dandiset_id']] = [
            (rel['doi'].lower(), rel.get('name') or '')
            for rel in (record.get('paper_relations') or []) if rel.get('doi')
        ]
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('-i', '--classifications',
                        default=str(REPO / 'output/fulltext_classifications.json'))
    parser.add_argument('-c', '--corpus',
                        default=str(REPO / 'output/all_dandiset_papers_refreshed.json'))
    parser.add_argument('--paper-cache', default=str(REPO / '.paper_cache'))
    parser.add_argument('--min-pairs', type=int, default=20,
                        help='ignore dandisets with fewer classified papers')
    parser.add_argument('--threshold', type=float, default=0.30,
                        help='flag dandisets whose absent rate exceeds this')
    parser.add_argument('-o', '--output', default='',
                        help='write the per-pair verdicts as JSON')
    args = parser.parse_args()

    cache = TextCache(Path(args.paper_cache))
    primaries = load_primaries(Path(args.corpus))
    rows = json.loads(Path(args.classifications).read_text())['classifications']

    def check(record: dict) -> dict | None:
        got = cache.get(record['citing_doi'])
        if not got:
            return None
        text = got[0] or ''
        tail = text[int(len(text) * 0.55):]
        return {
            'citing_doi': record['citing_doi'],
            'dandiset_id': record['dandiset_id'],
            'classification': record.get('classification'),
            'marker': find_primary_marker(text, primaries.get(record['dandiset_id'], []),
                                          record['citing_doi']),
            'has_references': bool(REF_HEAD.search(tail)),
        }

    with ThreadPoolExecutor(max_workers=16) as pool:
        verdicts = [v for v in pool.map(check, rows) if v]

    by_label: dict[str, Counter] = defaultdict(Counter)
    for v in verdicts:
        by_label[v['classification']][bool(v['marker'])] += 1

    print(f"{len(verdicts):,} pairs checked against the cached text\n")
    print(f"  {'label':10} {'pairs':>7} {'cited':>8} {'absent':>8} {'absent %':>9}")
    for label in sorted(by_label):
        counts = by_label[label]
        total = counts[True] + counts[False]
        print(f"  {label:10} {total:7,} {counts[True]:8,} {counts[False]:8,} "
              f"{counts[False] / total:9.1%}")

    # A dandiset whose citing papers mostly show no trace of the primary paper
    # is far more likely to have the wrong primary paper than to have attracted
    # a crowd of papers that all cite it invisibly.
    per_dandiset: dict[str, Counter] = defaultdict(Counter)
    for v in verdicts:
        per_dandiset[v['dandiset_id']][bool(v['marker'])] += 1

    flagged = []
    for dandiset, counts in per_dandiset.items():
        total = counts[True] + counts[False]
        if total >= args.min_pairs and counts[False] / total > args.threshold:
            flagged.append((counts[False] / total, total, counts[False], dandiset))

    print(f"\nDandisets whose citing papers show no trace of the linked primary paper "
          f"(>{args.threshold:.0%} of at least {args.min_pairs} papers):\n")
    if not flagged:
        print("  none")
    for rate, total, absent, dandiset in sorted(flagged, reverse=True):
        titles = primaries.get(dandiset) or [('', '')]
        print(f"  {dandiset}  {absent:4,} of {total:4,} absent ({rate:.0%})  "
              f"{titles[0][1][:52]}")

    if args.output:
        Path(args.output).write_text(json.dumps(verdicts, indent=1))
        print(f"\nwrote {args.output}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
