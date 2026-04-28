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
import random
import time
from collections import defaultdict
from pathlib import Path

import requests


AUTHOR_CACHE_PATH = Path(".review_author_cache.json")
YEAR_CACHE_PATH = Path(".review_year_cache.json")

# Fixed seed so stratified sampling is reproducible across runs and prefixes nest:
# the first 10 of a sample of 20 are exactly the same entries as a sample of 10.
SAMPLING_SEED = 0


def fetch_paper_metadata(dois: set[str]) -> tuple[dict[str, list[str]], dict[str, int]]:
    """Fetch author lists and publication years from OpenAlex, using on-disk caches.

    The author cache is shared with build_reuse_review.py (DOI -> list[str]).
    The year cache is specific to this script (DOI -> int).
    """
    author_cache = json.loads(AUTHOR_CACHE_PATH.read_text()) if AUTHOR_CACHE_PATH.exists() else {}
    year_cache = json.loads(YEAR_CACHE_PATH.read_text()) if YEAR_CACHE_PATH.exists() else {}

    need = [
        doi for doi in dois
        if doi and (doi.lower() not in author_cache or doi.lower() not in year_cache)
    ]
    if need:
        print(f"Fetching metadata for {len(need)} DOIs...")
        session = requests.Session()
        session.headers.update({"User-Agent": "FindReuse/1.0"})
        for i, doi in enumerate(sorted(need)):
            resp = session.get(f"https://api.openalex.org/works/doi:{doi}", timeout=10)
            if resp.status_code == 200:
                work = resp.json()
                author_cache[doi.lower()] = [
                    a["author"]["display_name"] for a in work.get("authorships", [])
                ]
                if work.get("publication_year"):
                    year_cache[doi.lower()] = work["publication_year"]
            if (i + 1) % 50 == 0:
                print(f"  {i + 1}/{len(need)}")
            time.sleep(0.05)
        AUTHOR_CACHE_PATH.write_text(json.dumps(author_cache))
        YEAR_CACHE_PATH.write_text(json.dumps(year_cache))

    return author_cache, year_cache


