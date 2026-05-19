#!/usr/bin/env python3
"""
generate_viewer.py - Generate HTML viewer from classification JSON data

Usage:
    python generate_viewer.py                          # Use default input/output
    python generate_viewer.py -i custom.json           # Custom input file
    python generate_viewer.py -o custom.html           # Custom output file
    python generate_viewer.py --open                   # Open in browser after generating
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

DEFAULT_INPUT = 'dandi_classifications.json'
DEFAULT_OUTPUT = 'classifications_viewer.html'

HTML_TEMPLATE = '''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>DANDI Dataset Usage Classifications</title>
    <style>
        * { box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            line-height: 1.6; color: #333; max-width: 1200px; margin: 0 auto;
            padding: 20px; background: #f5f5f5;
        }
        h1 { color: #2c3e50; border-bottom: 3px solid #3498db; padding-bottom: 10px; }
        .summary { display: flex; flex-wrap: wrap; gap: 15px; margin-bottom: 30px; }
        .summary-card {
            background: white; border-radius: 8px; padding: 20px; min-width: 150px;
            text-align: center; box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }
        .summary-card.primary { border-top: 4px solid #27ae60; }
        .summary-card.secondary { border-top: 4px solid #3498db; }
        .summary-card.neither { border-top: 4px solid #95a5a6; }
        .summary-card.unknown { border-top: 4px solid #f39c12; }
        .summary-card.error { border-top: 4px solid #e74c3c; }
        .summary-card.total { border-top: 4px solid #2c3e50; }
        .summary-card .count { font-size: 2.5em; font-weight: bold; color: #2c3e50; }
        .summary-card .label { font-size: 0.9em; color: #7f8c8d; text-transform: uppercase; }
        .filters {
            background: white; padding: 15px; border-radius: 8px;
            margin-bottom: 20px; box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }
        .filters label { margin-right: 15px; cursor: pointer; }
        .filters input[type="checkbox"] { margin-right: 5px; }
        .search-box {
            width: 100%; padding: 10px; border: 1px solid #ddd;
            border-radius: 4px; margin-bottom: 10px; font-size: 1em;
        }
        .paper-list { display: flex; flex-direction: column; gap: 15px; }
        .paper-card {
            background: white; border-radius: 8px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1); overflow: hidden;
        }
        .paper-header {
            padding: 15px 20px; cursor: pointer; display: flex;
            justify-content: space-between; align-items: center; gap: 15px;
        }
        .paper-header:hover { background: #f8f9fa; }
        .paper-info { flex: 1; }
        .paper-title { font-weight: bold; color: #2c3e50; font-size: 1.1em; line-height: 1.4; }
        .paper-title a { color: #2c3e50; text-decoration: none; }
        .paper-title a:hover { color: #3498db; }
        .paper-doi { font-size: 0.85em; color: #3498db; margin-top: 4px; }
        .paper-doi a { color: #3498db; text-decoration: none; }
        .paper-doi a:hover { text-decoration: underline; }
        .paper-meta { font-size: 0.85em; color: #7f8c8d; margin-top: 5px; }
        .dataset-chips { display: flex; flex-wrap: wrap; gap: 5px; margin-top: 8px; }
        .dataset-chip {
            background: #ecf0f1; padding: 2px 8px; border-radius: 12px;
            font-size: 0.8em; color: #2c3e50;
        }
        .dataset-chip a { color: inherit; text-decoration: none; }
        .dataset-chip a:hover { text-decoration: underline; }
        .classification-badge {
            padding: 8px 16px; border-radius: 20px; font-weight: bold;
            font-size: 0.85em; text-transform: uppercase; white-space: nowrap;
        }
        .classification-badge.primary { background: #d5f4e6; color: #1e8449; }
        .classification-badge.secondary { background: #d6eaf8; color: #2471a3; }
        .classification-badge.neither { background: #eaecee; color: #566573; }
        .classification-badge.unknown { background: #fdebd0; color: #b9770e; }
        .classification-badge.error { background: #fadbd8; color: #c0392b; }
        .expand-icon { font-size: 1.2em; color: #95a5a6; transition: transform 0.2s; }
        .paper-card.expanded .expand-icon { transform: rotate(180deg); }
        .paper-details { display: none; padding: 0 20px 20px; border-top: 1px solid #ecf0f1; }
        .paper-card.expanded .paper-details { display: block; }
        .reasoning {
            background: #f8f9fa; padding: 12px 15px; border-radius: 6px;
            margin: 15px 0; border-left: 4px solid #3498db;
        }
        .reasoning-label { font-weight: bold; color: #2c3e50; margin-bottom: 5px; }
        .confidence { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 0.8em; margin-left: 10px; }
        .confidence.high { background: #d5f4e6; color: #1e8449; }
        .confidence.medium { background: #fdebd0; color: #b9770e; }
        .confidence.low { background: #fadbd8; color: #c0392b; }
        .mentions-section { margin-top: 20px; }
        .mentions-header {
            font-weight: bold; color: #2c3e50; margin-bottom: 10px;
            cursor: pointer; display: flex; align-items: center; gap: 8px;
        }
        .mentions-list { display: none; }
        .mentions-section.expanded .mentions-list { display: block; }
        .mention {
            background: #fafafa; border: 1px solid #ecf0f1;
            border-radius: 6px; padding: 12px; margin-bottom: 10px;
        }
        .mention-header { display: flex; gap: 10px; margin-bottom: 8px; flex-wrap: wrap; }
        .mention-tag { background: #ecf0f1; padding: 2px 8px; border-radius: 4px; font-size: 0.8em; }
        .context {
            font-family: Georgia, serif; font-size: 0.9em; line-height: 1.7;
            color: #555; white-space: pre-wrap; word-wrap: break-word;
        }
        .context mark { background: #fff3cd; padding: 2px 4px; border-radius: 3px; }
        .no-results { text-align: center; padding: 40px; color: #7f8c8d; }
        @media (max-width: 768px) {
            .paper-header { flex-direction: column; align-items: flex-start; }
            .classification-badge { margin-top: 10px; }
        }
    </style>
</head>
<body>
    <h1>DANDI Dataset Usage Classifications</h1>
    <div class="summary" id="summary"></div>
    <div class="filters">
        <input type="text" class="search-box" id="search" placeholder="Search by title, DOI, or dataset ID...">
        <div>
            <label><input type="checkbox" class="filter-checkbox" value="PRIMARY" checked> Primary</label>
            <label><input type="checkbox" class="filter-checkbox" value="SECONDARY" checked> Secondary</label>
            <label><input type="checkbox" class="filter-checkbox" value="NEITHER" checked> Neither</label>
            <label><input type="checkbox" class="filter-checkbox" value="UNKNOWN" checked> Unknown</label>
            <label><input type="checkbox" class="filter-checkbox" value="ERROR" checked> Error</label>
        </div>
    </div>
    <div class="paper-list" id="paperList"></div>
    <script>
        const papers = __DATA_PLACEHOLDER__;

        function escapeHtml(text) {
            const div = document.createElement('div');
            div.textContent = text;
            return div.innerHTML;
        }

        function highlightMatch(text) {
            return text.replace(/\\[MATCH: ([^\\]]+)\\]/g, '<mark>$1</mark>');
        }

        function renderSummary() {
            const counts = { PRIMARY: 0, SECONDARY: 0, NEITHER: 0, UNKNOWN: 0, ERROR: 0 };
            papers.forEach(p => {
                const cls = (p.classification || 'UNKNOWN').toUpperCase();
                if (cls in counts) counts[cls]++;
                else if (cls === '(DRY RUN - NOT CLASSIFIED)') counts['UNKNOWN']++;
                else counts['UNKNOWN']++;
            });

            document.getElementById('summary').innerHTML = `
                <div class="summary-card total"><div class="count">${papers.length}</div><div class="label">Total Papers</div></div>
                <div class="summary-card primary"><div class="count">${counts.PRIMARY}</div><div class="label">Primary</div></div>
                <div class="summary-card secondary"><div class="count">${counts.SECONDARY}</div><div class="label">Secondary</div></div>
                <div class="summary-card unknown"><div class="count">${counts.UNKNOWN}</div><div class="label">Unknown</div></div>
                <div class="summary-card neither"><div class="count">${counts.NEITHER}</div><div class="label">Neither</div></div>
                <div class="summary-card error"><div class="count">${counts.ERROR}</div><div class="label">Error</div></div>
            `;
        }

        function renderPapers(filteredPapers) {
            const list = document.getElementById('paperList');
            if (filteredPapers.length === 0) {
                list.innerHTML = '<div class="no-results">No papers match your filters</div>';
                return;
            }

            list.innerHTML = filteredPapers.map((paper, idx) => {
                const cls = (paper.classification || 'unknown').toLowerCase();
                const title = paper.title || paper.doi;
                const datasetChips = (paper.dataset_ids || []).map(id =>
                    `<span class="dataset-chip"><a href="https://dandiarchive.org/dandiset/${id}" target="_blank">${id}</a></span>`
                ).join('');

                const mentions = (paper.mentions || []).map(m => `
                    <div class="mention">
                        <div class="mention-header">
                            <span class="mention-tag">Dataset: ${escapeHtml(m.dataset_id)}</span>
                            <span class="mention-tag">Pattern: ${escapeHtml(m.pattern_type)}</span>
                        </div>
                        <div class="context">${highlightMatch(escapeHtml(m.context))}</div>
                    </div>
                `).join('');

                return `
                    <div class="paper-card" data-classification="${cls}" data-idx="${idx}">
                        <div class="paper-header" onclick="toggleCard(${idx})">
                            <div class="paper-info">
                                <div class="paper-title"><a href="https://doi.org/${paper.doi}" target="_blank">${escapeHtml(title)}</a></div>
                                <div class="paper-doi"><a href="https://doi.org/${paper.doi}" target="_blank">${paper.doi}</a></div>
                                <div class="paper-meta">${paper.num_mentions || 0} mention(s) | Source: ${paper.source || 'unknown'}</div>
                                <div class="dataset-chips">${datasetChips}</div>
                            </div>
                            <span class="classification-badge ${cls}">${paper.classification || 'Unknown'}</span>
                            <span class="expand-icon">▼</span>
                        </div>
                        <div class="paper-details">
                            ${paper.reasoning ? `
                                <div class="reasoning">
                                    <div class="reasoning-label">LLM Reasoning
                                        ${paper.confidence ? `<span class="confidence ${paper.confidence}">${paper.confidence}</span>` : ''}
                                    </div>
                                    ${escapeHtml(paper.reasoning)}
                                </div>
                            ` : ''}
                            ${mentions ? `
                                <div class="mentions-section">
                                    <div class="mentions-header" onclick="event.stopPropagation(); toggleMentions(${idx})">
                                        <span>▶</span> Context Excerpts (${paper.mentions?.length || 0})
                                    </div>
                                    <div class="mentions-list">${mentions}</div>
                                </div>
                            ` : ''}
                        </div>
                    </div>
                `;
            }).join('');
        }

        function toggleCard(idx) {
            const card = document.querySelector(`[data-idx="${idx}"]`);
            card.classList.toggle('expanded');
        }

        function toggleMentions(idx) {
            const card = document.querySelector(`[data-idx="${idx}"]`);
            const section = card.querySelector('.mentions-section');
            section.classList.toggle('expanded');
            const header = section.querySelector('.mentions-header span');
            header.textContent = section.classList.contains('expanded') ? '▼' : '▶';
        }

        function filterPapers() {
            const search = document.getElementById('search').value.toLowerCase();
            const checkedFilters = Array.from(document.querySelectorAll('.filter-checkbox:checked')).map(c => c.value);

            const filtered = papers.filter(p => {
                const cls = (p.classification || 'UNKNOWN').toUpperCase();
                if (!checkedFilters.includes(cls)) return false;
                if (search) {
                    const title = (p.title || '').toLowerCase();
                    const doi = p.doi.toLowerCase();
                    const datasets = (p.dataset_ids || []).join(' ').toLowerCase();
                    if (!title.includes(search) && !doi.includes(search) && !datasets.includes(search)) return false;
                }
                return true;
            });

            renderPapers(filtered);
        }

        document.getElementById('search').addEventListener('input', filterPapers);
        document.querySelectorAll('.filter-checkbox').forEach(c => c.addEventListener('change', filterPapers));

        renderSummary();
        renderPapers(papers);
    </script>
</body>
</html>'''


def generate_html(input_file: Path, output_file: Path) -> dict:
    """
    Generate HTML viewer from JSON classification data.

    Returns dict with counts for each classification.
    """
    # Read JSON data
    with open(input_file) as f:
        data = json.load(f)

    # Ensure data is a list
    if isinstance(data, dict):
        data = [data]

    # Generate HTML with embedded data
    json_data = json.dumps(data)
    html_content = HTML_TEMPLATE.replace('__DATA_PLACEHOLDER__', json_data)

    # Write output
    with open(output_file, 'w') as f:
        f.write(html_content)

    # Count classifications
    counts = {'PRIMARY': 0, 'SECONDARY': 0, 'NEITHER': 0, 'UNKNOWN': 0, 'ERROR': 0}
    for paper in data:
        cls = (paper.get('classification') or 'UNKNOWN').upper()
        if cls in counts:
            counts[cls] += 1
        else:
            counts['UNKNOWN'] += 1

    counts['TOTAL'] = len(data)
    return counts


def main():
    parser = argparse.ArgumentParser(
        description='Generate HTML viewer from classification JSON data',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Generate with defaults
    python generate_viewer.py

    # Custom input/output
    python generate_viewer.py -i results.json -o viewer.html

    # Generate and open in browser
    python generate_viewer.py --open
        """
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
    print(f"  Total:     {counts['TOTAL']}")
    print(f"  Primary:   {counts['PRIMARY']}")
    print(f"  Secondary: {counts['SECONDARY']}")
    print(f"  Unknown:   {counts['UNKNOWN']}")
    print(f"  Neither:   {counts['NEITHER']}")
    print(f"  Error:     {counts['ERROR']}")

    if args.open:
        # Open in default browser
        if sys.platform == 'darwin':
            subprocess.run(['open', str(output_path)])
        elif sys.platform == 'win32':
            subprocess.run(['start', str(output_path)], shell=True)
        else:
            subprocess.run(['xdg-open', str(output_path)])


if __name__ == '__main__':
    main()
