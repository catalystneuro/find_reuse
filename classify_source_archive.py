#!/usr/bin/env python3
"""
classify_source_archive.py - Classify/normalize source_archive for all REUSE entries

Two-phase approach:
1. Normalize existing archive names to canonical forms
2. For "unclear" cases, extract data-relevant sections from paper text and
   send a focused LLM prompt to determine the source archive

Usage:
    python classify_source_archive.py                    # Dry run (normalize only, show stats)
    python classify_source_archive.py --resolve-unclear   # Also resolve unclear via LLM
    python classify_source_archive.py --resolve-unclear --write  # Write results back
"""

import argparse
import json
import re
import sys
import time
from collections import Counter
from pathlib import Path

from llm_utils import call_openrouter_api, get_api_key, parse_json_response

CLASSIFICATIONS_FILE = Path("output/all_classifications.json")
PAPER_CACHE_DIR = Path(".paper_cache")
SOURCE_ARCHIVE_CACHE_DIR = Path(".source_archive_cache")

# Canonical name mapping: {variant: canonical}
NORMALIZE_MAP = {
    "DANDI": "DANDI Archive",
    "DANDI archive": "DANDI Archive",
    "dandi archive": "DANDI Archive",
    "IBL database": "IBL",
    "IBL (International Brain Laboratory data portal)": "IBL",
    "International Brain Laboratory": "IBL",
    "microns-explorer.org": "MICrONS Explorer",
    "Microns Explorer": "MICrONS Explorer",
    "Neural Latents Benchmark (NLB)": "DANDI Archive",
    "Neural Latents Benchmark": "DANDI Archive",
    "buzsakilab.nyumc.org/datasets/": "Buzsaki Lab",
    "Buzsáki laboratory website": "Buzsaki Lab",
    "Buzsaki Lab data repository": "Buzsaki Lab",
    "Buzsaki Lab website": "Lab website",
    "CellExplorer": "Lab website",
    "Buzsaki Lab": "Lab website",
    "Nemo Archive": "NEMO Archive",
    "institutional repository / lab website": "Lab website",
    "Mouse Light Neuron Browser": "MouseLight",
    # LLM parenthetical variants
    "DANDI Archive (dandiarchive.org)": "DANDI Archive",
    "CRCNS (crcns.org)": "CRCNS",
    "MICrONS Explorer (microns-explorer.org)": "MICrONS Explorer",
    "Figshare (figshare.com)": "Figshare",
    "OSF (osf.io)": "OSF",
    "IBL (International Brain Laboratory)": "IBL",
    "Buzsaki Lab (buzsakilab.com)": "Lab website",
    "GIN (gin.g-node.org)": "GIN",
    "GitHub (github.com - for data, not code)": "GitHub",
    "NCBI Gene Expression Omnibus": "GEO",
}

# Known canonical archive names (for LLM validation)
CANONICAL_ARCHIVES = {
    "DANDI Archive", "CRCNS", "Figshare", "Allen Institute", "IBL",
    "Zenodo", "Dryad", "OSF", "GIN", "OpenNeuro", "EBRAINS",
    "Neural Latents Benchmark", "MICrONS Explorer", "Brain Image Library",
    "NeuroMorpho.org", "GitHub", "Buzsaki Lab", "Lab website",
    "MouseLight", "NEMO Archive", "AWS", "NIRD Research Data Archive",
}


def extract_data_sections(text: str) -> str:
    """Extract data availability, methods, and key resources sections from paper text."""
    sections = []

    # Data availability / Data access / Data and code availability
    for pattern in [
        r'(?:Data\s+(?:availability|access|and\s+code\s+availability|sharing|deposition)[^\n]*)\n([\s\S]{0,3000})',
        r'(?:Availability\s+of\s+data)[^\n]*\n([\s\S]{0,3000})',
        r'(?:Code\s+and\s+data\s+availability)[^\n]*\n([\s\S]{0,3000})',
    ]:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            sections.append(f"--- Data availability section ---\n{m.group(0)[:3000]}")

    # Key resources table (common in Cell Press journals)
    m = re.search(r'(Key\s+resources?\s+table[\s\S]{0,4000})', text, re.IGNORECASE)
    if m:
        sections.append(f"--- Key resources table ---\n{m.group(1)[:4000]}")

    # STAR Methods or Methods section - look for data-related subsections
    for pattern in [
        r'(?:STAR\s*\★?\s*Methods|Materials\s+and\s+Methods|Methods)\s*\n([\s\S]{0,6000})',
        r'(?:Experimental\s+(?:Model|Procedures))[^\n]*\n([\s\S]{0,4000})',
    ]:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            method_text = m.group(0)[:6000]
            # Try to extract just the data-related parts
            data_subsection = re.search(
                r'((?:Data|Dataset|Electrophysiol|Neural|Recording|Imaging)[^\n]*\n[\s\S]{0,2000})',
                method_text, re.IGNORECASE
            )
            if data_subsection:
                sections.append(f"--- Methods (data subsection) ---\n{data_subsection.group(1)[:2000]}")
            else:
                sections.append(f"--- Methods ---\n{method_text[:3000]}")

    # Deposited data section
    m = re.search(r'(Deposited\s+data[\s\S]{0,2000})', text, re.IGNORECASE)
    if m:
        sections.append(f"--- Deposited data ---\n{m.group(1)[:2000]}")

    if not sections:
        # Fallback: search for any paragraph mentioning download/obtain/access + data
        matches = re.finditer(
            r'[^\n]*(?:download|obtain|access|retriev|available\s+(?:at|from|on|via))[^\n]*(?:data|dataset|recording|archive)[^\n]*',
            text, re.IGNORECASE
        )
        for m in list(matches)[:5]:
            start = max(0, m.start() - 200)
            end = min(len(text), m.end() + 200)
            sections.append(f"--- Text near data access mention ---\n{text[start:end]}")

    return "\n\n".join(sections) if sections else ""


