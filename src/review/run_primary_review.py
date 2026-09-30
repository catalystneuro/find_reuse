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
from src.review.run_review import (PALETTE, PAPER_CACHE, paper_text, text_cache,
                                   text_page)

CSS = PALETTE + """
  *{box-sizing:border-box}
  html,body{height:100%}
  /* One dandiset fills the viewport. Only its panels scroll, so the toolbar and
     the add box stay in the same place on every dandiset. */
  body{margin:0;background:var(--ground);color:var(--ink);font-family:var(--sans);
       font-size:15px;line-height:1.5;-webkit-font-smoothing:antialiased;
       overflow:hidden;display:flex;flex-direction:column}
  a{color:var(--accent)}

  .toolbar{flex:0 0 auto;display:flex;flex-wrap:wrap;align-items:center;gap:12px;
           padding:10px clamp(12px,2vw,26px);background:var(--surface)}
  .toolbar:not(.controls){padding-bottom:8px}
  .toolbar.controls{border-bottom:1px solid var(--line);padding-top:0;gap:8px}
  .toolbar h1{font-size:15px;margin:0;font-weight:650}
  .filters{display:flex;align-items:center;gap:6px}
  .toolbar.controls .filters + .filters,
  .toolbar.controls .filters + .search{padding-left:9px;
                                       border-left:1px solid var(--line)}
  .filters .label{font-size:12px;color:var(--muted);margin-right:2px}
  .sep{width:1px;align-self:stretch;margin:0 3px;background:var(--line-strong)}
  .spacer{flex:1}
  .readout{font-family:var(--mono);font-size:12.5px;color:var(--muted);
           font-variant-numeric:tabular-nums;white-space:nowrap}
  .bar{flex:0 0 auto;width:150px;height:7px;border-radius:999px;overflow:hidden;
       background:var(--raise);border:1px solid var(--line)}
  .bar i{display:block;height:100%;width:0;border-radius:999px;
         background:var(--accent);transition:width .3s ease}
  .savestate{font-size:11.5px;min-width:11ch;color:var(--muted)}
  .savestate.ok{color:var(--ok)}
  .savestate.bad{color:var(--bad);font-weight:600}
  .btn{font:inherit;font-size:12.5px;padding:6px 12px;border-radius:999px;
       cursor:pointer;border:1px solid var(--line-strong);background:var(--surface);
       color:var(--muted)}
  .btn:hover{border-color:var(--accent);color:var(--ink)}
  .btn:disabled{opacity:.45;cursor:default}
  .btn:disabled:hover{border-color:var(--line-strong);color:var(--muted)}
  .btn[aria-pressed="true"]{background:var(--accent);border-color:var(--accent);
                            color:var(--on-accent)}
  .btn:focus-visible,a:focus-visible,textarea:focus-visible,
  input:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
  .search{font:inherit;font-size:12.5px;padding:6px 13px;border-radius:999px;
          border:1px solid var(--line-strong);background:var(--surface);
          color:var(--ink);flex:1 1 13ch;min-width:11ch;max-width:30ch}

  #card{flex:1 1 auto;min-height:0;display:flex;flex-direction:column;gap:14px;
        padding:18px clamp(12px,2vw,26px)}
  .sheet{flex:1 1 auto;min-height:0;display:grid;gap:16px;
         grid-template-columns:minmax(0,2fr) minmax(0,3fr)}
  @media (max-width:900px){.sheet{grid-template-columns:1fr;overflow:auto}}
  .panel{min-height:0;overflow:auto;background:var(--surface);
         border:1px solid var(--line);border-radius:14px;padding:18px 20px}
  .panel.dataset{background:var(--accent-soft);
                 border-color:color-mix(in srgb,var(--accent) 24%,transparent)}
  .role{font-size:10.5px;letter-spacing:.1em;text-transform:uppercase;
        color:var(--muted);font-weight:660}
  a.dsid{font-family:var(--mono);font-size:clamp(24px,2.5vw,35px);font-weight:700;
         color:var(--accent);text-decoration:none;line-height:1.1}
  a.dsid:hover{text-decoration:underline}
  .dsname{font-size:17px;font-weight:620;margin:4px 0}
  .meta{font-size:13px;color:var(--muted)}
  .tags{display:flex;flex-wrap:wrap;gap:6px;margin:10px 0}
  .tag{font-size:12px;padding:2px 8px;border-radius:6px;background:var(--surface)}
  .description{font-family:var(--serif);font-size:14.5px;white-space:pre-wrap}
  .candidate{padding:14px 0}
  .candidate + .candidate{border-top:1px solid var(--line)}
  .title{font-size:16px;font-weight:620;line-height:1.3}
  .links{display:flex;flex-wrap:wrap;align-items:baseline;gap:6px 12px;margin-top:3px}
  a.doi{font-family:var(--mono);font-size:12px;text-decoration:none;
        border-bottom:1px solid transparent;word-break:break-all}
  a.doi:hover{border-bottom-color:var(--accent)}
  /* The way into a paywalled paper: the text we fetched. */
  a.rawtext{font-size:11.5px;font-weight:600;text-decoration:none;
            padding:2px 9px;border-radius:999px;background:var(--accent-soft);
            white-space:nowrap}
  a.rawtext:hover{text-decoration:underline}
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

  /* One colour per call, the same on the worksheet, the overview and the filter
     chips, so an answer is recognised by its colour rather than read. */
  .calls{display:flex;flex-wrap:wrap;gap:8px;margin-top:10px}
  .calls button{font:inherit;font-size:14px;font-weight:560;padding:8px 22px;
                border-radius:10px;cursor:pointer;min-width:112px;border:1px solid;
                background:var(--surface);white-space:nowrap}
  .calls button:hover{border-color:currentColor}
  .calls button[aria-pressed="true"]{font-weight:760;
                                     box-shadow:inset 0 0 0 1px currentColor}
  .call.primary{color:var(--ok);
      border-color:color-mix(in srgb,var(--ok) 40%,transparent)}
  .call.not_primary{color:var(--bad);
      border-color:color-mix(in srgb,var(--bad) 40%,transparent)}
  .call.unsure{color:var(--warn);
      border-color:color-mix(in srgb,var(--warn) 40%,transparent)}
  .call[aria-pressed="true"].primary{background:var(--ok-soft)}
  .call[aria-pressed="true"].not_primary{background:var(--bad-soft)}
  .call[aria-pressed="true"].unsure{background:var(--warn-soft)}
  .btn.call[aria-pressed="true"]{color:var(--ink);font-weight:700}
  .btn.drop{color:var(--muted)}
  .btn.drop:hover{border-color:var(--bad);color:var(--bad)}

  .decide{flex:0 0 auto}
  .add{display:flex;gap:6px;align-items:center}
  .add .doiinput{flex:2}
  .add .addnote{flex:3}
  .addstate{font-size:12px;color:var(--muted)}
  .addstate.bad{color:var(--bad)}
  input,textarea{font:inherit;font-size:13.5px;padding:7px 11px;border-radius:9px;
                 border:1px solid var(--line-strong);background:var(--surface);
                 color:var(--ink)}
  textarea.note{display:block;width:100%;margin-top:8px;font-size:13px;
                line-height:1.45;resize:none;overflow:hidden}
  textarea.note::placeholder{color:var(--muted);opacity:.75}
  .empty{margin:auto;color:var(--muted);font-size:14px}

  .overview{flex:1 1 auto;min-height:0;overflow-y:auto;display:flex;
            flex-direction:column;gap:12px}
  .group{background:var(--surface);border:1px solid var(--line);border-radius:12px;
         padding:10px 16px}
  .group h3{display:flex;align-items:baseline;gap:10px;margin:0 0 4px;
            font-size:14.5px;cursor:pointer}
  .group h3:hover .groupname{color:var(--accent)}
  .groupid{font-family:var(--mono);color:var(--accent)}
  .groupname{flex:1}
  .tally{font-size:12px;color:var(--muted);font-weight:400;white-space:nowrap}
  .entry{display:flex;flex-wrap:wrap;align-items:center;gap:6px 16px;
         padding:8px 0;border-top:1px solid var(--line)}
  .entry .what{flex:1 1 320px;min-width:0;font-size:13.5px}
  .entry textarea.note{flex:1 1 100%;margin-top:0}
  .entry .calls{margin-top:0}
  .entry .calls button{font-size:12.5px;padding:5px 12px;min-width:0}
"""

