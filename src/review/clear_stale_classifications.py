#!/usr/bin/env python3
"""
Delete the cached classifications that no longer answer a pair in the corpus.

A cached classification is keyed by citing paper and dandiset, not by the paper
the pair was built from. Rediscovery leaves two kinds of entry stale:

  * a pair it now reaches through a different primary paper, whose cached
    answer was given with the old paper in its prompt;
  * a pair it no longer reaches at all, whose cached answer would still be
    carried into the output by a run that reads the cache rather than the
    corpus.

Comparing the corpus from before rediscovery with the one after finds both, and
deleting their entries leaves the cache holding only answers to the corpus's own
questions.

Usage:
    python -m src.review.clear_stale_classifications \
        --old output/all_dandiset_papers_refreshed.before.json \
        --new output/all_dandiset_papers_refreshed.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.shared.run_fulltext_classification import REPO, cache_path


def cited_papers(results: list[dict]) -> dict[tuple[str, str], str]:
    """The paper each (citing DOI, dandiset) pair was built from, lowercased."""
    return {(paper['doi'], record['dandiset_id']): paper['cited_paper_doi'].lower()
            for record in results for paper in record.get('citing_papers') or []
            if paper.get('doi')}


def stale_pairs(old: list[dict], new: list[dict]) -> list[tuple[str, str]]:
    """The old corpus's pairs that the new one dropped or built from a different paper."""
    before, after = cited_papers(old), cited_papers(new)
    return sorted(pair for pair in before if after.get(pair) != before[pair])


def delete_cached(pairs: list[tuple[str, str]], cache_dir: Path) -> list[Path]:
    """Delete each pair's cached classification, returning the files that held one."""
    deleted = [path for path in (cache_path(cache_dir, doi, dandiset)
                                 for doi, dandiset in pairs)
               if path.exists()]
    for path in deleted:
        path.unlink()
    return deleted


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--old', type=Path, required=True,
                        help='The corpus as it was before rediscovery.')
    parser.add_argument('--new', type=Path, required=True,
                        help='The corpus rediscovery wrote.')
    parser.add_argument('--cache-dir', type=Path,
                        default=REPO / '.fulltext_classification_cache')
    args = parser.parse_args()

    pairs = stale_pairs(json.loads(args.old.read_text())['results'],
                        json.loads(args.new.read_text())['results'])
    deleted = delete_cached(pairs, args.cache_dir)
    for path in deleted:
        print(f'deleted {path.name}')
    print(f'{len(pairs)} pairs dropped or citing a different paper; '
          f'{len(deleted)} cached classifications deleted')


if __name__ == '__main__':
    main()
