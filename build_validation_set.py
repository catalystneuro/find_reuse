#!/usr/bin/env python3
"""
build_validation_set.py — Build a validation dashboard for manual review of LLM classifications.

Samples 100 paper-dandiset pairs (50 REUSE, 50 NOT-REUSE) and creates an HTML
dashboard for manual annotation. Fetches author lists from OpenAlex to support
same/different lab judgment.

Generates: output/validation_dashboard.html
           output/validation_samples.json (machine-readable sample data)
"""

import json
import os
import random
import time
from pathlib import Path

import requests


def load_samples():
    """Sample 50 REUSE and 50 NOT-REUSE pairs with text."""
    with open("output/all_classifications.json") as f:
        cls = json.load(f)

    def has_evidence(c):
        """Check if the sample has excerpts or a data availability section."""
        if c.get("context_excerpts"):
            return True
        # Check for data availability section in cached text
        doi = c["citing_doi"]
        text = load_paper_text(doi)
        if text and extract_data_availability(text):
            return True
        return False

    reuse = [c for c in cls["classifications"]
             if c["classification"] == "REUSE" and c.get("text_length", 0) > 200
             and has_evidence(c)]
    not_reuse = [c for c in cls["classifications"]
                 if c["classification"] in ("MENTION", "NEITHER") and c.get("text_length", 0) > 200
                 and has_evidence(c)]

    n_per_class = int(os.environ.get("VALIDATION_N", 50))
    random.seed(42)
    reuse_sample = random.sample(reuse, min(n_per_class, len(reuse)))
    not_reuse_sample = random.sample(not_reuse, min(n_per_class, len(not_reuse)))

    # Shuffle together
    samples = reuse_sample + not_reuse_sample
    random.shuffle(samples)
    return samples


def fetch_authors(samples):
    """Fetch citing and primary paper authors from OpenAlex."""
    session = requests.Session()
    session.headers.update({"User-Agent": "FindReuse/1.0"})

    # Collect all unique DOIs
    citing_dois = set(s["citing_doi"] for s in samples)
    primary_dois = set(s.get("cited_doi", "") for s in samples if s.get("cited_doi"))

    all_dois = citing_dois | primary_dois
    author_cache = {}

    print(f"Fetching authors for {len(all_dois)} DOIs...")
    for i, doi in enumerate(sorted(all_dois)):
        if not doi:
            continue
        try:
            resp = session.get(f"https://api.openalex.org/works/doi:{doi}", timeout=10)
            if resp.status_code == 200:
                w = resp.json()
                authors = [a["author"]["display_name"] for a in w.get("authorships", [])]
                author_cache[doi.lower()] = authors
        except Exception:
            pass
        if (i + 1) % 50 == 0:
            print(f"  {i + 1}/{len(all_dois)}")
        time.sleep(0.05)

    return author_cache


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


def extract_data_availability(text, max_chars=1000):
    """Extract data availability section from paper text."""
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


def load_paper_text(doi):
    """Load cached paper text."""
    safe_doi = doi.replace("/", "_")
    cache_path = Path(".paper_cache") / f"{safe_doi}.json"
    if cache_path.exists():
        with open(cache_path) as f:
            d = json.load(f)
        return d.get("text", "")
    return ""


