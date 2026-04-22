#!/usr/bin/env python3
"""
run_pipeline.py — Run the complete DANDI reuse analysis pipeline end-to-end.

Steps:
1. Discover dandisets and their primary papers (metadata + LLM)
2. Merge formal + LLM paper sources
3. Fetch citing papers from OpenAlex
4. Discover direct dandiset references (Europe PMC)
5. Fetch paper text (multi-source)
6. Extract citation contexts
7. Classify citing papers (LLM)
8. Merge citation + direct reference classifications
9. Classify source archive
10. Classify reuse type
11. Update delay data
12. Andersen-Gill Cox PH regression
13. Regenerate all figures and flowcharts

Usage:
    python run_pipeline.py              # Full run
    python run_pipeline.py --skip-fetch # Skip steps 1-4 (use cached data)
    python run_pipeline.py --figures-only # Only regenerate figures (step 12)
"""

import argparse
import json
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

import requests


def run(cmd, desc):
    """Run a command, printing status."""
    print(f"\n{'='*60}", file=sys.stderr)
    print(f"  {desc}", file=sys.stderr)
    print(f"{'='*60}", file=sys.stderr, flush=True)
    result = subprocess.run(cmd, shell=isinstance(cmd, str))
    if result.returncode != 0:
        print(f"  WARNING: {desc} exited with code {result.returncode}", file=sys.stderr)
    return result.returncode


def step1_discover_dandisets(limit=None):
    """Discover dandisets and their primary papers."""
    max_citing = "20" if limit is not None else "999"
    cmd = ["python3", "dandi_primary_papers.py",
           "--citations", "--fetch-text",
           "--cache-dir", ".paper_cache",
           "--max-citing-papers", max_citing,
           "-o", "output/dandi_primary_papers_results.json"]
    if limit is not None:
        cmd += ["--max-dandisets", str(limit)]
    run(cmd, "Step 1: Discover dandisets and primary papers")


def step2_llm_find_papers():
    """Use LLM to find papers for dandisets without formal links."""
    run(
        ["python3", "find_missing_papers.py", "--workers", "8"],
        "Step 2: LLM paper discovery",
    )


def step3_merge_sources(limit=None):
    """Merge formal + LLM paper sources."""
    run(
        ["python3", "merge_paper_sources.py", "-o", "output/all_dandiset_papers.json"],
        "Step 3: Merge paper sources",
    )

    # Filter to non-empty dandisets
    session = requests.Session()
    nonempty = set()
    page = 1
    while True:
        resp = session.get(
            f"https://api.dandiarchive.org/api/dandisets/?page_size=200&page={page}",
            timeout=15,
        )
        data = resp.json()
        for ds in data.get("results", []):
            draft = ds.get("draft_version", {})
            if draft.get("asset_count", 0) > 0 or draft.get("size", 0) > 0:
                nonempty.add(ds["identifier"])
        if not data.get("next"):
            break
        page += 1

    with open("output/all_dandiset_papers.json") as f:
        data = json.load(f)
    data["results"] = [r for r in data["results"] if r["dandiset_id"] in nonempty]
    if limit is not None:
        data["results"] = sorted(data["results"], key=lambda r: r["dandiset_id"])[:limit]
    data["count"] = len(data["results"])
    with open("output/all_dandiset_papers.json", "w") as f:
        json.dump(data, f, indent=2)
    print(f"  Filtered to {data['count']} non-empty dandisets", file=sys.stderr)


def step4_fetch_citations():
    """Fetch citing papers from OpenAlex."""
    run(
        ["python3", "fetch_remaining_citations.py"],
        "Step 4: Fetch citing papers from OpenAlex",
    )


def step5_direct_refs(limit=None):
    """Discover direct dandiset references."""
    cmd = ["python3", "find_reuse.py", "--discover",
           "--archives", "DANDI Archive", "--deduplicate",
           "-o", "output/results_dandi.json"]
    if limit is not None:
        cmd += ["--max-results", str(max(100, limit * 10))]
    run(cmd, "Step 5a: Discover direct dandiset references")
    run(
        ["python3", "convert_refs_to_classifications.py",
         "-i", "output/results_dandi.json",
         "-o", "output/direct_ref_classifications.json"],
        "Step 5b: Classify direct references",
    )


