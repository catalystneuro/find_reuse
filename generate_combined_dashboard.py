#!/usr/bin/env python3
"""
generate_combined_dashboard.py - Generate unified dashboard from both analyses

Combines:
1. Direct dataset references (from convert_refs_to_classifications.py)
2. Citation-based classifications (from classify_citing_papers.py)

into a single interactive HTML dashboard.

Usage:
    python generate_combined_dashboard.py \\
        --refs output/direct_ref_classifications.json \\
        --citations output/test_all_classifications.json \\
        -o output/combined_dashboard.html --open
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path


def merge_data(refs_file: Path, citations_file: Path) -> dict:
    """Merge direct references and citation classifications.

    Deduplicates on (citing_doi, dandiset_id) pairs. When both sources
    have an entry for the same pair, marks it as source_type="both".
    """
    with open(refs_file) as f:
        refs_data = json.load(f)
    with open(citations_file) as f:
        cit_data = json.load(f)

    refs_cls = refs_data.get("classifications", [])
    cit_cls = cit_data.get("classifications", [])

    # Index citation entries by (doi, dandiset_id)
    cit_index = {}
    for c in cit_cls:
        key = (c["citing_doi"], c["dandiset_id"])
        cit_index[key] = c

    merged = []
    seen = set()

    # Add all direct reference entries, merging with citations where they overlap
    # Skip direct refs classified as NEITHER (false positives like equation fragments)
    skipped_neither = 0
    for r in refs_cls:
        key = (r["citing_doi"], r["dandiset_id"])

        if key in cit_index:
            # Both sources — keep citation entry but enrich with ref data
            seen.add(key)
            c = cit_index[key].copy()
            c["source_type"] = "both"
            c["match_patterns"] = r.get("match_patterns", [])
            # If citation doesn't have title but ref does, use ref's
            if not c.get("citing_title") and r.get("citing_title"):
                c["citing_title"] = r["citing_title"]
            if not c.get("citing_journal") and r.get("citing_journal"):
                c["citing_journal"] = r["citing_journal"]
            if not c.get("citing_date") and r.get("citing_date"):
                c["citing_date"] = r["citing_date"]
            if not c.get("dandiset_name") and r.get("dandiset_name"):
                c["dandiset_name"] = r["dandiset_name"]
            merged.append(c)
        elif r.get("classification") == "NEITHER":
            # Skip false positive direct references
            seen.add(key)
            skipped_neither += 1
        else:
            # Only in direct refs (PRIMARY or REUSE)
            seen.add(key)
            r_copy = r.copy()
            r_copy["source_type"] = "direct_reference"
            merged.append(r_copy)

    # Add citation entries not in direct refs
    for c in cit_cls:
        key = (c["citing_doi"], c["dandiset_id"])
        if key not in seen:
            c_copy = c.copy()
            c_copy["source_type"] = "citation_analysis"
            merged.append(c_copy)

    # Deduplicate preprints and their published versions
    preprint_map_file = Path(__file__).parent / ".preprint_published_map.json"
    preprint_dedup_count = 0
    if preprint_map_file.exists():
        with open(preprint_map_file) as f:
            preprint_map = json.load(f)  # {preprint_doi: published_doi}

        # Only remap if BOTH the preprint and published DOI are in merged
        merged_dois = set(m["citing_doi"] for m in merged)
        active_map = {}
        for preprint_doi, pub_doi in preprint_map.items():
            if preprint_doi in merged_dois and pub_doi.lower() in {d.lower() for d in merged_dois}:
                # Find the actual casing of the published DOI in merged
                pub_doi_actual = next(d for d in merged_dois if d.lower() == pub_doi.lower())
                active_map[preprint_doi] = pub_doi_actual

        if active_map:
            # Classification priority for deduplication
            cls_priority = {"REUSE": 0, "PRIMARY": 1, "MENTION": 2, "NEITHER": 3}

            # Remap preprint entries to published DOI
            for entry in merged:
                if entry["citing_doi"] in active_map:
                    entry["preprint_doi"] = entry["citing_doi"]
                    entry["citing_doi"] = active_map[entry["citing_doi"]]

            # Now deduplicate on (citing_doi, dandiset_id), keeping best classification
            deduped = {}
            for entry in merged:
                key = (entry["citing_doi"], entry["dandiset_id"])
                if key not in deduped:
                    deduped[key] = entry
                else:
                    # Keep the entry with higher-priority classification
                    existing_pri = cls_priority.get(deduped[key].get("classification", "NEITHER"), 3)
                    new_pri = cls_priority.get(entry.get("classification", "NEITHER"), 3)
                    if new_pri < existing_pri:
                        entry["preprint_doi"] = entry.get("preprint_doi") or deduped[key].get("preprint_doi")
                        deduped[key] = entry
                    else:
                        deduped[key]["preprint_doi"] = deduped[key].get("preprint_doi") or entry.get("preprint_doi")
                    preprint_dedup_count += 1

            merged = list(deduped.values())

            # Enrich with published metadata (title, journal, date) where missing
            for entry in merged:
                if entry.get("preprint_doi") and not entry.get("citing_title"):
                    # Try to get title from published version's other entries
                    pub_doi = entry["citing_doi"]
                    for other in merged:
                        if other["citing_doi"] == pub_doi and other.get("citing_title"):
                            entry["citing_title"] = other["citing_title"]
                            break

    # Enrich missing metadata (title, journal, date) from OpenAlex cache
    metadata_cache_file = Path(__file__).parent / ".doi_metadata_cache.json"
    if metadata_cache_file.exists():
        with open(metadata_cache_file) as f:
            meta_cache = json.load(f)
        titles_filled = journals_filled = dates_filled = 0
        for entry in merged:
            cached = meta_cache.get(entry["citing_doi"])
            if not cached:
                continue
            if not entry.get("citing_title") and cached.get("title"):
                entry["citing_title"] = cached["title"]
                titles_filled += 1
            if not entry.get("citing_journal") and cached.get("journal"):
                entry["citing_journal"] = cached["journal"]
                journals_filled += 1
            if not entry.get("citing_date") and cached.get("date"):
                entry["citing_date"] = cached["date"]
                dates_filled += 1
        filled = [f"{titles_filled} titles", f"{journals_filled} journals", f"{dates_filled} dates"]
        print(f"  Filled from OpenAlex cache: {', '.join(filled)}")

    # Build metadata
    ref_only = sum(1 for m in merged if m.get("source_type") == "direct_reference")
    cit_only = sum(1 for m in merged if m.get("source_type") == "citation_analysis")
    both = sum(1 for m in merged if m.get("source_type") == "both")

    reuse_count = sum(
        1 for m in merged if m.get("classification") == "REUSE"
    )
    primary_count = sum(
        1 for m in merged if m.get("classification") == "PRIMARY"
    )
    mention_count = sum(
        1 for m in merged if m.get("classification") == "MENTION"
    )
    neither_count = sum(
        1 for m in merged if m.get("classification") == "NEITHER"
    )

    all_ds = set(m["dandiset_id"] for m in merged)
    reuse_ds = set(
        m["dandiset_id"] for m in merged if m.get("classification") == "REUSE"
    )
    reuse_papers = set(
        m["citing_doi"] for m in merged if m.get("classification") == "REUSE"
    )

    metadata = {
        "source": "combined",
        "refs_file": str(refs_file),
        "citations_file": str(citations_file),
        "total_pairs": len(merged),
        "source_breakdown": {
            "direct_reference_only": ref_only,
            "citation_analysis_only": cit_only,
            "both_sources": both,
            "direct_ref_false_positives_excluded": skipped_neither,
            "preprint_published_deduped": preprint_dedup_count,
        },
        "classification_counts": {
            "PRIMARY": primary_count,
            "REUSE": reuse_count,
            "MENTION": mention_count,
            "NEITHER": neither_count,
        },
        "unique_dandisets": len(all_ds),
        "dandisets_with_reuse": len(reuse_ds),
        "unique_reuse_papers": len(reuse_papers),
        "citation_metadata": cit_data.get("metadata", {}),
        "refs_metadata": refs_data.get("metadata", {}),
    }

    return {"metadata": metadata, "classifications": merged}


HTML_TEMPLATE = '''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>DANDI Dataset Reuse - Combined Dashboard</title>
    <style>
        * { box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            line-height: 1.6; color: #333; max-width: 1400px; margin: 0 auto;
            padding: 20px; background: #f5f5f5;
        }
        h1 { color: #2c3e50; border-bottom: 3px solid #3498db; padding-bottom: 10px; }
        .metadata { font-size: 0.85em; color: #7f8c8d; margin-bottom: 20px; }
        .summary { display: flex; flex-wrap: wrap; gap: 16px; margin-bottom: 20px; }
        .summary-panel {
            background: white; border-radius: 8px; padding: 16px 20px; min-width: 260px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1); flex: 1;
        }
        .summary-panel.refs { border-top: 4px solid #6c3483; }
        .summary-panel.cites { border-top: 4px solid #2471a3; }
        .summary-panel .panel-title {
            font-size: 0.8em; text-transform: uppercase; color: #7f8c8d;
            margin-bottom: 8px; font-weight: 600;
        }
        .summary-panel .panel-total {
            font-size: 1.6em; font-weight: bold; color: #2c3e50; margin-bottom: 8px;
        }
        .summary-tree { font-size: 0.9em; line-height: 1.8; }
        .summary-tree .tree-row { display: flex; justify-content: space-between; padding: 1px 0; }
        .summary-tree .tree-row.indent1 { padding-left: 20px; }
        .summary-tree .tree-row.indent2 { padding-left: 40px; }
        .tree-label { color: #555; }
        .tree-count { font-weight: 600; color: #2c3e50; }
        .tree-count.primary { color: #1a5276; }
        .tree-count.reuse { color: #1e8449; }
        .tree-count.mention { color: #566573; }
        .tree-count.neither { color: #b9770e; }
        .tree-count.same-lab { color: #1a5276; }
        .tree-count.diff-lab { color: #27ae60; }

        .summary-card {
            background: white; border-radius: 8px; padding: 16px 20px; min-width: 130px;
            text-align: center; box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }
        .summary-card .count { font-size: 2.2em; font-weight: bold; color: #2c3e50; }
        .summary-card .label { font-size: 0.8em; color: #7f8c8d; text-transform: uppercase; }
        .summary-card.reuse { border-top: 4px solid #27ae60; }
        .summary-card.primary { border-top: 4px solid #2980b9; }
        .summary-card.mention { border-top: 4px solid #95a5a6; }
        .summary-card.neither { border-top: 4px solid #f39c12; }
        .summary-card.total { border-top: 4px solid #2c3e50; }
        .summary-card.datasets { border-top: 4px solid #8e44ad; }
        .summary-sub { font-size: 0.75em; color: #7f8c8d; margin-top: 2px; }
        .tree-papers { font-size: 0.85em; color: #95a5a6; font-weight: normal; }

        .tabs { display: flex; gap: 0; margin-bottom: 0; }
        .tab {
            padding: 10px 24px; cursor: pointer; background: #ecf0f1;
            border: 1px solid #ddd; border-bottom: none; border-radius: 8px 8px 0 0;
            font-weight: 600; color: #7f8c8d;
        }
        .tab.active { background: white; color: #2c3e50; border-bottom: 1px solid white; margin-bottom: -1px; z-index: 1; }
        .tab-content {
            display: none; background: white; border: 1px solid #ddd;
            border-radius: 0 8px 8px 8px; padding: 20px;
        }
        .tab-content.active { display: block; }

        .filters {
            background: #f8f9fa; padding: 12px; border-radius: 6px;
            margin-bottom: 15px; display: flex; flex-wrap: wrap; gap: 10px; align-items: center;
        }
        .filters label { cursor: pointer; font-size: 0.9em; }
        .filters input[type="checkbox"] { margin-right: 4px; }
        .search-box {
            padding: 8px 12px; border: 1px solid #ddd; border-radius: 4px;
            font-size: 0.95em; min-width: 250px;
        }

        .paper-list { display: flex; flex-direction: column; gap: 10px; }
        .paper-card {
            background: #fafafa; border-radius: 6px; border: 1px solid #ecf0f1;
            overflow: hidden;
        }
        .paper-header {
            padding: 12px 16px; cursor: pointer; display: flex;
            justify-content: space-between; align-items: center; gap: 12px;
        }
        .paper-header:hover { background: #f0f3f5; }
        .paper-info { flex: 1; min-width: 0; }
        .paper-title { font-weight: 600; color: #2c3e50; font-size: 0.95em; }
        .paper-title a { color: #2c3e50; text-decoration: none; }
        .paper-title a:hover { color: #3498db; }
        .paper-doi { font-size: 0.8em; color: #3498db; margin-top: 2px; }
        .paper-doi a { color: #3498db; text-decoration: none; }
        .paper-meta { font-size: 0.8em; color: #95a5a6; margin-top: 3px; }

        .badge {
            padding: 5px 12px; border-radius: 16px; font-weight: 600;
            font-size: 0.78em; text-transform: uppercase; white-space: nowrap;
        }
        .badge.reuse { background: #d5f4e6; color: #1e8449; }
        .badge.primary { background: #d4e6f1; color: #1a5276; }
        .badge.mention { background: #eaecee; color: #566573; }
        .badge.neither { background: #fdebd0; color: #b9770e; }
        .badge-sub { font-size: 0.7em; padding: 2px 8px; border-radius: 10px; margin-left: 4px; }
        .badge-sub.same-lab { background: #d6eaf8; color: #1a5276; }
        .badge-sub.diff-lab { background: #e8f8f0; color: #27ae60; }
        .badge-sub.src-ref { background: #f5eef8; color: #6c3483; }
        .badge-sub.src-cite { background: #eaf2f8; color: #2471a3; }
        .badge-sub.src-both { background: #fef9e7; color: #b7950b; }

        .expand-icon { font-size: 1.1em; color: #bdc3c7; transition: transform 0.2s; }
        .paper-card.expanded .expand-icon { transform: rotate(180deg); }
        .paper-details { display: none; padding: 0; border-top: 1px solid #ecf0f1; }
        .paper-card.expanded .paper-details { display: block; }
        .ds-tabs { display: flex; flex-wrap: wrap; gap: 0; border-bottom: 2px solid #ecf0f1; background: #f8f9fa; }
        .ds-tab {
            padding: 8px 14px; cursor: pointer; font-size: 0.82em; font-weight: 600;
            border: none; background: none; color: #7f8c8d; border-bottom: 2px solid transparent;
            margin-bottom: -2px; transition: color 0.15s, border-color 0.15s;
            display: flex; align-items: center; gap: 6px;
        }
        .ds-tab:hover { color: #2c3e50; background: #eef1f3; }
        .ds-tab.active { color: #2c3e50; border-bottom-color: #3498db; }
        .ds-tab .tab-badge { font-size: 0.8em; padding: 1px 6px; border-radius: 8px; }
        .ds-tab-panel { display: none; padding: 12px 16px; }
        .ds-tab-panel.active { display: block; }

        .reasoning {
            background: #f8f9fa; padding: 10px 14px; border-radius: 4px;
            margin: 10px 0; border-left: 3px solid #3498db; font-size: 0.9em;
        }
        .confidence { display: inline-block; padding: 2px 6px; border-radius: 3px; font-size: 0.75em; margin-left: 8px; }
        .confidence.conf-high { background: #d5f4e6; color: #1e8449; }
        .confidence.conf-mid { background: #fdebd0; color: #b9770e; }
        .confidence.conf-low { background: #fadbd8; color: #c0392b; }

        .context-excerpt {
            background: #fff; border: 1px solid #ecf0f1; border-radius: 4px;
            padding: 10px; margin: 6px 0; font-size: 0.85em; line-height: 1.5;
        }
        .context-method { font-size: 0.75em; color: #95a5a6; margin-bottom: 4px; }
        .excerpt-preview { cursor: pointer; }
        .excerpt-preview .excerpt-truncated { display: inline; }
        .excerpt-preview .excerpt-full { display: none; }
        .excerpt-preview.expanded .excerpt-truncated { display: none; }
        .excerpt-preview.expanded .excerpt-full { display: inline; }
        .excerpt-toggle {
            color: #3498db; cursor: pointer; font-size: 0.82em; font-weight: 600;
            margin-left: 4px; user-select: none;
        }
        .excerpt-toggle:hover { text-decoration: underline; }
        .excerpt-page {
            background: #fff; border: 1px solid #ecf0f1; border-radius: 4px;
            padding: 10px; margin: 6px 0; font-size: 0.85em; line-height: 1.5;
        }
        .excerpt-nav {
            display: flex; align-items: center; gap: 10px; margin-top: 6px; font-size: 0.85em;
        }
        .excerpt-nav button {
            padding: 3px 10px; border: 1px solid #bdc3c7; border-radius: 4px;
            background: #f8f9fa; cursor: pointer; font-size: 0.9em; color: #2c3e50;
        }
        .excerpt-nav button:hover { background: #ecf0f1; }
        .excerpt-counter { color: #7f8c8d; font-size: 0.9em; }
        .cite-highlight { background: #fff3cd; border-radius: 2px; padding: 0 2px; font-weight: 600; }

        .match-pattern {
            background: #f5eef8; border: 1px solid #e8daef; border-radius: 4px;
            padding: 6px 10px; margin: 4px 0; font-size: 0.85em; font-family: monospace;
        }
        .match-pattern .pattern-type { color: #6c3483; font-weight: 600; font-family: sans-serif; }

        .dataset-section { margin-bottom: 20px; }
        .dataset-header {
            padding: 12px 16px; background: #f8f9fa; border-radius: 6px;
            cursor: pointer; display: flex; justify-content: space-between;
            align-items: center; border: 1px solid #ecf0f1;
        }
        .dataset-header:hover { background: #ecf0f1; }
        .dataset-name { font-weight: 600; color: #2c3e50; }
        .dataset-id { font-size: 0.85em; color: #3498db; }
        .dataset-id a { color: #3498db; text-decoration: none; }
        .dataset-stats { display: flex; gap: 8px; font-size: 0.85em; align-items: center; }
        .stat-chip { padding: 2px 8px; border-radius: 10px; }
        .stat-chip.reuse { background: #d5f4e6; color: #1e8449; }
        .stat-chip.primary { background: #d4e6f1; color: #1a5276; }
        .stat-chip.mention { background: #eaecee; color: #566573; }
        .stat-chip.other { background: #fdebd0; color: #b9770e; }
        .stat-chip.ref { background: #f5eef8; color: #6c3483; }
        .stat-chip.cite { background: #eaf2f8; color: #2471a3; }

        .dataset-papers { display: none; padding: 12px 16px 4px; }
        .dataset-section.expanded .dataset-papers { display: block; }

        .no-results { text-align: center; padding: 40px; color: #7f8c8d; }
        .pagination {
            display: flex; justify-content: center; align-items: center; gap: 6px;
            margin-top: 16px; padding: 10px; flex-wrap: wrap;
        }
        .pagination button {
            padding: 6px 12px; border: 1px solid #ddd; border-radius: 4px;
            background: white; cursor: pointer; font-size: 0.9em; color: #2c3e50;
        }
        .pagination button:hover { background: #ecf0f1; }
        .pagination button.active { background: #3498db; color: white; border-color: #3498db; }
        .pagination button:disabled { opacity: 0.4; cursor: default; }
        .pagination .page-info { font-size: 0.85em; color: #7f8c8d; margin: 0 8px; }
        @media (max-width: 768px) {
            .paper-header { flex-direction: column; align-items: flex-start; }
            .badge { margin-top: 8px; }
        }
    </style>
</head>
<body>
    <h1>DANDI Dataset Reuse - Combined Dashboard</h1>
    <div class="metadata" id="metadata"></div>
    <div class="summary" id="summary"></div>

    <div class="tabs">
        <div class="tab active" onclick="switchTab('papers')">Papers</div>
        <div class="tab" onclick="switchTab('datasets')">By Dataset</div>
    </div>

    <div class="tab-content active" id="tab-papers">
        <div class="filters">
            <input type="text" class="search-box" id="search" placeholder="Search by title, DOI, or dataset ID...">
            <label><input type="checkbox" class="filter-cb" value="REUSE" checked> Reuse</label>
            <label><input type="checkbox" class="filter-cb" value="PRIMARY" checked> Primary</label>
            <label><input type="checkbox" class="filter-cb" value="MENTION" checked> Mention</label>
            <label><input type="checkbox" class="filter-cb" value="NEITHER" checked> Neither</label>
            <span style="border-left:1px solid #ddd;padding-left:10px;margin-left:4px">
                <label><input type="checkbox" class="source-cb" value="direct_reference" checked> Direct refs</label>
                <label><input type="checkbox" class="source-cb" value="citation_analysis" checked> Citation analysis</label>
                <label><input type="checkbox" class="source-cb" value="both" checked> Both sources</label>
            </span>
            <span style="border-left:1px solid #ddd;padding-left:10px;margin-left:4px">
                <label><input type="checkbox" id="filterSameLab"> Same lab only</label>
                <label><input type="checkbox" id="filterDiffLab"> Diff lab only</label>
            </span>
        </div>
        <div class="paper-list" id="paperList"></div>
        <div class="pagination" id="paperPagination"></div>
    </div>

    <div class="tab-content" id="tab-datasets">
        <div class="filters">
            <input type="text" class="search-box" id="dsSearch" placeholder="Search by dataset ID or name...">
        </div>
        <div id="datasetList"></div>
        <div class="pagination" id="datasetPagination"></div>
    </div>

    <script>
        const rawData = __DATA_PLACEHOLDER__;
        const meta = rawData.metadata || {};
        const classifications = rawData.classifications || [];

        function escapeHtml(t) {
            const d = document.createElement('div');
            d.textContent = t;
            return d.innerHTML;
        }

        function highlightRef(escapedText, excerpt) {
            const offset = excerpt.highlight_offset;
            if (offset == null) return escapedText;
            const raw = excerpt.text || '';
            let refStart = -1, refEnd = -1;
            if (excerpt.reference_number) {
                const rn = String(excerpt.reference_number);
                const openers = {'[': ']', '(': ')'};
                for (const [open, close] of Object.entries(openers)) {
                    let searchFrom = Math.max(0, offset - 15);
                    let oi = raw.indexOf(open, searchFrom);
                    while (oi >= 0 && oi < offset + 15) {
                        const ci = raw.indexOf(close, oi);
                        if (ci > oi && ci - oi < 60 && raw.substring(oi, ci + 1).includes(rn)) {
                            refStart = oi; refEnd = ci + 1; break;
                        }
                        oi = raw.indexOf(open, oi + 1);
                    }
                    if (refStart >= 0) break;
                }
            }
            if (refStart < 0 && excerpt.authors && excerpt.year) {
                const surname = excerpt.authors[0];
                const year = excerpt.year;
                const si = raw.indexOf(surname, Math.max(0, offset - 40));
                if (si >= 0 && si < offset + 40) {
                    const yi = raw.indexOf(year, si);
                    if (yi >= 0 && yi < si + 80) {
                        refStart = si;
                        refEnd = yi + year.length;
                        if (raw[refEnd] === ')') refEnd++;
                    }
                }
            }
            if (refStart < 0) return escapedText;
            const before = escapeHtml(raw.substring(0, refStart));
            const ref = escapeHtml(raw.substring(refStart, refEnd));
            const after = escapeHtml(raw.substring(refEnd));
            return before + '<span class="cite-highlight">' + ref + '</span>' + after;
        }

        function clsKey(c) { return (c || 'neither').toLowerCase(); }
        function isReuse(c) { return c === 'REUSE'; }
        function confClass(v) { return v >= 7 ? 'conf-high' : v >= 4 ? 'conf-mid' : 'conf-low'; }

        function labBadge(p) {
            if (p.classification !== 'REUSE' || p.same_lab == null) return '';
            const cls = p.same_lab ? 'same-lab' : 'diff-lab';
            const label = p.same_lab ? 'same lab' : 'diff lab';
            const conf = p.same_lab_confidence != null ? ` (${p.same_lab_confidence}/10)` : '';
            return `<span class="badge-sub ${cls}">${label}${conf}</span>`;
        }

        function sourceBadge(p) {
            const st = p.source_type || 'citation_analysis';
            if (st === 'direct_reference') return '<span class="badge-sub src-ref">direct ref</span>';
            if (st === 'both') return '<span class="badge-sub src-both">both sources</span>';
            return '<span class="badge-sub src-cite">citation</span>';
        }

        // Render metadata
        const sb = meta.source_breakdown || {};
        document.getElementById('metadata').innerHTML = [
            `Direct reference pairs: ${sb.direct_reference_only || 0}`,
            `Citation analysis pairs: ${sb.citation_analysis_only || 0}`,
            `Both sources: ${sb.both_sources || 0}`,
            sb.preprint_published_deduped ? `Preprint/published deduped: ${sb.preprint_published_deduped}` : '',
            `Total pairs: ${meta.total_pairs || 0}`,
            meta.citation_metadata && meta.citation_metadata.model ? `LLM: ${meta.citation_metadata.model}` : '',
        ].filter(Boolean).join(' | ');

        // Render summary
        function renderSummary() {
            // Count pairs and unique papers by source type and classification
            let refTotal = 0, refPrimary = 0, refReuse = 0, refNeither = 0;
            let refReuseSame = 0, refReuseDiff = 0;
            const refTotalP = new Set(), refPrimaryP = new Set(), refReuseP = new Set(), refNeitherP = new Set();
            const refReuseSameP = new Set(), refReuseDiffP = new Set();
            let citeTotal = 0, citeReuse = 0, citeMention = 0, citeNeither = 0;
            let citeReuseSame = 0, citeReuseDiff = 0;
            const citeTotalP = new Set(), citeReuseP = new Set(), citeMentionP = new Set(), citeNeitherP = new Set();
            const citeReuseSameP = new Set(), citeReuseDiffP = new Set();

            classifications.forEach(p => {
                const c = p.classification || 'NEITHER';
                const st = p.source_type || 'citation_analysis';
                const doi = p.citing_doi;

                if (st === 'direct_reference' || st === 'both') {
                    refTotal++; refTotalP.add(doi);
                    if (c === 'PRIMARY') { refPrimary++; refPrimaryP.add(doi); }
                    else if (c === 'REUSE') {
                        refReuse++; refReuseP.add(doi);
                        if (p.same_lab === true) { refReuseSame++; refReuseSameP.add(doi); }
                        else if (p.same_lab === false) { refReuseDiff++; refReuseDiffP.add(doi); }
                    }
                    else if (c === 'NEITHER') { refNeither++; refNeitherP.add(doi); }
                }

                if (st === 'citation_analysis' || st === 'both') {
                    citeTotal++; citeTotalP.add(doi);
                    if (c === 'REUSE') {
                        citeReuse++; citeReuseP.add(doi);
                        if (p.same_lab === true) { citeReuseSame++; citeReuseSameP.add(doi); }
                        else if (p.same_lab === false) { citeReuseDiff++; citeReuseDiffP.add(doi); }
                    }
                    else if (c === 'MENTION') { citeMention++; citeMentionP.add(doi); }
                    else if (c === 'NEITHER') { citeNeither++; citeNeitherP.add(doi); }
                }
            });

            function treeRow(label, pairs, papers, cls, indent) {
                const indentCls = indent ? ` indent${indent}` : '';
                const countCls = cls ? ` ${cls}` : '';
                const paperStr = papers !== pairs ? ` <span class="tree-papers">(${papers} papers)</span>` : '';
                return `<div class="tree-row${indentCls}"><span class="tree-label">${label}</span><span class="tree-count${countCls}">${pairs}${paperStr}</span></div>`;
            }

            let html = '';

            // Direct references panel
            html += `<div class="summary-panel refs">`;
            html += `<div class="panel-title">Direct References</div>`;
            html += `<div class="panel-total">${refTotal} pairs <span class="tree-papers">(${refTotalP.size} papers)</span></div>`;
            html += `<div class="summary-tree">`;
            html += treeRow('Primary', refPrimary, refPrimaryP.size, 'primary', 1);
            html += treeRow('Reuse', refReuse, refReuseP.size, 'reuse', 1);
            if (refReuseSame + refReuseDiff > 0) {
                html += treeRow('Same lab', refReuseSame, refReuseSameP.size, 'same-lab', 2);
                html += treeRow('Different lab', refReuseDiff, refReuseDiffP.size, 'diff-lab', 2);
            }
            html += treeRow('Neither', refNeither, refNeitherP.size, 'neither', 1);
            html += `</div></div>`;

            // Citation analysis panel
            html += `<div class="summary-panel cites">`;
            html += `<div class="panel-title">Citation Analysis</div>`;
            html += `<div class="panel-total">${citeTotal} pairs <span class="tree-papers">(${citeTotalP.size} papers)</span></div>`;
            html += `<div class="summary-tree">`;
            html += treeRow('Reuse', citeReuse, citeReuseP.size, 'reuse', 1);
            if (citeReuseSame + citeReuseDiff > 0) {
                html += treeRow('Same lab', citeReuseSame, citeReuseSameP.size, 'same-lab', 2);
                html += treeRow('Different lab', citeReuseDiff, citeReuseDiffP.size, 'diff-lab', 2);
            }
            html += treeRow('Mention', citeMention, citeMentionP.size, 'mention', 1);
            html += treeRow('Neither', citeNeither, citeNeitherP.size, 'neither', 1);
            html += `</div></div>`;

            // Overall totals panel
            const allReuse = refReuse + citeReuse;
            const allReuseSame = refReuseSame + citeReuseSame;
            const allReuseDiff = refReuseDiff + citeReuseDiff;
            const allReuseP = new Set([...refReuseP, ...citeReuseP]);
            const allReuseSameP = new Set([...refReuseSameP, ...citeReuseSameP]);
            const allReuseDiffP = new Set([...refReuseDiffP, ...citeReuseDiffP]);
            const reuseDs = meta.dandisets_with_reuse || 0;
            html += `<div class="summary-panel totals">`;
            html += `<div class="panel-title">Combined Totals</div>`;
            html += `<div class="panel-total">${reuseDs} dandisets with reuse</div>`;
            html += `<div class="summary-tree">`;
            html += treeRow('Reuse', allReuse, allReuseP.size, 'reuse', 1);
            html += treeRow('Same lab', allReuseSame, allReuseSameP.size, 'same-lab', 2);
            html += treeRow('Different lab', allReuseDiff, allReuseDiffP.size, 'diff-lab', 2);
            html += `</div></div>`;

            document.getElementById('summary').innerHTML = html;
        }

        // Pagination helper
        const PAGE_SIZE = 50;

        function renderPaginationControls(containerId, totalItems, currentPage, onPageChange) {
            const el = document.getElementById(containerId);
            const totalPages = Math.ceil(totalItems / PAGE_SIZE);
            if (totalPages <= 1) { el.innerHTML = ''; return; }
            const start = currentPage * PAGE_SIZE + 1;
            const end = Math.min((currentPage + 1) * PAGE_SIZE, totalItems);
            let html = '';
            html += `<button ${currentPage === 0 ? 'disabled' : ''} onclick="${onPageChange}(0)">&#171;</button>`;
            html += `<button ${currentPage === 0 ? 'disabled' : ''} onclick="${onPageChange}(${currentPage - 1})">&#8249;</button>`;
            const maxButtons = 7;
            let pages = [];
            if (totalPages <= maxButtons) {
                for (let i = 0; i < totalPages; i++) pages.push(i);
            } else {
                pages.push(0);
                let lo = Math.max(1, currentPage - 2);
                let hi = Math.min(totalPages - 2, currentPage + 2);
                if (lo <= 2) { lo = 1; hi = Math.max(hi, 5); }
                if (hi >= totalPages - 3) { hi = totalPages - 2; lo = Math.min(lo, totalPages - 6); }
                lo = Math.max(1, lo); hi = Math.min(totalPages - 2, hi);
                if (lo > 1) pages.push(-1);
                for (let i = lo; i <= hi; i++) pages.push(i);
                if (hi < totalPages - 2) pages.push(-1);
                pages.push(totalPages - 1);
            }
            pages.forEach(pg => {
                if (pg === -1) { html += '<span style="color:#95a5a6">...</span>'; return; }
                html += `<button class="${pg === currentPage ? 'active' : ''}" onclick="${onPageChange}(${pg})">${pg + 1}</button>`;
            });
            html += `<button ${currentPage >= totalPages - 1 ? 'disabled' : ''} onclick="${onPageChange}(${currentPage + 1})">&#8250;</button>`;
            html += `<button ${currentPage >= totalPages - 1 ? 'disabled' : ''} onclick="${onPageChange}(${totalPages - 1})">&#187;</button>`;
            html += `<span class="page-info">${start}-${end} of ${totalItems}</span>`;
            el.innerHTML = html;
        }

        // Papers tab — group all dandiset entries for a single paper under one card
        let currentPaperPage = 0;
        let currentFilteredPapers = [];

        const CLS_PRIORITY = {REUSE: 0, PRIMARY: 1, MENTION: 2, NEITHER: 3};

        function buildPaperGroups(items) {
            const groups = {};
            items.forEach(p => {
                const doi = p.citing_doi;
                if (!groups[doi]) {
                    groups[doi] = {
                        citing_doi: doi,
                        citing_title: p.citing_title,
                        citing_journal: p.citing_journal,
                        citing_date: p.citing_date,
                        preprint_doi: null,
                        entries: [],
                    };
                }
                const g = groups[doi];
                // Keep best metadata
                if (!g.citing_title && p.citing_title) g.citing_title = p.citing_title;
                if (!g.citing_journal && p.citing_journal) g.citing_journal = p.citing_journal;
                if (!g.citing_date && p.citing_date) g.citing_date = p.citing_date;
                if (p.preprint_doi) g.preprint_doi = p.preprint_doi;
                g.entries.push(p);
            });
            // Sort entries within each group: REUSE first, then by dandiset_id
            Object.values(groups).forEach(g => {
                g.entries.sort((a, b) => {
                    const ca = CLS_PRIORITY[a.classification] ?? 3;
                    const cb = CLS_PRIORITY[b.classification] ?? 3;
                    return ca - cb || (a.dandiset_id || '').localeCompare(b.dandiset_id || '');
                });
                // Top-level classification = highest priority across entries
                g.topCls = g.entries.reduce((best, e) => {
                    const p = CLS_PRIORITY[e.classification] ?? 3;
                    return p < best ? p : best;
                }, 3);
            });
            return Object.values(groups).sort((a, b) => {
                const da = a.citing_date || '';
                const db = b.citing_date || '';
                if (da !== db) return db.localeCompare(da);  // newest first
                return a.topCls - b.topCls;
            });
        }

        function renderMatchPatterns(patterns) {
            if (!patterns || !patterns.length) return '';
            return patterns.map(m => {
                return `<div class="match-pattern"><span class="pattern-type">${escapeHtml(m.pattern_type)}</span>: ${escapeHtml(m.matched_string)}</div>`;
            }).join('');
        }

        function renderDandisetEntry(p, cardIdx, entryIdx) {
            const cls = clsKey(p.classification);
            const allExcerpts = p.context_excerpts || [];
            let excerptsHTML = '';
            if (allExcerpts.length > 0) {
                const pagerId = `pager-${cardIdx}-${entryIdx}`;
                const pages = allExcerpts.map((e, ei) => {
                    const highlighted = highlightRef(escapeHtml(e.text || ''), e);
                    return `<div class="excerpt-page" data-pager="${pagerId}" data-page="${ei}" style="${ei > 0 ? 'display:none' : ''}"><div class="context-method">Found via: ${escapeHtml(e.method || 'unknown')}</div>${highlighted}</div>`;
                }).join('');
                const navHTML = allExcerpts.length > 1
                    ? `<div class="excerpt-nav"><button onclick="excerptNav('${pagerId}',-1)">&#9664; Prev</button><span class="excerpt-counter" id="${pagerId}-counter">1 / ${allExcerpts.length}</span><button onclick="excerptNav('${pagerId}',1)">Next &#9654;</button></div>`
                    : '';
                excerptsHTML = `<div style="margin-top:6px"><strong>Context Excerpts:</strong><div class="excerpt-pager" id="${pagerId}" data-current="0" data-total="${allExcerpts.length}">${pages}${navHTML}</div></div>`;
            }
            const matchPats = renderMatchPatterns(p.match_patterns);

            return `
            <div style="padding:8px 0">
                <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap">
                    <div>
                        <a href="https://dandiarchive.org/dandiset/${p.dandiset_id}" target="_blank" style="color:#3498db;text-decoration:none;font-weight:600">${p.dandiset_id}</a>
                        ${p.dandiset_name ? ' <span style="color:#7f8c8d;font-size:0.85em">' + escapeHtml(p.dandiset_name.substring(0, 60)) + (p.dandiset_name.length > 60 ? '...' : '') + '</span>' : ''}
                    </div>
                    <div><span class="badge ${cls}" style="font-size:0.7em;padding:3px 8px">${(p.classification || 'Neither').replace(/_/g, ' ')}</span>${sourceBadge(p)}${labBadge(p)}</div>
                </div>
                ${p.reasoning ? `<div class="reasoning" style="margin:6px 0"><strong>Reasoning</strong>${p.confidence != null ? `<span class="confidence ${confClass(p.confidence)}">${p.confidence}/10</span>` : ''}<br>${escapeHtml(p.reasoning)}</div>` : ''}
                ${p.cited_doi ? `<div class="paper-meta">Cited paper: <a href="https://doi.org/${p.cited_doi}" target="_blank">${p.cited_doi}</a></div>` : ''}
                ${matchPats ? `<div style="margin-top:6px"><strong>Dataset Reference Matches:</strong>${matchPats}</div>` : ''}
                ${excerptsHTML}
            </div>`;
        }

        function renderPaperCard(group, globalIdx) {
            const topClsName = ['REUSE','PRIMARY','MENTION','NEITHER'][group.topCls] || 'NEITHER';
            const cls = clsKey(topClsName);
            const title = group.citing_title || group.citing_doi || 'Unknown';
            const nDs = group.entries.length;
            const dsLabel = nDs > 1 ? `${nDs} dandisets` : '1 dandiset';

            // Collect unique dandiset IDs for header preview
            const dsIds = group.entries.map(e => e.dandiset_id);

            // Build details: tabs for multi-dandiset, plain for single
            let detailsHTML;
            if (nDs === 1) {
                detailsHTML = `<div style="padding:12px 16px">${renderDandisetEntry(group.entries[0], globalIdx, 0)}</div>`;
            } else {
                const tabsHTML = group.entries.map((e, ei) => {
                    const eCls = clsKey(e.classification);
                    return `<button class="ds-tab${ei === 0 ? ' active' : ''}" onclick="event.stopPropagation();switchDsTab(${globalIdx},${ei})">`
                        + `${e.dandiset_id} <span class="tab-badge badge ${eCls}">${(e.classification || 'NEITHER').replace(/_/g,' ')}</span></button>`;
                }).join('');
                const panelsHTML = group.entries.map((e, ei) => {
                    return `<div class="ds-tab-panel${ei === 0 ? ' active' : ''}" data-card="${globalIdx}" data-tab="${ei}">${renderDandisetEntry(e, globalIdx, ei)}</div>`;
                }).join('');
                detailsHTML = `<div class="ds-tabs" data-card="${globalIdx}">${tabsHTML}</div>${panelsHTML}`;
            }

            return `
            <div class="paper-card" data-idx="${globalIdx}">
                <div class="paper-header" onclick="toggleCard(${globalIdx})">
                    <div class="paper-info">
                        <div class="paper-title"><a href="https://doi.org/${group.citing_doi}" target="_blank">${escapeHtml(title)}</a></div>
                        <div class="paper-doi"><a href="https://doi.org/${group.citing_doi}" target="_blank">${group.citing_doi}</a></div>
                        <div class="paper-meta">
                            ${dsLabel}: ${dsIds.map(id => '<a href="https://dandiarchive.org/dandiset/' + id + '" target="_blank">' + id + '</a>').join(', ')}
                            ${group.citing_journal ? ' | ' + escapeHtml(group.citing_journal) : ''}
                            ${group.citing_date ? ' | ' + group.citing_date : ''}
                            ${group.preprint_doi ? ' | preprint: <a href="https://doi.org/' + group.preprint_doi + '" target="_blank">' + group.preprint_doi + '</a>' : ''}
                        </div>
                    </div>
                    <span class="badge ${cls}">${topClsName.replace(/_/g, ' ')}</span>
                    <span class="expand-icon">&#9660;</span>
                </div>
                <div class="paper-details">
                    ${detailsHTML}
                </div>
            </div>`;
        }

        function renderPapers(groups, page) {
            if (page == null) page = 0;
            currentFilteredPapers = groups;
            currentPaperPage = page;
            const list = document.getElementById('paperList');
            if (!groups.length) {
                list.innerHTML = '<div class="no-results">No papers match your filters</div>';
                document.getElementById('paperPagination').innerHTML = '';
                return;
            }
            const start = page * PAGE_SIZE;
            const pageItems = groups.slice(start, start + PAGE_SIZE);
            list.innerHTML = pageItems.map((g, i) => renderPaperCard(g, start + i)).join('');
            renderPaginationControls('paperPagination', groups.length, page, 'goToPaperPage');
        }

        function goToPaperPage(page) {
            renderPapers(currentFilteredPapers, page);
            document.getElementById('tab-papers').scrollIntoView({behavior: 'smooth'});
        }

        function toggleCard(i) {
            const el = document.querySelector(`[data-idx="${i}"]`);
            if (el) el.classList.toggle('expanded');
        }

        function switchDsTab(cardIdx, tabIdx) {
            const card = document.querySelector(`[data-idx="${cardIdx}"]`);
            if (!card) return;
            card.querySelectorAll(`.ds-tabs[data-card="${cardIdx}"] .ds-tab`).forEach((t, i) => {
                t.classList.toggle('active', i === tabIdx);
            });
            card.querySelectorAll(`.ds-tab-panel[data-card="${cardIdx}"]`).forEach((p, i) => {
                p.classList.toggle('active', i === tabIdx);
            });
        }

        function toggleExcerpt(eid) {
            document.getElementById(eid).classList.toggle('expanded');
        }

        function excerptNav(pagerId, dir) {
            const pager = document.getElementById(pagerId);
            if (!pager) return;
            const total = parseInt(pager.dataset.total);
            let cur = parseInt(pager.dataset.current);
            const next = (cur + dir + total) % total;
            pager.querySelectorAll('.excerpt-page').forEach(p => {
                p.style.display = parseInt(p.dataset.page) === next ? '' : 'none';
            });
            pager.dataset.current = next;
            document.getElementById(pagerId + '-counter').textContent = `${next + 1} / ${total}`;
        }

        function filterPapers() {
            const q = document.getElementById('search').value.toLowerCase();
            const checked = Array.from(document.querySelectorAll('.filter-cb:checked')).map(c => c.value);
            const sources = Array.from(document.querySelectorAll('.source-cb:checked')).map(c => c.value);
            const sameLab = document.getElementById('filterSameLab').checked;
            const diffLab = document.getElementById('filterDiffLab').checked;
            // Filter at the individual entry level, then group
            const filtered = classifications.filter(p => {
                const cls = (p.classification || 'NEITHER').toUpperCase();
                if (!checked.includes(cls)) return false;
                const st = p.source_type || 'citation_analysis';
                if (!sources.includes(st)) return false;
                if (cls === 'REUSE') {
                    if (sameLab && !p.same_lab) return false;
                    if (diffLab && p.same_lab) return false;
                }
                if (q) {
                    const fields = [p.citing_title, p.citing_doi, p.cited_doi, p.dandiset_id, p.dandiset_name].join(' ').toLowerCase();
                    if (!fields.includes(q)) return false;
                }
                return true;
            });
            renderPapers(buildPaperGroups(filtered), 0);
        }

        document.getElementById('search').addEventListener('input', filterPapers);
        document.querySelectorAll('.filter-cb').forEach(c => c.addEventListener('change', filterPapers));
        document.querySelectorAll('.source-cb').forEach(c => c.addEventListener('change', filterPapers));
        document.getElementById('filterSameLab').addEventListener('change', filterPapers);
        document.getElementById('filterDiffLab').addEventListener('change', filterPapers);

        // Datasets tab
        function buildDatasetGroups() {
            const groups = {};
            classifications.forEach(p => {
                const id = p.dandiset_id || 'unknown';
                if (!groups[id]) groups[id] = { id, name: p.dandiset_name || '', papers: [] };
                // Update name if we find a better one
                if (p.dandiset_name && !groups[id].name) groups[id].name = p.dandiset_name;
                groups[id].papers.push(p);
            });
            return Object.values(groups).sort((a, b) => {
                const ar = a.papers.filter(p => isReuse(p.classification)).length;
                const br = b.papers.filter(p => isReuse(p.classification)).length;
                return br - ar || a.papers.length - b.papers.length;
            });
        }

        let currentDatasetPage = 0;
        let currentFilteredDatasets = [];
        const DS_PAGE_SIZE = 25;

        function renderDatasetCard(g, gi) {
            const reuse = g.papers.filter(p => isReuse(p.classification)).length;
            const primary = g.papers.filter(p => p.classification === 'PRIMARY').length;
            const mention = g.papers.filter(p => p.classification === 'MENTION').length;
            const other = g.papers.length - reuse - primary - mention;
            const refCount = g.papers.filter(p => p.source_type === 'direct_reference' || p.source_type === 'both').length;
            const citeCount = g.papers.filter(p => p.source_type === 'citation_analysis' || p.source_type === 'both').length;
            const paperCards = g.papers.map((p, pi) => {
                const cls = clsKey(p.classification);
                const title = p.citing_title || p.citing_doi;
                return `<div style="display:flex;justify-content:space-between;align-items:center;padding:6px 0;border-bottom:1px solid #f0f0f0">
                    <div><a href="https://doi.org/${p.citing_doi}" target="_blank" style="color:#2c3e50;text-decoration:none;font-size:0.9em">${escapeHtml(title)}</a></div>
                    <div><span class="badge ${cls}" style="font-size:0.7em;padding:3px 8px">${(p.classification||'').replace(/_/g,' ')}</span>${sourceBadge(p)}${labBadge(p)}</div>
                </div>`;
            }).join('');
            return `
            <div class="dataset-section" data-dsi="${gi}">
                <div class="dataset-header" onclick="toggleDs(${gi})">
                    <div>
                        <span class="dataset-name">${escapeHtml(g.name || g.id)}</span>
                        <span class="dataset-id"> - <a href="https://dandiarchive.org/dandiset/${g.id}" target="_blank">${g.id}</a></span>
                    </div>
                    <div class="dataset-stats">
                        <span class="stat-chip reuse">${reuse} reuse</span>
                        ${primary ? `<span class="stat-chip primary">${primary} primary</span>` : ''}
                        <span class="stat-chip mention">${mention} mention</span>
                        ${other ? `<span class="stat-chip other">${other} other</span>` : ''}
                        ${refCount ? `<span class="stat-chip ref">${refCount} ref</span>` : ''}
                        ${citeCount ? `<span class="stat-chip cite">${citeCount} cite</span>` : ''}
                        <span style="color:#95a5a6">(${g.papers.length} total)</span>
                    </div>
                </div>
                <div class="dataset-papers">${paperCards}</div>
            </div>`;
        }

        function renderDatasets(groups, page) {
            if (page == null) page = 0;
            currentFilteredDatasets = groups;
            currentDatasetPage = page;
            const el = document.getElementById('datasetList');
            if (!groups.length) {
                el.innerHTML = '<div class="no-results">No datasets match</div>';
                document.getElementById('datasetPagination').innerHTML = '';
                return;
            }
            const start = page * DS_PAGE_SIZE;
            const pageGroups = groups.slice(start, start + DS_PAGE_SIZE);
            el.innerHTML = pageGroups.map((g, i) => renderDatasetCard(g, start + i)).join('');
            renderPaginationControls('datasetPagination', groups.length, page, 'goToDatasetPage');
        }

        function goToDatasetPage(page) {
            renderDatasets(currentFilteredDatasets, page);
            document.getElementById('tab-datasets').scrollIntoView({behavior: 'smooth'});
        }

        function toggleDs(i) {
            const el = document.querySelector(`[data-dsi="${i}"]`);
            if (el) el.classList.toggle('expanded');
        }

        function filterDatasets() {
            const q = document.getElementById('dsSearch').value.toLowerCase();
            const groups = buildDatasetGroups().filter(g => {
                if (!q) return true;
                return (g.id + ' ' + g.name).toLowerCase().includes(q);
            });
            renderDatasets(groups, 0);
        }
        document.getElementById('dsSearch').addEventListener('input', filterDatasets);

        // Tab switching
        function switchTab(name) {
            document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
            document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
            if (name === 'datasets') {
                document.querySelectorAll('.tab')[1].classList.add('active');
                document.getElementById('tab-datasets').classList.add('active');
                filterDatasets();
            } else {
                document.querySelectorAll('.tab')[0].classList.add('active');
                document.getElementById('tab-papers').classList.add('active');
            }
        }

        // Init
        renderSummary();
        renderPapers(buildPaperGroups(classifications), 0);
    </script>
</body>
</html>'''


def generate_html(merged_data: dict, output_file: Path) -> dict:
    """Generate HTML dashboard from merged data."""
    json_data = json.dumps(merged_data)
    html_content = HTML_TEMPLATE.replace('__DATA_PLACEHOLDER__', json_data)

    output_file.parent.mkdir(parents=True, exist_ok=True)
    with open(output_file, 'w') as f:
        f.write(html_content)

    return merged_data['metadata']


def main():
    parser = argparse.ArgumentParser(
        description='Generate combined dashboard from direct refs and citation classifications'
    )
    parser.add_argument('--refs', default='output/direct_ref_classifications.json',
                        help='Direct reference classifications JSON')
    parser.add_argument('--citations', default='output/test_all_classifications.json',
                        help='Citation-based classifications JSON')
    parser.add_argument('-o', '--output', default='output/combined_dashboard.html',
                        help='Output HTML file')
    parser.add_argument('--open', action='store_true',
                        help='Open in browser after generating')

    args = parser.parse_args()
    refs_path = Path(args.refs)
    cit_path = Path(args.citations)
    output_path = Path(args.output)

    if not refs_path.exists():
        print(f"Error: refs file not found: {refs_path}", file=sys.stderr)
        sys.exit(1)
    if not cit_path.exists():
        print(f"Error: citations file not found: {cit_path}", file=sys.stderr)
        sys.exit(1)

    print("Merging data sources...")
    merged = merge_data(refs_path, cit_path)
    meta = merged['metadata']

    print(f"\nMerge summary:")
    print(f"  Total pairs: {meta['total_pairs']}")
    print(f"  Direct ref only: {meta['source_breakdown']['direct_reference_only']}")
    print(f"  Citation only: {meta['source_breakdown']['citation_analysis_only']}")
    print(f"  Both sources: {meta['source_breakdown']['both_sources']}")
    if meta['source_breakdown'].get('direct_ref_false_positives_excluded'):
        print(f"  Direct ref false positives excluded: {meta['source_breakdown']['direct_ref_false_positives_excluded']}")
    if meta['source_breakdown'].get('preprint_published_deduped'):
        print(f"  Preprint/published deduped: {meta['source_breakdown']['preprint_published_deduped']}")
    print(f"  PRIMARY: {meta['classification_counts'].get('PRIMARY', 0)}")
    print(f"  REUSE: {meta['classification_counts']['REUSE']}")
    print(f"  MENTION: {meta['classification_counts']['MENTION']}")
    print(f"  NEITHER: {meta['classification_counts']['NEITHER']}")
    print(f"  Unique dandisets: {meta['unique_dandisets']}")
    print(f"  Dandisets with reuse: {meta['dandisets_with_reuse']}")

    print(f"\nGenerating dashboard...")
    generate_html(merged, output_path)
    print(f"Generated {output_path}")

    if args.open:
        if sys.platform == 'darwin':
            subprocess.run(['open', str(output_path)])
        elif sys.platform == 'win32':
            subprocess.run(['start', str(output_path)], shell=True)
        else:
            subprocess.run(['xdg-open', str(output_path)])


if __name__ == '__main__':
    main()
