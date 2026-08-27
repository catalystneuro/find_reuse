"""
Who reviews, and what their files are called.

The registry exists so a round cannot be dealt to somebody who does not exist:
a name that is not in it is a typo, and a typo that silently creates a reviewer
takes pairs out of circulation and gives them to nobody.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
REVIEWS_DIR = REPO / 'reviews'
REVIEWERS_FILE = REVIEWS_DIR / 'reviewers.json'
ASSIGNMENTS_DIR = REVIEWS_DIR / 'assignments'


def reviewer_slug(reviewer: str) -> str:
    return re.sub(r'[^a-z0-9]+', '-', reviewer.lower()).strip('-')


def load_reviewers(path: Path) -> list[dict]:
    """The registered reviewers, in the order the file lists them."""
    return json.loads(path.read_text())


def select_reviewers(registry: list[dict], names: str | None) -> list[dict]:
    """
    The reviewers a round is dealt to: all of them, or the named subset.

    Registry order is kept, so which reviewer breaks a tie in the deal is a
    property of the file rather than of how the argument was typed.
    """
    if not names:
        return registry
    wanted = [n.strip() for n in names.split(',') if n.strip()]
    known = {r['name'] for r in registry}
    unknown = [n for n in wanted if n not in known]
    if unknown:
        raise SystemExit(
            f'Unknown reviewer{"s" if len(unknown) > 1 else ""}: '
            f'{", ".join(unknown)}. Registered: {", ".join(sorted(known))}. '
            f'Add them to {REVIEWERS_FILE} first.')
    return [r for r in registry if r['name'] in wanted]


def assignment_path(reviewer: str, pathway: str,
                    assignments_dir: Path = ASSIGNMENTS_DIR) -> Path:
    return assignments_dir / f'{reviewer_slug(reviewer)}.{pathway}.json'


def answers_path(reviewer: str, reviews_dir: Path = REVIEWS_DIR) -> Path:
    return reviews_dir / f'{reviewer_slug(reviewer)}.json'
