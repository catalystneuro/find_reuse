#!/usr/bin/env python3
"""
Run a review session over an assigned list of reuse candidates.

Serves a worksheet that asks one question about one (paper, dataset) pair at a
time. Answer, and the next pair comes up.

The assignment says whose session this is and which pathway it covers, so a
session cannot be pointed at the wrong answer file or offered the wrong labels.
The two pathways ask different questions: a paper found by naming the dandiset
in its own text might have deposited that dataset, so the direct queue offers
PRIMARY and shows no cited paper; a paper found by citing the dandiset's
publication might only be mentioning that work, so the indirect queue offers
MENTION and leads with the paper it cited.

Answers are written to reviews/<reviewer>.json as they are made. That file is
the durable artifact of a review round and belongs in version control; nothing
about the model, the prompt or the run that produced the classification goes
into it, because none of that changes what the right answer is.

Usage:
    python -m src.review.run_review \
        --assignment reviews/assignments/rly.indirect.json
"""

from __future__ import annotations

import argparse
import json
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from src.review.build_candidates import CANDIDATES_FILE
from src.review.reviewers import REVIEWS_DIR, answers_path

CSS = """
  :root{
    --ground:#F4F6F7; --surface:#FFFFFF; --raise:#EDF1F3;
    --line:#DCE3E7; --line-strong:#C3CED4;
    --ink:#0F171C; --muted:#5C6F7C;
    --accent:#16697A; --accent-soft:#E1EFF2; --on-accent:#FFFFFF;
    --ok:#2C7358; --ok-soft:#E0F0E8;
    --warn:#8A5E0C; --warn-soft:#F6EBD5;
    --bad:#A22F3D; --bad-soft:#F7E2E4;
    --mention:#1C5D9B; --mention-soft:#E1ECF7;
    --primary:#6D3D9B; --primary-soft:#EEE6F7;
    --sans:ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
    --serif:ui-serif,"Iowan Old Style",Georgia,"Times New Roman",serif;
    --mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
  }
  @media (prefers-color-scheme:dark){
    :root{
      --ground:#0F1417; --surface:#171F23; --raise:#1E282D;
      --line:#27343A; --line-strong:#374850;
      --ink:#E6EDF0; --muted:#93A6B0;
      --accent:#54B6C8; --accent-soft:#12313A; --on-accent:#08181C;
      --ok:#5FC095; --ok-soft:#133026;
      --warn:#D9A63C; --warn-soft:#33280F;
      --bad:#EF8390; --bad-soft:#3A1B1F;
      --mention:#6DB3F2; --mention-soft:#10263A;
      --primary:#BE96E8; --primary-soft:#251B36;
    }
  }

  *{box-sizing:border-box}
  html,body{height:100%}
  /* One pair fills the viewport. Only the evidence box scrolls, so the paper
     links and the decision buttons stay in the same place on every pair. */
  body{margin:0;background:var(--ground);color:var(--ink);font-family:var(--sans);
       font-size:15px;line-height:1.5;-webkit-font-smoothing:antialiased;
       overflow:hidden;display:flex;flex-direction:column}

  .toolbar{flex:0 0 auto;display:flex;flex-wrap:wrap;align-items:center;gap:12px;
           padding:10px clamp(12px,2vw,26px);background:var(--surface);
           border-bottom:1px solid var(--line)}
  .btn{font:inherit;font-size:12.5px;padding:6px 12px;border-radius:999px;cursor:pointer;
       border:1px solid var(--line-strong);background:var(--surface);color:var(--muted)}
  .btn:hover{border-color:var(--accent);color:var(--ink)}
  .btn[aria-pressed="true"]{background:var(--accent);border-color:var(--accent);
                            color:var(--on-accent)}
  .filters{display:flex;gap:6px}
  .btn:focus-visible,a:focus-visible,textarea:focus-visible{outline:2px solid var(--accent);
                                                            outline-offset:2px}
  .spacer{flex:1}
  .readout{font-family:var(--mono);font-size:12.5px;color:var(--muted);
           font-variant-numeric:tabular-nums;white-space:nowrap}
  .who{font-size:12.5px;color:var(--muted);white-space:nowrap}
  .who b{color:var(--ink);font-weight:600}
  .mode{font-family:var(--mono);font-size:10.5px;letter-spacing:.09em;
        text-transform:uppercase;font-weight:600;padding:3px 9px;border-radius:6px;
        background:var(--accent-soft);color:var(--accent);white-space:nowrap}
  .bar{flex:0 0 auto;width:150px;height:7px;border-radius:999px;overflow:hidden;
       background:var(--raise);border:1px solid var(--line)}
  .bar i{display:block;height:100%;width:0;border-radius:999px;
         background:var(--accent);transition:width .3s ease}
  .savestate{font-size:11.5px;min-width:11ch;color:var(--muted)}
  .savestate.ok{color:var(--ok)}
  .savestate.bad{color:var(--bad);font-weight:600}

  .card{flex:1 1 auto;min-height:0;display:flex;flex-direction:column;gap:16px;
        padding:20px clamp(12px,2vw,26px) 20px}

  /* The papers and the dataset are what the answer is read off, so they get the
     top of the screen at a size meant to be read rather than scanned. */
  .subject{flex:0 0 auto;display:grid;gap:14px;align-items:stretch;
           grid-template-columns:repeat(3,1fr)}
  .subject.direct{grid-template-columns:repeat(2,1fr)}
  @media (max-width:980px){
    .subject,.subject.direct{grid-template-columns:1fr}
  }
  .party{min-width:0;display:flex;flex-direction:column;gap:11px;
         background:var(--surface);border:1px solid var(--line);border-radius:14px;
         padding:20px 22px 21px}
  .party .role{font-size:10.5px;letter-spacing:.1em;text-transform:uppercase;
               color:var(--muted);font-weight:660}
  a.name{font-size:clamp(18px,1.75vw,26px);font-weight:640;line-height:1.26;
         letter-spacing:-.015em;color:var(--ink);text-decoration:none;text-wrap:pretty}
  a.name:hover{color:var(--accent)}
  .party .absent{font-size:14px;color:var(--muted);font-style:italic}
  a.doi{font-family:var(--mono);font-size:11.5px;color:var(--accent);text-decoration:none;
        border-bottom:1px solid transparent;word-break:break-all;margin-top:auto}
  a.doi:hover{border-bottom-color:var(--accent)}

  .party.dataset{background:var(--accent-soft);
                 border-color:color-mix(in srgb,var(--accent) 24%,transparent)}
  .party.dataset .role{color:var(--accent);opacity:.85}
  a.dsid{font-family:var(--mono);font-size:clamp(24px,2.5vw,35px);font-weight:700;
         letter-spacing:-.015em;color:var(--accent);text-decoration:none;line-height:1.1}
  a.dsid:hover{text-decoration:underline}
  .dsname{font-size:14px;color:var(--ink);opacity:.82;line-height:1.35;text-wrap:pretty}

  .reasoning{margin:0 0 16px;font-size:13.5px;color:var(--muted)}
  .reasoning b{color:var(--ink);font-weight:600}

  .evidence{flex:1 1 auto;min-height:0;overflow-y:auto;background:var(--surface);
            border:1px solid var(--line);border-radius:14px;padding:16px 20px}
  .evidence .inner{max-width:104ch;margin:0 auto}
  .evidence h4{margin:0 0 6px;font-size:10.5px;letter-spacing:.09em;
               text-transform:uppercase;color:var(--muted);font-weight:600}
  .legend{display:flex;flex-wrap:wrap;gap:5px 20px;margin:0 0 14px;font-size:12px;
          color:var(--muted)}
  .key{display:inline-flex;align-items:center;gap:7px;white-space:nowrap}
  figure.q{margin:0;padding:9px 0 9px 14px;border-left:3px solid var(--line-strong)}
  figure.q.exact{border-left-color:var(--ok)}
  figure.q.normalized,figure.q.case_insensitive,figure.q.spacing_insensitive,
  figure.q.punctuation_insensitive{border-left-color:var(--warn)}
  figure.q.not_found{border-left-color:var(--bad)}
  figure.q blockquote{margin:0;font-family:var(--serif);font-size:15px;line-height:1.55}
  figure.q figcaption{margin-top:7px}
  .tier{display:inline-flex;align-items:center;font-family:var(--mono);font-size:10.5px;
        letter-spacing:.05em;text-transform:uppercase;padding:2px 7px;border-radius:5px;
        font-weight:600}
  .tier.exact{background:var(--ok-soft);color:var(--ok)}
  .tier.normalized,.tier.case_insensitive,.tier.spacing_insensitive,
  .tier.punctuation_insensitive{background:var(--warn-soft);color:var(--warn)}
  .tier.not_found{background:var(--bad-soft);color:var(--bad)}

  .decide{flex:0 0 auto;display:flex;flex-direction:column;align-items:center;gap:11px}
  .calls{display:flex;flex-wrap:wrap;gap:10px;justify-content:center}
  .calls button{font:inherit;font-size:15px;font-weight:560;padding:13px 30px;
                border-radius:11px;cursor:pointer;min-width:132px;
                border:1px solid;background:var(--surface);white-space:nowrap}
  .calls button:hover{border-color:currentColor}
  /* An answer that is already recorded is filled and bold; the ring is drawn
     inside so that selecting one does not shift the row. */
  .calls button[aria-pressed="true"]{font-weight:760;
                                     box-shadow:inset 0 0 0 1px currentColor}
  .decide textarea{width:min(100%,880px);font:inherit;font-size:13px;line-height:1.45;
                   padding:9px 12px;border-radius:9px;border:1px solid var(--line-strong);
                   background:var(--surface);color:var(--ink);resize:none;min-height:54px}
  .decide textarea::placeholder{color:var(--muted);opacity:.75}
  /* One colour per label, carried from the start so an answer is recognised by
     its colour rather than read off its text. */
  .calls button.reuse{color:var(--ok);
      border-color:color-mix(in srgb,var(--ok) 40%,transparent)}
  .calls button.mention{color:var(--mention);
      border-color:color-mix(in srgb,var(--mention) 40%,transparent)}
  .calls button.primary{color:var(--primary);
      border-color:color-mix(in srgb,var(--primary) 40%,transparent)}
  .calls button.neither{color:var(--bad);
      border-color:color-mix(in srgb,var(--bad) 40%,transparent)}
  .calls button.unsure{color:var(--warn);
      border-color:color-mix(in srgb,var(--warn) 40%,transparent)}
  .calls button[aria-pressed="true"].reuse{background:var(--ok-soft)}
  .calls button[aria-pressed="true"].mention{background:var(--mention-soft)}
  .calls button[aria-pressed="true"].primary{background:var(--primary-soft)}
  .calls button[aria-pressed="true"].neither{background:var(--bad-soft)}
  .calls button[aria-pressed="true"].unsure{background:var(--warn-soft)}
  .empty{margin:auto;color:var(--muted);font-size:14px}
  @media (prefers-reduced-motion:reduce){*{transition:none!important;animation:none!important}}
"""

