"""
Recover the original paper set from a review.html + review_state JSON.

Use this when the pipeline output files (datasets.json, citation_contexts.json,
classifications.json) have been overwritten but the review.html and review state
file from the previous run are still intact.

The citing-paper set is recovered from the embedded entryData in review.html. Dataset
descriptions are not embedded in review.html, so they are re-fetched from the archive
adapter (--archive) at recovery time. This way you can re-test classification on the
exact same paper set while still exercising prompt features that depend on the
description (e.g. the co_primary_paper rule).

Outputs:
  recovered_datasets.json  — input for extract_citation_contexts.py --results-file
                             (then feed the resulting contexts file into
                             classify_citing_papers.py --contexts-file)

Usage:
  python recover_from_review.py
  python recover_from_review.py --review-html output/minimal/crcns/review.html \
      --output-dir output/minimal/crcns
"""

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

from archives import get_adapter


def extract_entry_data(html_path: Path) -> list[dict]:
    text = html_path.read_text(encoding="utf-8")
    scripts = re.findall(r"<script[^>]*>(.*?)</script>", text, re.DOTALL)

    for script in scripts:
        script = script.strip()
        prefix = "const entryData = "
        if not script.startswith(prefix):
            continue
        start = len(prefix)
        # Walk characters to find the matching closing bracket
        depth = 0
        end = start
        for index, character in enumerate(script[start:], start=start):
            if character == "[":
                depth += 1
            elif character == "]":
                depth -= 1
                if depth == 0:
                    end = index + 1
                    break
        return json.loads(script[start:end])

    raise ValueError(f"Could not find entryData in {html_path}")


def build_recovered_datasets(entries: list[dict], descriptions: dict[str, str]) -> dict:
    groups: dict[str, dict] = defaultdict(
        lambda: {"citing_papers": [], "dandiset_name": "", "dandiset_url": ""}
    )
    for entry in entries:
        dataset_id = entry["dandiset_id"]
        groups[dataset_id]["dandiset_name"] = entry.get("dandiset_name", "")
        groups[dataset_id]["dandiset_url"] = entry.get("dandiset_url", "")
        groups[dataset_id]["citing_papers"].append(
            {
                "doi": entry["citing_doi"],
                "cited_paper_doi": entry["cited_doi"],
                "title": entry.get("citing_title", ""),
                "journal": entry.get("citing_journal", ""),
                "publication_date": entry.get("citing_date", ""),
            }
        )

    results = []
    for dataset_id in sorted(groups):
        group = groups[dataset_id]
        results.append(
            {
                "dataset_id": dataset_id,
                "dandiset_id": dataset_id,
                "dandiset_name": group["dandiset_name"],
                "dandiset_url": group["dandiset_url"],
                "dandiset_description": descriptions.get(dataset_id, ""),
                "citing_papers": group["citing_papers"],
            }
        )

    return {"count": len(results), "results": results}


def fetch_descriptions(archive: str, dataset_ids: set[str]) -> dict[str, str]:
    """Fetch dataset descriptions for the given dataset IDs from the archive adapter."""
    print(f"Fetching descriptions for {len(dataset_ids)} datasets from {archive} ...", file=sys.stderr)
    adapter = get_adapter(archive, output_dir=".")
    descriptions = {}
    for dataset in adapter.get_datasets():
        if dataset["id"] in dataset_ids:
            descriptions[dataset["id"]] = dataset.get("description", "")
    return descriptions


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--review-html",
        type=Path,
        default=Path("output/minimal/crcns/review.html"),
        help="Path to the review.html file from the original run",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("output/minimal/crcns"),
        help="Directory to write recovered output files",
    )
    parser.add_argument(
        "--archive",
        default="crcns",
        help="Archive short name for fetching dataset descriptions (default: crcns)",
    )
    arguments = parser.parse_args()

    if not arguments.review_html.exists():
        print(f"Error: review HTML not found at {arguments.review_html}", file=sys.stderr)
        sys.exit(1)
    print(f"Parsing {arguments.review_html} ...", file=sys.stderr)
    entries = extract_entry_data(arguments.review_html)
    print(f"  Found {len(entries)} embedded entries", file=sys.stderr)

    dataset_ids = {entry["dandiset_id"] for entry in entries}
    descriptions = fetch_descriptions(arguments.archive, dataset_ids)
    print(f"  Got descriptions for {sum(1 for v in descriptions.values() if v)}/{len(dataset_ids)} datasets", file=sys.stderr)

    datasets = build_recovered_datasets(entries, descriptions)

    arguments.output_dir.mkdir(parents=True, exist_ok=True)

    recovered_datasets_path = arguments.output_dir / "recovered_datasets.json"
    recovered_datasets_path.write_text(json.dumps(datasets, indent=2))
    print(f"Wrote {recovered_datasets_path}", file=sys.stderr)

    unique_datasets = {r["dataset_id"] for r in datasets["results"]}
    total_pairs = sum(len(r["citing_papers"]) for r in datasets["results"])
    print(f"\nRecovered {total_pairs} entries across {len(unique_datasets)} datasets.")
    recovered_contexts_path = arguments.output_dir / "recovered_citation_contexts.json"
    print(
        f"\nTo re-classify with Gemini Flash, first re-extract contexts:\n"
        f"  python extract_citation_contexts.py \\\n"
        f"    --results-file {recovered_datasets_path} \\\n"
        f"    -o {recovered_contexts_path}\n"
        f"\nThen classify:\n"
        f"  python classify_citing_papers.py \\\n"
        f"    --contexts-file {recovered_contexts_path} \\\n"
        f"    --model google/gemini-3-flash-preview \\\n"
        f"    -o {arguments.output_dir}/recovered_classifications_gemini.json"
    )


if __name__ == "__main__":
    main()
