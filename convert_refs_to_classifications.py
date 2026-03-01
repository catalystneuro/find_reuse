#!/usr/bin/env python3
"""
convert_refs_to_classifications.py - Convert direct dataset references to classification format

Transforms the output of find_reuse.py (results_dandi.json) into the same schema
used by classify_citing_papers.py, so both can be displayed in a combined dashboard.

For each paper-dandiset pair:
- Extracts context excerpts around DANDI mentions from cached paper text
- Uses LLM to classify as PRIMARY / REUSE / NEITHER
- source_type: "direct_reference" to distinguish from citation-based analysis

Usage:
    python convert_refs_to_classifications.py -i output/results_dandi.json -o output/direct_ref_classifications.json
"""

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

import requests
from tqdm import tqdm

from classify_usage import find_dandi_mentions_with_positions, extract_word_context
from llm_utils import get_api_key, call_openrouter_api, parse_json_response, DEFAULT_MODEL

CACHE_DIR = Path(__file__).parent / ".paper_cache"
CLASSIFICATION_CACHE_DIR = Path(__file__).parent / ".direct_ref_cache"
VALID_CLASSIFICATIONS = {"PRIMARY", "REUSE", "NEITHER"}
CONTEXT_WORDS = 100
API_DELAY = 0.5


def sanitize_doi(doi: str) -> str:
    """Sanitize DOI for use as a filename."""
    return doi.replace("/", "_").replace(":", "_").replace("\\", "_")


def load_paper_text(doi: str) -> str | None:
    """Load paper text from the paper cache."""
    cache_path = CACHE_DIR / f"{sanitize_doi(doi)}.json"
    if not cache_path.exists():
        return None
    try:
        with open(cache_path) as f:
            data = json.load(f)
        return data.get("text", "")
    except (json.JSONDecodeError, KeyError):
        return None


def get_cache_key(doi: str, dandiset_id: str) -> str:
    """Generate a cache key for a paper-dandiset classification."""
    return f"{sanitize_doi(doi)}__{dandiset_id}"


def get_cached_classification(doi: str, dandiset_id: str) -> dict | None:
    """Check for a cached classification result."""
    cache_path = CLASSIFICATION_CACHE_DIR / f"{get_cache_key(doi, dandiset_id)}.json"
    if cache_path.exists():
        try:
            with open(cache_path) as f:
                return json.load(f)
        except (json.JSONDecodeError, KeyError):
            pass
    return None


def save_classification_cache(doi: str, dandiset_id: str, result: dict):
    """Save a classification result to cache."""
    CLASSIFICATION_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = CLASSIFICATION_CACHE_DIR / f"{get_cache_key(doi, dandiset_id)}.json"
    with open(cache_path, "w") as f:
        json.dump(result, f, indent=2)


def extract_contexts_for_dataset(text: str, dandiset_id: str) -> list[dict]:
    """Extract context excerpts around mentions of a specific dandiset in paper text.

    Returns list of context excerpt dicts matching the citation classification schema.
    """
    mentions = find_dandi_mentions_with_positions(text)

    # Filter to mentions of this specific dandiset
    ds_mentions = [m for m in mentions if m["id"] == dandiset_id]

    if not ds_mentions:
        return []

    excerpts = []
    for mention in ds_mentions:
        ctx = extract_word_context(text, mention["start"], mention["end"], CONTEXT_WORDS)
        excerpts.append(
            {
                "text": ctx["context"],
                "method": mention["pattern_type"],
                "highlight_offset": mention["start"] - ctx["context_start"],
            }
        )

    return excerpts


def build_classification_prompt(
    dandiset_id: str,
    contexts: list[dict],
    doi: str,
) -> str:
    """Build LLM prompt for classifying a direct reference as PRIMARY/REUSE/NEITHER."""
    excerpts_text = ""
    for i, ctx in enumerate(contexts, 1):
        excerpts_text += f"--- Excerpt {i} (matched via {ctx['method']}) ---\n{ctx['text']}\n\n"

    return f"""Analyze these excerpts from a scientific paper that directly references DANDI dataset {dandiset_id}.

Paper DOI: {doi}

{excerpts_text}

Based on the excerpts above, classify the paper's relationship to DANDI dataset {dandiset_id} as one of:
- PRIMARY: The authors of THIS PAPER created and deposited this dataset. Look for language like "we deposited our data", "data are available at", "our dataset", "we recorded and shared", "data generated in this study have been deposited".
- REUSE: The authors downloaded/accessed and reused this existing dataset created by others. Look for "we downloaded data from", "we used the dataset", "data were obtained from", "we analyzed data from".
- NEITHER: Not a meaningful reference to actually using or creating the dataset (e.g., general mention of the DANDI archive, listing it as a resource, methodology description mentioning DANDI as an example).

Key guidance:
- If the paper says "our data" or "data generated in this study" followed by the DANDI reference, it's PRIMARY.
- If the paper says "we used data from" or "obtained from" the DANDI archive, it's REUSE.
- If the reference appears only in a data availability statement saying the authors deposited their own data, it's PRIMARY.
- If the reference is just mentioning DANDI as a platform/resource without using or creating specific data, it's NEITHER.

DECISION 2 - If REUSE, is it the same lab?
Check whether the citing paper's author list shares names with the primary dataset's authors. If the same group reused or extended their own data, same_lab is true. If a different group used it, same_lab is false.

Respond with ONLY a raw JSON object (no markdown, no code blocks, no extra text):
{{"classification": "PRIMARY|REUSE|NEITHER", "confidence": <1-10>, "same_lab": <true|false>, "same_lab_confidence": <1-10>, "reasoning": "Brief 1-2 sentence explanation"}}

Only include same_lab and same_lab_confidence when classification is REUSE.
Confidence scale: 1 = pure guess, 5 = uncertain but leaning, 8 = fairly confident, 10 = certain."""


