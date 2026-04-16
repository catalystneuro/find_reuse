#!/usr/bin/env python3
"""
filter_patchseq_genetic.py — Identify Patch-seq reuse entries that only use genetic data.

Patch-seq datasets on DANDI contain electrophysiology and morphology data, but the
transcriptomic component is typically hosted elsewhere (GEO, CELLxGENE, NeMO). Papers
that only reuse the transcriptomic/gene expression data are not actually using DANDI data.

This script sends each Patch-seq REUSE entry to an LLM to determine which data modality
was used, and reclassifies genetics-only entries as MENTION.

Usage:
    python filter_patchseq_genetic.py             # reclassify
    python filter_patchseq_genetic.py --dry-run    # show what would change
"""

import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

CACHE_DIR = Path(".patchseq_filter_cache")
MODEL = "google/gemini-2.5-flash"

PROMPT = """You are classifying what type of data a paper reused from a Patch-seq dataset.

Patch-seq datasets contain THREE modalities:
1. **Electrophysiology** (voltage traces, firing patterns, intrinsic properties)
2. **Morphology** (cell reconstructions, dendritic/axonal morphology)
3. **Transcriptomics** (gene expression, RNA-seq, cell type clustering)

On DANDI, only the electrophysiology and morphology data are stored. The transcriptomic data
is typically hosted on other platforms (GEO, CELLxGENE, NeMO Archive, Allen Cell Types Database).

Based on the citation context and classification reasoning below, determine which data
modality the citing paper actually used.

Dandiset: {dandiset_id} — {dandiset_name}
Prior classification reasoning: {reasoning}

Citation context excerpts:
{excerpts}

Respond with ONLY a JSON object:
{{
  "modality": "ephys_or_morphology" | "transcriptomics_only" | "both" | "unclear",
  "confidence": 1-10,
  "reasoning": "brief explanation"
}}
"""


def get_api_key():
    key = os.environ.get("OPENROUTER_API_KEY", "")
    if not key:
        from dotenv import load_dotenv
        load_dotenv()
        key = os.environ.get("OPENROUTER_API_KEY", "")
    if not key:
        raise ValueError("OPENROUTER_API_KEY not set")
    return key


def classify_entry(entry, api_key):
    """Classify a single Patch-seq entry."""
    doi = entry["citing_doi"]
    did = entry.get("dandiset_id", "")
    cache_file = CACHE_DIR / f"{doi.replace('/', '_')}_{did}.json"

    if cache_file.exists():
        with open(cache_file) as f:
            return json.load(f)

    excerpts = entry.get("context_excerpts", [])
    excerpt_text = ""
    for ex in excerpts[:5]:
        if isinstance(ex, dict):
            excerpt_text += ex.get("text", "") + "\n\n"
        else:
            excerpt_text += str(ex) + "\n\n"
    if not excerpt_text.strip():
        excerpt_text = "(no excerpts available)"

    prompt = PROMPT.format(
        dandiset_id=did,
        dandiset_name=entry.get("dandiset_name", ""),
        reasoning=entry.get("reasoning", ""),
        excerpts=excerpt_text[:3000],
    )

    try:
        resp = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 300,
                "temperature": 0,
            },
            timeout=30,
        )
        if resp.status_code == 200:
            content = resp.json()["choices"][0]["message"]["content"]
            # Parse JSON from response
            content = content.strip()
            if content.startswith("```"):
                content = content.split("\n", 1)[1].rsplit("```", 1)[0]
            result = json.loads(content)
            result["citing_doi"] = doi
            result["dandiset_id"] = did

            CACHE_DIR.mkdir(exist_ok=True)
            with open(cache_file, "w") as f:
                json.dump(result, f, indent=2)
            return result
    except Exception as e:
        return {"citing_doi": doi, "dandiset_id": did, "modality": "unclear",
                "error": str(e)}

    return {"citing_doi": doi, "dandiset_id": did, "modality": "unclear"}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()

    with open("output/all_classifications.json") as f:
        cls = json.load(f)

    patchseq = [
        c for c in cls["classifications"]
        if c["classification"] == "REUSE"
        and "patch" in (c.get("dandiset_name") or "").lower()
    ]
    print(f"Patch-seq REUSE entries: {len(patchseq)}", file=sys.stderr)

    api_key = get_api_key()

    # Classify in parallel
    results = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(classify_entry, e, api_key): e for e in patchseq}
        for i, future in enumerate(as_completed(futures)):
            result = future.result()
            results.append(result)
            if (i + 1) % 50 == 0:
                print(f"  {i + 1}/{len(patchseq)}", file=sys.stderr)

    # Summarize
    from collections import Counter
    modalities = Counter(r.get("modality") for r in results)
    print(f"\nModality classification:", file=sys.stderr)
    for m, n in modalities.most_common():
        print(f"  {m}: {n}", file=sys.stderr)

    transcriptomics_only = [
        r for r in results
        if r.get("modality") == "transcriptomics_only"
    ]
    print(f"\nTranscriptomics-only (to be reclassified): {len(transcriptomics_only)}", file=sys.stderr)

    if args.dry_run:
        print("\nDry run — no changes made.", file=sys.stderr)
        for r in transcriptomics_only[:10]:
            print(f"  {r['citing_doi']} -> {r['dandiset_id']}: {r.get('reasoning', '')[:100]}", file=sys.stderr)
        return

    # Reclassify transcriptomics-only entries as MENTION
    reclassify_keys = set(
        (r["citing_doi"], r["dandiset_id"]) for r in transcriptomics_only
    )
    n_changed = 0
    for c in cls["classifications"]:
        key = (c["citing_doi"], c.get("dandiset_id", ""))
        if key in reclassify_keys and c["classification"] == "REUSE":
            c["classification"] = "MENTION"
            c["reclassified_from"] = "REUSE"
            c["reclassify_reason"] = "transcriptomics_only_not_on_dandi"
            n_changed += 1

    with open("output/all_classifications.json", "w") as f:
        json.dump(cls, f, indent=2)

    print(f"Reclassified {n_changed} entries from REUSE to MENTION", file=sys.stderr)


if __name__ == "__main__":
    main()