def build_html(samples, author_cache, reuse_types):
    """Build HTML validation dashboard."""
    html_parts = ["""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>DANDI Reuse Validation Dashboard</title>
<style>
  body { font-family: -apple-system, sans-serif; max-width: 900px; margin: 0 auto; padding: 20px; background: #f5f5f5; }
  h1 { text-align: center; }
  .instructions { background: #e3f2fd; padding: 15px; border-radius: 8px; margin-bottom: 20px; }
  .sample { background: white; border: 1px solid #ddd; border-radius: 8px; padding: 20px; margin-bottom: 20px; }
  .sample-header { display: flex; justify-content: space-between; align-items: center; }
  .sample-num { font-size: 18px; font-weight: bold; color: #1565c0; }
  .pipeline-label { font-size: 12px; color: #999; display: none; }
  .field { margin: 8px 0; }
  .field-label { font-weight: bold; color: #555; font-size: 13px; }
  .field-value { margin-top: 2px; }
  .excerpt { background: #fafafa; border-left: 3px solid #1565c0; padding: 8px 12px; margin: 5px 0; font-size: 13px; line-height: 1.5; }
  .data-avail { background: #fff8e1; border-left: 3px solid #f9a825; padding: 8px 12px; margin: 5px 0; font-size: 13px; }
  .authors { font-size: 12px; color: #666; }
  .form-group { margin: 12px 0; padding: 10px; background: #f0f7ff; border-radius: 6px; }
  .form-group label { font-weight: bold; font-size: 13px; }
  .radio-group { display: flex; gap: 15px; margin-top: 5px; flex-wrap: wrap; }
  .radio-group label { font-weight: normal; cursor: pointer; }
  .reuse-fields { display: none; margin-top: 10px; padding: 10px; background: #e8f5e9; border-radius: 6px; }
  .notes { width: 100%; height: 40px; margin-top: 5px; }
  button.export { display: block; margin: 20px auto; padding: 12px 30px; font-size: 16px; background: #1565c0; color: white; border: none; border-radius: 6px; cursor: pointer; }
  button.export:hover { background: #0d47a1; }
  button.reveal { font-size: 11px; color: #999; border: 1px solid #ccc; background: white; padding: 2px 8px; border-radius: 4px; cursor: pointer; }
  .pipeline-info { display: none; margin-top: 8px; padding: 8px; background: #fff3e0; border-radius: 4px; font-size: 12px; }
</style>
</head>
<body>
<h1>DANDI Reuse Validation Dashboard</h1>
<div class="instructions">
<strong>Instructions:</strong> For each paper-dandiset pair below, review the excerpts and judge:
<ol>
<li><strong>REUSE or NOT REUSE</strong> — Did this paper download and analyze the actual data from this dandiset?</li>
<li>If REUSE: <strong>Reuse type</strong>, <strong>same/different lab</strong> correct?, <strong>source archive</strong> correct?</li>
</ol>
After labeling all 100, click "Export Results" at the bottom to download your annotations as JSON.
</div>
"""]

    for i, s in enumerate(samples):
        doi = s["citing_doi"]
        dandiset_id = s.get("dandiset_id", "")
        pipeline_cls = s["classification"]
        reuse_type = reuse_types.get((doi, dandiset_id), "")

        citing_authors = author_cache.get(doi.lower(), [])
        primary_doi = s.get("cited_doi", "")
        primary_authors = author_cache.get(primary_doi.lower(), []) if primary_doi else []

        excerpts = s.get("context_excerpts", [])
        excerpt_html = ""
        for ex in excerpts[:3]:
            if isinstance(ex, dict):
                text = ex.get("text", "")
                authors = ex.get("authors", [])
                year = ex.get("year", "")
                method = ex.get("method", "")
                offset = ex.get("highlight_offset")

                # Build citation identifier label
                if authors and year:
                    cite_label = f"{', '.join(authors)} {year}"
                elif method == "numbered_citation":
                    cite_label = "numbered reference"
                else:
                    cite_label = "citation"

                # Highlight the citation location in the text
                text_escaped = text.replace("<", "&lt;").replace(">", "&gt;")
                if offset is not None and 0 <= offset < len(text):
                    # Find a reasonable highlight span around the offset
                    hl_start = max(0, offset - 5)
                    hl_end = min(len(text), offset + 40)
                    pre = text[:hl_start].replace("<", "&lt;").replace(">", "&gt;")
                    hl = text[hl_start:hl_end].replace("<", "&lt;").replace(">", "&gt;")
                    post = text[hl_end:].replace("<", "&lt;").replace(">", "&gt;")
                    text_escaped = f'{pre}<mark>{hl}</mark>{post}'

                excerpt_html += (
                    f'<div class="excerpt">'
                    f'<span style="font-size:11px;color:#1565c0;font-weight:bold;">Citation: {cite_label}</span><br>'
                    f'{text_escaped}</div>\n'
                )
            else:
                text = str(ex).replace("<", "&lt;").replace(">", "&gt;")
                excerpt_html += f'<div class="excerpt">{text}</div>\n'
        if not excerpt_html:
            excerpt_html = '<div class="excerpt"><em>No excerpts available</em></div>'

        # Data availability
        paper_text = load_paper_text(doi)
        data_avail = extract_data_availability(paper_text)
        data_avail_html = ""
        if data_avail:
            data_avail = data_avail.replace("<", "&lt;").replace(">", "&gt;")
            data_avail_html = f'<div class="field"><span class="field-label">Data availability section:</span><div class="data-avail">{data_avail}</div></div>'

        citing_authors_str = ", ".join(citing_authors[:10])
        if len(citing_authors) > 10:
            citing_authors_str += f" ... (+{len(citing_authors)-10} more)"
        primary_authors_str = ", ".join(primary_authors[:10])
        if len(primary_authors) > 10:
            primary_authors_str += f" ... (+{len(primary_authors)-10} more)"

        html_parts.append(f"""
<div class="sample" id="sample-{i}">
  <div class="sample-header">
    <span class="sample-num">#{i+1} of 100</span>
    <button class="reveal" onclick="document.getElementById('info-{i}').style.display='block'; this.style.display='none';">Show pipeline classification</button>
  </div>

  <div class="field">
    <span class="field-label">Dandiset:</span>
    <span class="field-value"><a href="https://dandiarchive.org/dandiset/{dandiset_id}" target="_blank">DANDI:{dandiset_id}</a> — {s.get('dandiset_name', '')}</span>
  </div>

  <div class="field">
    <span class="field-label">Citing paper:</span>
    <span class="field-value"><a href="https://doi.org/{doi}" target="_blank">{doi}</a> — {s.get('citing_title', '')}</span>
    <div class="authors">Authors: {citing_authors_str or '<em>not available</em>'}</div>
  </div>

  <div class="field">
    <span class="field-label">Primary paper:</span>
    <span class="field-value"><a href="https://doi.org/{primary_doi}" target="_blank">{primary_doi}</a></span>
    <div class="authors">Authors: {primary_authors_str or '<em>not available</em>'}</div>
  </div>

  <div class="field">
    <span class="field-label">Citation context excerpts:</span>
    {excerpt_html}
  </div>
  {data_avail_html}

  <div class="pipeline-info" id="info-{i}">
    <strong>Pipeline classification:</strong> {pipeline_cls} (confidence: {s.get('confidence', '?')})<br>
    <strong>Same lab:</strong> {s.get('same_lab', '?')}<br>
    <strong>Source archive:</strong> {s.get('source_archive', '?')}<br>
    <strong>Reuse type:</strong> {reuse_type or 'not classified'}<br>
    <strong>Reasoning:</strong> {(s.get('reasoning', '') or '')[:300]}
  </div>

  <div class="form-group">
    <label>Your classification:</label>
    <div class="radio-group">
      <label><input type="radio" name="cls-{i}" value="REUSE" onchange="showReuseFields({i})"> REUSE</label>
      <label><input type="radio" name="cls-{i}" value="NOT_REUSE" onchange="hideReuseFields({i})"> NOT REUSE</label>
      <label><input type="radio" name="cls-{i}" value="UNSURE" onchange="hideReuseFields({i})"> UNSURE</label>
    </div>
  </div>

  <div class="reuse-fields" id="reuse-fields-{i}">
    <div class="form-group">
      <label>Reuse type:</label>
      <div class="radio-group">
        <label><input type="radio" name="type-{i}" value="TOOL_DEMO"> Tool demo</label>
        <label><input type="radio" name="type-{i}" value="NOVEL_ANALYSIS"> Novel analysis</label>
        <label><input type="radio" name="type-{i}" value="AGGREGATION"> Aggregation</label>
        <label><input type="radio" name="type-{i}" value="BENCHMARK"> Benchmark</label>
        <label><input type="radio" name="type-{i}" value="CONFIRMATORY"> Confirmatory</label>
        <label><input type="radio" name="type-{i}" value="SIMULATION"> Simulation</label>
        <label><input type="radio" name="type-{i}" value="ML_TRAINING"> ML training</label>
        <label><input type="radio" name="type-{i}" value="TEACHING"> Teaching</label>
      </div>
    </div>
    <div class="form-group">
      <label>Same lab as primary paper authors?</label>
      <div class="radio-group">
        <label><input type="radio" name="samelab-{i}" value="yes"> Yes</label>
        <label><input type="radio" name="samelab-{i}" value="no"> No</label>
        <label><input type="radio" name="samelab-{i}" value="unsure"> Can't tell</label>
      </div>
    </div>
    <div class="form-group">
      <label>Source archive:</label>
      <div class="radio-group">
        <label><input type="radio" name="archive-{i}" value="DANDI Archive"> DANDI</label>
        <label><input type="radio" name="archive-{i}" value="Allen Institute"> Allen Institute</label>
        <label><input type="radio" name="archive-{i}" value="IBL"> IBL</label>
        <label><input type="radio" name="archive-{i}" value="CRCNS"> CRCNS</label>
        <label><input type="radio" name="archive-{i}" value="Figshare"> Figshare</label>
        <label><input type="radio" name="archive-{i}" value="CELLxGENE"> CELLxGENE</label>
        <label><input type="radio" name="archive-{i}" value="other"> Other</label>
        <label><input type="radio" name="archive-{i}" value="unclear"> Unclear</label>
      </div>
    </div>
  </div>

  <div class="form-group">
    <label>Notes (optional):</label><br>
    <textarea class="notes" id="notes-{i}" placeholder="Any comments..."></textarea>
  </div>
</div>
""")

    # Export script
    html_parts.append(f"""
<button class="export" onclick="exportResults()">Export Results (JSON)</button>

<script>
const N = {len(samples)};
const sampleMeta = {json.dumps([{
    'citing_doi': s['citing_doi'],
    'dandiset_id': s.get('dandiset_id', ''),
    'pipeline_classification': s['classification'],
    'pipeline_confidence': s.get('confidence'),
    'pipeline_same_lab': s.get('same_lab'),
    'pipeline_source_archive': s.get('source_archive'),
    'pipeline_reuse_type': reuse_types.get((s['citing_doi'], s.get('dandiset_id', '')), ''),
} for s in samples])};

function showReuseFields(i) {{
  document.getElementById('reuse-fields-' + i).style.display = 'block';
}}
function hideReuseFields(i) {{
  document.getElementById('reuse-fields-' + i).style.display = 'none';
}}

function getRadioValue(name) {{
  const el = document.querySelector('input[name="' + name + '"]:checked');
  return el ? el.value : null;
}}

function exportResults() {{
  const results = [];
  let unlabeled = 0;
  for (let i = 0; i < N; i++) {{
    const cls = getRadioValue('cls-' + i);
    if (!cls) {{ unlabeled++; continue; }}
    const entry = {{
      ...sampleMeta[i],
      human_classification: cls,
      human_reuse_type: cls === 'REUSE' ? getRadioValue('type-' + i) : null,
      human_same_lab: cls === 'REUSE' ? getRadioValue('samelab-' + i) : null,
      human_source_archive: cls === 'REUSE' ? getRadioValue('archive-' + i) : null,
      notes: document.getElementById('notes-' + i).value || null,
    }};
    results.push(entry);
  }}
  if (unlabeled > 0) {{
    if (!confirm(unlabeled + ' samples not yet labeled. Export anyway?')) return;
  }}
  const blob = new Blob([JSON.stringify(results, null, 2)], {{type: 'application/json'}});
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = 'validation_results.json';
  a.click();
}}
</script>
</body>
</html>
""")

    return "\n".join(html_parts)