JS = """
let calls = {};
let notes = {};
let index = 0;
// Two ways of working: take the pairs still owed an answer, or look back over
// the ones already given one. A session opens on the work still to do.
let filter = 'todo';

const esc = s => String(s ?? '').replace(/[&<>"']/g, c =>
  ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const tierLabel = t => ({exact:'exact', normalized:'normalized',
  case_insensitive:'case only', spacing_insensitive:'spacing only',
  punctuation_insensitive:'punctuation only',
  not_found:'not in paper'}[t] || t);

const TIER_KEY = `<div class="legend">
    <span class="key"><span class="tier exact">exact</span>character for character</span>
    <span class="key"><span class="tier normalized">normalized</span>case, punctuation or
      spacing folded</span>
    <span class="key"><span class="tier not_found">not in paper</span>does not appear in
      the paper</span>
  </div>`;

function setSaveState(text, cls){
  const el = document.getElementById('savestate');
  el.textContent = text;
  el.className = 'savestate ' + (cls || '');
}

// Answers land in reviews/<reviewer>.json as they are made.
let saveTimer = null;

// `manual` only changes what the indicator says afterwards: a write the
// reviewer asked for reads back differently from one that happened on its own.
function saveNow(manual){
  clearTimeout(saveTimer);
  setSaveState('Saving\\u2026', '');
  return fetch('/save', {method: 'POST', headers: {'Content-Type': 'application/json'},
                         body: JSON.stringify({reviewer: REVIEWER, calls, notes})})
    .then(r => setSaveState(
      r.ok ? (manual ? 'Saved' : 'Auto-saved') : 'Save failed \\u2014 ' + r.status,
      r.ok ? 'ok' : 'bad'))
    .catch(e => setSaveState('Save failed \\u2014 ' + e.message, 'bad'));
}

// Debounced, so that typing a note is one write rather than one per keystroke.
function save(){
  clearTimeout(saveTimer);
  setSaveState('Saving\\u2026', '');
  saveTimer = setTimeout(() => saveNow(false), 500);
}

function visible(){
  if (filter === 'all') return ROWS;
  const answered = filter === 'done';
  return ROWS.filter(r => Boolean(calls[r.key]) === answered);
}

function paperPanel(role, doi, title){
  const body = doi
    ? `<a class="name" href="https://doi.org/${encodeURI(doi)}"
          target="_blank" rel="noopener">${esc(title || doi)}</a>
       <a class="doi" href="https://doi.org/${encodeURI(doi)}"
          target="_blank" rel="noopener">${esc(doi)}</a>`
    : `<span class="absent">Not recorded for this pair.</span>`;
  return `<div class="party"><span class="role">${esc(role)}</span>${body}</div>`;
}

function datasetPanel(r){
  return `<div class="party dataset">
      <span class="role">Cited Dataset</span>
      <a class="dsid" href="https://dandiarchive.org/dandiset/${esc(r.dandiset)}"
         target="_blank" rel="noopener">${esc(r.dandiset)}</a>
      <span class="dsname">${esc(r.dandiset_name)}</span>
    </div>`;
}

function quoteBlock(q){
  return `<figure class="q ${q.tier}">
      <blockquote>&ldquo;${esc(q.q)}&rdquo;</blockquote>
      <figcaption><span class="tier ${q.tier}">${tierLabel(q.tier)}</span></figcaption>
    </figure>`;
}

function callButtons(key){
  return LABELS.map(label => {
    const name = label[0].toUpperCase() + label.slice(1);
    return `<button class="${label}" data-v="${label}"
              aria-pressed="${calls[key] === label}">${name}</button>`;
  }).join('');
}

function render(){
  const rows = visible();
  index = Math.min(index, Math.max(rows.length - 1, 0));
  const done = ROWS.filter(x => calls[x.key]).length;
  document.getElementById('position').textContent =
    rows.length ? `Pair ${index + 1} of ${rows.length}` : 'No pairs';
  document.getElementById('progress').textContent = `${done} of ${ROWS.length} reviewed`;
  document.getElementById('bar').style.width = (100 * done / ROWS.length) + '%';

  const r = rows[index];
  if (!r){
    document.getElementById('card').innerHTML = `<p class="empty">${
      filter === 'done' ? 'Nothing answered yet.' : 'Every pair has an answer.'}</p>`;
    return;
  }

  const quotes = r.quotes.length ? r.quotes.map(quoteBlock).join('')
    : `<figure class="q"><blockquote><em>No quote returned.</em></blockquote></figure>`;
  // Where discovery held no pairing, the panel shows the dataset's own declared
  // paper, and says so rather than claiming this paper cited it.
  const citedRole = r.cited_role === 'Cited' ? 'Cited Paper' : 'Dataset Paper';

  document.getElementById('card').innerHTML = `
    <div class="subject ${MODE}">
      ${paperPanel('Citing Paper', r.doi, r.title)}
      ${MODE === 'indirect'
        ? paperPanel(citedRole, r.cited_doi, r.cited_title) : ''}
      ${datasetPanel(r)}
    </div>

    <div class="decide">
      <div class="calls">${callButtons(r.key)}</div>
      <textarea id="note" placeholder="Why \\u2014 optional"
        >${esc(notes[r.key] || '')}</textarea>
    </div>

    <div class="evidence">
      <div class="inner">
        <h4>Model Reasoning</h4>
        <p class="reasoning">${esc(r.reasoning)}</p>
        <h4>Quoted Evidence</h4>
        ${TIER_KEY}
        ${quotes}
      </div>
    </div>`;
}

function go(next){
  index = Math.min(Math.max(next, 0), Math.max(visible().length - 1, 0));
  render();
}

// Answering advances. Under a filter the answered pair drops out of the list and
// the next one slides into its place, so holding position is the advance.
function mark(value){
  const r = visible()[index];
  if (calls[r.key] === value) delete calls[r.key]; else calls[r.key] = value;
  save();
  const after = visible();
  if (after[index] === r && calls[r.key] && index < after.length - 1) index++;
  render();
}

document.getElementById('card').addEventListener('click', e => {
  const button = e.target.closest('button[data-v]');
  if (button) mark(button.dataset.v);
});

// A note is held as it is typed, but the card is not redrawn: that would take
// the cursor out of the box mid-word.
document.getElementById('card').addEventListener('input', e => {
  if (e.target.id !== 'note') return;
  const key = visible()[index].key;
  if (e.target.value.trim()) notes[key] = e.target.value; else delete notes[key];
  save();
});

document.getElementById('save').addEventListener('click', () => saveNow(true));
document.getElementById('prev').addEventListener('click', () => go(index - 1));
document.getElementById('next').addEventListener('click', () => go(index + 1));

document.querySelectorAll('.filters .btn').forEach(b => {
  b.addEventListener('click', () => {
    filter = b.dataset.f;
    index = 0;
    document.querySelectorAll('.filters .btn').forEach(o =>
      o.setAttribute('aria-pressed', String(o === b)));
    render();
  });
});

fetch('/load')
  .then(r => r.json())
  .then(data => { calls = data.calls || {}; notes = data.notes || {}; render(); })
  .catch(e => { setSaveState('Load failed \\u2014 ' + e.message, 'bad'); render(); });
"""