def classify_direct_reference(
    doi: str,
    dandiset_id: str,
    contexts: list[dict],
    api_key: str,
    model: str,
) -> dict:
    """Classify a single paper-dandiset direct reference using LLM."""
    if not contexts:
        return {
            "classification": "REUSE",
            "confidence": 3,
            "reasoning": "No text context available; defaulting to REUSE since paper directly references dataset",
        }

    prompt = build_classification_prompt(dandiset_id, contexts, doi)

    try:
        result = call_openrouter_api(prompt, api_key, model)
        if result and isinstance(result, dict):
            # Normalize classification
            cls = result.get("classification", "").upper().replace(" ", "_")
            if cls not in VALID_CLASSIFICATIONS:
                cls = "REUSE"
            result["classification"] = cls
            # Ensure confidence is numeric
            conf = result.get("confidence", 5)
            if isinstance(conf, str):
                conf = {"high": 8, "medium": 5, "low": 3}.get(conf.lower(), 5)
            result["confidence"] = conf
            # Ensure same_lab_confidence is numeric if present
            if "same_lab_confidence" in result:
                slc = result["same_lab_confidence"]
                if isinstance(slc, str):
                    slc = {"high": 8, "medium": 5, "low": 3}.get(slc.lower(), 5)
                result["same_lab_confidence"] = slc
            return result
    except Exception as e:
        return {
            "classification": "REUSE",
            "confidence": 1,
            "reasoning": f"LLM error: {e}",
        }

    return {
        "classification": "REUSE",
        "confidence": 1,
        "reasoning": "Failed to get LLM classification",
    }


def fetch_dandiset_names(dandiset_ids: list[str]) -> dict[str, str]:
    """Fetch dandiset names from DANDI API for a list of IDs."""
    names = {}
    for ds_id in tqdm(dandiset_ids, desc="Fetching dandiset names"):
        try:
            resp = requests.get(
                f"https://api.dandiarchive.org/api/dandisets/{ds_id}/",
                timeout=10,
            )
            if resp.ok:
                data = resp.json()
                draft = data.get("draft_version", {})
                names[ds_id] = draft.get("name", "")
        except Exception:
            pass
    return names


