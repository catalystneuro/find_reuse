#!/usr/bin/env python3
"""
Run a review session over the dandisets whose primary paper a model picked.

One dandiset at a time: what DANDI says the data is, and every paper put forward
as the paper describing it. Each paper is called primary, not primary or unsure
on its own, since a dandiset can have several primary papers. A paper nobody
put forward is added by DOI, and comes in called primary.

Calls are written to primary_paper_confirmation/confirmed_primary_papers.json
as they are made. That file is what rediscovery reads, so it belongs in version
control: a dandiset with no paper called primary is searched from nothing.

Usage:
    python -m src.review.run_primary_review
"""

from __future__ import annotations

import argparse
import json
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import requests

from src.review import paper_metadata
from src.review.primary_papers import CALLS, CANDIDATES_FILE, CONFIRMED_FILE
from src.review.run_review import PALETTE, PAPER_CACHE, paper_text, text_page

CSS = PALETTE + """
  *{box-sizing:border-box}
  html,body{height:100%}
  /* One dandiset fills the viewport. Only its panels scroll, so the navigation
     and the add box stay in the same place on every dandiset. */
  body{margin:0;background:var(--ground);color:var(--ink);font-family:var(--sans);
       font-size:15px;line-height:1.5;-webkit-font-smoothing:antialiased;
       overflow:hidden;display:flex;flex-direction:column}
  a{color:var(--accent)}
  .toolbar{flex:0 0 auto;display:flex;flex-wrap:wrap;align-items:center;gap:12px;
           padding:10px clamp(12px,2vw,26px);background:var(--surface);
           border-bottom:1px solid var(--line)}
  .toolbar h1{font-size:15px;margin:0;font-weight:650}
  .filters{display:flex;gap:6px}
  .spacer{flex:1}
  .readout{font-family:var(--mono);font-size:12.5px;color:var(--muted);
           font-variant-numeric:tabular-nums;white-space:nowrap}
  .btn{font:inherit;font-size:12.5px;padding:6px 12px;border-radius:999px;
       cursor:pointer;border:1px solid var(--line-strong);background:var(--surface);
       color:var(--muted)}
  .btn:hover{border-color:var(--accent);color:var(--ink)}
  .btn:disabled{opacity:.45;cursor:default}
  .btn[aria-pressed="true"]{background:var(--accent);border-color:var(--accent);
                            color:var(--on-accent)}
  .btn.primary[aria-pressed="true"]{background:var(--ok);border-color:var(--ok)}
  .btn.not_primary[aria-pressed="true"]{background:var(--bad);border-color:var(--bad)}
  .btn.unsure[aria-pressed="true"]{background:var(--warn);border-color:var(--warn)}
  .btn:focus-visible,a:focus-visible,textarea:focus-visible,
  input:focus-visible{outline:2px solid var(--accent);outline-offset:2px}

  .card{flex:1 1 auto;min-height:0;display:grid;gap:16px;
        grid-template-columns:minmax(0,2fr) minmax(0,3fr);
        padding:18px clamp(12px,2vw,26px)}
  @media (max-width:900px){.card{grid-template-columns:1fr;overflow:auto}}
  .panel{min-height:0;overflow:auto;background:var(--surface);
         border:1px solid var(--line);border-radius:14px;padding:18px 20px}
  .panel.dataset{background:var(--accent-soft);
                 border-color:color-mix(in srgb,var(--accent) 24%,transparent)}
  .role{font-size:10.5px;letter-spacing:.1em;text-transform:uppercase;
        color:var(--muted);font-weight:660}
  a.dsid{font-family:var(--mono);font-size:clamp(24px,2.5vw,35px);font-weight:700;
         color:var(--accent);text-decoration:none;line-height:1.1}
  .dsname{font-size:17px;font-weight:620;margin:4px 0}
  .meta{font-size:13px;color:var(--muted)}
  .tags{display:flex;flex-wrap:wrap;gap:6px;margin:10px 0}
  .tag{font-size:12px;padding:2px 8px;border-radius:6px;background:var(--surface)}
  .description{font-family:var(--serif);font-size:14.5px;white-space:pre-wrap}
  .candidate{padding:14px 0}
  .candidate + .candidate{border-top:1px solid var(--line)}
  .title{font-size:16px;font-weight:620;line-height:1.3}
  .doi{font-family:var(--mono);font-size:12px}
  .mismatch{margin:8px 0;padding:6px 10px;border-radius:8px;
            background:var(--bad-soft);color:var(--bad);font-size:13px}
  .source{margin:8px 0 0;font-size:13px}
  .chip{font-family:var(--mono);font-size:10.5px;letter-spacing:.06em;
        text-transform:uppercase;font-weight:600;padding:2px 8px;border-radius:6px;
        background:var(--accent-soft);color:var(--accent)}
  .chip.llm_identified{background:var(--warn-soft);color:var(--warn)}
  .chip.mismatch{background:var(--bad);color:var(--surface)}
  .chip.added{background:var(--primary-soft);color:var(--primary)}
  blockquote{margin:6px 0;padding:4px 10px;border-left:3px solid var(--line-strong);
             font-family:var(--serif)}
  .reasoning{color:var(--muted)}
  .calls{display:flex;gap:6px;margin:10px 0 0}

  .footer{flex:0 0 auto;display:grid;gap:10px;grid-template-columns:1fr 1fr;
          padding:0 clamp(12px,2vw,26px) 16px}
  .add{display:flex;gap:6px}
  input,textarea{font:inherit;font-size:13.5px;padding:6px 10px;border-radius:8px;
                 border:1px solid var(--line-strong);background:var(--surface);
                 color:var(--ink)}
  .add input{flex:1}
  textarea{width:100%;height:2.6em;resize:vertical}
  .empty{margin:auto;color:var(--muted)}
"""

