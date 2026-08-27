"""
Who reviews, and what their files are called.

The registry exists so a round cannot be dealt to somebody who does not exist:
a username that is not in it is a typo, and a typo that silently creates a
reviewer takes pairs out of circulation and gives them to nobody.

A reviewer has two names because they do two different jobs. The `username`
names their files and joins the registry to their assignments and their reviews,
so it has to be stable and usable as a filename; a GitHub handle is the obvious
choice and needs no thought. The `name` is who that is, for whoever opens this
file to decide who should take a round. Nothing reads it, which is the point of
a file kept by hand rather than generated.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
# Candidates in, confirmed reuse out; everything the checking needs lives here.
REUSE_CONFIRMATION_DIR = REPO / 'reuse_confirmation'
REVIEWERS_FILE = REUSE_CONFIRMATION_DIR / 'reviewers.json'

# What a username may be, which is what a filename may be: a GitHub handle
# already qualifies, so registering one takes no thought.
USERNAME = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]*$')


def load_reviewers(path: Path) -> list[dict]:
    """
    The registered reviewers, in the order the file lists them.

    A username that could not name a file is refused here rather than quietly
    rewritten into one that can: a reviewer whose username does not survive the
    trip to a filename would come back from it as somebody else, holding none of
    their own work.
    """
    registry = json.loads(path.read_text())
    unusable = [r['username'] for r in registry if not USERNAME.match(r['username'])]
    if unusable:
        raise SystemExit(
            f'Unusable username{"s" if len(unusable) > 1 else ""} in {path}: '
            f'{", ".join(repr(u) for u in unusable)}. A username names files, so '
            f'it may hold only letters, digits, dots, dashes and underscores. '
            f'A GitHub handle already does.')
    return registry


def select_reviewers(registry: list[dict], usernames: str | None) -> list[dict]:
    """
    The reviewers a round is dealt to: all of them, or the named subset.

    Registry order is kept, so which reviewer breaks a tie in the deal is a
    property of the file rather than of how the argument was typed.
    """
    if not usernames:
        return registry
    wanted = [u.strip() for u in usernames.split(',') if u.strip()]
    known = {r['username'] for r in registry}
    unknown = [u for u in wanted if u not in known]
    if unknown:
        raise SystemExit(
            f'Unknown reviewer{"s" if len(unknown) > 1 else ""}: '
            f'{", ".join(unknown)}. Registered: {", ".join(sorted(known))}. '
            f'Add them to {REVIEWERS_FILE} first.')
    return [r for r in registry if r['username'] in wanted]


def reviewer_dir(username: str, base: Path = REUSE_CONFIRMATION_DIR) -> Path:
    """
    One reviewer's own corner of the tree.

    The top of the tree is what everybody shares -- the registry, the candidate
    list -- and a directory below it is one person's. Their username names it,
    which is why a username has to be able to name a file.
    """
    return base / username


def assignment_path(username: str, pathway: str,
                    base: Path = REUSE_CONFIRMATION_DIR) -> Path:
    return reviewer_dir(username, base) / f'assignment-{pathway}.json'


def reviews_path(username: str, base: Path = REUSE_CONFIRMATION_DIR) -> Path:
    return reviewer_dir(username, base) / 'reviews.json'


def assignment_paths(base: Path = REUSE_CONFIRMATION_DIR) -> list[Path]:
    """Every assignment on disk, whosever it is."""
    return sorted(base.glob('*/assignment-*.json'))