def convert(input_file: Path, output_file: Path, classify: bool = True, model: str = DEFAULT_MODEL) -> dict:
    """Convert direct references to classification format.

    Args:
        input_file: Path to results_dandi.json
        output_file: Path to write classification JSON
        classify: If True, use LLM to classify PRIMARY/REUSE/NEITHER
        model: LLM model to use

    Returns dict with counts.
    """
    with open(input_file) as f:
        data = json.load(f)

    results = data.get("results", data if isinstance(data, list) else [])

    # Collect all dandiset IDs we need names for
    all_ds_ids = set()
    for r in results:
        dandi = r.get("archives", {}).get("DANDI Archive", {})
        for did in dandi.get("dataset_ids", []):
            all_ds_ids.add(did)

    print(f"Found {len(results)} papers referencing {len(all_ds_ids)} unique dandisets")

    # Fetch dandiset names
    print(f"Fetching names for {len(all_ds_ids)} dandisets from DANDI API...")
    ds_names = fetch_dandiset_names(sorted(all_ds_ids))
    named = sum(1 for v in ds_names.values() if v)
    print(f"Got names for {named}/{len(all_ds_ids)} dandisets")

    # Get API key if classifying
    api_key = None
    if classify:
        try:
            api_key = get_api_key()
        except ValueError as e:
            print(f"Warning: {e} - will skip LLM classification", file=sys.stderr)
            classify = False

    # Build all paper-dandiset pairs first
    pairs = []
    for r in results:
        dandi = r.get("archives", {}).get("DANDI Archive", {})
        dataset_ids = dandi.get("dataset_ids", [])
        matches = dandi.get("matches", [])

        # Group matches by dataset ID
        matches_by_ds = {}
        for m in matches:
            ds_id = m["id"]
            matches_by_ds.setdefault(ds_id, []).append(m)

        for ds_id in dataset_ids:
            ds_matches = matches_by_ds.get(ds_id, [])
            pairs.append((r, ds_id, ds_matches))

    print(f"\nProcessing {len(pairs)} paper-dandiset pairs...")

    # Process each pair
    classifications = []
    cache_hits = 0
    api_calls = 0
    no_text = 0
    counts = {"PRIMARY": 0, "REUSE": 0, "NEITHER": 0}

    for r, ds_id, ds_matches in tqdm(pairs, desc="Classifying"):
        doi = r["doi"]
        pattern_types = sorted(set(m["pattern_type"] for m in ds_matches))

        # Extract context excerpts from paper text
        text = load_paper_text(doi)
        context_excerpts = []
        if text:
            context_excerpts = extract_contexts_for_dataset(text, ds_id)
        else:
            no_text += 1

        # Classify using LLM (or cache)
        if classify:
            cached = get_cached_classification(doi, ds_id)
            if cached:
                cls_result = cached
                cache_hits += 1
            else:
                cls_result = classify_direct_reference(
                    doi, ds_id, context_excerpts, api_key, model
                )
                save_classification_cache(doi, ds_id, cls_result)
                api_calls += 1
                time.sleep(API_DELAY)

            classification = cls_result.get("classification", "REUSE")
            confidence = cls_result.get("confidence", 5)
            reasoning = cls_result.get("reasoning", "")
            same_lab = cls_result.get("same_lab")
            same_lab_confidence = cls_result.get("same_lab_confidence")
        else:
            classification = "REUSE"
            confidence = 10
            reasoning = f"Paper directly references DANDI dataset {ds_id} via {', '.join(pattern_types)}"
            same_lab = None
            same_lab_confidence = None

        counts[classification] = counts.get(classification, 0) + 1

        entry = {
            "citing_doi": doi,
            "cited_doi": None,
            "dandiset_id": ds_id,
            "dandiset_name": ds_names.get(ds_id, ""),
            "classification": classification,
            "confidence": confidence,
            "reasoning": reasoning,
            "same_lab": same_lab,
            "same_lab_confidence": same_lab_confidence,
            "source_type": "direct_reference",
            "citing_title": r.get("title", ""),
            "citing_journal": r.get("journal", ""),
            "citing_date": r.get("date", ""),
            "text_source": r.get("source", ""),
            "text_length": r.get("text_length"),
            "num_contexts": len(context_excerpts),
            "context_excerpts": context_excerpts,
            "match_patterns": [
                {
                    "pattern_type": m["pattern_type"],
                    "matched_string": m["matched_string"],
                }
                for m in ds_matches
            ],
        }
        classifications.append(entry)

    output_data = {
        "metadata": {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime()),
            "source": "direct_reference_conversion",
            "input_file": str(input_file),
            "total_papers": len(results),
            "total_pairs": len(classifications),
            "total_dandisets": len(all_ds_ids),
            "model": model if classify else None,
            "classification_counts": counts,
        },
        "classifications": classifications,
    }

    output_file.parent.mkdir(parents=True, exist_ok=True)
    with open(output_file, "w") as f:
        json.dump(output_data, f, indent=2)

    print(f"\nWrote {len(classifications)} classification entries to {output_file}")
    if classify:
        print(f"  Cache hits: {cache_hits}, API calls: {api_calls}, No text: {no_text}")
    print(f"  PRIMARY: {counts.get('PRIMARY', 0)}, REUSE: {counts.get('REUSE', 0)}, NEITHER: {counts.get('NEITHER', 0)}")
    return {"total": len(classifications), "dandisets": len(all_ds_ids)}


def main():
    parser = argparse.ArgumentParser(
        description="Convert direct dataset references to classification format"
    )
    parser.add_argument(
        "-i",
        "--input",
        default="output/results_dandi.json",
        help="Input results_dandi.json file",
    )
    parser.add_argument(
        "-o",
        "--output",
        default="output/direct_ref_classifications.json",
        help="Output classification JSON file",
    )
    parser.add_argument(
        "--no-classify",
        action="store_true",
        help="Skip LLM classification (mark all as REUSE)",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"LLM model to use (default: {DEFAULT_MODEL})",
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)

    if not input_path.exists():
        print(f"Error: Input file not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    convert(input_path, output_path, classify=not args.no_classify, model=args.model)


if __name__ == "__main__":
    main()