HTML_TEMPLATE = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>{archive_name} Reuse Review ({n} entries)</title>
<style>
  html, body {{ height: 100%; margin: 0; }}
  body {{ font-family: -apple-system, sans-serif; background: #f5f5f5; display: flex; flex-direction: column; overflow: hidden; }}
  .page {{ max-width: 1100px; width: 100%; margin: 0 auto; padding: 8px 16px; box-sizing: border-box; display: flex; flex-direction: column; flex: 1; min-height: 0; gap: 8px; }}
  .topbar {{ background: #e3f2fd; padding: 6px 12px; border-radius: 6px; display: flex; gap: 10px; align-items: center; flex-wrap: wrap; font-size: 13px; }}
  .topbar h1 {{ margin: 0; font-size: 15px; color: #0d47a1; margin-right: 6px; }}
  .topbar button {{ padding: 4px 10px; border-radius: 4px; border: 1px solid #1565c0; background: #1565c0; color: white; cursor: pointer; font-size: 12px; }}
  .topbar button:hover {{ background: #0d47a1; }}
  .topbar button.secondary {{ background: white; color: #1565c0; }}
  .topbar button.secondary:hover {{ background: #e3f2fd; }}
  .topbar select {{ padding: 2px 4px; font-size: 12px; }}
  .topbar .divider {{ width: 1px; height: 18px; background: #bbb; margin: 0 4px; }}
  .topbar .nav-btn {{ padding: 4px 10px; border-radius: 4px; border: 1px solid #888; background: white; color: #333; cursor: pointer; font-size: 12px; }}
  .topbar .nav-btn:hover:not(:disabled) {{ background: #eee; }}
  .topbar .nav-btn:disabled {{ opacity: 0.4; cursor: not-allowed; }}
  .progress {{ font-size: 12px; color: #555; margin-left: auto; }}
  .entry {{ background: white; border: 1px solid #ddd; border-radius: 8px; padding: 12px 16px; display: flex; flex-direction: column; flex: 1; min-height: 0; gap: 6px; }}
  .entry.reviewed {{ border-left: 4px solid #4caf50; }}
  .entry.not-reuse {{ border-left: 4px solid #e53935; }}
  .entry.unsure {{ border-left: 4px solid #ff9800; }}
  .entry-header {{ display: flex; justify-content: space-between; align-items: baseline; }}
  .entry-num {{ font-size: 15px; font-weight: bold; color: #1565c0; }}
  .cls-tag {{ display: inline-block; padding: 1px 7px; border-radius: 10px; font-size: 11px; font-weight: bold; color: white; }}
  .cls-REUSE {{ background: #2e7d32; }}
  .cls-MENTION {{ background: #1565c0; }}
  .cls-NEITHER {{ background: #757575; }}
  .field {{ margin: 0; font-size: 13px; line-height: 1.35; }}
  .field-label {{ font-weight: bold; color: #555; }}
  .authors {{ color: #444; font-size: 12px; }}
  .excerpts-box {{ flex: 1; min-height: 0; overflow-y: auto; border: 1px solid #eee; border-radius: 4px; padding: 4px 8px; background: #fafafa; }}
  .excerpt {{ background: white; border-left: 3px solid #1565c0; padding: 6px 10px; margin: 4px 0; font-size: 12px; line-height: 1.45; }}
  .excerpt .cite-label {{ font-size: 11px; color: #1565c0; font-weight: bold; }}
  .reasoning {{ padding: 6px 8px; background: #f5f5f5; border-radius: 4px; font-size: 12px; color: #444; line-height: 1.4; }}
  .bottom-row {{ display: flex; gap: 10px; align-items: stretch; }}
  .notes {{ flex: 1; display: flex; flex-direction: column; }}
  .notes label {{ font-weight: bold; font-size: 12px; margin-bottom: 2px; }}
  .notes textarea {{ width: 100%; box-sizing: border-box; flex: 1; min-height: 48px; padding: 4px 6px; font-family: inherit; font-size: 12px; resize: none; }}
  .decision-bar {{ display: flex; flex-direction: column; gap: 4px; justify-content: flex-end; }}
  .decision-bar button {{ padding: 8px 14px; border-radius: 4px; border: 1px solid #ccc; background: white; cursor: pointer; font-size: 13px; font-weight: bold; white-space: nowrap; }}
  .decision-bar button.yes {{ border-color: #4caf50; color: #2e7d32; }}
  .decision-bar button.yes:hover, .decision-bar button.yes.active {{ background: #4caf50; color: white; }}
  .decision-bar button.no {{ border-color: #e53935; color: #c62828; }}
  .decision-bar button.no:hover, .decision-bar button.no.active {{ background: #e53935; color: white; }}
  .decision-bar button.unsure {{ border-color: #ff9800; color: #e65100; }}
  .decision-bar button.unsure:hover, .decision-bar button.unsure.active {{ background: #ff9800; color: white; }}
  .decision-bar button.clear {{ border-color: #888; color: #555; font-weight: normal; font-size: 11px; padding: 4px 10px; }}
  .decision-bar button.clear:hover {{ background: #eee; }}
  .empty {{ text-align: center; padding: 40px; color: #888; background: white; border-radius: 8px; }}
</style>
</head>
<body>
<div class="page">
  <div class="topbar">
    <h1>{archive_name} Review</h1>
    <button onclick="saveState()">Save</button>
    <button class="secondary" onclick="document.getElementById('loadFile').click()">Load</button>
    <input type="file" id="loadFile" accept=".json" style="display:none" onchange="loadState(event)">
    <span class="divider"></span>
    <label>Class:</label>
    <select id="filterCls" onchange="onFilterChange()">
      <option value="all">All</option>
      <option value="REUSE" selected>REUSE</option>
      <option value="MENTION">MENTION</option>
      <option value="NEITHER">NEITHER</option>
    </select>
    <label>Status:</label>
    <select id="filterReview" onchange="onFilterChange()">
      <option value="all">All</option>
      <option value="unreviewed">Unreviewed</option>
      <option value="yes">Confirmed Yes</option>
      <option value="no">Confirmed No</option>
      <option value="unsure">Unsure</option>
    </select>
    <label>Sort:</label>
    <select id="sortBy" onchange="onFilterChange()">
      <option value="dataset">Dataset ID</option>
      <option value="confidence">Confidence</option>
      <option value="sampleOrder">Sample Order</option>
    </select>
    <span class="divider"></span>
    <button class="nav-btn" onclick="goPrev()" id="prevBtn">&larr; Prev</button>
    <button class="nav-btn" onclick="goNext()" id="nextBtn">Next &rarr;</button>
    <button class="secondary" onclick="jumpToNextUnreviewed()">Next unreviewed</button>
    <span class="progress" id="progress"></span>
  </div>
  <div id="entry-container" style="display: flex; flex-direction: column; flex: 1; min-height: 0;"></div>
</div>

<script>
const entryData = {entries_json};
const N = entryData.length;

let state = {{}};
for (let i = 0; i < N; i++) {{
  const key = entryData[i].citing_doi + '|' + entryData[i].dandiset_id;
  state[key] = {{ confirmed: null, notes: '' }};
}}

let currentIndex = 0;  // index into the current filtered list

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
      html += '<div class="excerpt"><span class="cite-label">Citation: ' + escapeHtml(label) + '</span><br>' + escapeHtml(ex.text || '') + '</div>';
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

function formatAuthors(authors) {{
  if (!authors || authors.length === 0) return '<em>authors unavailable</em>';
  const shown = authors.slice(0, 10).map(escapeHtml).join(', ');
  const extra = authors.length > 10 ? ' <em>... (+' + (authors.length - 10) + ' more)</em>' : '';
  return shown + extra;
}}

function renderEntry(e, displayIndex, total) {{
  const key = keyOf(e);
  const s = state[key];
  const dsLink = e.dandiset_url
    ? '<a href="' + escapeHtml(e.dandiset_url) + '" target="_blank">' + escapeHtml(e.dandiset_id) + '</a>'
    : escapeHtml(e.dandiset_id);
  const sameLab = e.classification === 'REUSE'
    ? ' | ' + (e.same_lab ? 'same-lab' : 'diff-lab')
    : '';
  const srcArchive = (e.classification === 'REUSE' && e.source_archive)
    ? ' | source: ' + escapeHtml(e.source_archive)
    : '';
  const active = v => s.confirmed === v ? 'active' : '';
  return `
    <div class="${{cardClass(s)}}" id="entry-${{key}}">
      <div class="entry-header">
        <span class="entry-num">#${{displayIndex}} of ${{total}} &mdash; ${{dsLink}} &mdash; <span class="cls-tag cls-${{e.classification}}">${{e.classification}}</span> ${{escapeHtml(e.dandiset_name || '')}}</span>
        <span style="font-size:12px;color:#999">conf: ${{e.confidence != null ? e.confidence : '?'}}${{sameLab}}${{srcArchive}}</span>
      </div>
      <div class="field"><span class="field-label">Citing:</span> <a href="https://doi.org/${{encodeURIComponent(e.citing_doi)}}" target="_blank">${{escapeHtml(e.citing_doi)}}</a> &mdash; ${{escapeHtml(e.citing_title || '')}} <span style="color:#666">(${{escapeHtml(e.citing_journal || '')}}${{e.citing_date ? ', ' + escapeHtml(e.citing_date) : ''}})</span></div>
      <div class="field"><span class="field-label">Primary:</span> <a href="https://doi.org/${{encodeURIComponent(e.cited_doi)}}" target="_blank">${{escapeHtml(e.cited_doi)}}</a>${{e.primary_year ? ' <span class="authors">(' + e.primary_year + ')</span>' : ''}} &mdash; <span class="authors">${{formatAuthors(e.primary_authors)}}</span></div>
      <div class="reasoning"><strong>Pipeline reasoning:</strong> ${{escapeHtml(e.reasoning || '')}}</div>
      <div class="excerpts-box">${{renderExcerpts(e.context_excerpts)}}</div>
      <div class="bottom-row">
        <div class="notes">
          <label>Notes:</label>
          <textarea onchange="setNotes('${{key}}', this.value)">${{escapeHtml(s.notes || '')}}</textarea>
        </div>
        <div class="decision-bar">
          <div style="display:flex;gap:6px;">
            <button class="yes ${{active('yes')}}" onclick="markAndAdvance('${{key}}', 'yes')">&check; Correct</button>
            <button class="no ${{active('no')}}" onclick="markAndAdvance('${{key}}', 'no')">&times; Incorrect</button>
            <button class="unsure ${{active('unsure')}}" onclick="markAndAdvance('${{key}}', 'unsure')">? Unsure</button>
          </div>
          <button class="clear" onclick="clearConfirmation('${{key}}')">Clear</button>
        </div>
      </div>
    </div>`;
}}

function currentFiltered() {{
  const sortBy = document.getElementById('sortBy').value;
  const filtered = entryData.filter(passesFilter);
  if (sortBy === 'confidence') {{
    filtered.sort((a, b) => (b.confidence || 0) - (a.confidence || 0));
  }} else if (sortBy === 'sampleOrder') {{
    filtered.sort((a, b) =>
      ((a.sample_order ?? 1e9) - (b.sample_order ?? 1e9))
      || (a.classification || '').localeCompare(b.classification || '')
    );
  }} else {{
    filtered.sort((a, b) => (a.dandiset_id || '').localeCompare(b.dandiset_id || '') || a.citing_doi.localeCompare(b.citing_doi));
  }}
  return filtered;
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
  const filtered = currentFiltered();
  const container = document.getElementById('entry-container');
  const navPosition = document.getElementById('navPosition');
  const prevBtn = document.getElementById('prevBtn');
  const nextBtn = document.getElementById('nextBtn');

  if (filtered.length === 0) {{
    container.innerHTML = '<div class="empty">No entries match the current filters.</div>';
    navPosition.textContent = '0 of 0';
    prevBtn.disabled = true;
    nextBtn.disabled = true;
  }} else {{
    if (currentIndex >= filtered.length) currentIndex = filtered.length - 1;
    if (currentIndex < 0) currentIndex = 0;
    const entry = filtered[currentIndex];
    container.innerHTML = renderEntry(entry, currentIndex + 1, filtered.length);
    navPosition.textContent = (currentIndex + 1) + ' of ' + filtered.length;
    prevBtn.disabled = currentIndex === 0;
    nextBtn.disabled = currentIndex === filtered.length - 1;
  }}
  updateProgress();
}}

function onFilterChange() {{
  currentIndex = 0;
  render();
}}

function goPrev() {{
  if (currentIndex > 0) {{ currentIndex--; render(); }}
}}

function goNext() {{
  const filtered = currentFiltered();
  if (currentIndex < filtered.length - 1) {{ currentIndex++; render(); }}
}}

function markAndAdvance(key, value) {{
  state[key].confirmed = value;
  const filtered = currentFiltered();
  // If filtering by unreviewed/status, the current entry may drop out — stay at same index;
  // otherwise advance to next entry.
  const rv = document.getElementById('filterReview').value;
  const filterDropsMarked = rv === 'unreviewed' || (rv !== 'all' && rv !== value);
  if (!filterDropsMarked && currentIndex < filtered.length - 1) {{
    currentIndex++;
  }}
  render();
}}

function clearConfirmation(key) {{
  state[key].confirmed = null;
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

function jumpToNextUnreviewed() {{
  const filtered = currentFiltered();
  for (let i = 0; i < filtered.length; i++) {{
    if (!state[keyOf(filtered[i])].confirmed) {{
      currentIndex = i;
      render();
      return;
    }}
  }}
  alert('All entries in the current filter are reviewed.');
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
        for dataset in datasets:
            dandiset_urls[dataset["dandiset_id"]] = dataset.get("dandiset_url", "")

    dois_to_fetch = set()
    for c in classifications:
        if c.get("cited_doi"):
            dois_to_fetch.add(c["cited_doi"])
    author_cache, year_cache = fetch_paper_metadata(dois_to_fetch)

    entries = []
    for c in classifications:
        cited_doi = c.get("cited_doi", "")
        entries.append({
            "citing_doi": c.get("citing_doi", ""),
            "cited_doi": cited_doi,
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
            "primary_authors": author_cache.get(cited_doi.lower(), []) if cited_doi else [],
            "primary_year": year_cache.get(cited_doi.lower()) if cited_doi else None,
        })

    entries.sort(key=lambda e: (e["dandiset_id"], e["citing_doi"]))
    return entries


def stratified_sample(entries: list[dict], samples_per_class: int) -> list[dict]:
    """Stratified sample: shuffle each classification stratum once with SAMPLING_SEED,
    then take the first `samples_per_class` entries. Shuffling (rather than `random.sample`)
    ensures prefixes nest, so a sample of 20 contains the same first-10 as a sample of 10.
    """
    by_class = defaultdict(list)
    for entry in entries:
        by_class[entry["classification"]].append(entry)

    sampled = []
    for classification in sorted(by_class):
        group = by_class[classification]
        rng = random.Random(SAMPLING_SEED)
        rng.shuffle(group)
        take = min(samples_per_class, len(group))
        for index, entry in enumerate(group[:take]):
            entry["sample_order"] = index
        sampled.extend(group[:take])
        print(f"  {classification}: sampled {take} of {len(group)}")

    sampled.sort(key=lambda e: (e["dandiset_id"], e["citing_doi"]))
    return sampled


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--archive", required=True,
                        help="Archive short name (e.g. dandi, crcns, openneuro, sparc).")
    parser.add_argument("--output", type=Path, default=None,
                        help="Output HTML path (default: output/minimal/<archive>/review.html).")
    parser.add_argument("--stratified-sample", action="store_true",
                        help="Randomly sample entries stratified by classification (REUSE/MENTION/NEITHER).")
    parser.add_argument("--samples-per-class", type=int, default=50,
                        help="Number of entries to sample per classification when --stratified-sample is set (default: 50).")
    args = parser.parse_args()

    input_dir = Path("output/minimal") / args.archive
    classifications_path = input_dir / "classifications.json"
    datasets_path = input_dir / "datasets.json"
    output_path = args.output or (input_dir / "review.html")

    entries = build_entries(classifications_path, datasets_path)

    if args.stratified_sample:
        print(f"Stratified sampling (samples_per_class={args.samples_per_class}, seed={SAMPLING_SEED}):")
        entries = stratified_sample(entries, args.samples_per_class)

    html = HTML_TEMPLATE.format(
        archive_name=args.archive.upper(),
        n=len(entries),
        entries_json=json.dumps(entries),
    )
    output_path.write_text(html)
    print(f"Wrote {output_path} ({len(entries)} entries)")


if __name__ == "__main__":
    main()