def step6_fetch_text_and_classify(limit=None):
    """Fetch text, extract contexts, classify, merge, normalize."""
    cmd = ["python3", "fetch_and_classify_new.py"]
    if limit is not None:
        cmd += ["--max-citing-papers", "20"]
    run(cmd, "Step 6: Fetch text, extract contexts, classify, merge, normalize")


def step6b_deduplicate_preprints():
    """Remove preprint/published duplicate entries."""
    run(
        ["python3", "deduplicate_preprints.py"],
        "Step 6b: Deduplicate preprint/published pairs",
    )


def step7_classify_reuse_type():
    """Classify reuse type for all REUSE papers."""
    run(
        ["python3", "classify_reuse_type.py", "--workers", "8"],
        "Step 7: Classify reuse type",
    )


def step8_update_delays():
    """Update delay data for all REUSE entries."""
    print("\n" + "="*60, file=sys.stderr)
    print("  Step 8: Update delay data", file=sys.stderr)
    print("="*60, file=sys.stderr, flush=True)

    with open("output/all_classifications.json") as f:
        cls_data = json.load(f)
    with open("output/all_dandiset_papers.json") as f:
        papers_data = json.load(f)

    dc = {}
    for r in papers_data["results"]:
        did = r["dandiset_id"]
        created = r.get("data_accessible") or r.get("dandiset_created", "")
        if created:
            dc[did] = datetime.fromisoformat(created.replace("Z", "+00:00")).replace(tzinfo=None)

    # Load existing delays
    delays_file = Path("output/dandi_reuse_delays.json")
    existing = json.load(open(delays_file)) if delays_file.exists() else []
    existing_keys = set((d["citing_doi"], d["dandiset_id"]) for d in existing)

    # Find all REUSE entries needing delays
    targets = [
        c for c in cls_data["classifications"]
        if c.get("classification") == "REUSE"
        and (c["citing_doi"], c.get("dandiset_id", "")) not in existing_keys
    ]

    if not targets:
        print("  No new delays to add", file=sys.stderr)
        return

    # Fetch pub dates
    new_dois = list(set(c["citing_doi"] for c in targets) - set(d["citing_doi"] for d in existing))
    session = requests.Session()
    session.headers.update({"User-Agent": "FindReuse/1.0"})
    doi_dates = {d["citing_doi"]: datetime.strptime(d["pub_date"], "%Y-%m-%d") for d in existing}

    for doi in new_dois:
        try:
            resp = session.get(f"https://api.crossref.org/works/{doi}", timeout=15)
            if resp.status_code == 200:
                msg = resp.json().get("message", {})
                for df in ["published-online", "published-print", "published", "created"]:
                    dp = msg.get(df, {}).get("date-parts", [[]])
                    if dp and dp[0] and len(dp[0]) >= 2:
                        p = dp[0]
                        doi_dates[doi] = datetime(p[0], p[1] if len(p)>1 else 1, p[2] if len(p)>2 else 1)
                        break
        except Exception:
            pass
        if doi not in doi_dates:
            try:
                resp = session.get(f"https://api.openalex.org/works/doi:{doi}", timeout=15)
                if resp.status_code == 200:
                    pd_str = resp.json().get("publication_date", "")
                    if pd_str:
                        doi_dates[doi] = datetime.strptime(pd_str, "%Y-%m-%d")
            except Exception:
                pass
        time.sleep(0.03)

    added = 0
    for c in targets:
        doi, did = c["citing_doi"], c.get("dandiset_id", "")
        if doi in doi_dates and did in dc:
            pub, created = doi_dates[doi], dc[did]
            existing.append({
                "dandiset_id": did,
                "dandiset_name": c.get("dandiset_name", ""),
                "citing_doi": doi,
                "pub_date": pub.strftime("%Y-%m-%d"),
                "dandiset_created": created.strftime("%Y-%m-%d"),
                "delay_days": (pub - created).days,
                "delay_months": round((pub - created).days / 30.44, 1),
                "same_lab": c.get("same_lab", False),
                "source_archive": c.get("source_archive", "unclear"),
            })
            added += 1

    # Sync source_archive with classifications
    cls_archive = {}
    for c in cls_data["classifications"]:
        if c["classification"] == "REUSE":
            cls_archive[(c["citing_doi"], c.get("dandiset_id", ""))] = c.get("source_archive")

    synced = 0
    for d in existing:
        key = (d["citing_doi"], d["dandiset_id"])
        if key in cls_archive and cls_archive[key] != d.get("source_archive"):
            d["source_archive"] = cls_archive[key]
            synced += 1

    with open(delays_file, "w") as f:
        json.dump(existing, f, indent=2)
    print(f"  Added {added} delays, synced {synced} archives (total: {len(existing)})", file=sys.stderr)


