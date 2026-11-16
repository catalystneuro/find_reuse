"""
Dandisets counted as one dataset when measuring how often the classifier was right.

Some datasets are released as several dandisets that share a primary paper or a
source, and a paper reusing one of them is proposed for all of them. Reviewers
call the one it used Reuse and the rest Neither, so counting pair by pair turns
one correct call into one confirmation and several rejections. The groups in
config/dandiset_groups.json say which dandisets those are, and merge_reviews
counts a paper's pairs with the dandisets of one group as a single unit.

Dandisets that name the same primary paper are found from the corpus, so the
file only has to list the ones nothing in the metadata ties together: a
benchmark copy of a dataset, a release that cites a different paper of the same
project. related_dandisets() joins the two.
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
GROUPS_FILE = REPO / 'config' / 'dandiset_groups.json'
CORPUS = REPO / 'output' / 'all_dandiset_papers_refreshed.json'


def load_groups(path: Path = GROUPS_FILE) -> dict[str, str]:
    """
    The group each grouped dandiset belongs to, keyed by dandiset.

    A dandiset listed in two groups is refused rather than assigned to either:
    it would join two groups' pairs into one unit or split them depending on
    which was read last, and neither is something anybody decided.
    """
    config = json.loads(path.read_text())
    group_of: dict[str, str] = {}
    for name, group in config.items():
        if name.startswith('_'):
            continue
        for dandiset in group['dandisets']:
            if dandiset in group_of:
                raise SystemExit(
                    f'{dandiset} is in both the {group_of[dandiset]} and the '
                    f'{name} groups in {path}. A dandiset can belong to one '
                    f'group only.')
            group_of[dandiset] = name
    return group_of


def shared_paper_sets(corpus_path: Path = CORPUS) -> dict[str, set[str]]:
    """The dandisets naming each primary paper that more than one dandiset names."""
    named: dict[str, set[str]] = defaultdict(set)
    if not corpus_path.exists():
        # A checkout without the pipeline's outputs, a reviewer's for one, still
        # has to merge reviews. It gets the hand-listed groups, and says so.
        print(f'{corpus_path} is not here, so dandisets are grouped from '
              f'{GROUPS_FILE.name} alone, without those that share a primary paper.',
              file=sys.stderr)
        return {}
    for dandiset in json.loads(corpus_path.read_text())['results']:
        for paper in dandiset.get('paper_relations', []):
            if paper.get('doi'):
                named[paper['doi'].lower()].add(dandiset['dandiset_id'])
    return {doi: ids for doi, ids in named.items() if len(ids) > 1}


def components(corpus_path: Path = CORPUS, groups_path: Path = GROUPS_FILE) -> list[set[str]]:
    """
    Dandisets that count as one dataset: those sharing a primary paper, joined
    with the hand-listed groups, and joined again wherever the two overlap.
    """
    sets = list(shared_paper_sets(corpus_path).values())
    by_group: dict[str, set[str]] = defaultdict(set)
    for dandiset, name in load_groups(groups_path).items():
        by_group[name].add(dandiset)
    sets += by_group.values()

    parent: dict[str, str] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for ids in sets:
        first, *rest = sorted(ids)
        for other in rest:
            parent[find(other)] = find(first)
    out: dict[str, set[str]] = defaultdict(set)
    for dandiset in list(parent):
        out[find(dandiset)].add(dandiset)
    return sorted(out.values(), key=min)


def related_dandisets(corpus_path: Path = CORPUS,
                      groups_path: Path = GROUPS_FILE) -> dict[str, set[str]]:
    """For each grouped dandiset, the others it is counted with."""
    return {d: ids - {d} for ids in components(corpus_path, groups_path) for d in ids}


def units(corpus_path: Path = CORPUS, groups_path: Path = GROUPS_FILE) -> dict[str, str]:
    """
    The unit each grouped dandiset is counted in, keyed by dandiset.

    A unit that holds a hand-listed group carries that group's name, so the
    names the analysis and the manuscript use stay put. One found only from a
    shared primary paper is named after its lowest dandiset number. A shared
    paper that would join two hand-listed groups is refused: the file says
    they are different datasets, and the metadata says they are one.
    """
    listed = load_groups(groups_path)
    out: dict[str, str] = {}
    for ids in components(corpus_path, groups_path):
        names = sorted({listed[d] for d in ids if d in listed})
        if len(names) > 1:
            raise SystemExit(
                f'The {" and ".join(names)} groups in {groups_path} are joined by a '
                f'primary paper their dandisets share ({", ".join(sorted(ids))}). '
                f'Merge the groups or correct the primary paper.')
        name = names[0] if names else f'shared_paper_{min(ids)}'
        for dandiset in ids:
            out[dandiset] = name
    return out
