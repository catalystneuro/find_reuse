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
import subprocess
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

from src.shared.run_fulltext_classification import primary_paper_index

REPO = Path(__file__).resolve().parents[2]
CANDIDATES_FILE = REPO / 'reviews/reuse_candidates.json'
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
def corpus_papers(results_path: Path) -> tuple[dict, dict, dict]:
    """
    Paper titles, dataset names, and the paper each dataset declares.

    primary_paper_index says which paper a pair was built from but not what it
    is called, and a reviewer choosing what to open needs the title as much as
    the identifier. The declared paper stands in for pairs the citing pathway
    never saw; a dandiset naming several, the one asserting it describes the
    data is the one to read.
    """
    data = json.loads(results_path.read_text())
    paper_titles, dandiset_names, declared = {}, {}, {}
    for ds in data.get('results', []):
        dandiset_names[ds['dandiset_id']] = ds.get('dandiset_name') or ''
        relations = [r for r in ds.get('paper_relations') or [] if r.get('doi')]
        for relation in relations:
            if relation.get('name'):
                paper_titles.setdefault(relation['doi'], relation['name'])
        described = next((r for r in relations
                          if r.get('relation') == 'dcite:IsDescribedBy'), None)
        if described or relations:
            declared[ds['dandiset_id']] = (described or relations[0])['doi']
    return paper_titles, dandiset_names, declared


def merge_by_pair(inputs: list[str]) -> dict:
    """
    One row per (paper, dataset), which is the unit the classifier answers about.

    A paper reusing several datasets stands in a separate relationship to each,
    supported by its own passage, so each is judged on its own. The direct and
    citing pathways can both reach the same pair, so their quotes are unioned
    and the fields they each answered are reconciled.
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
                'key': f'{doi}\t{dandiset}', 'doi': doi, 'dandiset': dandiset,
                'title': '', 'reasoning': '', 'quotes': [],
                'pathways': set(), 'confidence': 0, 'same_lab_values': set(),
                'reused_neurophysiology': False, 'reused_dandi_hosted': False,
                'reused_modalities': [], 'archives': [], 'reuse_types': [],
            })
            row['pathways'].add(r['mode'])
            if r.get('title') and len(r['title']) > len(row['title']):
                row['title'] = r['title'].strip()
            if len(r.get('reasoning') or '') > len(row['reasoning']):
                row['reasoning'] = r.get('reasoning') or ''
            for q in r.get('evidence_quotes', []):
                rec = {'q': q['quote'], 'tier': q['match_type']}
                if rec not in row['quotes']:
                    row['quotes'].append(rec)

            row['confidence'] = max(row['confidence'], r.get('confidence') or 0)
            if r.get('same_lab') is not None:
                row['same_lab_values'].add(bool(r['same_lab']))
            for field in ('reused_neurophysiology', 'reused_dandi_hosted'):
                row[field] = row[field] or bool(r.get(field))
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

    # A claim resting only on passages that are not in the paper is a claim with
    # nothing behind it, which is a reason to put a round in front of a person
    # sooner rather than later.
    row['unverifiable_quotes'] = (bool(row['quotes'])
                                  and all(q['tier'] == 'not_found'
                                          for q in row['quotes']))
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
    _, dandiset_names, _ = corpus_papers(results_path)
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
    """
    primaries = primary_paper_index(results_path)
    paper_titles, _, declared = corpus_papers(results_path)
    for row in rows:
        cited = primaries.get((row['doi'].lower(), row['dandiset']), '')
        row['cited_role'] = 'Cited' if cited else 'Dataset paper'
        cited = cited or declared.get(row['dandiset'], '')
        row['cited_doi'] = cited
        row['cited_title'] = paper_titles.get(cited, '')


def build_candidates(inputs: list[str], results_path: Path,
                     direct_results_path: Path) -> list[dict]:
    """Every REUSE pair, carrying everything review and assignment need."""
    rows = [finalize(row) for row in merge_by_pair(inputs).values()]
    rows.sort(key=lambda r: r['key'])
    attach_dandiset_names(rows, results_path)
    attach_missing_titles(rows, direct_results_path)
    attach_cited_papers(rows, results_path)
    # The direct queue shows no cited paper, so carrying one would only invite a
    # reviewer to read a paper the question is not about.
    for row in rows:
        if row['pathway'] == 'direct':
            row['cited_doi'] = row['cited_title'] = row['cited_role'] = ''
    return rows


def git_sha() -> str:
    result = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=REPO,
                            capture_output=True, text=True, check=False)
    return result.stdout.strip()


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


def write_candidates(pairs: list[dict], inputs: list[str], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        'generated_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'git_sha': git_sha(),
        'inputs': input_stamps(inputs),
        'pairs': pairs,
    }, indent=2, ensure_ascii=False) + '\n')


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
    write_candidates(pairs, args.input, CANDIDATES_FILE)

    indirect = sum(1 for p in pairs if p['pathway'] == 'indirect')
    print(f'{len(pairs)} candidate pairs '
          f'({indirect} indirect, {len(pairs) - indirect} direct) '
          f'across {len({p["doi"] for p in pairs})} papers')
    print(f'Wrote {CANDIDATES_FILE}')


if __name__ == '__main__':
    main()