def step9_andersen_gill():
    """Run Andersen-Gill Cox PH regression analysis."""
    run(
        ["python3", "andersen_gill_analysis.py"],
        "Step 9: Andersen-Gill Cox PH regression",
    )


def step10_regenerate_figures():
    """Regenerate all figures and flowcharts."""
    print("\n" + "="*60, file=sys.stderr)
    print("  Step 10: Regenerate all figures", file=sys.stderr)
    print("="*60, file=sys.stderr, flush=True)

    scripts = [
        ("analyze_reuse_delays.py", "Analysis figures"),
        ("render_phase2_flow.py", "Phase 2 flowchart"),
        ("render_dandiset_coverage_flow.py", "Phase 1 flowchart"),
        ("render_reference_flow.py", "Reference flow"),
        ("render_flow.py", "Paper fetching flow"),
    ]
    for script, desc in scripts:
        if Path(script).exists():
            run(["python3", script], f"  {desc}")

    # Regenerate Andersen-Gill forest plot from cached results
    if Path("output/andersen_gill_results.json").exists():
        run(["python3", "andersen_gill_analysis.py", "--plot-only"], "  Andersen-Gill forest plot")


def mirror_to_dandi_dir():
    """Copy key outputs to output/dandi/ for multi-archive consistency."""
    import shutil
    dandi_dir = Path("output/dandi")
    dandi_dir.mkdir(parents=True, exist_ok=True)
    (dandi_dir / "figures").mkdir(exist_ok=True)

    # Key data files
    file_map = {
        "output/all_dandiset_papers.json": "output/dandi/datasets.json",
        "output/all_classifications.json": "output/dandi/classifications.json",
        "output/results_dandi.json": "output/dandi/direct_refs.json",
        "output/direct_ref_classifications.json": "output/dandi/direct_ref_classifications.json",
        "output/dandi_reuse_delays.json": "output/dandi/delays.json",
        "output/andersen_gill_results.json": "output/dandi/andersen_gill_results.json",
    }
    for src, dst in file_map.items():
        if Path(src).exists():
            shutil.copy2(src, dst)

    # Figures
    if Path("output/figures").exists():
        for fig in Path("output/figures").glob("*.png"):
            shutil.copy2(fig, dandi_dir / "figures" / fig.name)

    # Flowcharts
    for flowchart in Path("output").glob("*.png"):
        shutil.copy2(flowchart, dandi_dir / "figures" / flowchart.name)

    print("  Mirrored outputs to output/dandi/", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description="Run complete DANDI reuse analysis pipeline")
    parser.add_argument("--skip-fetch", action="store_true",
                        help="Skip data fetching steps (use cached data)")
    parser.add_argument("--figures-only", action="store_true",
                        help="Only regenerate figures")
    parser.add_argument("--limit", type=int, default=None,
                        help="Cap to the first N dandisets (sorted by ID) for fast iteration. Default: all.")
    args = parser.parse_args()

    start = time.time()

    Path("output").mkdir(exist_ok=True)

    if args.figures_only:
        step10_regenerate_figures()
    else:
        if not args.skip_fetch:
            step1_discover_dandisets(limit=args.limit)
            if args.limit is None:
                step2_llm_find_papers()
            else:
                print("\nSkipping step 2 (LLM paper discovery) in --limit mode", file=sys.stderr)
            step3_merge_sources(limit=args.limit)
            step4_fetch_citations()
            step5_direct_refs(limit=args.limit)
        step6_fetch_text_and_classify(limit=args.limit)
        step6b_deduplicate_preprints()
        step7_classify_reuse_type()
        step8_update_delays()
        step9_andersen_gill()
        step10_regenerate_figures()

    # Mirror outputs to output/dandi/ for multi-archive consistency
    mirror_to_dandi_dir()

    elapsed = time.time() - start
    print(f"\n{'='*60}", file=sys.stderr)
    print(f"  Pipeline complete in {elapsed/60:.1f} minutes", file=sys.stderr)
    print(f"{'='*60}", file=sys.stderr)


if __name__ == "__main__":
    main()
