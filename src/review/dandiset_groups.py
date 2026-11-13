"""
Dandisets counted as one dataset when measuring how often the classifier was right.

Some datasets are released as several dandisets that share a primary paper or a
source, and a paper reusing one of them is proposed for all of them. Reviewers
call the one it used Reuse and the rest Neither, so counting pair by pair turns
one correct call into one confirmation and several rejections. The groups in
config/dandiset_groups.json say which dandisets those are, and merge_reviews
counts a paper's pairs with the dandisets of one group as a single unit.
"""

from __future__ import annotations

import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
GROUPS_FILE = REPO / 'config' / 'dandiset_groups.json'


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