SCRIPT = """
const CALLS = %(calls)s;
const CARDS = %(cards)s;
let reviews = {};
const addedTitles = {};

// Two ways of reading the same list. The worksheet asks about one dandiset at a
// time; the overview lays every dandiset out with its papers, which is how you
// go back over answers already given.
let view = 'worksheet';
let index = 0;

// A call is one click, and so is the wrong call. What a paper was called before
// is kept here with where it was called from, so both can be put back.
const undoStack = [];

// What the session is looking at. `filter` is whether a dandiset is answered,
// or which call one of its papers carries; `direct`, `title` and `added` narrow
// by what a card holds, either way round.
const controls = {filter: 'todo', direct: 'any', title: 'any', added: 'any',
                  search: ''};

const esc = s => String(s ?? '').replace(/[&<>"']/g,
  c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const callName = call => call.replace('_', ' ')
  .replace(/\\b\\w/g, letter => letter.toUpperCase());
const review = id => (reviews[id] ??= {calls: {}, notes: {}});
const noteOf = (card, doi) => review(card.dandiset).notes[doi] || '';
const callsOf = card => review(card.dandiset).calls;
const offered = card => new Set(card.candidates.map(c => c.doi.toLowerCase()));
const added = card => Object.keys(callsOf(card))
  .filter(doi => !offered(card).has(doi.toLowerCase()));
const done = card => card.candidates.every(c => callsOf(card)[c.doi]);
const hasDirect = card => card.candidates.some(c =>
  c.sources.some(s => s.kind === 'direct_primary'));
const mismatched = card => card.candidates.some(c =>
  'claimed_name' in c && !c.name_matches);
const cardByDandiset = id => CARDS.find(card => card.dandiset === id);

function setSaveState(text, cls){
  const el = document.getElementById('savestate');
  el.textContent = text;
  el.className = 'savestate ' + (cls || '');
}

// Calls land in confirmed_primary_papers.json as they are made. `manual` only
// changes what the indicator says afterwards.
let saveTimer = null;
function saveNow(manual){
  clearTimeout(saveTimer);
  setSaveState('Saving\\u2026', '');
  return fetch('/save', {method: 'POST', headers: {'Content-Type': 'application/json'},
                         body: JSON.stringify({dandisets: reviews})})
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

// Commas separate alternatives and spaces are all-of.
function matchesSearch(card){
  const query = controls.search.trim().toLowerCase();
  if (!query) return true;
  const text = [card.dandiset, card.dandiset_name, card.contact_person,
                ...card.candidates.flatMap(c => [c.doi, c.title, c.claimed_name]),
                ...added(card)].join(' ').toLowerCase();
  return query.split(',').some(alternative => {
    const terms = alternative.split(/\\s+/).filter(Boolean);
    return terms.length && terms.every(t => text.includes(t));
  });
}

const narrowedBy = (setting, holds) =>
  setting === 'any' || (setting === 'with') === holds;

// The work the session is looking at, before anything is asked about the calls.
// Progress is measured over this, so narrowing to one call does not read as
// having finished.
const inScope = () => CARDS.filter(card =>
  narrowedBy(controls.direct, hasDirect(card)) &&
  narrowedBy(controls.title, mismatched(card)) &&
  narrowedBy(controls.added, added(card).length > 0) &&
  matchesSearch(card));

function matchesFilter(card){
  if (controls.filter === 'all') return true;
  if (controls.filter === 'todo') return !done(card);
  if (controls.filter === 'done') return done(card);
  return Object.values(callsOf(card)).includes(controls.filter);
}

const visible = () => inScope().filter(matchesFilter);

// Every call goes through here, so every call can be undone. A paper added or
// removed takes its note with it, so `note` is given then and left out when
// only the call changes.
function record(card, doi, call, note){
  const {calls, notes} = review(card.dandiset);
  undoStack.push({dandiset: card.dandiset, doi, before: calls[doi],
                  beforeNote: notes[doi], view, index});
  if (call) calls[doi] = call; else delete calls[doi];
  if (note !== undefined){
    if (note.trim()) notes[doi] = note; else delete notes[doi];
  }
  save();
}

// Answering every paper advances, but only on the worksheet. Under Unreviewed
// the answered dandiset drops out of the list and the next one slides into its
// place, so holding position is the advance.
function mark(card, doi, call){
  record(card, doi, callsOf(card)[doi] === call ? '' : call);
  if (view === 'worksheet'){
    const after = visible();
    if (after[index] === card && done(card) && index < after.length - 1) index++;
  }
  render();
}

// Puts the last call back and returns to where it was made, since the dandiset
// may have left the list on being answered.
function undo(){
  const last = undoStack.pop();
  if (!last) return;
  const {calls, notes} = review(last.dandiset);
  if (last.before) calls[last.doi] = last.before; else delete calls[last.doi];
  if (last.beforeNote) notes[last.doi] = last.beforeNote; else delete notes[last.doi];
  save();
  setView(last.view);
  index = Math.min(last.index, Math.max(visible().length - 1, 0));
  render();
}

function setView(next){
  view = next;
  document.querySelectorAll('[data-view]').forEach(b =>
    b.setAttribute('aria-pressed', String(b.dataset.view === next)));
}

function go(next){
  index = Math.min(Math.max(next, 0), Math.max(visible().length - 1, 0));
  render();
}

function openCard(card){
  setView('worksheet');
  index = Math.max(visible().indexOf(card), 0);
  render();
}

function callButtons(card, doi){
  const current = callsOf(card)[doi];
  return `<div class="calls">${CALLS.map(call =>
    `<button class="call ${call}" data-dandiset="${card.dandiset}"
       data-doi="${esc(doi)}" data-call="${call}"
       aria-pressed="${current === call}">${callName(call)}</button>`).join('')}</div>`;
}

const rawText = (doi, dandiset) =>
  `<a class="rawtext" href="/text?doi=${encodeURIComponent(doi)}&dandiset=${dandiset}"
      target="_blank" rel="noopener">Raw Text</a>`;

function source(s){
  if (s.kind === 'llm_identified') return `<div class="source">
    <span class="chip llm_identified">LLM-identified</span> confidence ${esc(s.confidence)}
    <div class="reasoning">${esc(s.reasoning)}</div></div>`;
  return `<div class="source">
    <span class="chip">direct pipeline primary</span> confidence ${esc(s.confidence)}
    ${s.quotes.map(q => `<blockquote>${esc(q)}</blockquote>`).join('')}
    <div class="reasoning">${esc(s.reasoning)}</div></div>`;
}

function mismatchNote(c){
  if (!('claimed_name' in c) || c.name_matches) return '';
  return `<div class="mismatch"><span class="chip mismatch">title mismatch</span>
    The LLM gave this DOI the title &ldquo;${esc(c.claimed_name)}&rdquo;${
    c.resolves ? ', but the DOI resolves to the paper above'
               : ', and no registrar knows the DOI'}.</div>`;
}

function candidateBlock(card, c){
  return `<div class="candidate">
    <div class="title">${esc(c.title || '(no title on record)')}</div>
    <div class="links">
      ${c.citation ? `<span class="meta">${esc(c.citation)}</span>` : ''}
      <a class="doi" href="https://doi.org/${esc(c.doi)}" target="_blank"
         rel="noopener">${esc(c.doi)}</a>
      ${c.has_text ? rawText(c.doi, card.dandiset) : ''}
    </div>
    ${mismatchNote(c)}
    ${c.sources.map(source).join('')}
    ${callButtons(card, c.doi)}
    ${noteBox(card, c.doi)}
  </div>`;
}

// One note per paper, beside the call it explains.
const noteBox = (card, doi) =>
  `<textarea class="note" rows="1" data-dandiset="${card.dandiset}"
     data-doi="${esc(doi)}" placeholder="Note \\u2014 optional"
     >${esc(noteOf(card, doi))}</textarea>`;

// A paper the reviewer added comes in called primary; removing it is how that
// call is taken back.
function addedBlock(card, doi){
  const paper = addedTitles[doi.toLowerCase()];
  return `<div class="candidate">
    <div class="title">${esc(paper?.title || '(no title on record)')}</div>
    <div class="links"><span class="chip added">added</span>
      ${paper?.citation ? `<span class="meta">${esc(paper.citation)}</span>` : ''}
      <a class="doi" href="https://doi.org/${esc(doi)}" target="_blank"
         rel="noopener">${esc(doi)}</a>
      <button class="btn drop" data-dandiset="${card.dandiset}"
        data-remove="${esc(doi)}">\\u00d7 Remove Paper</button></div>
    ${noteBox(card, doi)}
  </div>`;
}

function datasetPanel(card){
  const scholar = 'https://scholar.google.com/scholar?q=' +
    encodeURIComponent(`"${card.dandiset_name}" ${card.contact_person.split(',')[0]}`);
  const tags = [...card.species, ...card.approaches, ...card.techniques];
  return `<section class="panel dataset">
      <div class="role">Dandiset</div>
      <a class="dsid" href="${esc(card.dandiset_url)}" target="_blank"
         rel="noopener">${card.dandiset}</a>
      <div class="dsname">${esc(card.dandiset_name)}</div>
      <div class="meta">${[esc(card.contact_person), `created ${esc(card.created)}`,
        `<a href="${scholar}" target="_blank" rel="noopener">Search Scholar</a>`]
        .filter(Boolean).join(' &middot; ')}</div>
      <div class="tags">${tags.map(t => `<span class="tag">${esc(t)}</span>`).join('')}</div>
      <div class="description">${esc(card.description)}</div>
    </section>`;
}

// An empty list says which control emptied it.
function emptyMessage(){
  if (!inScope().length)
    return controls.search.trim() ? 'Nothing matches that search.'
                                  : 'No dandisets under these filters.';
  if (controls.filter === 'todo') return 'Every dandiset has an answer.';
  if (controls.filter === 'done') return 'Nothing answered yet.';
  return `No paper called ${callName(controls.filter).toLowerCase()}.`;
}

// A note sizes to what it holds, so a paper grows only for the notes that have
// something to say.
function autosize(box){
  box.style.height = 'auto';
  box.style.height = box.scrollHeight + 'px';
}
const autosizeAll = root => root.querySelectorAll('textarea.note')
  .forEach(box => box.value && autosize(box));

function renderWorksheet(rows){
  const card = rows[index];
  const box = document.getElementById('card');
  if (!card){
    box.innerHTML = `<p class="empty">${emptyMessage()}</p>`;
    return;
  }
  box.innerHTML = `
    <div class="sheet">
      ${datasetPanel(card)}
      <section class="panel">
        <div class="role">Candidate Primary Papers</div>
        ${card.candidates.map(c => candidateBlock(card, c)).join('')}
        ${added(card).map(doi => addedBlock(card, doi)).join('')}
      </section>
    </div>
    <div class="decide">
      <form class="add" data-dandiset="${card.dandiset}">
        <input class="doiinput" autocomplete="off"
               placeholder="Add a primary paper by DOI" aria-label="DOI">
        <input class="addnote" autocomplete="off"
               placeholder="Why \\u2014 optional, goes with the paper" aria-label="Note">
        <button class="btn" type="submit">+ Add Paper</button>
        <span class="addstate" role="status"></span>
      </form>
    </div>`;
  autosizeAll(box);
}

// What a dandiset came to, which is what a second pass is reading for.
function tally(card){
  const calls = [...card.candidates.map(c => callsOf(card)[c.doi] || 'left'),
                 ...added(card).map(() => 'primary')];
  const counts = {};
  for (const call of calls) counts[call] = (counts[call] || 0) + 1;
  const named = CALLS.filter(call => counts[call])
    .map(call => `${counts[call]} ${callName(call).toLowerCase()}`);
  if (counts.left) named.push(`${counts.left} left`);
  return `${calls.length} paper${calls.length === 1 ? '' : 's'} \\u00b7 ${named.join(' \\u00b7 ')}`;
}

function entry(card, doi, title, isAdded){
  return `<div class="entry">
      <div class="what">${esc(title || '(no title on record)')}
        <div class="meta">${isAdded ? '<span class="chip added">added</span> ' : ''}${
          esc(doi)}</div></div>
      ${isAdded ? `<button class="btn drop" data-dandiset="${card.dandiset}"
          data-remove="${esc(doi)}">\\u00d7 Remove Paper</button>`
                : callButtons(card, doi)}
      ${noteBox(card, doi)}
    </div>`;
}

function renderOverview(rows){
  const box = document.getElementById('card');
  if (!rows.length){
    box.innerHTML = `<p class="empty">${emptyMessage()}</p>`;
    return;
  }
  // Calling a paper from the list redraws it, and a list that jumped back to
  // the top on every call could not be worked down.
  const held = box.querySelector('.overview');
  const scrollTop = held ? held.scrollTop : 0;
  box.innerHTML = `<div class="overview">${rows.map(card => `
    <section class="group">
      <h3 data-open="${card.dandiset}" title="Open on the worksheet">
        <span class="groupid">${card.dandiset}</span>
        <span class="groupname">${esc(card.dandiset_name)}</span>
        <span class="tally">${tally(card)}</span>
      </h3>
      ${card.candidates.map(c => entry(card, c.doi, c.title, false)).join('')}
      ${added(card).map(doi =>
        entry(card, doi, addedTitles[doi.toLowerCase()]?.title, true)).join('')}
    </section>`).join('')}</div>`;
  box.querySelector('.overview').scrollTop = scrollTop;
  autosizeAll(box);
}

function render(){
  const scoped = inScope();
  const rows = visible();
  index = Math.min(index, Math.max(rows.length - 1, 0));
  const answered = scoped.filter(done).length;
  document.getElementById('position').textContent = rows.length
    ? (view === 'overview' ? `${rows.length} dandisets`
                           : `Dandiset ${index + 1} of ${rows.length}`)
    : 'No dandisets';
  document.getElementById('progress').textContent =
    `${answered} of ${scoped.length} reviewed`;
  document.getElementById('bar').style.width =
    (scoped.length ? 100 * answered / scoped.length : 0) + '%%';
  document.querySelectorAll('.btn[data-control]').forEach(b =>
    b.setAttribute('aria-pressed',
                   String(controls[b.dataset.control] === b.dataset.value)));
  document.querySelectorAll('.step').forEach(b => b.hidden = view === 'overview');
  document.getElementById('undo').disabled = !undoStack.length;
  if (view === 'overview') renderOverview(rows); else renderWorksheet(rows);
}

async function describeAdded(doi){
  const response = await fetch('/paper?doi=' + encodeURIComponent(doi));
  if (response.ok) addedTitles[doi.toLowerCase()] = await response.json();
  return response.ok;
}

async function addPaper(form){
  const card = cardByDandiset(form.dataset.dandiset);
  const state = form.querySelector('.addstate');
  const doi = form.querySelector('.doiinput').value.trim()
    .replace(/^https?:\\/\\/(dx\\.)?doi\\.org\\//, '');
  if (!doi) return;
  if (offered(card).has(doi.toLowerCase()) || added(card).some(d =>
      d.toLowerCase() === doi.toLowerCase())){
    state.textContent = `${doi} is already on this dandiset.`;
    state.className = 'addstate bad';
    return;
  }
  state.textContent = 'Looking up\\u2026';
  state.className = 'addstate';
  if (!await describeAdded(doi)){
    state.textContent = `No registrar knows ${doi}; check it for a typo.`;
    state.className = 'addstate bad';
    return;
  }
  record(card, doi, 'primary', form.querySelector('.addnote').value);
  render();
}

document.addEventListener('click', e => {
  const button = e.target.closest('button');
  const heading = e.target.closest('h3[data-open]');
  if (heading){ openCard(cardByDandiset(heading.dataset.open)); return; }
  if (!button) return;
  if (button.dataset.control){
    controls[button.dataset.control] = button.dataset.value;
    index = 0;
    render();
  }
  else if (button.dataset.view){ setView(button.dataset.view); render(); }
  else if (button.id === 'prev') go(index - 1);
  else if (button.id === 'next') go(index + 1);
  else if (button.id === 'undo') undo();
  else if (button.id === 'save') saveNow(true);
  else if (button.dataset.call)
    mark(cardByDandiset(button.dataset.dandiset), button.dataset.doi,
         button.dataset.call);
  else if (button.dataset.remove){
    record(cardByDandiset(button.dataset.dandiset), button.dataset.remove, '', '');
    render();
  }
});

document.addEventListener('submit', e => {
  const form = e.target.closest('form.add');
  if (!form) return;
  e.preventDefault();
  addPaper(form);
});

// A note is held as it is typed, but the card is not redrawn: that would take
// the cursor out of the box mid-word.
document.addEventListener('input', e => {
  if (e.target.id === 'search'){
    controls.search = e.target.value;
    index = 0;
    render();
    return;
  }
  const box = e.target.closest('textarea.note');
  if (!box) return;
  const {notes} = review(box.dataset.dandiset);
  if (box.value.trim()) notes[box.dataset.doi] = box.value;
  else delete notes[box.dataset.doi];
  autosize(box);
  save();
});

document.addEventListener('keydown', e => {
  if (e.key !== 'z' || !(e.metaKey || e.ctrlKey)) return;
  if (e.target.closest('textarea, input')) return;
  e.preventDefault();
  undo();
});

fetch('/load')
  .then(r => r.json())
  .then(async saved => {
    reviews = saved.dandisets;
    for (const held of Object.values(reviews)) held.notes ??= {};
    await Promise.all(CARDS.flatMap(card => added(card).map(describeAdded)));
    render();
  })
  .catch(e => { setSaveState('Load failed \\u2014 ' + e.message, 'bad'); render(); });
"""