def build_source_archive_prompt(
    dandiset_id: str,
    dandiset_name: str,
    citing_doi: str,
    cited_doi: str,
    data_sections: str,
    context_excerpts: list[dict],
) -> str:
    """Build a focused prompt for source archive classification."""
    prompt = f"""You are determining WHERE a scientific paper obtained data that it reused.

We already know this paper REUSED data associated with a DANDI neuroscience dataset. Your ONLY job is to determine which archive or repository the data was accessed from.

DANDI DATASET: {dandiset_id} - {dandiset_name}
PRIMARY PAPER DOI (paper that originally published the dataset): {cited_doi}
CITING PAPER DOI (paper that reused the data): {citing_doi}

"""
    if context_excerpts:
        prompt += f"Citation context excerpts ({len(context_excerpts)} found):\n\n"
        for i, ctx in enumerate(context_excerpts, 1):
            prompt += f"--- Excerpt {i} ---\n{ctx.get('text', '')}\n\n"

    if data_sections:
        prompt += f"Data-related sections from the paper:\n\n{data_sections}\n\n"

    prompt += """Based on the text above, determine which archive/repository was used to ACCESS the data.

Known archives (use these exact names when applicable):
- DANDI Archive (dandiarchive.org)
- CRCNS (crcns.org)
- Figshare (figshare.com)
- Allen Institute (brain-map.org, allensdk)
- IBL (International Brain Laboratory)
- Zenodo (zenodo.org)
- Dryad (datadryad.org)
- OSF (osf.io)
- GIN (gin.g-node.org)
- OpenNeuro (openneuro.org)
- EBRAINS (ebrains.eu)
- Neural Latents Benchmark
- MICrONS Explorer (microns-explorer.org)
- Brain Image Library
- NeuroMorpho.org
- GitHub (github.com - for data, not code)
- Buzsaki Lab (buzsakilab.com)
- Lab website (author's own institutional/lab page)

IMPORTANT: Focus ONLY on where THIS SPECIFIC dataset's data was obtained, not other datasets mentioned in the paper. If the paper mentions multiple archives, identify the one used for the dataset above.

Respond ONLY with a JSON object:
{"source_archive": "<archive name or unclear>", "reasoning": "Brief explanation"}
"""
    return prompt


def get_source_cache_path(citing_doi: str, dandiset_id: str) -> Path:
    """Cache path for source archive classification."""
    safe_doi = citing_doi.replace("/", "_")
    return SOURCE_ARCHIVE_CACHE_DIR / f"{safe_doi}__{dandiset_id}.json"


