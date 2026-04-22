#!/usr/bin/env python3
"""Fetch text and classify new citing papers from the expanded dandiset set.
Run with: nohup python3 fetch_and_classify_new.py > /tmp/fetch_classify.log 2>&1 &
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, ".")
from dandi_primary_papers import fetch_citing_paper_texts

parser = argparse.ArgumentParser(description="Fetch text and classify new citing papers")
parser.add_argument(
    "--max-citing-papers", type=int, default=999,
    help="Maximum citing papers to fetch per dandiset (default: 999)",
)
args = parser.parse_args()

# Step 1: Fetch text for new citing papers
print("=== Step 1: Fetch text ===", file=sys.stderr, flush=True)

with open("output/all_dandiset_papers.json") as f:
    data = json.load(f)

results = data["results"]
results = fetch_citing_paper_texts(
    results,
    max_citing_papers_per_dandiset=args.max_citing_papers,
    show_progress=True,
    cache_dir=".paper_cache",
    verbose=False,
)

data["results"] = results
with open("output/all_dandiset_papers.json", "w") as f:
    json.dump(data, f, indent=2)
print("Saved after text fetch", file=sys.stderr, flush=True)

# Step 2: Extract citation contexts
print("\n=== Step 2: Extract contexts ===", file=sys.stderr, flush=True)
import subprocess
subprocess.run([
    "python3", "extract_citation_contexts.py",
    "--results-file", "output/all_dandiset_papers.json",
    "--cache-dir", ".paper_cache",
    "-o", "output/citation_contexts.json",
], check=True)

# Step 3: Classify
print("\n=== Step 3: Classify ===", file=sys.stderr, flush=True)
subprocess.run([
    "python3", "classify_citing_papers.py",
    "--results-file", "output/all_dandiset_papers.json",
    "--cache-dir", ".paper_cache",
    "-o", "output/all_classifications_new.json",
    "--fetch-text", "--workers", "4",
], check=True)

# Step 4: Merge with direct references
print("\n=== Step 4: Merge ===", file=sys.stderr, flush=True)
with open("output/all_classifications_new.json") as f:
    cite_data = json.load(f)
with open("output/direct_ref_classifications.json") as f:
    direct_data = json.load(f)

from collections import Counter

for c in cite_data["classifications"]:
    c["source_type"] = "citation"
for c in direct_data["classifications"]:
    c["source_type"] = "direct_reference"
    if c["classification"] == "REUSE" and not c.get("source_archive"):
        c["source_archive"] = "DANDI Archive"

existing = {}
for c in cite_data["classifications"]:
    key = (c["citing_doi"], c.get("dandiset_id", ""))
    existing[key] = c

added = upgraded = 0
for c in direct_data["classifications"]:
    key = (c["citing_doi"], c.get("dandiset_id", ""))
    if key in existing:
        ex = existing[key]
        if c["classification"] == "REUSE" and ex["classification"] != "REUSE":
            ex["classification"] = "REUSE"
            ex["source_type"] = "both"
            ex["source_archive"] = "DANDI Archive"
            upgraded += 1
        elif c["classification"] == ex["classification"]:
            ex["source_type"] = "both"
    else:
        existing[key] = c
        added += 1

merged = list(existing.values())
counts = Counter(c["classification"] for c in merged)

cite_data["classifications"] = merged
cite_data["metadata"]["total_pairs"] = len(merged)
cite_data["metadata"]["classification_counts"] = dict(counts)

with open("output/all_classifications.json", "w") as f:
    json.dump(cite_data, f, indent=2)

print(f"\nMerged: {len(merged)} (added={added}, upgraded={upgraded})", file=sys.stderr)
print(f"Classifications: {dict(counts)}", file=sys.stderr)

# Step 5: Normalize source_archive
print("\n=== Step 5: Normalize source_archive ===", file=sys.stderr, flush=True)
subprocess.run([
    "python3", "classify_source_archive.py",
    "--resolve-unclear", "--write",
], check=True)

print("\n=== DONE ===", file=sys.stderr, flush=True)