# The labels a reviewer picks from, which are the ones the classifier chose
# between in that pathway. Both sides naming a class is what a confusion matrix
# is built from, so offering a label the classifier could not have produced puts
# the answer off the matrix: only the direct pathway can say a paper is the one
# that deposited the dataset, and only the indirect pathway distinguishes a
# mention from a bare citation. 'unsure' is the reviewer's alone.
LABELS = {
    'direct': ['reuse', 'primary', 'neither', 'unsure'],
    'indirect': ['reuse', 'mention', 'neither', 'unsure'],
}

def build(rows: list[dict], reviewer: str, mode: str) -> str:
    """Render the worksheet around one reviewer's session in one pathway."""
    payload = json.dumps(rows, ensure_ascii=False).replace('</', r'<\/')
    n = len(rows)
    return f"""<title>DANDI {mode} reuse review &mdash; {n} pairs</title>
<style>{CSS}</style>

<div class="toolbar">
  <span class="who">Reviewing as <b>{reviewer}</b></span>
  <span class="mode">{mode}</span>
  <button class="btn" id="prev">&larr; Prev</button>
  <button class="btn" id="next">Next &rarr;</button>
  <div class="filters" role="group" aria-label="Show">
    <button class="btn" data-f="all" aria-pressed="false">All</button>
    <button class="btn" data-f="todo" aria-pressed="true">Unreviewed</button>
    <button class="btn" data-f="done" aria-pressed="false">Reviewed</button>
  </div>
  <span class="readout" id="position">Pair 1 of {n}</span>
  <div class="spacer"></div>
  <div class="bar"><i id="bar"></i></div>
  <span class="readout" id="progress">0 of {n} reviewed</span>
  <button class="btn" id="save">Save</button>
  <span class="savestate" id="savestate"></span>
</div>

<div class="card" id="card"></div>

<script>
const ROWS = {payload};
const REVIEWER = {json.dumps(reviewer)};
const MODE = {json.dumps(mode)};
const LABELS = {json.dumps(LABELS[mode])};
{JS}
</script>
"""


