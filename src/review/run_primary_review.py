#!/usr/bin/env python3
"""
Run a review session over the dandisets whose primary paper a model picked.

One card per dandiset: what DANDI says the data is, and every paper put forward
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
  body{margin:0;background:var(--ground);color:var(--ink);font-family:var(--sans);
       font-size:15px;line-height:1.5;-webkit-font-smoothing:antialiased}
  a{color:var(--accent)}
  .toolbar{position:sticky;top:0;z-index:1;display:flex;flex-wrap:wrap;
           align-items:center;gap:12px;padding:10px clamp(12px,2vw,26px);
           background:var(--surface);border-bottom:1px solid var(--line)}
  .toolbar h1{font-size:15px;margin:0;font-weight:650}
  .spacer{flex:1}
  .readout{font-family:var(--mono);font-size:12.5px;color:var(--muted);
           font-variant-numeric:tabular-nums}
  .btn{font:inherit;font-size:12.5px;padding:5px 12px;border-radius:999px;
       cursor:pointer;border:1px solid var(--line-strong);background:var(--surface);
       color:var(--muted)}
  .btn:hover{border-color:var(--accent);color:var(--ink)}
  .btn[aria-pressed="true"]{background:var(--accent);border-color:var(--accent);
                            color:var(--on-accent)}
  .btn.primary[aria-pressed="true"]{background:var(--ok);border-color:var(--ok)}
  .btn.not_primary[aria-pressed="true"]{background:var(--bad);border-color:var(--bad)}
  .btn.unsure[aria-pressed="true"]{background:var(--warn);border-color:var(--warn)}
  main{max-width:1000px;margin:0 auto;padding:18px clamp(12px,2vw,26px) 60px;
       display:flex;flex-direction:column;gap:18px}
  .card{background:var(--surface);border:1px solid var(--line);border-radius:12px;
        padding:16px 18px}
  .card.done{border-left:4px solid var(--ok)}
  .card h2{font-size:17px;margin:0 0 4px}
  .meta{font-size:13px;color:var(--muted)}
  .tags{display:flex;flex-wrap:wrap;gap:6px;margin:8px 0}
  .tag{font-size:12px;padding:2px 8px;border-radius:6px;background:var(--raise)}
  details.description{margin:8px 0;font-family:var(--serif);font-size:14.5px}
  details.description summary{cursor:pointer;font-family:var(--sans);font-size:13px;
                              color:var(--muted)}
  .candidate{border-top:1px solid var(--line);padding:12px 0 4px}
  .title{font-weight:600}
  .doi{font-family:var(--mono);font-size:12.5px}
  .mismatch{margin:6px 0;padding:6px 10px;border-radius:8px;
            background:var(--bad-soft);color:var(--bad);font-size:13px}
  .source{margin:6px 0 0 0;font-size:13px}
  .chip{font-family:var(--mono);font-size:10.5px;letter-spacing:.06em;
        text-transform:uppercase;font-weight:600;padding:2px 8px;border-radius:6px;
        background:var(--accent-soft);color:var(--accent)}
  .chip.llm_identified{background:var(--warn-soft);color:var(--warn)}
  .chip.added{background:var(--primary-soft);color:var(--primary)}
  blockquote{margin:6px 0;padding:4px 10px;border-left:3px solid var(--line-strong);
             font-family:var(--serif)}
  .reasoning{color:var(--muted)}
  .calls{display:flex;gap:6px;margin:8px 0}
  .add{display:flex;gap:6px;margin-top:12px}
  input,textarea{font:inherit;font-size:13.5px;padding:6px 10px;border-radius:8px;
                 border:1px solid var(--line-strong);background:var(--surface);
                 color:var(--ink)}
  .add input{flex:1}
  textarea{width:100%;margin-top:10px;min-height:2.4em;resize:vertical}
"""

