#!/usr/bin/env python3
"""
Re-classify the REUSE rows whose modality list contains 'other'.

'other' was absorbing two unrelated things: structural images of tissue, which
are real data that DANDI hosts, and things that are not data at all, such as a
reused stimulus list or a metadata record. Adding 'imaging' to the vocabulary
separates them, but only for pairs classified after the change.

The batch runner cannot do this job. Adding the category bumped PROMPT_VERSION,
which marks every previously classified pair stale, so a normal run would redo
the whole corpus. This re-runs exactly the affected pairs and merges them back.

That leaves the corpus at two prompt versions, which is a hazard the pipeline
otherwise guards against. It is acceptable here only because the change is
confined to the modality vocabulary: no row outside this set could have answered
'imaging', because 'imaging' did not exist and those rows did not say 'other'.
Every rewritten row records the version it was produced under, so the mixture is
visible rather than silent.

Usage:
    python scripts/reclassify_other_modality.py --dry-run
    python scripts/reclassify_other_modality.py --workers 8
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from paper_text_fetcher import TextCache  # noqa: E402
from src.shared.classify_fulltext_reuse import (  # noqa: E402
    classify_paper_reuse, MODE_CITING, MODE_DIRECT, PROMPT_VERSION)
from src.shared.run_fulltext_classification import cache_path  # noqa: E402

TARGETS = (
    ('output/fulltext_classifications.json', '.fulltext_classification_cache', MODE_CITING),
    ('output/fulltext_direct_openalex.json', '.fulltext_direct_cache', MODE_DIRECT),
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--workers', type=int, default=8)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()

    papers = TextCache(REPO / '.paper_cache')
    corpus = json.loads((REPO / 'output/all_dandiset_papers_refreshed.json').read_text())
    meta = {r['dandiset_id']: r for r in corpus['results']}

    for out_path, cache_dir, mode in TARGETS:
        data = json.loads((REPO / out_path).read_text())
        rows = data['classifications']
        targets = [r for r in rows
                   if r['classification'] == 'REUSE'
                   and 'other' in (r.get('reused_modalities') or [])]
        print(f"\n{out_path}: {len(targets)} rows list 'other'", file=sys.stderr, flush=True)
        if args.dry_run or not targets:
            continue

        def redo(rec: dict) -> tuple[dict, dict | None]:
            got = papers.get(rec['citing_doi'])
            if not got:
                return rec, None
            ds = meta.get(rec['dandiset_id'], {})
            rels = ds.get('paper_relations') or []
            try:
                fresh = classify_paper_reuse(
                    got[0],
                    dataset_id=rec['dandiset_id'],
                    dataset_name=ds.get('dandiset_name', ''),
                    primary_paper_doi=(rels[0].get('doi') if rels else ''),
                    paper_doi=rec['citing_doi'],
                    mode=mode)
            except Exception as exc:  # keep the old row rather than lose it
                print(f"  {rec['citing_doi']}: {type(exc).__name__}", file=sys.stderr)
                return rec, None
            # A failed or downgraded answer must not replace a good one.
            if fresh.get('classification') != 'REUSE':
                print(f"  {rec['citing_doi']}: came back {fresh.get('classification')}, keeping REUSE",
                      file=sys.stderr)
                return rec, None
            for field in ('citing_doi', 'dandiset_id', 'title'):
                if field in rec:
                    fresh[field] = rec[field]
            return rec, fresh

        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            results = list(pool.map(redo, targets))

        by_key = {}
        before, after = Counter(), Counter()
        for old, fresh in results:
            before['+'.join(sorted(old.get('reused_modalities') or []))] += 1
            if fresh is None:
                after['(kept old)'] += 1
                continue
            key = (old['citing_doi'], old['dandiset_id'])
            by_key[key] = fresh
            after['+'.join(sorted(fresh.get('reused_modalities') or []))] += 1
            cache_path(Path(cache_dir), old['citing_doi'], old['dandiset_id']).write_text(
                json.dumps(fresh, indent=2))

        for i, r in enumerate(rows):
            fresh = by_key.get((r['citing_doi'], r['dandiset_id']))
            if fresh:
                rows[i] = fresh
        data['classifications'] = rows
        (REPO / out_path).write_text(json.dumps(data, indent=2))

        print(f"  before: {dict(before.most_common())}", file=sys.stderr)
        print(f"  after : {dict(after.most_common())}", file=sys.stderr)
        cost = sum((f.get('usage') or {}).get('cost') or 0 for _, f in results if f)
        print(f"  cost  : ${cost:.2f}   rows rewritten at prompt_version {PROMPT_VERSION}",
              file=sys.stderr)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