def classify_source_archives(resolve_unclear: bool = False, write: bool = False):
    """Main function to normalize and optionally resolve source archives."""
    with open(CLASSIFICATIONS_FILE) as f:
        data = json.load(f)

    classifications = data["classifications"]
    reuse = [c for c in classifications if c.get("classification") == "REUSE"]
    print(f"Total REUSE entries: {len(reuse)}", file=sys.stderr)

    # Phase 1: Normalize existing values
    normalized_count = 0
    for c in reuse:
        archive = c.get("source_archive", "")
        if archive in NORMALIZE_MAP:
            c["source_archive"] = NORMALIZE_MAP[archive]
            normalized_count += 1

    print(f"Normalized {normalized_count} archive names", file=sys.stderr)

    # Show current distribution
    dist = Counter(c.get("source_archive", "MISSING") for c in reuse)
    print("\nCurrent distribution after normalization:", file=sys.stderr)
    for name, count in dist.most_common():
        print(f"  {count:4d}  {name}", file=sys.stderr)

    # Phase 2: Resolve unclear via LLM
    unclear = [c for c in reuse if c.get("source_archive") == "unclear"]
    print(f"\nUnclear entries to resolve: {len(unclear)}", file=sys.stderr)

    if resolve_unclear and unclear:
        api_key = get_api_key()
        SOURCE_ARCHIVE_CACHE_DIR.mkdir(parents=True, exist_ok=True)

        resolved = 0
        still_unclear = 0
        errors = 0
        cache_hits = 0

        for i, c in enumerate(unclear):
            citing_doi = c["citing_doi"]
            dandiset_id = c["dandiset_id"]

            # Check source archive cache
            cache_path = get_source_cache_path(citing_doi, dandiset_id)
            if cache_path.exists():
                try:
                    with open(cache_path) as f:
                        cached = json.load(f)
                    archive = cached.get("source_archive", "unclear")
                    if archive in NORMALIZE_MAP:
                        archive = NORMALIZE_MAP[archive]
                    c["source_archive"] = archive
                    if archive != "unclear":
                        resolved += 1
                    else:
                        still_unclear += 1
                    cache_hits += 1
                    continue
                except (json.JSONDecodeError, OSError):
                    pass

            # Load paper text
            paper_file = PAPER_CACHE_DIR / f"{citing_doi.replace('/', '_')}.json"
            if not paper_file.exists():
                still_unclear += 1
                continue

            try:
                with open(paper_file) as f:
                    paper_data = json.load(f)
            except (json.JSONDecodeError, OSError):
                still_unclear += 1
                continue

            text = paper_data.get("text", "")
            if not text:
                still_unclear += 1
                continue

            # Extract data-relevant sections
            data_sections = extract_data_sections(text)
            context_excerpts = c.get("context_excerpts", [])

            if not data_sections and not context_excerpts:
                # Nothing useful to send - keep unclear
                still_unclear += 1
                continue

            prompt = build_source_archive_prompt(
                dandiset_id=dandiset_id,
                dandiset_name=c.get("dandiset_name", ""),
                citing_doi=citing_doi,
                cited_doi=c.get("cited_doi", ""),
                data_sections=data_sections,
                context_excerpts=context_excerpts,
            )

            response = call_openrouter_api(
                prompt, api_key, return_raw=True, max_tokens=400, timeout=60,
            )

            if response:
                # Parse JSON manually (parse_json_response expects classification field)
                archive = "unclear"
                reasoning = ""
                try:
                    # Strip markdown code blocks if present
                    text = response.strip()
                    if text.startswith("```"):
                        lines = text.split("\n")
                        text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
                    parsed = json.loads(text)
                    archive = parsed.get("source_archive", "unclear")
                    reasoning = parsed.get("reasoning", "")
                except json.JSONDecodeError:
                    # Try extracting JSON from response
                    m = re.search(r'\{[^{}]*"source_archive"[^{}]*\}', response)
                    if m:
                        try:
                            parsed = json.loads(m.group(0))
                            archive = parsed.get("source_archive", "unclear")
                            reasoning = parsed.get("reasoning", "")
                        except json.JSONDecodeError:
                            pass

                # Normalize
                if archive in NORMALIZE_MAP:
                    archive = NORMALIZE_MAP[archive]

                c["source_archive"] = archive

                # Cache result
                cache_result = {
                    "source_archive": archive,
                    "reasoning": reasoning,
                    "citing_doi": citing_doi,
                    "dandiset_id": dandiset_id,
                }
                with open(cache_path, "w") as f:
                    json.dump(cache_result, f, indent=2)

                if archive != "unclear":
                    resolved += 1
                else:
                    still_unclear += 1
            else:
                errors += 1
                still_unclear += 1

            if (i + 1) % 50 == 0:
                print(
                    f"  Progress: {i+1}/{len(unclear)} "
                    f"(resolved={resolved}, unclear={still_unclear}, "
                    f"errors={errors}, cached={cache_hits})",
                    file=sys.stderr,
                )

        print(
            f"\nResolution complete: resolved={resolved}, "
            f"still_unclear={still_unclear}, errors={errors}, "
            f"cache_hits={cache_hits}",
            file=sys.stderr,
        )

    # Final distribution
    final_dist = Counter(c.get("source_archive", "MISSING") for c in reuse)
    print("\nFinal distribution:", file=sys.stderr)
    for name, count in final_dist.most_common():
        print(f"  {count:4d}  {name}", file=sys.stderr)

    if write:
        with open(CLASSIFICATIONS_FILE, "w") as f:
            json.dump(data, f, indent=2)
        print(f"\nWrote updated classifications to {CLASSIFICATIONS_FILE}", file=sys.stderr)
    elif resolve_unclear or normalized_count > 0:
        print("\nDry run — use --write to save changes", file=sys.stderr)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Classify/normalize source_archive for REUSE entries")
    parser.add_argument("--resolve-unclear", action="store_true", help="Resolve unclear entries via LLM")
    parser.add_argument("--write", action="store_true", help="Write results back to classifications file")
    parser.add_argument("--input", type=str, default=None, help="Input classifications file (default: output/all_classifications.json)")
    args = parser.parse_args()

    if args.input:
        global CLASSIFICATIONS_FILE
        CLASSIFICATIONS_FILE = Path(args.input)

    classify_source_archives(resolve_unclear=args.resolve_unclear, write=args.write)