SCRIPT = """
const CALLS = %(calls)s;
const CARDS = %(cards)s;
let reviews = {};
let shown = 'todo';
// The dandisets being stepped through. Taken when the filter is chosen rather
// than on every call, so a dandiset answered under To do stays in front of the
// reviewer until they move on.
let list = [];
let index = 0;
// Narrowing by what a card holds, on top of whether it is reviewed.
const only = {direct: false, mismatch: false};
const NARROWS = {
  direct: card => card.candidates.some(c =>
    c.sources.some(s => s.kind === 'direct_primary')),
  mismatch: card => card.candidates.some(c =>
    'claimed_name' in c && !c.name_matches),
};
const addedTitles = {};

const esc = s => String(s ?? '').replace(/[&<>"']/g,
  c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const review = id => (reviews[id] ??= {calls: {}, note: ''});
const offered = card => new Set(card.candidates.map(c => c.doi.toLowerCase()));
const added = card => Object.keys(review(card.dandiset).calls)
  .filter(doi => !offered(card).has(doi.toLowerCase()));
const done = card => card.candidates.every(c => review(card.dandiset).calls[c.doi]);
const anyPrimary = card => Object.values(review(card.dandiset).calls)
  .includes('primary');

let timer;
function save(){
  clearTimeout(timer);
  timer = setTimeout(() => fetch('/save', {method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({dandisets: reviews})}), 400);
}

function callButtons(id, doi){
  const current = review(id).calls[doi];
  return `<div class="calls">${CALLS.map(call =>
    `<button class="btn ${call}" data-doi="${esc(doi)}" data-call="${call}"
       aria-pressed="${current === call}">${call.replace('_', ' ')}</button>`
    ).join('')}</div>`;
}

function source(card, candidate, s){
  if (s.kind === 'llm_identified') return `<div class="source">
    <span class="chip llm_identified">LLM-identified</span> confidence ${esc(s.confidence)}
    <div class="reasoning">${esc(s.reasoning)}</div></div>`;
  return `<div class="source">
    <span class="chip">direct pipeline primary</span>
    confidence ${esc(s.confidence)} &middot;
    <a href="/text?doi=${encodeURIComponent(candidate.doi)}&dandiset=${card.dandiset}"
       target="_blank" rel="noopener">fetched text</a>
    ${s.quotes.map(q => `<blockquote>${esc(q)}</blockquote>`).join('')}
    <div class="reasoning">${esc(s.reasoning)}</div></div>`;
}

function candidateBlock(card, c){
  const mismatch = 'claimed_name' in c && !c.name_matches
    ? `<div class="mismatch"><span class="chip mismatch">title mismatch</span>
         The LLM gave this DOI the title &ldquo;${esc(c.claimed_name)}&rdquo;${
         c.resolves ? ', but the DOI resolves to the paper above'
                    : ', and no registrar knows the DOI'}.</div>` : '';
  return `<div class="candidate">
    <div class="title">${esc(c.title || '(no title on record)')}</div>
    <div class="meta">${esc(c.citation)} &middot;
      <a class="doi" href="https://doi.org/${esc(c.doi)}" target="_blank"
         rel="noopener">${esc(c.doi)}</a></div>
    ${mismatch}
    ${c.sources.map(s => source(card, c, s)).join('')}
    ${callButtons(card.dandiset, c.doi)}
  </div>`;
}

function addedBlock(doi){
  const paper = addedTitles[doi.toLowerCase()];
  return `<div class="candidate">
    <div class="title">${esc(paper?.title || '')}</div>
    <div class="meta"><span class="chip added">added</span> ${esc(paper?.citation || '')}
      &middot; <a class="doi" href="https://doi.org/${esc(doi)}" target="_blank"
                  rel="noopener">${esc(doi)}</a></div>
    <div class="calls"><button class="btn" data-remove="${esc(doi)}">remove</button></div>
  </div>`;
}

function cardBlock(card){
  const scholar = 'https://scholar.google.com/scholar?q=' +
    encodeURIComponent(`"${card.dandiset_name}" ${card.contact_person.split(',')[0]}`);
  const tags = [...card.species, ...card.approaches, ...card.techniques];
  return `<section class="panel dataset">
      <div class="role">Dandiset</div>
      <a class="dsid" href="${esc(card.dandiset_url)}" target="_blank"
         rel="noopener">${card.dandiset}</a>
      <div class="dsname">${esc(card.dandiset_name)}</div>
      <div class="meta">${[esc(card.contact_person), `created ${esc(card.created)}`,
        `<a href="${scholar}" target="_blank" rel="noopener">search Scholar</a>`]
        .filter(Boolean).join(' &middot; ')}</div>
      <div class="tags">${tags.map(t => `<span class="tag">${esc(t)}</span>`).join('')}</div>
      <div class="description">${esc(card.description)}</div>
    </section>
    <section class="panel">
      <div class="role">Candidate primary papers</div>
      ${card.candidates.map(c => candidateBlock(card, c)).join('')}
      ${added(card).map(addedBlock).join('')}
    </section>`;
}

function render(){
  const card = list[index];
  document.getElementById('progress').textContent =
    `${CARDS.filter(done).length} of ${CARDS.length} reviewed`;
  document.getElementById('position').textContent =
    list.length ? `Dandiset ${index + 1} of ${list.length}` : 'Nothing here';
  document.getElementById('prev').disabled = index === 0;
  document.getElementById('next').disabled = index >= list.length - 1;
  document.querySelectorAll('[data-show]').forEach(b =>
    b.setAttribute('aria-pressed', b.dataset.show === shown));
  document.querySelectorAll('[data-only]').forEach(b =>
    b.setAttribute('aria-pressed', only[b.dataset.only]));
  document.querySelector('.card').innerHTML =
    card ? cardBlock(card) : '<p class="empty">No dandisets under this filter.</p>';
  document.querySelector('.footer').hidden = !card;
  if (card){
    document.getElementById('note').value = review(card.dandiset).note;
    document.getElementById('add').value = '';
  }
}

function show(filter){
  shown = filter;
  list = CARDS.filter(card =>
    (shown === 'all' || (shown === 'done') === done(card)) &&
    Object.keys(only).every(key => !only[key] || NARROWS[key](card)));
  index = 0;
  render();
}

function go(next){
  index = Math.min(Math.max(next, 0), Math.max(list.length - 1, 0));
  render();
}

// Answering every paper advances, once one of them is the dandiset's primary
// paper. With none called primary the dandiset stays, so a paper can be added,
// and Next is how "no primary paper" is left behind.
function mark(doi, call){
  const card = list[index];
  const calls = review(card.dandiset).calls;
  if (calls[doi] === call) delete calls[doi]; else calls[doi] = call;
  save();
  if (done(card) && anyPrimary(card) && index < list.length - 1) index++;
  render();
}

async function describeAdded(doi){
  const response = await fetch('/paper?doi=' + encodeURIComponent(doi));
  if (response.ok) addedTitles[doi.toLowerCase()] = await response.json();
}

async function addPaper(){
  const card = list[index];
  const doi = document.getElementById('add').value.trim()
    .replace(/^https?:\\/\\/(dx\\.)?doi\\.org\\//, '');
  if (!doi) return;
  review(card.dandiset).calls[doi] = 'primary';
  save();
  await describeAdded(doi);
  render();
}

document.addEventListener('click', event => {
  const b = event.target.closest('button');
  if (!b) return;
  if (b.dataset.show) show(b.dataset.show);
  else if (b.dataset.only){ only[b.dataset.only] = !only[b.dataset.only]; show(shown); }
  else if (b.id === 'prev') go(index - 1);
  else if (b.id === 'next') go(index + 1);
  else if (b.id === 'add-button') addPaper();
  else if (b.dataset.call) mark(b.dataset.doi, b.dataset.call);
  else if (b.dataset.remove){
    delete review(list[index].dandiset).calls[b.dataset.remove];
    save();
    render();
  }
});
document.getElementById('add').addEventListener('keydown', event => {
  if (event.key === 'Enter') addPaper();
});
document.getElementById('note').addEventListener('input', event => {
  review(list[index].dandiset).note = event.target.value;
  save();
});

fetch('/load').then(r => r.json()).then(async saved => {
  reviews = saved.dandisets;
  await Promise.all(CARDS.flatMap(card => added(card).map(describeAdded)));
  show('todo');
});
"""


