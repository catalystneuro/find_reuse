#!/usr/bin/env python3
"""Fetch text and classify citing papers for CRCNS datasets.

Mirrors fetch_and_classify_new.py but uses CRCNS-specific paths.
"""

import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, ".")
from dandi_primary_papers import fetch_citing_paper_texts

DATASETS_FILE = "output/crcns/datasets.json"
CLASSIFICATIONS_FILE = "output/crcns/classifications.json"
DIRECT_REFS_FILE = "output/crcns/direct_refs.json"
DIRECT_CLASSIFICATIONS_FILE = "output/crcns/direct_ref_classifications.json"
CONTEXTS_FILE = "output/crcns/citation_contexts.json"
ARCHIVE_NAME = "CRCNS"

# Step 1: Fetch text for citing papers
print("=== Step 1: Fetch text ===", file=sys.stderr, flush=True)

with open(DATASETS_FILE) as f:
    data = json.load(f)

results = data["results"]
results = fetch_citing_paper_texts(
    results,
    max_citing_papers_per_dandiset=999,
    show_progress=True,
    cache_dir=".paper_cache",
    verbose=False,
)

data["results"] = results
with open(DATASETS_FILE, "w") as f:
    json.dump(data, f, indent=2)
print("Saved after text fetch", file=sys.stderr, flush=True)

# Step 2: Extract citation contexts
print("\n=== Step 2: Extract contexts ===", file=sys.stderr, flush=True)
subprocess.run([
    "python3", "extract_citation_contexts.py",
    "--results-file", DATASETS_FILE,
    "--cache-dir", ".paper_cache",
    "-o", CONTEXTS_FILE,
], check=True)

# Step 2b: Pre-populate classification cache from direct references
print("\n=== Step 2b: Pre-populate cache from direct refs ===", file=sys.stderr, flush=True)
if Path(DIRECT_CLASSIFICATIONS_FILE).exists():
    with open(DIRECT_CLASSIFICATIONS_FILE) as f:
        direct_cls = json.load(f)
    from classify_citing_papers import get_cache_path, CLASSIFICATION_CACHE_DIR
    CLASSIFICATION_CACHE_DIR.mkdir(exist_ok=True)
    n_cached = 0
    for c in direct_cls.get("classifications", []):
        cited_doi = c.get("cited_doi", "")
        citing_doi = c.get("citing_doi", "")
        if not cited_doi or not citing_doi:
            continue
        cache_path = get_cache_path(citing_doi, cited_doi)
        if not cache_path.exists():
            with open(cache_path, "w") as f:
                json.dump(c, f, indent=2)
            n_cached += 1
    print(f"Pre-cached {n_cached} direct ref classifications", file=sys.stderr)

# Step 3: Classify
print("\n=== Step 3: Classify ===", file=sys.stderr, flush=True)
cite_cls_file = "output/crcns/classifications_cite.json"
subprocess.run([
    "python3", "classify_citing_papers.py",
    "--results-file", DATASETS_FILE,
    "--cache-dir", ".paper_cache",
    "-o", cite_cls_file,
    "--fetch-text", "--workers", "16",
], check=True)

# Step 4: Convert direct references to classifications
print("\n=== Step 4: Convert direct refs ===", file=sys.stderr, flush=True)
if Path(DIRECT_REFS_FILE).exists():
    subprocess.run([
        "python3", "convert_refs_to_classifications.py",
        "-i", DIRECT_REFS_FILE,
        "-o", DIRECT_CLASSIFICATIONS_FILE,
    ], check=True)

# Step 5: Merge citation + direct reference classifications
print("\n=== Step 5: Merge ===", file=sys.stderr, flush=True)
with open(cite_cls_file) as f:
    cite_data = json.load(f)

for c in cite_data["classifications"]:
    c["source_type"] = "citation"

