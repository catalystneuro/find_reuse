#!/usr/bin/env python3
"""
classify_reuse_type.py — Classify the TYPE of data reuse for REUSE papers.

Categories:
- TOOL_DEMO: Showcasing a new analysis tool, software, or pipeline on open data
- BENCHMARK: Using data as a standard benchmark for comparing methods/algorithms
- AGGREGATION: Combining multiple datasets for statistical power or cross-dataset analysis
- CONFIRMATORY: Validating/replicating own findings using independent open data
- NOVEL_ANALYSIS: Applying a new scientific question to existing data
- ML_TRAINING: Using data to train machine learning / deep learning models
- SIMULATION: Using real data to validate or parameterize computational models
- TEACHING: Educational use (tutorials, courses, workshops)

Usage:
    python classify_reuse_type.py                    # Classify all REUSE papers
    python classify_reuse_type.py --max 50           # Classify first 50
    python classify_reuse_type.py --workers 8        # Parallel workers
"""

import argparse
import json
import re
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
import threading

from llm_utils import call_openrouter_api, get_api_key

CACHE_DIR = Path(".reuse_type_cache")
REUSE_TYPES = [
    "TOOL_DEMO",
    "BENCHMARK",
    "AGGREGATION",
    "CONFIRMATORY",
    "NOVEL_ANALYSIS",
    "ML_TRAINING",
    "SIMULATION",
    "TEACHING",
]


def build_prompt(citing_doi, dandiset_id, dandiset_name, reasoning, context_excerpts):
    """Build prompt for reuse type classification."""
    prompt = f"""You are classifying the TYPE of data reuse in a scientific paper.

We already know this paper REUSED data from a DANDI neuroscience dataset.
Your job is to determine HOW the data was reused.

CITING PAPER DOI: {citing_doi}
DATASET: {dandiset_id} — {dandiset_name}
REUSE REASONING: {reasoning}

"""
    if context_excerpts:
        prompt += "Text excerpts showing how the data was used:\n\n"
        for i, ctx in enumerate(context_excerpts[:3], 1):
            text = ctx.get("text", "")[:800]
            prompt += f"--- Excerpt {i} ---\n{text}\n\n"

    prompt += """Classify the reuse into ONE primary category:

- TOOL_DEMO: The paper demonstrates a new analysis tool, software package, or processing pipeline using this dataset as example data. The scientific question is secondary to showcasing the method.
- BENCHMARK: The dataset is used as a standard benchmark to compare algorithm/decoder/model performance against other methods. Common with NLB, Brain-Computer Interface decoders.
- AGGREGATION: The paper combines this dataset with other datasets to increase statistical power, do cross-dataset comparisons, or build a larger pooled analysis.
- CONFIRMATORY: The paper has its own novel data/experiments and uses this open dataset to replicate, validate, or confirm their primary findings.
- NOVEL_ANALYSIS: The paper asks a new scientific question of this existing dataset that the original authors did not explore. The focus is on new scientific insight.
- ML_TRAINING: The dataset is primarily used to train a machine learning or deep learning model (neural network, foundation model, etc.).
- SIMULATION: The paper uses the real data to validate, constrain, or parameterize a computational model or simulation.
- TEACHING: Used for educational purposes (tutorials, courses, textbook examples).

If multiple categories apply, choose the PRIMARY one — the main reason the authors used this dataset.

Respond with JSON only:
{"reuse_type": "CATEGORY", "confidence": 1-10, "reasoning": "Brief explanation"}
"""
    return prompt


def classify_one(entry, api_key):
    """Classify reuse type for one entry."""
    citing_doi = entry["citing_doi"]
    dandiset_id = entry.get("dandiset_id", "")

    # Check cache
    cache_file = CACHE_DIR / f"{citing_doi.replace('/', '_')}__{dandiset_id}.json"
    if cache_file.exists():
        try:
            with open(cache_file) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass

    prompt = build_prompt(
        citing_doi=citing_doi,
        dandiset_id=dandiset_id,
        dandiset_name=entry.get("dandiset_name", ""),
        reasoning=entry.get("reasoning", ""),
        context_excerpts=entry.get("context_excerpts", []),
    )

    response = call_openrouter_api(
        prompt, api_key, return_raw=True, max_tokens=300, timeout=60,
    )

    result = {"citing_doi": citing_doi, "dandiset_id": dandiset_id}

    if response:
        try:
            text = response.strip()
            if text.startswith("```"):
                lines = text.split("\n")
                text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
            parsed = json.loads(text)
            result.update(parsed)
        except json.JSONDecodeError:
            m = re.search(r'\{[^{}]*"reuse_type"[^{}]*\}', response)
            if m:
                try:
                    result.update(json.loads(m.group(0)))
                except json.JSONDecodeError:
                    result["reuse_type"] = "UNKNOWN"
                    result["error"] = "parse_error"
            else:
                result["reuse_type"] = "UNKNOWN"
                result["error"] = "parse_error"
    else:
        result["reuse_type"] = "UNKNOWN"
        result["error"] = "no_response"

    # Validate
    if result.get("reuse_type") not in REUSE_TYPES:
        result["reuse_type"] = "UNKNOWN"

    result["classified_at"] = datetime.now().isoformat()

    # Save cache
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with open(cache_file, "w") as f:
        json.dump(result, f, indent=2)

    return result


def main():
    parser = argparse.ArgumentParser(description="Classify reuse type for REUSE papers")
    parser.add_argument("--max", type=int, default=None)
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()

    api_key = get_api_key()

    with open("output/all_classifications.json") as f:
        data = json.load(f)

    reuse = [c for c in data["classifications"] if c["classification"] == "REUSE"]
    print(f"Total REUSE papers: {len(reuse)}", file=sys.stderr)

    # Filter to those not yet cached
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    to_classify = []
    cached = 0
    for c in reuse:
        cache_file = CACHE_DIR / f"{c['citing_doi'].replace('/', '_')}__{c.get('dandiset_id', '')}.json"
        if cache_file.exists():
            cached += 1
        else:
            to_classify.append(c)

    print(f"Already cached: {cached}", file=sys.stderr)
    print(f"To classify: {len(to_classify)}", file=sys.stderr)

    if args.max:
        to_classify = to_classify[:args.max]

    # Classify in parallel
    results = []
    lock = threading.Lock()
    done = 0

    def process(entry):
        return classify_one(entry, api_key)

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {executor.submit(process, c): c for c in to_classify}
        for future in as_completed(futures):
            result = future.result()
            results.append(result)
            with lock:
                nonlocal_done = len(results)
                if nonlocal_done % 50 == 0:
                    print(f"  {nonlocal_done}/{len(to_classify)}", file=sys.stderr)

    # Load all cached results
    all_results = []
    for c in reuse:
        cache_file = CACHE_DIR / f"{c['citing_doi'].replace('/', '_')}__{c.get('dandiset_id', '')}.json"
        if cache_file.exists():
            with open(cache_file) as f:
                all_results.append(json.load(f))

    # Summary
    types = Counter(r.get("reuse_type", "UNKNOWN") for r in all_results)
    print(f"\n{'='*50}", file=sys.stderr)
    print(f"Reuse Type Distribution ({len(all_results)} papers):", file=sys.stderr)
    for t, n in types.most_common():
        pct = 100 * n / len(all_results) if all_results else 0
        print(f"  {t:<20s} {n:4d} ({pct:5.1f}%)", file=sys.stderr)

    # Output JSON
    json.dump(all_results, sys.stdout, indent=2)
    print(file=sys.stdout)


if __name__ == "__main__":
    main()
