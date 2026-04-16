#!/usr/bin/env python3
"""
build_reuse_review.py — Build a review dashboard for all REUSE entries sourced from DANDI Archive.

Shows each entry with dandiset info, paper info, excerpts, and data availability.
Allows labeling: confirmed/not reuse/unsure, reuse type, source archive.
Supports save/load of review state to JSON.

Generates: output/reuse_review_dashboard.html
"""

import json
import time
from pathlib import Path

import requests


def _resolve_preprint_pairs():
    """Build a mapping from preprint DOIs to their published versions using OpenAlex cache."""
    # We'll detect pairs where both a biorxiv/medrxiv DOI and a journal DOI
    # appear for the same paper by checking title similarity in our data
    # For now, use a simpler heuristic: group by citing_title
    return {}


def load_entries():
    """Load all REUSE entries with source_archive == 'DANDI Archive', deduplicated to unique papers.

    When a paper reuses multiple dandisets, we keep one entry but list all dandisets.
    """
    with open("output/all_classifications.json") as f:
        cls = json.load(f)

    raw = [
        c for c in cls["classifications"]
        if c["classification"] == "REUSE"
        and c.get("source_archive") == "DANDI Archive"
    ]

    # Group by citing_doi, merging dandiset info
    by_doi = {}
    for c in raw:
        doi = c["citing_doi"]
        if doi not in by_doi:
            by_doi[doi] = {
                **c,
                "all_dandisets": [],
            }
        by_doi[doi]["all_dandisets"].append({
            "dandiset_id": c.get("dandiset_id", ""),
            "dandiset_name": c.get("dandiset_name", ""),
            "cited_doi": c.get("cited_doi", ""),
            "same_lab": c.get("same_lab"),
        })
        # Keep the entry with the most excerpts
        existing_excerpts = len(by_doi[doi].get("context_excerpts") or [])
        new_excerpts = len(c.get("context_excerpts") or [])
        if new_excerpts > existing_excerpts:
            all_ds = by_doi[doi]["all_dandisets"]
            by_doi[doi].update(c)
            by_doi[doi]["all_dandisets"] = all_ds

    # Deduplicate preprint/published pairs by title
    # Group entries by normalized title
    from difflib import SequenceMatcher
    entries_list = list(by_doi.values())
    titles = {}
    remove_dois = set()
    for e in entries_list:
        title = (e.get("citing_title") or "").strip().lower()
        if not title or len(title) < 20:
            continue
        matched = False
        for existing_title, existing_doi in titles.items():
            if SequenceMatcher(None, title, existing_title).ratio() > 0.85:
                # Prefer the non-preprint (journal) version
                e_is_preprint = "10.1101/" in e["citing_doi"] or "arxiv" in e["citing_doi"]
                existing_is_preprint = "10.1101/" in existing_doi or "arxiv" in existing_doi
                if e_is_preprint and not existing_is_preprint:
                    # Keep existing (journal), remove this preprint
                    remove_dois.add(e["citing_doi"])
                    # Merge dandisets
                    by_doi[existing_doi]["all_dandisets"].extend(e["all_dandisets"])
                elif existing_is_preprint and not e_is_preprint:
                    # Keep this (journal), remove existing preprint
                    remove_dois.add(existing_doi)
                    by_doi[e["citing_doi"]]["all_dandisets"].extend(by_doi[existing_doi]["all_dandisets"])
                    titles[title] = e["citing_doi"]
                matched = True
                break
        if not matched:
            titles[title] = e["citing_doi"]

    entries = [e for e in entries_list if e["citing_doi"] not in remove_dois]
    entries.sort(key=lambda c: (c.get("dandiset_id", ""), c["citing_doi"]))
    return entries


def load_reuse_types():
    """Load reuse type classifications from cache."""
    cache_dir = Path(".reuse_type_cache")
    types = {}
    if cache_dir.exists():
        for f in cache_dir.glob("*.json"):
            with open(f) as fh:
                d = json.load(fh)
            key = (d["citing_doi"], d["dandiset_id"])
            types[key] = d.get("reuse_type", "")
    return types