def embed(value) -> str:
    """A value as a script literal that no string inside it can close the script from."""
    return json.dumps(value).replace('</', '<\\/')


def narrow_counts(cards: list[dict]) -> dict[str, int]:
    """
    How many cards each narrowing filter keeps: those with a direct-pathway
    PRIMARY paper, and those whose model pick resolves to some other title.
    """
    return {
        'direct': sum(any(source['kind'] == 'direct_primary'
                          for candidate in card['candidates']
                          for source in candidate['sources']) for card in cards),
        'mismatch': sum(any('claimed_name' in candidate and not candidate['name_matches']
                            for candidate in card['candidates']) for card in cards),
    }


def build(cards: list[dict]) -> str:
    """The review page, carrying every card."""
    counts = narrow_counts(cards)
    return f"""<!doctype html>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Primary paper review</title>
<style>{CSS}</style>
<div class="toolbar">
  <h1>Primary paper review</h1>
  <div class="filters">
    <button class="btn" data-show="todo">To do</button>
    <button class="btn" data-show="done">Done</button>
    <button class="btn" data-show="all">All</button>
  </div>
  <div class="filters">
    <button class="btn" data-only="direct">Direct pipeline primary ({counts['direct']})</button>
    <button class="btn" data-only="mismatch">Title mismatch ({counts['mismatch']})</button>
  </div>
  <button class="btn" id="prev">&larr; Prev</button>
  <button class="btn" id="next">Next &rarr;</button>
  <span class="readout" id="position"></span>
  <span class="spacer"></span>
  <span class="readout" id="progress"></span>
</div>
<main class="card"></main>
<div class="footer">
  <div class="add"><input id="add" placeholder="Add a primary paper by DOI">
    <button class="btn" id="add-button">add</button></div>
  <textarea id="note" placeholder="Note"></textarea>
</div>
<script>{SCRIPT % {'calls': embed(list(CALLS)), 'cards': embed(cards)}}</script>
"""