def make_handler(page: str, reviewer: str, save_path: Path):
    """A request handler bound to one reviewer's page and answer file."""

    class ReviewHandler(BaseHTTPRequestHandler):
        def _send(self, status: int, body: bytes, content_type: str):
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == '/':
                self._send(200, page.encode(), 'text/html; charset=utf-8')
            elif self.path == '/load':
                saved = (json.loads(save_path.read_text()) if save_path.exists()
                         else {'reviewer': reviewer, 'calls': {}, 'notes': {}})
                self._send(200, json.dumps(saved).encode(), 'application/json')
            else:
                self._send(404, b'not found', 'text/plain')

        def do_POST(self):
            if self.path != '/save':
                self._send(404, b'not found', 'text/plain')
                return
            length = int(self.headers.get('Content-Length', 0))
            incoming = json.loads(self.rfile.read(length))
            # Only the answers. Which model or prompt produced the
            # classification does not change what the right answer is, so it has
            # no place in the record of the answer.
            save_path.parent.mkdir(parents=True, exist_ok=True)
            save_path.write_text(json.dumps({
                'reviewer': reviewer,
                'calls': incoming.get('calls') or {},
                'notes': incoming.get('notes') or {},
            }, indent=2, ensure_ascii=False) + '\n')
            self._send(200, b'{"ok":true}', 'application/json')

        def log_message(self, *args):
            """Quiet: autosave would otherwise print a line every few seconds."""

    return ReviewHandler