def fetch_authors(entries):
    """Fetch citing and primary paper authors from OpenAlex."""
    session = requests.Session()
    session.headers.update({"User-Agent": "FindReuse/1.0"})

    all_dois = set()
    for e in entries:
        all_dois.add(e["citing_doi"])
        if e.get("cited_doi"):
            all_dois.add(e["cited_doi"])

    # Check for cached authors
    cache_path = Path(".review_author_cache.json")
    if cache_path.exists():
        with open(cache_path) as f:
            author_cache = json.load(f)
    else:
        author_cache = {}

    need = [d for d in all_dois if d.lower() not in author_cache]
    if need:
        print(f"Fetching authors for {len(need)} DOIs...")
        for i, doi in enumerate(sorted(need)):
            try:
                resp = session.get(f"https://api.openalex.org/works/doi:{doi}", timeout=10)
                if resp.status_code == 200:
                    w = resp.json()
                    authors = [a["author"]["display_name"] for a in w.get("authorships", [])]
                    author_cache[doi.lower()] = authors
            except Exception:
                pass
            if (i + 1) % 100 == 0:
                print(f"  {i + 1}/{len(need)}")
            time.sleep(0.05)
        with open(cache_path, "w") as f:
            json.dump(author_cache, f)

    return author_cache


def extract_data_availability(doi, max_chars=1000):
    """Extract data availability section from cached paper text."""
    safe_doi = doi.replace("/", "_")
    cache_path = Path(".paper_cache") / f"{safe_doi}.json"
    if not cache_path.exists():
        return ""
    with open(cache_path) as f:
        d = json.load(f)
    text = d.get("text", "")
    if not text:
        return ""
    lower = text.lower()
    markers = [
        "data availability", "data access", "data and code availability",
        "data sharing", "code and data", "key resources",
        "star methods", "data deposition",
    ]
    best_start = -1
    for marker in markers:
        idx = lower.find(marker)
        if idx != -1 and (best_start == -1 or idx < best_start):
            best_start = idx
    if best_start == -1:
        return ""
    return text[best_start:best_start + max_chars]


def build_html(entries, author_cache, reuse_types):
    """Build HTML review dashboard."""
    n = len(entries)

    # Build entry data for JS
    entry_data = []
    for i, e in enumerate(entries):
        doi = e["citing_doi"]
        dandiset_id = e.get("dandiset_id", "")
        primary_doi = e.get("cited_doi", "")

        citing_authors = author_cache.get(doi.lower(), [])
        primary_authors = author_cache.get(primary_doi.lower(), []) if primary_doi else []

        excerpts = e.get("context_excerpts", [])
        reuse_type = reuse_types.get((doi, dandiset_id), "")

        data_avail = extract_data_availability(doi)

        entry_data.append({
            "citing_doi": doi,
            "dandiset_id": dandiset_id,
            "dandiset_name": e.get("dandiset_name", ""),
            "all_dandisets": e.get("all_dandisets", [{"dandiset_id": dandiset_id, "dandiset_name": e.get("dandiset_name", "")}]),
            "citing_title": e.get("citing_title", ""),
            "citing_journal": e.get("citing_journal", ""),
            "citing_date": e.get("citing_date", ""),
            "primary_doi": primary_doi,
            "citing_authors": citing_authors,
            "primary_authors": primary_authors,
            "confidence": e.get("confidence"),
            "same_lab": e.get("same_lab"),
            "pipeline_source_archive": e.get("source_archive", ""),
            "pipeline_reuse_type": reuse_type,
            "reasoning": e.get("reasoning", ""),
            "excerpts": excerpts,
            "data_avail": data_avail,
        })

    html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>DANDI Reuse Review Dashboard</title>