def quotes_by_paper(cards: list[dict]) -> dict[tuple[str, str], list[str]]:
    """The passages to mark in a paper's text, for each dandiset it was put forward for."""
    return {(candidate['doi'], card['dandiset']): source['quotes']
            for card in cards for candidate in card['candidates']
            for source in candidate['sources'] if source['kind'] == 'direct_primary'}


def reviewed_only(dandisets: dict) -> dict:
    """The dandisets a reviewer has said something about, notes left out where empty."""
    kept = {}
    for dandiset, review in sorted(dandisets.items()):
        if not review['calls'] and not review.get('note'):
            continue
        kept[dandiset] = {'calls': review['calls']}
        if review.get('note'):
            kept[dandiset]['note'] = review['note']
    return kept


def make_handler(page: str, save_path: Path, paper_cache: Path = PAPER_CACHE,
                 quotes: dict | None = None):
    """A request handler bound to the review page and the file its calls go to."""
    quotes = quotes or {}

    class PrimaryReviewHandler(BaseHTTPRequestHandler):
        def _send(self, status: int, body: bytes, content_type: str):
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_json(self, status: int, body: dict):
            self._send(status, json.dumps(body).encode(), 'application/json')

        def do_GET(self):
            url = urlparse(self.path)
            query = parse_qs(url.query)
            if url.path == '/':
                self._send(200, page.encode(), 'text/html; charset=utf-8')
            elif url.path == '/load':
                saved = (json.loads(save_path.read_text()) if save_path.exists()
                         else {'dandisets': {}})
                self._send_json(200, saved)
            elif url.path == '/text':
                doi = (query.get('doi') or [''])[0]
                dandiset = (query.get('dandiset') or [''])[0]
                text, source = paper_text(paper_cache, doi)
                body = text_page(doi, text, source, quotes.get((doi, dandiset), []))
                self._send(200, body.encode(), 'text/html; charset=utf-8')
            elif url.path == '/paper':
                doi = (query.get('doi') or [''])[0]
                session = requests.Session()
                session.headers['User-Agent'] = paper_metadata.USER_AGENT
                paper = paper_metadata.fetch(session, doi)
                if paper is None:
                    self._send_json(404, {'error': f'No registrar knows {doi}.'})
                else:
                    self._send_json(200, {'title': paper['title'],
                                          'citation': paper_metadata.citation(paper)})
            else:
                self._send(404, b'not found', 'text/plain')

        def do_POST(self):
            if self.path != '/save':
                self._send(404, b'not found', 'text/plain')
                return
            length = int(self.headers.get('Content-Length', 0))
            incoming = json.loads(self.rfile.read(length))
            save_path.parent.mkdir(parents=True, exist_ok=True)
            save_path.write_text(json.dumps(
                {'dandisets': reviewed_only(incoming['dandisets'])},
                indent=2, ensure_ascii=False) + '\n')
            self._send(200, b'{"ok":true}', 'application/json')

        def log_message(self, *args):
            """Quiet: autosave would otherwise print a line every few seconds."""

    return PrimaryReviewHandler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidates', type=Path, default=CANDIDATES_FILE)
    parser.add_argument('--paper-cache', type=Path, default=PAPER_CACHE,
                        help='Fetched paper text, served for papers behind a paywall.')
    parser.add_argument('--port', type=int, default=8001)
    args = parser.parse_args()

    cards = json.loads(args.candidates.read_text())['dandisets']
    handler = make_handler(build(cards), CONFIRMED_FILE, args.paper_cache,
                           quotes_by_paper(cards))
    server = ThreadingHTTPServer(('127.0.0.1', args.port), handler)
    url = f'http://127.0.0.1:{server.server_address[1]}/'
    print(f'{len(cards)} dandisets; calls go to {CONFIRMED_FILE}')
    print(f'Serving {url} — Ctrl-C to stop')
    webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\nStopped.')
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