def serve(rows: list[dict], reviewer: str, mode: str, port: int,
          reviews_dir: Path = REVIEWS_DIR, open_browser: bool = True) -> None:
    if not rows:
        raise SystemExit('That assignment holds no pairs; nothing to review.')
    save_path = answers_path(reviewer, reviews_dir)
    handler = make_handler(build(rows, reviewer, mode), reviewer, save_path)
    server = ThreadingHTTPServer(('127.0.0.1', port), handler)
    url = f'http://127.0.0.1:{server.server_address[1]}/'
    papers = len({r['doi'] for r in rows})
    print(f'{len(rows)} {mode} pairs across {papers} papers')
    print(f'Reviewing as {reviewer}; answers go to {save_path}')
    print(f'Serving {url} — Ctrl-C to stop')
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\nStopped.')
    finally:
        server.server_close()


def load_assignment(assignment_path: Path,
                    candidates_path: Path = CANDIDATES_FILE) -> tuple[list[dict], str, str]:
    """
    The pairs one assignment covers, with everything needed to judge them.

    An assignment names its pairs and leaves the records in the candidate list,
    so the two have to be read together. A key the candidate list does not hold
    means the assignment was dealt from a list that has since been rebuilt
    without it, which is a mismatch to fix rather than to review around.
    """
    assignment = json.loads(assignment_path.read_text())
    by_key = {p['key']: p for p in json.loads(candidates_path.read_text())['pairs']}
    missing = [k for k in assignment['keys'] if k not in by_key]
    if missing:
        raise SystemExit(
            f'{len(missing)} assigned pair(s) are not in {candidates_path}, '
            f'starting with {missing[0]!r}. Rebuild the candidate list, or '
            f'reassign against the one you have.')
    rows = [by_key[k] for k in assignment['keys']]
    rows.sort(key=lambda r: (r['doi'], r['dandiset']))
    return rows, assignment['reviewer'], assignment['pathway']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--assignment', required=True,
                        help='The assignment to work through; it names the '
                             'reviewer and the pathway.')
    parser.add_argument('--port', type=int, default=8000)
    args = parser.parse_args()

    rows, reviewer, pathway = load_assignment(Path(args.assignment))
    serve(rows, reviewer, pathway, args.port)


if __name__ == '__main__':
    main()
