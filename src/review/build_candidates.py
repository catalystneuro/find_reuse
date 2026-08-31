#!/usr/bin/env python3
"""
Build the list of (paper, dataset) pairs a person still has to check.

Every pair the classifier called REUSE goes in, with everything a reviewer
needs to judge it: the paper, the dataset, the paper it cited where there is
one, the model's reasoning and the passages it quoted. Reviewing reads only
this file, so the classification outputs and the discovery corpora are consulted
once here rather than at the start of every session.

The fields the classifier answered alongside REUSE come along too. They are not
shown during review, which asks one question; they are what assign_reviews
filters a round on.

Writes reviews/reuse_candidates.json, sorted by pair, so rerunning the pipeline
shows up as the pairs it added rather than as an unreadable rewrite.

Usage:
    python -m src.review.build_candidates \
        -i output/fulltext_classifications.json \
        -i output/fulltext_direct_openalex.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

from src.shared.run_fulltext_classification import primary_paper_index

# What it takes for a passage to be talking about DANDI at all.
DANDI_MARKER = re.compile(r'dandiarchive\.org|10\.48324|\bDANDI\b|dandiset', re.I)

REPO = Path(__file__).resolve().parents[2]
CANDIDATES_FILE = REPO / 'reuse_confirmation/reuse_candidates.json'
RESULTS_FILE = REPO / 'output/all_dandiset_papers_refreshed.json'
DIRECT_RESULTS_FILE = REPO / 'output/results_dandi_openalex.json'


# Version suffixes. '/vN' is unambiguous. '.N' is not: 10.1002/brx2.47 and
# brx2.65 are different articles, not versions of one, so it only counts as a
# version when the unversioned DOI is also present or the base ends in a long
# article number.
_VERSION_SLASH = re.compile(r'^(?P<base>.+?)/v\d{1,2}$', re.I)
_VERSION_DOT = re.compile(r'^(?P<base>.+?)\.\d{1,2}$')
_ARTICLE_NUMBER = re.compile(r'\d{4,}$')


def canonical_doi(doi: str, known: set) -> str:
    """
    Collapse a versioned DOI onto the work it is a version of.

    eLife's reviewed-preprint model mints .1, .2 and .3 alongside the base DOI,
    so one paper can appear five times and be counted five times. Research
    Square, F1000Research, Authorea and Qeios do the same with other suffixes.
    """
    m = _VERSION_SLASH.match(doi)
    if m:
        return m.group('base')
    m = _VERSION_DOT.match(doi)
    if m:
        base = m.group('base')
        if base in known or _ARTICLE_NUMBER.search(base.split('/')[-1]):
            return base
    return doi


@lru_cache(maxsize=1)
def corpus_papers(results_path: Path) -> tuple[dict, dict, dict, dict]:
    """
    Paper titles, dataset names, the paper each dataset declares, and how that
    paper came to be named.

    primary_paper_index says which paper a pair was built from but not what it
    is called, and a reviewer choosing what to open needs the title as much as
    the identifier. The declared paper stands in for pairs the citing pathway
    never saw; a dandiset naming several, the one asserting it describes the
    data is the one to read.

    For most of these dandisets DANDI names no paper and a model was asked to
    pick one. A wrong pick makes every pair built under it a paper citing
    something else entirely, which only a reviewer can catch, and only if the
    card says which kind of link it is looking at.

    Origins are keyed by pair rather than by DOI: the same paper is a model's
    guess for one dandiset and DANDI's own claim for another.
    """
    data = json.loads(results_path.read_text())
    paper_titles, dandiset_names, declared, origins = {}, {}, {}, {}
    for ds in data.get('results', []):
        dandiset_names[ds['dandiset_id']] = ds.get('dandiset_name') or ''
        relations = [r for r in ds.get('paper_relations') or [] if r.get('doi')]
        for relation in relations:
            if relation.get('name'):
                paper_titles.setdefault(relation['doi'], relation['name'])
            origins[(ds['dandiset_id'], relation['doi'].lower())] = \
                relation.get('relation') or 'unknown'
        described = next((r for r in relations
                          if r.get('relation') == 'dcite:IsDescribedBy'), None)
        if described or relations:
            declared[ds['dandiset_id']] = (described or relations[0])['doi']
    return paper_titles, dandiset_names, declared, origins


def merge_by_pair(inputs: list[str]) -> dict:
    """
    One row per (paper, dataset), which is the unit the classifier answers about.

    A paper reusing several datasets stands in a separate relationship to each,
    supported by its own passage, so each is judged on its own. The direct and
    citing pathways can both reach the same pair, so their quotes are unioned
    and the fields they each answered are reconciled.

    Only the fields something downstream reads are kept: what review shows, and
    what a round is cut on.
    """
    loaded = [json.loads(Path(p).read_text()) for p in inputs]
    known = {r['citing_doi'] for d in loaded for r in d['classifications']}

    merged: dict = {}
    for data in loaded:
        for r in data['classifications']:
            if r.get('classification') != 'REUSE':
                continue
            doi = canonical_doi(r['citing_doi'], known)
            dandiset = r.get('dandiset_id') or ''
            row = merged.setdefault((doi, dandiset), {
                'doi': doi, 'dandiset': dandiset,
                'title': '', 'reasoning': '', 'quotes': [], 'source_quotes': [],
                'pathways': set(), 'same_lab_values': set(),
                'reused_neurophysiology': False,
                'reused_modalities': [], 'archives': [], 'reuse_types': [],
            })
            row['pathways'].add(r['mode'])
            if r.get('title') and len(r['title']) > len(row['title']):
                row['title'] = r['title'].strip()
            if len(r.get('reasoning') or '') > len(row['reasoning']):
                row['reasoning'] = r.get('reasoning') or ''
            for field, into in (('evidence_quotes', 'quotes'),
                                ('source_quotes', 'source_quotes')):
                for q in r.get(field) or []:
                    rec = {'q': q['quote'], 'tier': q['match_type']}
                    if rec not in row[into]:
                        row[into].append(rec)

            if r.get('same_lab') is not None:
                row['same_lab_values'].add(bool(r['same_lab']))
            row['reused_neurophysiology'] = (row['reused_neurophysiology']
                                             or bool(r.get('reused_neurophysiology')))
            for value in r.get('reused_modalities') or []:
                if value not in row['reused_modalities']:
                    row['reused_modalities'].append(value)
            for value, field in ((r.get('source_archive'), 'archives'),
                                 (r.get('reuse_type'), 'reuse_types')):
                if value and value not in row[field]:
                    row[field].append(value)
    return merged


def finalize(row: dict) -> dict:
    """
    Settle the fields that only make sense once every record for a pair is in.

    A pair both pathways reached is still one pair asking one question, so it is
    reviewed once. It goes to the direct queue, the only one that can answer
    that these authors deposited the dataset rather than reusing it.
    """
    pathways = row.pop('pathways')
    row['pathway'] = 'direct' if 'direct' in pathways else 'indirect'

    values = row.pop('same_lab_values')
    row['same_lab'] = (True if values == {True} else
                       False if values == {False} else
                       'mixed' if values == {True, False} else None)

    # Why this pair counts as DANDI data, which is a different question from
    # whether DANDI hosts the modality it reused. A paper naming the dandiset
    # says so outright; otherwise it is the archive the classifier read off the
    # text, or failing that a passage that mentions DANDI and is really in the
    # paper. The passages themselves are not carried further: they answer where
    # the data came from, which review does not ask.
    source_quotes = row.pop('source_quotes')
    if row['pathway'] == 'direct':
        row['dandi_reason'] = 'names a DANDI identifier in its text'
    elif 'DANDI Archive' in row['archives']:
        row['dandi_reason'] = 'names DANDI Archive as the source'
    elif any(q['tier'] != 'not_found' and DANDI_MARKER.search(q['q'])
             for q in source_quotes + row['quotes']):
        row['dandi_reason'] = 'quotes DANDI in the text'
    else:
        row['dandi_reason'] = None
    return row


def attach_missing_titles(rows: list[dict], direct_results_path: Path) -> None:
    """
    Title the papers the direct pathway found, which its classifications lack.

    A direct pair is built by matching a dandiset identifier in a paper's text,
    and the classification records only the DOI that was matched. Discovery kept
    the title, and a DOI is not what a reviewer recognises a paper by.
    """
    data = json.loads(direct_results_path.read_text())
    titles = {r['doi'].lower(): r['title'] for r in data.get('results', [])
              if r.get('doi') and r.get('title')}
    for row in rows:
        if not row['title']:
            row['title'] = titles.get(row['doi'].lower(), '')


def attach_dandiset_names(rows: list[dict], results_path: Path) -> None:
    """Name the dataset, which both queues show beside its identifier."""
    _, dandiset_names, _, _ = corpus_papers(results_path)
    for row in rows:
        row['dandiset_name'] = dandiset_names.get(row['dandiset'], '')


def attach_cited_papers(rows: list[dict], results_path: Path) -> None:
    """
    Name the paper each pair was built from, which the indirect queue asks about.

    A classification record says which pair it answered but not which paper the
    classifier was asked about, so the pairing has to come back from discovery.
    A dandiset can declare several papers, and the one this citing work actually
    cited is the one a reviewer has to read.

    Discovery does not always hold the pairing. Where it does not, the dataset's
    own declared paper is what a reviewer opens instead, and `cited_role` says
    which of the two is on offer.

    `cited_source` says how the dataset came to name that paper, which is the
    other thing a reviewer has to know before reading it. A cited DOI the corpus
    no longer holds is `unknown` rather than nothing: saying nothing would read
    as vouching for it.
    """
    primaries = primary_paper_index(results_path)
    paper_titles, _, declared, origins = corpus_papers(results_path)
    for row in rows:
        cited = primaries.get((row['doi'].lower(), row['dandiset']), '')
        row['cited_role'] = 'Cited' if cited else 'Dataset paper'
        cited = cited or declared.get(row['dandiset'], '')
        row['cited_doi'] = cited
        row['cited_title'] = paper_titles.get(cited, '')
        row['cited_source'] = (
            origins.get((row['dandiset'], cited.lower()), 'unknown')
            if cited else '')


def build_candidates(inputs: list[str], results_path: Path,
                     direct_results_path: Path) -> list[dict]:
    """Every REUSE pair, carrying everything review and assignment need."""
    rows = [finalize(row) for row in merge_by_pair(inputs).values()]
    rows.sort(key=lambda r: (r['doi'], r['dandiset']))
    attach_dandiset_names(rows, results_path)
    attach_missing_titles(rows, direct_results_path)
    attach_cited_papers(rows, results_path)
    # The direct queue shows no cited paper, so carrying one would only invite a
    # reviewer to read a paper the question is not about.
    for row in rows:
        if row['pathway'] == 'direct':
            row['cited_doi'] = row['cited_title'] = ''
            row['cited_role'] = row['cited_source'] = ''
    return rows


def input_stamps(inputs: list[str]) -> list[dict]:
    """
    Which run of the pipeline each input came out of.

    A candidate list is a claim about a corpus made by a particular model
    answering a particular question, and re-running with either one changed
    produces different claims about the same papers. Recording the model, the
    prompt version and the labels that run could reach is what makes two
    candidate lists comparable, and what says which of them a set of answers was
    checking.

    The labels are the ones the run actually produced rather than a vocabulary
    written down here, so they cannot drift from what the file holds. They are
    also what separates the two pathways: the citing one reaches MENTION and the
    direct one reaches PRIMARY.
    """
    stamps = []
    for path in inputs:
        raw = Path(path).read_bytes()
        classifications = json.loads(raw)['classifications']
        stamps.append({
            'path': str(Path(path)),
            'sha256': hashlib.sha256(raw).hexdigest(),
            'classifications': len(classifications),
            'reuse': sum(1 for c in classifications
                         if c.get('classification') == 'REUSE'),
            'models': distinct(classifications, 'model'),
            'prompt_versions': distinct(classifications, 'prompt_version'),
            'labels': distinct(classifications, 'classification'),
        })
    return stamps


def distinct(classifications: list[dict], field: str) -> list:
    """The values a run produced for one field, in a stable order."""
    return sorted({c[field] for c in classifications if c.get(field) is not None})


def write_stamped(path: Path, body: dict) -> bool:
    """
    Write a generated file under a timestamp, unless it would say the same
    thing again.

    Returns whether the file changed. Rebuilding is how you check whether the
    pipeline moved, so it has to be cheap to do; stamping a new time on an
    otherwise identical file would make every check look like a change.
    """
    if path.exists():
        current = json.loads(path.read_text())
        if {k: v for k, v in current.items() if k != 'generated_at'} == body:
            return False

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        'generated_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        **body,
    }, indent=2, ensure_ascii=False) + '\n')
    return True


def write_candidates(pairs: list[dict], inputs: list[str], path: Path) -> bool:
    """The candidate list, and whether it changed."""
    return write_stamped(path, {'inputs': input_stamps(inputs), 'pairs': pairs})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('-i', '--input', action='append', required=True,
                        help='Classification JSON; repeat to merge both pathways.')
    parser.add_argument('--results-file', default=str(RESULTS_FILE),
                        help='Discovery corpus, for the paper each pair was built from.')
    parser.add_argument('--direct-results-file', default=str(DIRECT_RESULTS_FILE),
                        help='Direct discovery output, for the titles it kept.')
    args = parser.parse_args()

    pairs = build_candidates(args.input, Path(args.results_file),
                             Path(args.direct_results_file))
    changed = write_candidates(pairs, args.input, CANDIDATES_FILE)

    indirect = sum(1 for p in pairs if p['pathway'] == 'indirect')
    print(f'{len(pairs)} candidate pairs '
          f'({indirect} indirect, {len(pairs) - indirect} direct) '
          f'across {len({p["doi"] for p in pairs})} papers')
    print(f'{"Wrote" if changed else "Unchanged:"} {CANDIDATES_FILE}')


if __name__ == '__main__':
    main()
