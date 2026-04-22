#!/usr/bin/env python3
"""Build a minimal HTML review dashboard from run_minimal_pipeline.py output.

Reads  output/minimal/<archive>/classifications.json
       output/minimal/<archive>/datasets.json  (optional, for dandiset URLs)
Writes output/minimal/<archive>/review.html

Usage:
    python build_minimal_review.py --archive dandi
"""

import argparse
import json
from pathlib import Path


HTML_TEMPLATE = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>{archive_name} Reuse Review ({n} entries)</title>
<style>
  body {{ font-family: -apple-system, sans-serif; max-width: 950px; margin: 0 auto; padding: 20px; background: #f5f5f5; }}
  h1 {{ text-align: center; }}
  .controls {{ background: #e3f2fd; padding: 15px; border-radius: 8px; margin-bottom: 20px; display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }}
  .controls button {{ padding: 8px 16px; border-radius: 4px; border: 1px solid #1565c0; background: #1565c0; color: white; cursor: pointer; font-size: 13px; }}
  .controls button:hover {{ background: #0d47a1; }}
  .controls button.secondary {{ background: white; color: #1565c0; }}
  .controls button.secondary:hover {{ background: #e3f2fd; }}
  .progress {{ font-size: 13px; color: #555; margin-left: auto; }}
  .filter-bar {{ background: #fff; padding: 10px 15px; border-radius: 8px; margin-bottom: 15px; display: flex; gap: 15px; align-items: center; font-size: 13px; flex-wrap: wrap; }}
  .entry {{ background: white; border: 1px solid #ddd; border-radius: 8px; padding: 20px; margin-bottom: 15px; }}
  .entry.reviewed {{ border-left: 4px solid #4caf50; }}
  .entry.not-reuse {{ border-left: 4px solid #e53935; }}
  .entry.unsure {{ border-left: 4px solid #ff9800; }}
  .entry-header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; }}
  .entry-num {{ font-size: 16px; font-weight: bold; color: #1565c0; }}
  .cls-tag {{ display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 11px; font-weight: bold; color: white; }}
  .cls-REUSE {{ background: #2e7d32; }}
  .cls-MENTION {{ background: #1565c0; }}
  .cls-NEITHER {{ background: #757575; }}
  .field {{ margin: 6px 0; font-size: 13px; }}
  .field-label {{ font-weight: bold; color: #555; }}
  .excerpt {{ background: #fafafa; border-left: 3px solid #1565c0; padding: 8px 12px; margin: 5px 0; font-size: 12px; line-height: 1.5; }}
  .excerpt .cite-label {{ font-size: 11px; color: #1565c0; font-weight: bold; }}
  .reasoning {{ margin-top: 8px; padding: 8px; background: #f5f5f5; border-radius: 4px; font-size: 12px; color: #444; }}
  .form-row {{ display: flex; gap: 20px; margin: 10px 0; flex-wrap: wrap; align-items: flex-start; }}
  .form-group {{ font-size: 13px; }}
  .form-group label {{ font-weight: bold; display: block; margin-bottom: 4px; }}
  .form-group select, .form-group textarea {{ padding: 4px 8px; font-size: 13px; font-family: inherit; }}
  .form-group textarea {{ width: 350px; height: 40px; }}
  mark {{ background: #fff176; }}
</style>
</head>
<body>
<h1>{archive_name} Reuse Review ({n} entries)</h1>

<div class="controls">
  <button onclick="saveState()">Save to JSON</button>
  <button class="secondary" onclick="document.getElementById('loadFile').click()">Load from JSON</button>
  <input type="file" id="loadFile" accept=".json" style="display:none" onchange="loadState(event)">
  <button class="secondary" onclick="jumpToNext()">Jump to next unreviewed</button>
  <span class="progress" id="progress"></span>
</div>

<div class="filter-bar">
  <label>Classification:</label>
  <select id="filterCls" onchange="render()">
    <option value="all">All</option>
    <option value="REUSE">REUSE</option>
    <option value="MENTION">MENTION</option>
    <option value="NEITHER">NEITHER</option>
  </select>
  <label>Review status:</label>
  <select id="filterReview" onchange="render()">
    <option value="all">All</option>
    <option value="unreviewed">Unreviewed</option>
    <option value="yes">Confirmed Yes</option>
    <option value="no">Confirmed No</option>
    <option value="unsure">Unsure</option>
  </select>
  <label>Sort:</label>
  <select id="sortBy" onchange="render()">
    <option value="dataset">Dataset ID</option>
    <option value="confidence">Pipeline confidence (desc)</option>
  </select>
</div>

<div id="entries"></div>

<script>
const entryData = {entries_json};
const N = entryData.length;

let state = {{}};
for (let i = 0; i < N; i++) {{
  const key = entryData[i].citing_doi + '|' + entryData[i].dandiset_id;
  state[key] = {{ confirmed: null, notes: '' }};
}}

function keyOf(e) {{ return e.citing_doi + '|' + e.dandiset_id; }}

function escapeHtml(text) {{
  const div = document.createElement('div');
  div.textContent = text == null ? '' : String(text);
  return div.innerHTML;
}}

function renderExcerpts(excerpts) {{
  if (!excerpts || excerpts.length === 0) return '<div class="excerpt"><em>No excerpts available</em></div>';
  let html = '';
  for (const ex of excerpts.slice(0, 5)) {{
    if (typeof ex === 'object' && ex !== null) {{
      let label = '';
      if (ex.authors && ex.year) label = ex.authors.join(', ') + ' ' + ex.year;
      else label = 'citation';
      let text = escapeHtml(ex.text || '');
      if (ex.highlight_offset != null && ex.highlight_offset >= 0 && ex.highlight_offset < (ex.text||'').length) {{
        const raw = ex.text;
        const hs = Math.max(0, ex.highlight_offset - 5);
        const he = Math.min(raw.length, ex.highlight_offset + 40);
        text = escapeHtml(raw.slice(0, hs)) + '<mark>' + escapeHtml(raw.slice(hs, he)) + '</mark>' + escapeHtml(raw.slice(he));
      }}
      html += '<div class="excerpt"><span class="cite-label">Citation: ' + escapeHtml(label) + '</span><br>' + text + '</div>';
    }} else {{
      html += '<div class="excerpt">' + escapeHtml(String(ex)) + '</div>';
    }}
  }}
  return html;
}}

function cardClass(s) {{
  if (s.confirmed === 'yes') return 'entry reviewed';
  if (s.confirmed === 'no') return 'entry not-reuse';
  if (s.confirmed === 'unsure') return 'entry unsure';
  return 'entry';
}}

function renderEntry(e, displayIndex) {{
  const key = keyOf(e);
  const s = state[key];
  const sel = v => s.confirmed === v ? 'selected' : '';
  const dsLink = e.dandiset_url
    ? '<a href="' + escapeHtml(e.dandiset_url) + '" target="_blank">' + escapeHtml(e.dandiset_id) + '</a>'
    : escapeHtml(e.dandiset_id);
  const sameLab = e.classification === 'REUSE'
    ? ' | ' + (e.same_lab ? 'same-lab' : 'diff-lab')
    : '';
  const srcArchive = (e.classification === 'REUSE' && e.source_archive)
    ? ' | source: ' + escapeHtml(e.source_archive)
    : '';
  return `
    <div class="${{cardClass(s)}}" id="entry-${{key}}">
      <div class="entry-header">
        <span class="entry-num">#${{displayIndex}} &mdash; ${{dsLink}}</span>
        <span style="font-size:12px;color:#999">conf: ${{e.confidence != null ? e.confidence : '?'}}${{sameLab}}${{srcArchive}}</span>
      </div>
      <div class="field"><span class="cls-tag cls-${{e.classification}}">${{e.classification}}</span> &mdash; ${{escapeHtml(e.dandiset_name || '')}}</div>
      <div class="field"><span class="field-label">Citing paper:</span> <a href="https://doi.org/${{encodeURIComponent(e.citing_doi)}}" target="_blank">${{escapeHtml(e.citing_doi)}}</a> &mdash; ${{escapeHtml(e.citing_title || '')}}</div>
      <div class="field" style="color:#666;font-size:12px">${{escapeHtml(e.citing_journal || '')}}${{e.citing_date ? ', ' + escapeHtml(e.citing_date) : ''}}</div>
      <div class="field"><span class="field-label">Primary paper:</span> <a href="https://doi.org/${{encodeURIComponent(e.cited_doi)}}" target="_blank">${{escapeHtml(e.cited_doi)}}</a></div>
      <div class="field"><span class="field-label">Excerpts:</span>${{renderExcerpts(e.context_excerpts)}}</div>
      <div class="reasoning"><strong>Pipeline reasoning:</strong> ${{escapeHtml(e.reasoning || '')}}</div>
      <div class="form-row">
        <div class="form-group">
          <label>Confirmed?</label>
          <select onchange="setConfirmed('${{key}}', this.value)">
            <option value="">--</option>
            <option value="yes" ${{sel('yes')}}>Yes</option>
            <option value="no" ${{sel('no')}}>No</option>
            <option value="unsure" ${{sel('unsure')}}>Unsure</option>
          </select>
        </div>
        <div class="form-group">
          <label>Notes:</label>
          <textarea onchange="setNotes('${{key}}', this.value)">${{escapeHtml(s.notes || '')}}</textarea>
        </div>
      </div>
    </div>`;
}}

function setConfirmed(key, value) {{
  state[key].confirmed = value || null;
  render();
}}

function setNotes(key, value) {{
  state[key].notes = value;
  updateProgress();
}}

function updateProgress() {{
  let reviewed = 0;
  for (const k in state) if (state[k].confirmed) reviewed++;
  document.getElementById('progress').textContent = reviewed + ' / ' + N + ' reviewed';
}}

function passesFilter(e) {{
  const cls = document.getElementById('filterCls').value;
  if (cls !== 'all' && e.classification !== cls) return false;
  const rv = document.getElementById('filterReview').value;
  const s = state[keyOf(e)];
  if (rv === 'unreviewed' && s.confirmed) return false;
  if (rv !== 'all' && rv !== 'unreviewed' && s.confirmed !== rv) return false;
  return true;
}}

function render() {{
  const sortBy = document.getElementById('sortBy').value;
  const filtered = entryData.filter(passesFilter);
  if (sortBy === 'confidence') {{
    filtered.sort((a, b) => (b.confidence || 0) - (a.confidence || 0));
  }} else {{
    filtered.sort((a, b) => (a.dandiset_id || '').localeCompare(b.dandiset_id || '') || a.citing_doi.localeCompare(b.citing_doi));
  }}
  const container = document.getElementById('entries');
  container.innerHTML = filtered.map((e, i) => renderEntry(e, i + 1)).join('');
  updateProgress();
}}

function jumpToNext() {{
  for (const e of entryData) {{
    if (!state[keyOf(e)].confirmed) {{
      const el = document.getElementById('entry-' + keyOf(e));
      if (el) {{ el.scrollIntoView({{behavior: 'smooth', block: 'center'}}); return; }}
    }}
  }}
  alert('All entries reviewed.');
}}

function saveState() {{
  const blob = new Blob([JSON.stringify(state, null, 2)], {{type: 'application/json'}});
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = 'review_state.json';
  a.click();
  URL.revokeObjectURL(url);
}}

function loadState(event) {{
  const file = event.target.files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = function(ev) {{
    const loaded = JSON.parse(ev.target.result);
    for (const k in loaded) {{
      if (state[k]) state[k] = Object.assign(state[k], loaded[k]);
    }}
    render();
  }};
  reader.readAsText(file);
}}

render();
</script>
</body>
</html>
"""


def build_entries(classifications_path: Path, datasets_path: Path) -> list[dict]:
    classifications = json.loads(classifications_path.read_text())["classifications"]

    dandiset_urls = {}
    if datasets_path.exists():
        datasets = json.loads(datasets_path.read_text()).get("results", [])
        for ds in datasets:
            dandiset_urls[ds["dandiset_id"]] = ds.get("dandiset_url", "")

    entries = []
    for c in classifications:
        entries.append({
            "citing_doi": c.get("citing_doi", ""),
            "cited_doi": c.get("cited_doi", ""),
            "dandiset_id": c.get("dandiset_id", ""),
            "dandiset_name": c.get("dandiset_name", ""),
            "dandiset_url": dandiset_urls.get(c.get("dandiset_id", ""), ""),
            "citing_title": c.get("citing_title", ""),
            "citing_journal": c.get("citing_journal", ""),
            "citing_date": c.get("citing_date", ""),
            "classification": c.get("classification", ""),
            "confidence": c.get("confidence"),
            "reasoning": c.get("reasoning", ""),
            "same_lab": c.get("same_lab"),
            "source_archive": c.get("source_archive", ""),
            "context_excerpts": c.get("context_excerpts", []),
        })

    entries.sort(key=lambda e: (e["dandiset_id"], e["citing_doi"]))
    return entries


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--archive", required=True,
                        help="Archive short name (e.g. dandi, crcns, openneuro, sparc).")
    parser.add_argument("--output", type=Path, default=None,
                        help="Output HTML path (default: output/minimal/<archive>/review.html).")
    args = parser.parse_args()

    input_dir = Path("output/minimal") / args.archive
    classifications_path = input_dir / "classifications.json"
    datasets_path = input_dir / "datasets.json"
    output_path = args.output or (input_dir / "review.html")

    entries = build_entries(classifications_path, datasets_path)

    html = HTML_TEMPLATE.format(
        archive_name=args.archive.upper(),
        n=len(entries),
        entries_json=json.dumps(entries),
    )
    output_path.write_text(html)
    print(f"Wrote {output_path} ({len(entries)} entries)")


if __name__ == "__main__":
    main()