def embed(value) -> str:
    """A value as a script literal that no string inside it can close the script from."""
    return json.dumps(value).replace('</', '<\\/')


def narrow_counts(cards: list[dict]) -> dict[str, int]:
    """
    How many cards each narrowing filter keeps: those with a direct-pipeline
    PRIMARY paper, and those whose model pick resolves to some other title.
    """
    return {
        'direct': sum(any(source['kind'] == 'direct_primary'
                          for candidate in card['candidates']
                          for source in candidate['sources']) for card in cards),
        'mismatch': sum(any('claimed_name' in candidate and not candidate['name_matches']
                            for candidate in card['candidates']) for card in cards),
    }


def choices(control: str, label: str, options: list[tuple[str, str]]) -> str:
    """One filter group: its label, then a chip for each (value, name) it offers."""
    chips = ''.join(
        f'\n    <button class="btn" data-control="{control}" data-value="{value}" '
        f'aria-pressed="false">{name}</button>' for value, name in options)
    return (f'\n  <div class="filters" role="group" aria-label="{label}">'
            f'<span class="label">{label}</span>{chips}\n  </div>')


def build(cards: list[dict]) -> str:
    """The review page, carrying every card."""
    counts = narrow_counts(cards)
    total = len(cards)
    calls = choices('filter', 'Status', [
        ('all', 'All'), ('todo', 'Unreviewed'), ('done', 'Reviewed'),
        *[(call, call.replace('_', ' ').title()) for call in CALLS]])
    direct = choices('direct', 'Direct pipeline primary', [
        ('any', 'Any'), ('with', f'With ({counts["direct"]})'),
        ('without', f'Without ({total - counts["direct"]})')])
    title = choices('title', 'Title mismatch', [
        ('any', 'Any'), ('with', f'With ({counts["mismatch"]})'),
        ('without', f'Without ({total - counts["mismatch"]})')])
    added = choices('added', 'Added paper', [
        ('any', 'Any'), ('with', 'With'), ('without', 'Without')])
    return f"""<!doctype html>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Primary paper review &mdash; {total} dandisets</title>
<style>{CSS}</style>
<div class="toolbar">
  <h1>Primary paper review</h1>
  <div class="filters" role="group" aria-label="View">
    <button class="btn" data-view="worksheet" aria-pressed="true">Worksheet</button>
    <button class="btn" data-view="overview" aria-pressed="false">Overview</button>
  </div>
  <button class="btn step" id="prev">&larr; Prev</button>
  <button class="btn step" id="next">Next &rarr;</button>
  <span class="readout" id="position"></span>
  <span class="spacer"></span>
  <div class="bar"><i id="bar"></i></div>
  <span class="readout" id="progress"></span>
  <button class="btn" id="undo" disabled>&#8630; Undo</button>
  <button class="btn" id="save">Save</button>
  <span class="savestate" id="savestate"></span>
</div>
<div class="toolbar controls">{calls}{direct}{title}{added}
  <input class="search" id="search" type="search" autocomplete="off"
         placeholder="Search &mdash; commas for any">
</div>
<main id="card"></main>
<script>{SCRIPT % {'calls': embed(list(CALLS)), 'cards': embed(cards)}}</script>
"""


def attach_paper_texts(cards: list[dict], cache_dir: Path) -> None:
    """Say which candidate papers the fetched text is on hand for."""
    cache = text_cache(cache_dir)
    for card in cards:
        for candidate in card['candidates']:
            candidate['has_text'] = bool(cache.get(candidate['doi']))


def quotes_by_paper(cards: list[dict]) -> dict[tuple[str, str], list[str]]:
    """The passages to mark in a paper's text, for each dandiset it was put forward for."""
    return {(candidate['doi'], card['dandiset']): source['quotes']
            for card in cards for candidate in card['candidates']
            for source in candidate['sources'] if source['kind'] == 'direct_primary'}


def reviewed_only(dandisets: dict) -> dict:
    """The dandisets a reviewer has said something about, empty notes left out."""
    kept = {}
    for dandiset, review in sorted(dandisets.items()):
        notes = {doi: note for doi, note in review.get('notes', {}).items()
                 if note.strip()}
        if not review['calls'] and not notes:
            continue
        kept[dandiset] = {'calls': review['calls']}
        if notes:
            kept[dandiset]['notes'] = notes
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
    attach_paper_texts(cards, args.paper_cache)
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