<style>
  body {{ font-family: -apple-system, sans-serif; max-width: 950px; margin: 0 auto; padding: 20px; background: #f5f5f5; }}
  h1 {{ text-align: center; }}
  .controls {{ background: #e3f2fd; padding: 15px; border-radius: 8px; margin-bottom: 20px; display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }}
  .controls button {{ padding: 8px 16px; border-radius: 4px; border: 1px solid #1565c0; background: #1565c0; color: white; cursor: pointer; font-size: 13px; }}
  .controls button:hover {{ background: #0d47a1; }}
  .controls button.secondary {{ background: white; color: #1565c0; }}
  .controls button.secondary:hover {{ background: #e3f2fd; }}
  .progress {{ font-size: 13px; color: #555; margin-left: auto; }}
  .filter-bar {{ background: #fff; padding: 10px 15px; border-radius: 8px; margin-bottom: 15px; display: flex; gap: 15px; align-items: center; font-size: 13px; }}
  .entry {{ background: white; border: 1px solid #ddd; border-radius: 8px; padding: 20px; margin-bottom: 15px; }}
  .entry.reviewed {{ border-left: 4px solid #4caf50; }}
  .entry.not-reuse {{ border-left: 4px solid #e53935; }}
  .entry.unsure {{ border-left: 4px solid #ff9800; }}
  .entry-header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; }}
  .entry-num {{ font-size: 16px; font-weight: bold; color: #1565c0; }}
  .field {{ margin: 6px 0; font-size: 13px; }}
  .field-label {{ font-weight: bold; color: #555; }}
  .authors {{ font-size: 12px; color: #666; }}
  .excerpt {{ background: #fafafa; border-left: 3px solid #1565c0; padding: 8px 12px; margin: 5px 0; font-size: 12px; line-height: 1.5; }}
  .excerpt .cite-label {{ font-size: 11px; color: #1565c0; font-weight: bold; }}
  .data-avail {{ background: #fff8e1; border-left: 3px solid #f9a825; padding: 8px 12px; margin: 5px 0; font-size: 12px; }}
  .pipeline-info {{ margin-top: 8px; padding: 8px; background: #f5f5f5; border-radius: 4px; font-size: 12px; color: #666; }}
  .form-row {{ display: flex; gap: 20px; margin: 10px 0; flex-wrap: wrap; }}
  .form-group {{ font-size: 13px; }}
  .form-group label {{ font-weight: bold; display: block; margin-bottom: 4px; }}
  .form-group select {{ padding: 4px 8px; font-size: 13px; }}
  .radio-group {{ display: flex; gap: 10px; }}
  .radio-group label {{ font-weight: normal; cursor: pointer; padding: 4px 10px; border: 1px solid #ddd; border-radius: 4px; font-size: 12px; }}
  .radio-group input:checked + span {{ font-weight: bold; }}
  mark {{ background: #fff176; }}
  .hidden {{ display: none; }}
</style>
</head>
<body>
<h1>DANDI Reuse Review ({n} entries)</h1>

<div class="controls">
  <button onclick="saveState()">Save to JSON</button>
  <button class="secondary" onclick="document.getElementById('loadFile').click()">Load from JSON</button>
  <input type="file" id="loadFile" accept=".json" style="display:none" onchange="loadState(event)">
  <button class="secondary" onclick="jumpToNext()">Jump to next unreviewed</button>
  <span class="progress" id="progress"></span>
</div>

<div class="filter-bar">
  <label>Show: </label>
  <select id="filterSelect" onchange="applyFilter()">
    <option value="all">All ({n})</option>
    <option value="unreviewed">Unreviewed</option>
    <option value="confirmed">Confirmed REUSE</option>
    <option value="not_reuse">Not REUSE</option>
    <option value="unsure">Unsure</option>
  </select>
  <label style="margin-left:15px">Sort by: </label>
  <select id="sortSelect" onchange="applyFilter()">
    <option value="default">Dandiset ID</option>
    <option value="confidence">Pipeline confidence</option>
  </select>
</div>

<div id="entries"></div>

<script>
const entryData = {json.dumps(entry_data)};
const N = entryData.length;

// State: one object per entry
let state = {{}};
for (let i = 0; i < N; i++) {{
  const key = entryData[i].citing_doi + '|' + entryData[i].dandiset_id;
  state[key] = {{
    confirmed: null,  // 'yes', 'no', 'unsure'
    reuse_type: null,
    source_archive: null,
  }};
}}

function getKey(i) {{
  return entryData[i].citing_doi + '|' + entryData[i].dandiset_id;
}}

function escapeHtml(text) {{
  const div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
}}

function renderExcerpts(excerpts) {{
  if (!excerpts || excerpts.length === 0) return '<div class="excerpt"><em>No excerpts available</em></div>';
  let html = '';
  for (const ex of excerpts.slice(0, 5)) {{
    if (typeof ex === 'object') {{
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

function renderEntry(i) {{
  const e = entryData[i];
  const key = getKey(i);
  const s = state[key];

  let cls = 'entry';
  if (s.confirmed === 'yes') cls += ' reviewed';
  else if (s.confirmed === 'no') cls += ' not-reuse';
  else if (s.confirmed === 'unsure') cls += ' unsure';

  const citingAuthors = (e.citing_authors || []).slice(0, 8).join(', ') + (e.citing_authors.length > 8 ? ' ...' : '');
  const primaryAuthors = (e.primary_authors || []).slice(0, 8).join(', ') + (e.primary_authors.length > 8 ? ' ...' : '');

  let dataAvailHtml = '';
  if (e.data_avail) {{
    dataAvailHtml = '<div class="field"><span class="field-label">Data availability:</span><div class="data-avail">' + escapeHtml(e.data_avail) + '</div></div>';
  }}

  const sel = (name, val) => s[name] === val ? 'selected' : '';

  return `
  <div class="${{cls}}" id="entry-${{i}}">
    <div class="entry-header">
      <span class="entry-num">#${{i+1}} &mdash; DANDI:${{e.dandiset_id}}</span>
      <span style="font-size:12px;color:#999">conf: ${{e.confidence}} | ${{e.same_lab ? 'same-lab' : 'diff-lab'}}</span>
    </div>
    <div class="field"><span class="field-label">Dandiset(s):</span> ${{e.all_dandisets.map(d => '<a href="https://dandiarchive.org/dandiset/' + d.dandiset_id + '" target="_blank">DANDI:' + d.dandiset_id + '</a> (' + escapeHtml(d.dandiset_name) + ')').join(', ')}}</div>
    <div class="field"><span class="field-label">Citing paper:</span> <a href="https://doi.org/${{e.citing_doi}}" target="_blank">${{e.citing_doi}}</a> &mdash; ${{escapeHtml(e.citing_title || '')}}</div>
    <div class="authors">Citing authors: ${{citingAuthors || '<em>not available</em>'}}</div>
    <div class="field"><span class="field-label">Primary paper:</span> <a href="https://doi.org/${{e.primary_doi}}" target="_blank">${{e.primary_doi}}</a></div>
    <div class="authors">Primary authors: ${{primaryAuthors || '<em>not available</em>'}}</div>
    <div class="field"><span class="field-label">Excerpts:</span>${{renderExcerpts(e.excerpts)}}</div>
    ${{dataAvailHtml}}
    <div class="pipeline-info"><strong>Pipeline reasoning:</strong> ${{escapeHtml((e.reasoning || '').slice(0, 400))}}<br><strong>Pipeline reuse type:</strong> ${{e.pipeline_reuse_type || 'not classified'}}</div>
    <div class="form-row">
      <div class="form-group">
        <label>Confirmed REUSE?</label>
        <select onchange="setState(${{i}}, 'confirmed', this.value)">
          <option value="">--</option>
          <option value="yes" ${{sel('confirmed','yes')}}>Yes</option>
          <option value="no" ${{sel('confirmed','no')}}>No</option>
          <option value="unsure" ${{sel('confirmed','unsure')}}>Unsure</option>
        </select>
      </div>
      <div class="form-group">
        <label>Reuse type:</label>
        <select onchange="setState(${{i}}, 'reuse_type', this.value)">
          <option value="">--</option>
          <option value="TOOL_DEMO" ${{sel('reuse_type','TOOL_DEMO')}}>Tool demo</option>
          <option value="NOVEL_ANALYSIS" ${{sel('reuse_type','NOVEL_ANALYSIS')}}>Novel analysis</option>
          <option value="AGGREGATION" ${{sel('reuse_type','AGGREGATION')}}>Aggregation</option>
          <option value="BENCHMARK" ${{sel('reuse_type','BENCHMARK')}}>Benchmark</option>
          <option value="CONFIRMATORY" ${{sel('reuse_type','CONFIRMATORY')}}>Confirmatory</option>
          <option value="SIMULATION" ${{sel('reuse_type','SIMULATION')}}>Simulation</option>
          <option value="ML_TRAINING" ${{sel('reuse_type','ML_TRAINING')}}>ML training</option>
          <option value="TEACHING" ${{sel('reuse_type','TEACHING')}}>Teaching</option>
          <option value="unsure" ${{sel('reuse_type','unsure')}}>Unsure</option>
        </select>
      </div>
      <div class="form-group">
        <label>Source archive:</label>
        <select onchange="setState(${{i}}, 'source_archive', this.value)">
          <option value="">--</option>
          <option value="DANDI Archive" ${{sel('source_archive','DANDI Archive')}}>DANDI</option>
          <option value="Allen Institute" ${{sel('source_archive','Allen Institute')}}>Allen Institute</option>
          <option value="IBL" ${{sel('source_archive','IBL')}}>IBL</option>
          <option value="CRCNS" ${{sel('source_archive','CRCNS')}}>CRCNS</option>
          <option value="Figshare" ${{sel('source_archive','Figshare')}}>Figshare</option>
          <option value="CELLxGENE" ${{sel('source_archive','CELLxGENE')}}>CELLxGENE</option>
          <option value="other" ${{sel('source_archive','other')}}>Other</option>
          <option value="unsure" ${{sel('source_archive','unsure')}}>Unsure</option>
        </select>
      </div>
    </div>
  </div>`;
}}

let visibleIndices = [];

function applyFilter() {{
  const filter = document.getElementById('filterSelect').value;
  const sort = document.getElementById('sortSelect').value;

  visibleIndices = [];
  for (let i = 0; i < N; i++) {{
    const s = state[getKey(i)];
    if (filter === 'all') visibleIndices.push(i);
    else if (filter === 'unreviewed' && !s.confirmed) visibleIndices.push(i);
    else if (filter === 'confirmed' && s.confirmed === 'yes') visibleIndices.push(i);
    else if (filter === 'not_reuse' && s.confirmed === 'no') visibleIndices.push(i);
    else if (filter === 'unsure' && s.confirmed === 'unsure') visibleIndices.push(i);
  }}

  if (sort === 'confidence') {{
    visibleIndices.sort((a, b) => (entryData[a].confidence || 0) - (entryData[b].confidence || 0));
  }}

  renderVisible();
  updateProgress();
}}

function renderVisible() {{
  const container = document.getElementById('entries');
  // Only render first 50 visible for performance, with a "show more" button
  const limit = Math.min(visibleIndices.length, 50);
  let html = '';
  for (let j = 0; j < limit; j++) {{
    html += renderEntry(visibleIndices[j]);
  }}
  if (visibleIndices.length > limit) {{
    html += `<button onclick="showAll()" style="display:block;margin:20px auto;padding:10px 20px;font-size:14px;cursor:pointer;">Show all ${{visibleIndices.length}} entries (may be slow)</button>`;
  }}
  container.innerHTML = html;
}}

function showAll() {{
  const container = document.getElementById('entries');
  let html = '';
  for (const i of visibleIndices) html += renderEntry(i);
  container.innerHTML = html;
}}

function setState(i, field, value) {{
  const key = getKey(i);
  state[key][field] = value || null;
  // Re-render just this entry
  const el = document.getElementById('entry-' + i);
  if (el) {{
    const tmp = document.createElement('div');
    tmp.innerHTML = renderEntry(i);
    el.replaceWith(tmp.firstElementChild);
  }}
  updateProgress();
}}

function updateProgress() {{
  let reviewed = 0;
  let confirmed = 0;
  let notReuse = 0;
  let unsure = 0;
  for (const key in state) {{
    if (state[key].confirmed === 'yes') {{ reviewed++; confirmed++; }}
    else if (state[key].confirmed === 'no') {{ reviewed++; notReuse++; }}
    else if (state[key].confirmed === 'unsure') {{ reviewed++; unsure++; }}
  }}
  document.getElementById('progress').textContent =
    `${{reviewed}}/${{N}} reviewed (${{confirmed}} yes, ${{notReuse}} no, ${{unsure}} unsure)`;
}}

function saveState() {{
  const out = [];
  for (let i = 0; i < N; i++) {{
    const key = getKey(i);
    const s = state[key];
    if (s.confirmed || s.reuse_type || s.source_archive) {{
      out.push({{
        citing_doi: entryData[i].citing_doi,
        dandiset_id: entryData[i].dandiset_id,
        ...s
      }});
    }}
  }}
  const blob = new Blob([JSON.stringify(out, null, 2)], {{type: 'application/json'}});
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = 'reuse_review_state.json';
  a.click();
}}

function loadState(event) {{
  const file = event.target.files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = function(e) {{
    const data = JSON.parse(e.target.result);
    for (const d of data) {{
      const key = d.citing_doi + '|' + d.dandiset_id;
      if (state[key]) {{
        state[key].confirmed = d.confirmed || null;
        state[key].reuse_type = d.reuse_type || null;
        state[key].source_archive = d.source_archive || null;
      }}
    }}
    applyFilter();
  }};
  reader.readAsText(file);
}}

function jumpToNext() {{
  for (let i = 0; i < N; i++) {{
    if (!state[getKey(i)].confirmed) {{
      // Make sure it's visible
      document.getElementById('filterSelect').value = 'all';
      applyFilter();
      setTimeout(() => {{
        const el = document.getElementById('entry-' + i);
        if (el) el.scrollIntoView({{behavior: 'smooth', block: 'center'}});
      }}, 100);
      return;
    }}
  }}
  alert('All entries reviewed!');
}}

// Initial render
applyFilter();
</script>
</body>
</html>"""

    return html


def main():
    print("Loading REUSE entries with source_archive='DANDI Archive'...")
    entries = load_entries()
    print(f"  {len(entries)} entries")

    print("Loading reuse types...")
    reuse_types = load_reuse_types()

    print("Fetching authors...")
    author_cache = fetch_authors(entries)
    print(f"  Authors cached for {len(author_cache)} DOIs")

    print("Building HTML dashboard...")
    html = build_html(entries, author_cache, reuse_types)

    Path("output").mkdir(exist_ok=True)
    with open("output/reuse_review_dashboard.html", "w") as f:
        f.write(html)
    print(f"Saved output/reuse_review_dashboard.html ({len(entries)} entries)")


if __name__ == "__main__":
    main()
