#!/bin/bash
# Build a shareable package of the caches and results.
#
# The code lives in two public repos and does not belong here. What cannot be
# reconstructed cheaply is the data: the paper cache took days to build and
# hits Europe PMC, NCBI, Unpaywall and publishers hard, and the classification
# caches represent about $60 of inference. Anyone rebuilding those from scratch
# repeats both costs.
#
# Archives are separate so a colleague can take only what they need. The paper
# cache is by far the largest and the most worth having.
#
# Usage:
#   bash scripts/build_share_package.sh [OUTPUT_DIR]

set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="${1:-$HOME/Desktop/dandi_reuse_share}"
cd "$REPO"

mkdir -p "$OUT"
echo "Building share package in $OUT"

# Never package credentials. .env holds live API keys.
if [ -f .env ]; then
  echo "  (.env present and deliberately excluded)"
fi

pack() {
  local name="$1"; shift
  local missing=1
  for path in "$@"; do [ -e "$path" ] && missing=0; done
  if [ "$missing" = 1 ]; then
    echo "  skip $name (nothing to pack)"
    return
  fi
  echo "  packing $name ..."
  tar czf "$OUT/$name" --exclude='.DS_Store' --exclude='*.tmp-*' "$@"
  echo "    $(du -h "$OUT/$name" | cut -f1)  $name"
}

# 1. Fetched paper text. The expensive one.
pack paper_cache.tar.gz .paper_cache

# 2. Classification results, keyed per paper. Roughly $60 of inference.
pack classification_caches.tar.gz \
     .fulltext_classification_cache .fulltext_direct_cache .description_doi_cache

# 3. Pipeline outputs: corpora, classifications, search results.
pack results.tar.gz \
     output/fulltext_classifications.json \
     output/fulltext_direct_openalex.json \
     config/primary_paper_overrides.json \
     RUNNING.md \
     output/all_dandiset_papers_refreshed.json \
     output/all_dandiset_papers_discovered.json \
     output/results_dandi_openalex.json \
     output/results_dandi_20260731.json \
     output/rediscovery_report.json

echo
echo "Contents:"
ls -lh "$OUT" | awk 'NR>1 {printf "  %-32s %s\n", $9, $5}'
echo
echo "Total: $(du -sh "$OUT" | cut -f1)"