def main():
    print("Sampling 100 pairs...")
    samples = load_samples()
    print(f"  {sum(1 for s in samples if s['classification'] == 'REUSE')} REUSE, "
          f"{sum(1 for s in samples if s['classification'] != 'REUSE')} NOT-REUSE")

    print("Loading reuse types...")
    reuse_types = load_reuse_types()

    print("Fetching author lists from OpenAlex...")
    author_cache = fetch_authors(samples)
    print(f"  Got authors for {len(author_cache)} DOIs")

    print("Building HTML dashboard...")
    html = build_html(samples, author_cache, reuse_types)

    Path("output").mkdir(exist_ok=True)
    with open("output/validation_dashboard.html", "w") as f:
        f.write(html)
    print("Saved output/validation_dashboard.html")

    # Also save machine-readable sample data
    sample_data = []
    for s in samples:
        sample_data.append({
            "citing_doi": s["citing_doi"],
            "dandiset_id": s.get("dandiset_id", ""),
            "pipeline_classification": s["classification"],
            "pipeline_confidence": s.get("confidence"),
            "pipeline_same_lab": s.get("same_lab"),
            "pipeline_source_archive": s.get("source_archive"),
            "pipeline_reuse_type": reuse_types.get((s["citing_doi"], s.get("dandiset_id", "")), ""),
        })
    with open("output/validation_samples.json", "w") as f:
        json.dump(sample_data, f, indent=2)
    print("Saved output/validation_samples.json")


if __name__ == "__main__":
    main()