if Path(DIRECT_CLASSIFICATIONS_FILE).exists():
    with open(DIRECT_CLASSIFICATIONS_FILE) as f:
        direct_data = json.load(f)

    for c in direct_data["classifications"]:
        c["source_type"] = "direct_reference"
        if c["classification"] == "REUSE" and not c.get("source_archive"):
            c["source_archive"] = ARCHIVE_NAME

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
                ex["source_archive"] = ARCHIVE_NAME
                upgraded += 1
            elif c["classification"] == ex["classification"]:
                ex["source_type"] = "both"
        else:
            existing[key] = c
            added += 1

    merged = list(existing.values())
    print(f"Merged: {len(merged)} (added={added}, upgraded={upgraded})", file=sys.stderr)
else:
    merged = cite_data["classifications"]
    print(f"No direct refs to merge, {len(merged)} classifications", file=sys.stderr)

counts = Counter(c["classification"] for c in merged)
cite_data["classifications"] = merged
cite_data["metadata"]["total_pairs"] = len(merged)
cite_data["metadata"]["classification_counts"] = dict(counts)

with open(CLASSIFICATIONS_FILE, "w") as f:
    json.dump(cite_data, f, indent=2)

print(f"Classifications: {dict(counts)}", file=sys.stderr)

# Step 6: Normalize source_archive
print("\n=== Step 6: Normalize source_archive ===", file=sys.stderr, flush=True)
subprocess.run([
    "python3", "classify_source_archive.py",
    "--input", CLASSIFICATIONS_FILE,
    "--resolve-unclear", "--write",
], check=False)  # may fail if no unclear entries, that's fine

# Step 7: Set source_archive=CRCNS for papers that cite CRCNS datasets directly
print("\n=== Step 7: Assign CRCNS archive for direct dataset citations ===", file=sys.stderr, flush=True)
import re
with open(CLASSIFICATIONS_FILE) as f:
    cls_data = json.load(f)

n_assigned = 0
for c in cls_data["classifications"]:
    if c["classification"] != "REUSE":
        continue
    if c.get("source_archive") and c["source_archive"] != "unclear":
        continue
    # Check if paper text contains CRCNS DOI or URL
    doi = c["citing_doi"]
    safe_doi = doi.replace("/", "_")
    cache_file = Path(f".paper_cache/{safe_doi}.json")
    if cache_file.exists():
        with open(cache_file) as f2:
            paper = json.load(f2)
        text = paper.get("text", "")
        if re.search(r"10\.6080/|crcns\.org/data-sets/|\bCRCNS\b", text):
            c["source_archive"] = "CRCNS"
            n_assigned += 1

with open(CLASSIFICATIONS_FILE, "w") as f:
    json.dump(cls_data, f, indent=2)
print(f"Assigned CRCNS archive to {n_assigned} REUSE entries based on direct citations", file=sys.stderr)

# Step 8: Remove DANDI ID leakage (6-digit IDs from DANDI pattern matching)
print("\n=== Step 8: Remove DANDI ID leakage ===", file=sys.stderr, flush=True)
with open(CLASSIFICATIONS_FILE) as f:
    cls_data = json.load(f)
before = len(cls_data["classifications"])
cls_data["classifications"] = [c for c in cls_data["classifications"]
                                if not re.match(r"^\d{6}$", c.get("dandiset_id", ""))]
after = len(cls_data["classifications"])
with open(CLASSIFICATIONS_FILE, "w") as f:
    json.dump(cls_data, f, indent=2)
print(f"Removed {before - after} DANDI-leaked entries", file=sys.stderr)

# Step 9: Deduplicate preprints
print("\n=== Step 9: Deduplicate preprints ===", file=sys.stderr, flush=True)
from deduplicate_preprints import deduplicate, is_preprint_doi, normalize_title

with open(CLASSIFICATIONS_FILE) as f:
    cls_data = json.load(f)

before = len(cls_data["classifications"])
cls_data["classifications"] = deduplicate(cls_data["classifications"])
after = len(cls_data["classifications"])
cls_data["count"] = after

with open(CLASSIFICATIONS_FILE, "w") as f:
    json.dump(cls_data, f, indent=2)
print(f"Deduplicated: {before} -> {after} ({before - after} removed)", file=sys.stderr)

# Final counts
counts = Counter(c["classification"] for c in cls_data["classifications"])
print(f"\nFinal: {dict(counts)}", file=sys.stderr)
print("\n=== DONE ===", file=sys.stderr, flush=True)
