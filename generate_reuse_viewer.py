#!/usr/bin/env python3
"""
generate_reuse_viewer.py - Generate HTML dashboard from citing paper classifications

Produces a self-contained HTML file with embedded CSS/JS that shows:
- Summary statistics with classification breakdown
- Datasets tab: table of dandisets with reuse counts, expandable citing papers
- Papers tab: searchable/filterable table of all classified papers

Usage:
    python generate_reuse_viewer.py -i output/citing_paper_classifications.json
    python generate_reuse_viewer.py -i output/citing_paper_classifications.json -o output/reuse_dashboard.html
    python generate_reuse_viewer.py -i output/citing_paper_classifications.json --open
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

DEFAULT_INPUT = 'output/citing_paper_classifications.json'
DEFAULT_OUTPUT = 'output/reuse_dashboard.html'

HTML_TEMPLATE = '''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>DANDI Dataset Reuse Dashboard</title>
    <style>
        * { box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            line-height: 1.6; color: #333; max-width: 1400px; margin: 0 auto;
            padding: 20px; background: #f5f5f5;
        }
        h1 { color: #2c3e50; border-bottom: 3px solid #3498db; padding-bottom: 10px; }
        .metadata { font-size: 0.85em; color: #7f8c8d; margin-bottom: 20px; }
        .summary { display: flex; flex-wrap: wrap; gap: 12px; margin-bottom: 20px; }
        .summary-card {
            background: white; border-radius: 8px; padding: 16px 20px; min-width: 130px;
            text-align: center; box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }
        .summary-card .count { font-size: 2.2em; font-weight: bold; color: #2c3e50; }
        .summary-card .label { font-size: 0.8em; color: #7f8c8d; text-transform: uppercase; }
        .summary-card.reuse { border-top: 4px solid #27ae60; }
        .summary-card.mention { border-top: 4px solid #95a5a6; }
        .summary-card.neither { border-top: 4px solid #f39c12; }
        .summary-card.total { border-top: 4px solid #2c3e50; }
        .summary-sub { font-size: 0.75em; color: #7f8c8d; margin-top: 2px; }

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
        .badge.mention { background: #eaecee; color: #566573; }
        .badge.neither { background: #fdebd0; color: #b9770e; }
        .badge-sub { font-size: 0.7em; padding: 2px 8px; border-radius: 10px; margin-left: 4px; }
        .badge-sub.same-lab { background: #d6eaf8; color: #1a5276; }
        .badge-sub.diff-lab { background: #e8f8f0; color: #27ae60; }

        .expand-icon { font-size: 1.1em; color: #bdc3c7; transition: transform 0.2s; }
        .paper-card.expanded .expand-icon { transform: rotate(180deg); }
        .paper-details { display: none; padding: 12px 16px; border-top: 1px solid #ecf0f1; }
        .paper-card.expanded .paper-details { display: block; }

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
        .cite-highlight { background: #fff3cd; border-radius: 2px; padding: 0 2px; font-weight: 600; }

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
        .dataset-stats { display: flex; gap: 8px; font-size: 0.85em; }
        .stat-chip { padding: 2px 8px; border-radius: 10px; }
        .stat-chip.reuse { background: #d5f4e6; color: #1e8449; }
        .stat-chip.mention { background: #eaecee; color: #566573; }
        .stat-chip.other { background: #fdebd0; color: #b9770e; }

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
    <h1>DANDI Dataset Reuse Dashboard</h1>
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
            <label><input type="checkbox" class="filter-cb" value="MENTION" checked> Mention</label>
            <label><input type="checkbox" class="filter-cb" value="NEITHER" checked> Neither</label>
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
            // Find the citation reference in the escaped text and wrap with <mark>
            const offset = excerpt.highlight_offset;
            if (offset == null) return escapedText;
            const raw = excerpt.text || '';
            // Find a citation pattern near the offset in the raw text
            let refStart = -1, refEnd = -1;
            const nearby = raw.substring(Math.max(0, offset - 5), Math.min(raw.length, offset + 60));
            // Try numbered: [42], [41,42,43], (42), superscript42
            let m;
            if (excerpt.reference_number) {
                const rn = String(excerpt.reference_number);
                // Search for bracket [...] or paren (...) group near offset containing the ref number
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
                // Author-year: find (Author et al., YYYY) or Author et al. (YYYY)
                const surname = excerpt.authors[0];
                const year = excerpt.year;
                // Search for surname near offset
                const si = raw.indexOf(surname, Math.max(0, offset - 40));
                if (si >= 0 && si < offset + 40) {
                    // Extend to include year
                    const yi = raw.indexOf(year, si);
                    if (yi >= 0 && yi < si + 80) {
                        refStart = si;
                        refEnd = yi + year.length;
                        // Include trailing paren if present
                        if (raw[refEnd] === ')') refEnd++;
                    }
                }
            }
            if (refStart < 0) return escapedText;
            // Map raw offsets to escaped text via escaping each segment
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

        // Render metadata
        document.getElementById('metadata').innerHTML = [
            meta.timestamp ? `Generated: ${new Date(meta.timestamp).toLocaleDateString()}` : '',
            meta.model ? `Model: ${meta.model}` : '',
            meta.total_pairs ? `Total pairs: ${meta.total_pairs}` : '',
            meta.api_calls ? `API calls: ${meta.api_calls}` : '',
            meta.from_cache ? `From cache: ${meta.from_cache}` : '',
        ].filter(Boolean).join(' | ');

        // Render summary
        function renderSummary() {
            const counts = {};
            let sameLab = 0, diffLab = 0;
            classifications.forEach(p => {
                const c = p.classification || 'NEITHER';
                counts[c] = (counts[c] || 0) + 1;
                if (c === 'REUSE') {
                    if (p.same_lab === true) sameLab++;
                    else if (p.same_lab === false) diffLab++;
                }
            });
            const order = ['REUSE', 'MENTION', 'NEITHER'];
            const labels = { REUSE: 'Reuse', MENTION: 'Mention', NEITHER: 'Neither' };
            let html = `<div class="summary-card total"><div class="count">${classifications.length}</div><div class="label">Total</div></div>`;
            order.forEach(c => {
                let sub = '';
                if (c === 'REUSE') sub = `<div class="summary-sub">${diffLab} diff lab, ${sameLab} same lab</div>`;
                html += `<div class="summary-card ${clsKey(c)}"><div class="count">${counts[c]||0}</div><div class="label">${labels[c]}</div>${sub}</div>`;
            });
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
            // Show page buttons with ellipsis
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

        // Papers tab
        let currentPaperPage = 0;
        let currentFilteredPapers = [];

        function renderPaperCard(p, globalIdx) {
            const cls = clsKey(p.classification);
            const title = p.citing_title || p.citing_doi || 'Unknown';
            const excerpts = (p.context_excerpts || []).map((e, ei) => {
                const highlighted = highlightRef(escapeHtml(e.text || ''), e);
                const plain = escapeHtml(e.text || '');
                const LIMIT = 250;
                if (plain.length <= LIMIT) {
                    return `<div class="context-excerpt"><div class="context-method">Found via: ${escapeHtml(e.method || 'unknown')}</div>${highlighted}</div>`;
                }
                const truncated = plain.slice(0, LIMIT) + '...';
                const eid = `exc-${globalIdx}-${ei}`;
                return `<div class="context-excerpt"><div class="context-method">Found via: ${escapeHtml(e.method || 'unknown')}</div><div class="excerpt-preview" id="${eid}" onclick="toggleExcerpt('${eid}')"><span class="excerpt-truncated">${truncated} <span class="excerpt-toggle">&#9654; Show all</span></span><span class="excerpt-full">${highlighted} <span class="excerpt-toggle">&#9650; Show less</span></span></div></div>`;
            }).join('');
            return `
            <div class="paper-card" data-cls="${(p.classification||'').toUpperCase()}" data-idx="${globalIdx}">
                <div class="paper-header" onclick="toggleCard(${globalIdx})">
                    <div class="paper-info">
                        <div class="paper-title"><a href="https://doi.org/${p.citing_doi}" target="_blank">${escapeHtml(title)}</a></div>
                        <div class="paper-doi"><a href="https://doi.org/${p.citing_doi}" target="_blank">${p.citing_doi}</a></div>
                        <div class="paper-meta">
                            Dataset: <a href="https://dandiarchive.org/dandiset/${p.dandiset_id}" target="_blank">${p.dandiset_id}</a>
                            ${p.citing_journal ? ' | ' + escapeHtml(p.citing_journal) : ''}
                            ${p.citing_date ? ' | ' + p.citing_date : ''}
                            ${p.num_contexts != null ? ' | ' + p.num_contexts + ' context(s)' : ''}
                        </div>
                    </div>
                    <span class="badge ${cls}">${(p.classification || 'Neither').replace(/_/g, ' ')}</span>${labBadge(p)}
                    <span class="expand-icon">&#9660;</span>
                </div>
                <div class="paper-details">
                    ${p.reasoning ? `<div class="reasoning"><strong>Reasoning</strong>${p.confidence != null ? `<span class="confidence ${confClass(p.confidence)}">${p.confidence}/10</span>` : ''}<br>${escapeHtml(p.reasoning)}</div>` : ''}
                    <div class="paper-meta">Cited paper: <a href="https://doi.org/${p.cited_doi}" target="_blank">${p.cited_doi}</a></div>
                    ${excerpts ? `<div style="margin-top:10px"><strong>Context Excerpts:</strong>${excerpts}</div>` : ''}
                </div>
            </div>`;
        }

        function renderPapers(items, page) {
            if (page == null) page = 0;
            currentFilteredPapers = items;
            currentPaperPage = page;
            const list = document.getElementById('paperList');
            if (!items.length) {
                list.innerHTML = '<div class="no-results">No papers match your filters</div>';
                document.getElementById('paperPagination').innerHTML = '';
                return;
            }
            const start = page * PAGE_SIZE;
            const pageItems = items.slice(start, start + PAGE_SIZE);
            list.innerHTML = pageItems.map((p, i) => renderPaperCard(p, start + i)).join('');
            renderPaginationControls('paperPagination', items.length, page, 'goToPaperPage');
        }

        function goToPaperPage(page) {
            renderPapers(currentFilteredPapers, page);
            document.getElementById('tab-papers').scrollIntoView({behavior: 'smooth'});
        }

        function toggleCard(i) {
            const el = document.querySelector(`[data-idx="${i}"]`);
            if (el) el.classList.toggle('expanded');
        }

        function toggleExcerpt(eid) {
            document.getElementById(eid).classList.toggle('expanded');
        }

        function filterPapers() {
            const q = document.getElementById('search').value.toLowerCase();
            const checked = Array.from(document.querySelectorAll('.filter-cb:checked')).map(c => c.value);
            const sameLab = document.getElementById('filterSameLab').checked;
            const diffLab = document.getElementById('filterDiffLab').checked;
            const filtered = classifications.filter(p => {
                const cls = (p.classification || 'NEITHER').toUpperCase();
                if (!checked.includes(cls)) return false;
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
            renderPapers(filtered, 0);
        }

        document.getElementById('search').addEventListener('input', filterPapers);
        document.querySelectorAll('.filter-cb').forEach(c => c.addEventListener('change', filterPapers));
        document.getElementById('filterSameLab').addEventListener('change', filterPapers);
        document.getElementById('filterDiffLab').addEventListener('change', filterPapers);

        // Datasets tab
        function buildDatasetGroups() {
            const groups = {};
            classifications.forEach(p => {
                const id = p.dandiset_id || 'unknown';
                if (!groups[id]) groups[id] = { id, name: p.dandiset_name || '', papers: [] };
                groups[id].papers.push(p);
            });
            return Object.values(groups).sort((a, b) => {
                const ar = a.papers.filter(p => isReuse(p.classification)).length;
                const br = b.papers.filter(p => isReuse(p.classification)).length;
                return br - ar;
            });
        }

        let currentDatasetPage = 0;
        let currentFilteredDatasets = [];
        const DS_PAGE_SIZE = 25;

        function renderDatasetCard(g, gi) {
            const reuse = g.papers.filter(p => isReuse(p.classification)).length;
            const mention = g.papers.filter(p => p.classification === 'MENTION').length;
            const other = g.papers.length - reuse - mention;
            const paperCards = g.papers.map((p, pi) => {
                const cls = clsKey(p.classification);
                const title = p.citing_title || p.citing_doi;
                return `<div style="display:flex;justify-content:space-between;align-items:center;padding:6px 0;border-bottom:1px solid #f0f0f0">
                    <div><a href="https://doi.org/${p.citing_doi}" target="_blank" style="color:#2c3e50;text-decoration:none;font-size:0.9em">${escapeHtml(title)}</a></div>
                    <div><span class="badge ${cls}" style="font-size:0.7em;padding:3px 8px">${(p.classification||'').replace(/_/g,' ')}</span>${labBadge(p)}</div>
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
                        <span class="stat-chip mention">${mention} mention</span>
                        ${other ? `<span class="stat-chip other">${other} other</span>` : ''}
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
        renderPapers(classifications, 0);
    </script>
</body>
</html>'''


def generate_html(input_file: Path, output_file: Path) -> dict:
    """
    Generate HTML dashboard from citing paper classification JSON.

    Returns dict with counts for each classification.
    """
    with open(input_file) as f:
        data = json.load(f)

    # Handle both formats: {metadata, classifications} or bare list
    if isinstance(data, list):
        data = {'metadata': {}, 'classifications': data}

    json_data = json.dumps(data)
    html_content = HTML_TEMPLATE.replace('__DATA_PLACEHOLDER__', json_data)

    output_file.parent.mkdir(parents=True, exist_ok=True)
    with open(output_file, 'w') as f:
        f.write(html_content)

    # Count classifications
    classifications = data.get('classifications', [])
    counts = {}
    for paper in classifications:
        cls = (paper.get('classification') or 'NEITHER').upper()
        counts[cls] = counts.get(cls, 0) + 1
    counts['TOTAL'] = len(classifications)
    return counts


def main():
    parser = argparse.ArgumentParser(
        description='Generate HTML dashboard from citing paper classification data'
    )
    parser.add_argument('-i', '--input', default=DEFAULT_INPUT,
                        help=f'Input JSON file (default: {DEFAULT_INPUT})')
    parser.add_argument('-o', '--output', default=DEFAULT_OUTPUT,
                        help=f'Output HTML file (default: {DEFAULT_OUTPUT})')
    parser.add_argument('--open', action='store_true',
                        help='Open the generated HTML in default browser')

    args = parser.parse_args()
    input_path = Path(args.input)
    output_path = Path(args.output)

    if not input_path.exists():
        print(f"Error: Input file not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    counts = generate_html(input_path, output_path)

    print(f"Generated {output_path}")
    print(f"\nClassification counts:")
    for cls in ['REUSE', 'MENTION', 'NEITHER']:
        print(f"  {cls}: {counts.get(cls, 0)}")
    print(f"  Total: {counts['TOTAL']}")

    if args.open:
        if sys.platform == 'darwin':
            subprocess.run(['open', str(output_path)])
        elif sys.platform == 'win32':
            subprocess.run(['start', str(output_path)], shell=True)
        else:
            subprocess.run(['xdg-open', str(output_path)])


if __name__ == '__main__':
    main()