SCRIPT = """
const CALLS = %(calls)s;
const CARDS = %(cards)s;
let reviews = {};
let shown = 'todo';

const esc = s => String(s ?? '').replace(/[&<>"']/g,
  c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const review = id => (reviews[id] ??= {calls: {}, note: ''});
const offered = card => new Set(card.candidates.map(c => c.doi.toLowerCase()));
const added = card => Object.keys(review(card.dandiset).calls)
  .filter(doi => !offered(card).has(doi.toLowerCase()));
const done = card => card.candidates.every(c => review(card.dandiset).calls[c.doi]);
const addedTitles = {};

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
    `<button class="btn ${call}" data-id="${id}" data-doi="${esc(doi)}"
       data-call="${call}" aria-pressed="${current === call}"
     >${call.replace('_', ' ')}</button>`).join('')}</div>`;
}

function source(card, candidate, s){
  if (s.kind === 'llm_identified') return `<div class="source">
    <span class="chip llm_identified">model's pick</span> confidence ${esc(s.confidence)}
    <div class="reasoning">${esc(s.reasoning)}</div></div>`;
  if (s.kind === 'direct_primary') return `<div class="source">
    <span class="chip">names this dandiset as its own deposit</span>
    confidence ${esc(s.confidence)} &middot;
    <a href="/text?doi=${encodeURIComponent(candidate.doi)}&dandiset=${card.dandiset}"
       target="_blank" rel="noopener">fetched text</a>
    ${s.quotes.map(q => `<blockquote>${esc(q)}</blockquote>`).join('')}
    <div class="reasoning">${esc(s.reasoning)}</div></div>`;
  return `<div class="source"><span class="chip">called primary by ${esc(s.reviewer)}</span>
    ${s.note ? `<div class="reasoning">${esc(s.note)}</div>` : ''}</div>`;
}

function candidateBlock(card, c){
  const mismatch = 'claimed_name' in c && !c.name_matches
    ? `<div class="mismatch">The model named this DOI
         &ldquo;${esc(c.claimed_name)}&rdquo;${c.resolves ? '' :
         ', and no registrar knows the DOI'}.</div>` : '';
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

function addedBlock(card, doi){
  const paper = addedTitles[doi.toLowerCase()];
  return `<div class="candidate">
    <div class="title">${esc(paper?.title || '')}</div>
    <div class="meta"><span class="chip added">added</span> ${esc(paper?.citation || '')}
      &middot; <a class="doi" href="https://doi.org/${esc(doi)}" target="_blank"
                  rel="noopener">${esc(doi)}</a>
      <button class="btn" data-remove="${esc(doi)}" data-id="${card.dandiset}"
       >remove</button></div>
  </div>`;
}

function cardBlock(card){
  const scholar = 'https://scholar.google.com/scholar?q=' +
    encodeURIComponent(`"${card.dandiset_name}" ${card.contact_person.split(',')[0]}`);
  const tags = [...card.species, ...card.approaches, ...card.techniques];
  return `<section class="card ${done(card) ? 'done' : ''}" id="d${card.dandiset}">
    <h2><a href="${esc(card.dandiset_url)}" target="_blank" rel="noopener"
         >${card.dandiset}</a> ${esc(card.dandiset_name)}</h2>
    <div class="meta">${[esc(card.contact_person), `created ${esc(card.created)}`,
      `<a href="${scholar}" target="_blank" rel="noopener">search Scholar</a>`]
      .filter(Boolean).join(' &middot; ')}</div>
    <div class="tags">${tags.map(t => `<span class="tag">${esc(t)}</span>`).join('')}</div>
    <details class="description" open><summary>Description</summary>
      ${esc(card.description)}</details>
    ${card.candidates.map(c => candidateBlock(card, c)).join('')}
    ${added(card).map(doi => addedBlock(card, doi)).join('')}
    <div class="add"><input placeholder="Add a primary paper by DOI"
      data-add="${card.dandiset}"><button class="btn" data-add-button="${card.dandiset}"
      >add</button></div>
    <textarea placeholder="Note" data-note="${card.dandiset}"
      >${esc(review(card.dandiset).note)}</textarea>
  </section>`;
}

function render(){
  const cards = CARDS.filter(card => shown === 'all' ||
                                     (shown === 'done') === done(card));
  document.querySelector('main').innerHTML = cards.map(cardBlock).join('');
  document.querySelector('.readout').textContent =
    `${CARDS.filter(done).length} of ${CARDS.length} reviewed`;
  document.querySelectorAll('[data-show]').forEach(b =>
    b.setAttribute('aria-pressed', b.dataset.show === shown));
}

function rerenderCard(id){
  const card = CARDS.find(c => c.dandiset === id);
  document.getElementById('d' + id).outerHTML = cardBlock(card);
  document.querySelector('.readout').textContent =
    `${CARDS.filter(done).length} of ${CARDS.length} reviewed`;
}

async function describeAdded(doi){
  const response = await fetch('/paper?doi=' + encodeURIComponent(doi));
  if (response.ok) addedTitles[doi.toLowerCase()] = await response.json();
}

async function addPaper(id){
  const input = document.querySelector(`[data-add="${id}"]`);
  const doi = input.value.trim().replace(/^https?:\\/\\/(dx\\.)?doi\\.org\\//, '');
  if (!doi) return;
  review(id).calls[doi] = 'primary';
  save();
  await describeAdded(doi);
  rerenderCard(id);
}

document.addEventListener('click', event => {
  const b = event.target.closest('button');
  if (!b) return;
  if (b.dataset.show){ shown = b.dataset.show; render(); return; }
  if (b.dataset.addButton){ addPaper(b.dataset.addButton); return; }
  const id = b.dataset.id;
  if (b.dataset.remove){ delete review(id).calls[b.dataset.remove]; }
  else if (b.dataset.call){
    const calls = review(id).calls;
    if (calls[b.dataset.doi] === b.dataset.call) delete calls[b.dataset.doi];
    else calls[b.dataset.doi] = b.dataset.call;
  }
  else return;
  save();
  rerenderCard(id);
});
document.addEventListener('keydown', event => {
  if (event.key === 'Enter' && event.target.dataset.add)
    addPaper(event.target.dataset.add);
});
document.addEventListener('input', event => {
  if (event.target.dataset.note){
    review(event.target.dataset.note).note = event.target.value;
    save();
  }
});

fetch('/load').then(r => r.json()).then(async saved => {
  reviews = saved.dandisets;
  await Promise.all(CARDS.flatMap(card => added(card).map(describeAdded)));
  render();
});
"""


def embed(value) -> str:
    """A value as a script literal that no string inside it can close the script from."""
    return json.dumps(value).replace('</', '<\\/')


def build(cards: list[dict]) -> str:
    """The review page, carrying every card."""
    return f"""<!doctype html>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Primary paper review</title>
<style>{CSS}</style>
<div class="toolbar">
  <h1>Primary paper review</h1>
  <button class="btn" data-show="todo">To do</button>
  <button class="btn" data-show="done">Done</button>
  <button class="btn" data-show="all">All</button>
  <span class="spacer"></span>
  <span class="readout"></span>
</div>
<main></main>
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
