#!/usr/bin/env python3
"""
Build the manual verification page for full-text REUSE classifications.

Emits a self-contained HTML worksheet: every REUSE claim with the passage the
model quoted, how well that quote matches the paper, and controls for recording
a human judgement and the reasoning behind it.

Reviewers work independently. Each sees only their own labels and notes, held in
this browser's localStorage under their name, so that agreement between two
reviewers measures two independent readings of the paper. Regenerating the page
does not discard work already done, and Save writes one reviewer's answers to a
file for combining with everyone else's once the round is finished.

Usage:
    python -m src.analysis.build_reuse_verification_page \
        -i output/fulltext_classifications.json -o /tmp/reuse_verify.html
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

CSS = """
  :root{
    --ground:#F4F6F7; --surface:#FFFFFF; --raise:#EDF1F3;
    --line:#DCE3E7; --line-strong:#C3CED4;
    --ink:#0F171C; --muted:#5C6F7C;
    --accent:#16697A; --accent-soft:#E1EFF2; --on-accent:#FFFFFF;
    --ok:#2C7358; --ok-soft:#E0F0E8;
    --warn:#8A5E0C; --warn-soft:#F6EBD5;
    --bad:#A22F3D; --bad-soft:#F7E2E4;
    --shadow:0 1px 2px rgba(15,23,28,.06),0 8px 24px -18px rgba(15,23,28,.35);
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
      --shadow:0 1px 2px rgba(0,0,0,.5),0 10px 28px -18px rgba(0,0,0,.9);
    }
  }
  :root[data-theme="light"]{
    --ground:#F4F6F7; --surface:#FFFFFF; --raise:#EDF1F3;
    --line:#DCE3E7; --line-strong:#C3CED4;
    --ink:#0F171C; --muted:#5C6F7C;
    --accent:#16697A; --accent-soft:#E1EFF2; --on-accent:#FFFFFF;
    --ok:#2C7358; --ok-soft:#E0F0E8;
    --warn:#8A5E0C; --warn-soft:#F6EBD5;
    --bad:#A22F3D; --bad-soft:#F7E2E4;
    --shadow:0 1px 2px rgba(15,23,28,.06),0 8px 24px -18px rgba(15,23,28,.35);
  }
  :root[data-theme="dark"]{
    --ground:#0F1417; --surface:#171F23; --raise:#1E282D;
    --line:#27343A; --line-strong:#374850;
    --ink:#E6EDF0; --muted:#93A6B0;
    --accent:#54B6C8; --accent-soft:#12313A; --on-accent:#08181C;
    --ok:#5FC095; --ok-soft:#133026;
    --warn:#D9A63C; --warn-soft:#33280F;
    --bad:#EF8390; --bad-soft:#3A1B1F;
    --shadow:0 1px 2px rgba(0,0,0,.5),0 10px 28px -18px rgba(0,0,0,.9);
  }

  *{box-sizing:border-box}
  body{margin:0;background:var(--ground);color:var(--ink);font-family:var(--sans);
       font-size:15px;line-height:1.5;-webkit-font-smoothing:antialiased}
  .wrap{max-width:none;margin:0;padding:36px clamp(16px,2.2vw,36px) 96px}

  header.page{display:flex;flex-direction:column;gap:10px;margin-bottom:28px}
  .eyebrow{font-family:var(--mono);font-size:11px;letter-spacing:.14em;
           text-transform:uppercase;color:var(--accent)}
  h1{margin:0;font-size:clamp(26px,3.4vw,38px);line-height:1.12;letter-spacing:-.02em;
     font-weight:660;text-wrap:balance}
  .lede{margin:0;max-width:66ch;color:var(--muted);font-size:15.5px}

  .stats{display:flex;flex-wrap:wrap;gap:10px;margin-top:6px}
  .stat{background:var(--surface);border:1px solid var(--line);border-radius:9px;
        padding:9px 13px;display:flex;align-items:baseline;gap:8px}
  .stat b{font-family:var(--mono);font-size:16px;font-variant-numeric:tabular-nums}
  .stat span{font-size:12.5px;color:var(--muted)}

  .toolbar{position:sticky;top:0;z-index:20;
           background:color-mix(in srgb,var(--ground) 92%,transparent);
           backdrop-filter:blur(8px);border-bottom:1px solid var(--line);
           padding:12px 0;margin:24px 0 0;display:flex;flex-wrap:wrap;gap:12px;
           align-items:center}
  .filters{display:flex;flex-wrap:wrap;gap:6px}
  .btn{font:inherit;font-size:12.5px;padding:6px 12px;border-radius:999px;cursor:pointer;
       border:1px solid var(--line-strong);background:var(--surface);color:var(--muted)}
  .btn:hover{border-color:var(--accent);color:var(--ink)}
  .btn[aria-pressed="true"]{background:var(--accent);border-color:var(--accent);
                            color:var(--on-accent)}
  .btn:focus-visible,a:focus-visible,textarea:focus-visible{outline:2px solid var(--accent);
                                                            outline-offset:2px}
  .spacer{flex:1}
  .progress{font-family:var(--mono);font-size:12.5px;color:var(--muted);
            font-variant-numeric:tabular-nums;white-space:nowrap}

  .tablewrap{overflow-x:auto;margin-top:20px;border:1px solid var(--line);
             border-radius:12px;background:var(--surface);box-shadow:var(--shadow)}
  table{border-collapse:collapse;width:100%;min-width:1080px}
  /* The wrapper scrolls horizontally, which makes overflow-y compute to auto and
     makes it the sticky scrollport. A non-zero `top` would shove the header down
     inside a container that never scrolls vertically, overlapping the first row. */
  thead th{position:sticky;top:0;z-index:10;background:var(--raise);
           font-size:11px;letter-spacing:.1em;text-transform:uppercase;color:var(--muted);
           text-align:left;padding:11px 14px;border-bottom:1px solid var(--line);
           font-weight:600}
  tbody td{padding:18px 14px;border-bottom:1px solid var(--line);vertical-align:top}
  /* The quote column takes whatever room is left once the others are laid out,
     so it is capped to keep the table close to a laptop's width. */
  tbody td.evidence-cell{max-width:620px}
  tbody tr:last-child td{border-bottom:none}
  tbody tr.flagged{background:var(--bad-soft)}
  thead th:last-child,tbody td:last-child{position:sticky;right:0;
      background:var(--surface);border-left:1px solid var(--line)}
  thead th:last-child{background:var(--raise);z-index:11}
  tbody tr.flagged td:last-child{background:var(--bad-soft)}
  tbody tr.done{opacity:.55}
  tbody tr.done:focus-within{opacity:1}

  .idx{font-family:var(--mono);font-size:12px;color:var(--muted);
       font-variant-numeric:tabular-nums;white-space:nowrap;
       padding-left:10px;padding-right:4px;text-align:right}
  .paper{max-width:28ch}
  .title{font-weight:560;line-height:1.35;margin:0 0 5px;text-wrap:pretty}
  a.doi{font-family:var(--mono);font-size:11.5px;color:var(--accent);
        text-decoration:none;border-bottom:1px solid transparent;word-break:break-all}
  a.doi:hover{border-bottom-color:var(--accent)}
  a.ds{font-family:var(--mono);font-size:12.5px;color:var(--accent);text-decoration:none;
       border-bottom:1px solid transparent;white-space:nowrap}
  a.ds:hover{border-bottom-color:var(--accent)}

  .meta{display:flex;flex-direction:column;gap:7px;min-width:120px}
  .chip{display:inline-flex;align-items:center;gap:5px;font-size:11.5px;
        padding:3px 8px;border-radius:6px;border:1px solid var(--line-strong);
        color:var(--muted);width:fit-content;white-space:nowrap}
  .chip.samelab{background:var(--warn-soft);color:var(--warn);border-color:transparent;
                font-weight:600}
  .chip.difflab{background:var(--ok-soft);color:var(--ok);border-color:transparent;
                font-weight:600}
  .chip.archive{font-family:var(--mono);font-size:11px}
  .chip.dandi{background:var(--accent-soft);color:var(--accent);border-color:transparent;
              font-weight:600;white-space:normal;max-width:22ch}
  .chip.both{background:var(--raise);color:var(--muted);border-style:dashed}
  .chip.neuro{background:var(--ok-soft);color:var(--ok);border-color:transparent;
              font-weight:660}
  .chip.notneuro{background:var(--bad-soft);color:var(--bad);border-color:transparent;
                 font-weight:660}
  .mods{display:flex;flex-wrap:wrap;gap:4px}
  .mod{font-size:10.5px;font-family:var(--mono);text-transform:uppercase;
       letter-spacing:.04em;padding:2px 6px;border-radius:4px;
       background:var(--raise);color:var(--muted);border:1px solid var(--line)}
  .mod.hosted{background:var(--ok-soft);color:var(--ok);border-color:transparent}
  .provenance{margin-top:4px;padding-top:12px;border-top:1px dashed var(--line-strong)}
  .provenance h4{margin:0 0 7px;font-size:10.5px;letter-spacing:.09em;
                 text-transform:uppercase;color:var(--muted);font-weight:600}
  .provenance .none{font-size:12.5px;color:var(--bad);font-style:italic}
  .chip.unknown{opacity:.7;font-style:italic}
  .conf{font-family:var(--mono);font-size:15px;font-variant-numeric:tabular-nums;
        font-weight:600;white-space:nowrap}
  .conf small{display:block;font-size:10px;font-weight:400;color:var(--muted);
              letter-spacing:.06em;text-transform:uppercase;margin-top:2px}

  .evidence{max-width:78ch;display:flex;flex-direction:column;gap:12px}
  figure.q{margin:0;padding:9px 0 9px 14px;border-left:3px solid var(--line-strong)}
  figure.q.exact{border-left-color:var(--ok)}
  figure.q.normalized,figure.q.case_insensitive,figure.q.spacing_insensitive{
      border-left-color:var(--warn)}
  figure.q.not_found{border-left-color:var(--bad)}
  figure.q blockquote{margin:0;font-family:var(--serif);font-size:15px;line-height:1.55}
  figure.q figcaption{margin-top:7px}
  .tier{display:inline-flex;align-items:center;font-family:var(--mono);font-size:10.5px;
        letter-spacing:.05em;text-transform:uppercase;padding:2px 7px;border-radius:5px;
        font-weight:600}
  .tier.exact{background:var(--ok-soft);color:var(--ok)}
  .tier.normalized,.tier.case_insensitive,.tier.spacing_insensitive{
      background:var(--warn-soft);color:var(--warn)}
  .tier.not_found{background:var(--bad-soft);color:var(--bad)}
  details.why{margin-top:2px}
  details.why summary{cursor:pointer;font-size:12px;color:var(--muted);list-style:none;
                      display:inline-flex;gap:5px;align-items:center}
  details.why summary::-webkit-details-marker{display:none}
  details.why summary::before{content:"\\25B8";font-size:9px;transition:transform .15s}
  details.why[open] summary::before{transform:rotate(90deg)}
  details.why p{margin:7px 0 0;font-size:13px;color:var(--muted);max-width:78ch}

  .call{display:flex;flex-direction:column;gap:6px;min-width:168px}
  .callrow{display:flex;flex-direction:column;gap:4px}
  .call button{font:inherit;font-size:12px;padding:6px 9px;border-radius:7px;cursor:pointer;
               border:1px solid var(--line-strong);background:var(--surface);
               color:var(--muted);text-align:left}
  .call button:hover{border-color:var(--accent);color:var(--ink)}
  .call button[aria-pressed="true"][data-v="reuse"]{background:var(--ok-soft);
      border-color:var(--ok);color:var(--ok);font-weight:600}
  .call button[aria-pressed="true"][data-v="mention"],
  .call button[aria-pressed="true"][data-v="neither"],
  .call button[aria-pressed="true"][data-v="primary"]{background:var(--bad-soft);
      border-color:var(--bad);color:var(--bad);font-weight:600}
  .call button[aria-pressed="true"][data-v="unsure"]{background:var(--warn-soft);
      border-color:var(--warn);color:var(--warn);font-weight:600}
  .call label{font-size:10.5px;letter-spacing:.08em;text-transform:uppercase;
              color:var(--muted);margin-top:4px}
  .who{display:flex;align-items:center;gap:8px;font-size:11px;letter-spacing:.07em;
       text-transform:uppercase;color:var(--muted);margin-top:4px}
  .who input{font:inherit;font-family:var(--sans);font-size:12.5px;letter-spacing:0;
             text-transform:none;padding:5px 9px;border-radius:7px;width:130px;
             border:1px solid var(--line-strong);background:var(--surface);color:var(--ink)}
  .who input:focus{outline:2px solid var(--accent);outline-offset:1px}
  .call textarea{font:inherit;font-size:12.5px;line-height:1.45;padding:7px 9px;
                 border-radius:7px;border:1px solid var(--line-strong);
                 background:var(--ground);color:var(--ink);resize:vertical;
                 min-height:62px;width:100%;font-family:var(--sans)}
  .call textarea::placeholder{color:var(--muted);opacity:.75}
  .call textarea:not(:placeholder-shown){border-color:var(--accent);
                                         background:var(--surface)}
  .saved{font-size:10.5px;color:var(--ok);min-height:13px}

  .note{margin-top:16px;padding:13px 16px;border-radius:10px;background:var(--accent-soft);
        border:1px solid color-mix(in srgb,var(--accent) 26%,transparent);
        font-size:13.5px;max-width:76ch}
  .note b{font-weight:640}
  footer.page{margin-top:34px;color:var(--muted);font-size:12.5px;max-width:76ch}
  .empty{padding:44px 16px;text-align:center;color:var(--muted);font-size:14px}
  @media (prefers-reduced-motion:reduce){*{transition:none!important;animation:none!important}}
"""

JS = """
const LOOSE = new Set(['normalized','case_insensitive','spacing_insensitive']);
const REVIEWS_KEY = 'dandi-reuse-reviews-v1';
const REVIEWER_KEY = 'dandi-reuse-reviewer-v1';

// The label a reviewer assigns, drawn from the same set the classifier chooses
// from. Both sides naming a class is what a confusion matrix is built from: a
// row where the classifier said REUSE and the reviewer said MENTION is a cell in
// it, and precision and recall come from those cells.
const LABELS = ['reuse','mention','neither','primary','unsure'];
// Shape of the save file, so anything reading one knows which fields to expect.
const SAVE_SCHEMA = 1;

// Reviewers judge independently. A reviewer sees only their own answers, and
// nothing another reviewer has recorded reaches this page, so that agreement
// between two of them measures two independent readings of the paper. Answers
// are held one bucket per reviewer so that a shared browser keeps them apart.
// Work done before a name was given sits under UNATTRIBUTED and is adopted by
// the first name entered.
const UNATTRIBUTED = '';
let reviews = {};
let me = '';

const readStore = key => {
  try { return JSON.parse(localStorage.getItem(key) || '{}') || {}; }
  catch (e) { return {}; }
};
const persist = () => {
  // A bucket holding neither answers nor notes is dropped, so the store lists
  // only names that have work behind them.
  Object.keys(reviews).forEach(n => {
    if (n !== me && !Object.keys(reviews[n].calls).length
                 && !Object.keys(reviews[n].notes).length) delete reviews[n];
  });
  try {
    localStorage.setItem(REVIEWS_KEY, JSON.stringify(reviews));
    localStorage.setItem(REVIEWER_KEY, me);
  } catch (e) { /* storage unavailable; the page still works in memory */ }
};

const bucket = name => (reviews[name] = reviews[name] || {calls: {}, notes: {}});
const myCalls = () => bucket(me).calls;
const myNotes = () => bucket(me).notes;
reviews = readStore(REVIEWS_KEY);
me = localStorage.getItem(REVIEWER_KEY) || '';
bucket(me);
let filter = 'all';

const esc = s => String(s ?? '').replace(/[&<>"']/g, c =>
  ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const tierLabel = t => ({exact:'exact', normalized:'normalized',
  case_insensitive:'case only', spacing_insensitive:'spacing only',
  not_found:'not in paper'}[t] || t);

const isFlagged = r => r.quotes.length > 0 && r.quotes.every(q => q.tier === 'not_found');
const isLoose = r => r.quotes.length > 0 &&
  !r.quotes.some(q => q.tier === 'exact') && !isFlagged(r);

function visible(){
  return ROWS.filter(r => {
    if (filter === 'neuro')     return r.neurophys === true;
    if (filter === 'notneuro')  return r.neurophys === false;
    if (filter === 'nosource')  return !r.source_quotes || !r.source_quotes.length;
    if (filter === 'flagged')   return r.quotes.some(q => q.tier === 'not_found') ||
                                       (r.source_quotes || []).some(q => q.tier === 'not_found');
    if (filter === 'loose')     return isLoose(r);
    if (filter === 'samelab')   return r.same_lab === true;
    if (filter === 'noarchive') return !r.archive;
    if (filter === 'todo')      return !myCalls()[r.key];
    return true;
  });
}

function neuroChip(r){
  if (r.neurophys === true)  return `<span class="chip neuro">Neurophysiology</span>`;
  if (r.neurophys === false) return `<span class="chip notneuro">Not neurophysiology</span>`;
  return `<span class="chip unknown">Modality not assessed</span>`;
}

function modChips(r){
  if (!r.modalities || !r.modalities.length) return '';
  const hosted = new Set(['neurophysiology','behavior']);
  return `<div class="mods">` + r.modalities.map(m =>
    `<span class="mod ${hosted.has(m) ? 'hosted' : ''}">${esc(m)}</span>`).join('') + `</div>`;
}

function quoteBlock(q){
  return `<figure class="q ${q.tier}">
      <blockquote>&ldquo;${esc(q.q)}&rdquo;</blockquote>
      <figcaption><span class="tier ${q.tier}">${tierLabel(q.tier)}</span></figcaption>
    </figure>`;
}

function labChip(r){
  if (r.same_lab === 'mixed') return `<span class="chip samelab">Mixed across datasets</span>`;
  if (r.same_lab === true)  return `<span class="chip samelab">Same lab${
    r.same_lab_confidence ? ' \\u00b7 ' + esc(r.same_lab_confidence) : ''}</span>`;
  if (r.same_lab === false) return `<span class="chip difflab">Different lab${
    r.same_lab_confidence ? ' \\u00b7 ' + esc(r.same_lab_confidence) : ''}</span>`;
  return `<span class="chip unknown">Lab not assessed</span>`;
}

function render(){
  const rows = visible();
  document.getElementById('body').innerHTML = rows.map(r => {
    const n = ROWS.indexOf(r) + 1;
    const call = myCalls()[r.key] || '';
    const note = myNotes()[r.key] || '';
    const quotes = r.quotes.length ? r.quotes.map(quoteBlock).join('') :
      `<figure class="q"><blockquote><em>No quote returned.</em></blockquote></figure>`;
    const provenance = `<div class="provenance">
        <h4>Where the data came from</h4>
        ${r.source_quotes && r.source_quotes.length
          ? r.source_quotes.map(quoteBlock).join('')
          : '<p class="none">The paper never states a source.</p>'}
      </div>`;
    return `<tr class="${isFlagged(r) ? 'flagged' : ''} ${call ? 'done' : ''}">
      <td class="idx">${n}</td>
      <td class="paper">
        <p class="title">${esc(r.title)}</p>
        <a class="doi" href="https://doi.org/${encodeURI(r.doi)}" target="_blank"
           rel="noopener">${esc(r.doi)}</a>
      </td>
      <td>
        <div class="meta">
          <a class="ds" href="https://dandiarchive.org/dandiset/${esc(r.dandiset)}"
             target="_blank" rel="noopener">${esc(r.dandiset)}</a>
          ${neuroChip(r)}
          ${modChips(r)}
          ${labChip(r)}
          ${r.dandi_reason ? `<span class="chip dandi">DANDI: ${esc(r.dandi_reason)}</span>` : ''}
          ${(r.pathways || []).length > 1 ? `<span class="chip both">both pathways</span>` : ''}
          ${r.archive ? `<span class="chip archive">${esc(r.archive)}</span>`
                      : `<span class="chip unknown">No archive named</span>`}
          <span class="conf">${esc(r.confidence)}<small>confidence</small></span>
        </div>
      </td>
      <td class="evidence-cell">
        <div class="evidence">${quotes}
          ${provenance}
          <details class="why"><summary>Model's reasoning</summary>
            <p>${esc(r.reasoning)}</p></details>
        </div>
      </td>
      <td>
        <div class="call" data-key="${esc(r.key)}">
          <div class="callrow">
            <button data-v="reuse"   aria-pressed="${call==='reuse'}">&#10003; Reuse</button>
            <button data-v="mention" aria-pressed="${call==='mention'}">Mention</button>
            <button data-v="neither" aria-pressed="${call==='neither'}">Neither</button>
            <button data-v="primary" aria-pressed="${call==='primary'}">Primary</button>
            <button data-v="unsure"  aria-pressed="${call==='unsure'}">? Unsure</button>
          </div>
          <label for="note-${n}">Why</label>
          <textarea id="note-${n}" data-note="${esc(r.key)}" rows="3"
            placeholder="Your reasoning\\u2026">${esc(note)}</textarea>
          <div class="saved" data-saved="${esc(r.key)}"></div>
        </div>
      </td>
    </tr>`;
  }).join('');
  document.getElementById('empty').hidden = rows.length > 0;
  updateProgress();
}

function updateProgress(){
  const done = ROWS.filter(r => myCalls()[r.key]).length;
  const noted = ROWS.filter(r => (myNotes()[r.key] || '').trim()).length;
  document.getElementById('prog').textContent =
    `${done} of ${ROWS.length} checked \\u00b7 ${noted} with notes`;
}

document.getElementById('body').addEventListener('click', e => {
  const btn = e.target.closest('button[data-v]');
  if (!btn) return;
  const key = btn.closest('.call').dataset.key;
  const c = myCalls();
  if (c[key] === btn.dataset.v) delete c[key]; else c[key] = btn.dataset.v;
  persist();
  render();
});

// Notes save as you type. Re-rendering on every keystroke would steal focus, so
// the row is left alone and only the saved indicator updates.
let saveTimer = null;
document.getElementById('body').addEventListener('input', e => {
  const ta = e.target.closest('textarea[data-note]');
  if (!ta) return;
  const key = ta.dataset.note;
  const nts = myNotes();
  if (ta.value.trim()) nts[key] = ta.value; else delete nts[key];
  clearTimeout(saveTimer);
  saveTimer = setTimeout(() => {
    persist();
    const flag = document.querySelector(`[data-saved="${CSS.escape(key)}"]`);
    if (flag){ flag.textContent = 'Saved'; setTimeout(() => { flag.textContent = ''; }, 1400); }
    updateProgress();
  }, 400);
});

document.querySelectorAll('.filters .btn').forEach(b => {
  b.addEventListener('click', () => {
    filter = b.dataset.f;
    document.querySelectorAll('.filters .btn').forEach(o =>
      o.setAttribute('aria-pressed', String(o === b)));
    render();
  });
});

// The name identifies whose answers these are, in the file and to whoever
// combines the files later. It sits in the toolbar so that a reviewer can see
// whose session they are in at any point, which matters when two people share a
// machine.
const nameBox = document.getElementById('reviewer');
nameBox.value = me;

function setReviewer(next){
  next = next.trim();
  if (next === me) return;
  // Work done before a name was given belongs to the first person to claim it.
  const orphan = reviews[UNATTRIBUTED];
  if (next && orphan && Object.keys(orphan.calls).length && !reviews[next]) {
    reviews[next] = orphan;
    delete reviews[UNATTRIBUTED];
  }
  me = next;
  bucket(me);
  persist();
  render();
}
nameBox.addEventListener('change', () => setReviewer(nameBox.value));
nameBox.addEventListener('blur', () => setReviewer(nameBox.value));

document.getElementById('reset').addEventListener('click', () => {
  const label = me ? `every call and note by ${me}` : 'every call and note';
  if (!confirm(`Clear ${label}? Save to a file first if you want to keep them.`)) return;
  delete reviews[me];
  bucket(me);
  persist();
  render();
});

// A reviewer's work lives in this browser's localStorage, which no other person
// and no other machine can read. Saving writes it to a file that can be sent on,
// and the files are combined once every reviewer has finished.
document.getElementById('save').addEventListener('click', () => {
  if (!me) {
    alert('Enter your name first, so this file can be told from anyone else\\u2019s.');
    nameBox.focus();
    return;
  }
  const payload = {schema: SAVE_SCHEMA, reviewer: me,
                   saved_at: new Date().toISOString(),
                   corpus: CORPUS, labels: LABELS,
                   calls: myCalls(), notes: myNotes()};
  const blob = new Blob([JSON.stringify(payload, null, 2)], {type: 'application/json'});
  const slug = me.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') || 'reviewer';
  const day = new Date().toISOString().slice(0, 10).replace(/-/g, '');
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = `reuse-verification-${slug}-${day}.json`;
  a.click();
  URL.revokeObjectURL(a.href);
});

document.getElementById('load').addEventListener('click', () =>
  document.getElementById('loadFile').click());

// Loading restores this reviewer's own answers, on a second machine or after
// site data was cleared. A file belonging to someone else is refused, so no
// other reviewer's judgement can reach this page.
document.getElementById('loadFile').addEventListener('change', async e => {
  const file = e.target.files && e.target.files[0];
  e.target.value = '';
  if (!file) return;
  let data;
  try { data = JSON.parse(await file.text()); }
  catch (err) { alert('That file is not JSON.'); return; }
  const incoming = data && typeof data === 'object' ? data.calls : null;
  if (!incoming || typeof incoming !== 'object') {
    alert('No calls found in that file.'); return;
  }
  const who = String(data.reviewer || '').trim();
  if (who && me && who !== me) {
    alert(`That file holds ${who}'s answers, and this page shows only your own.\\n` +
          'Reviewers judge independently, so their answers are combined once ' +
          'everyone has finished rather than here.');
    return;
  }
  const existing = Object.keys(myCalls()).length;
  if (existing && !confirm(
      `You already have ${existing} call(s) here. Replace them with this file?`)) return;

  const calls = {}, notes = {};
  let skipped = 0;
  Object.keys(incoming).forEach(k => {
    if (LABELS.includes(incoming[k])) calls[k] = incoming[k]; else skipped++;
  });
  const incomingNotes = (data.notes && typeof data.notes === 'object') ? data.notes : {};
  Object.keys(incomingNotes).forEach(k => {
    const t = String(incomingNotes[k] || '').trim();
    if (t) notes[k] = incomingNotes[k];
  });
  if (who && !me) { me = who; nameBox.value = who; }
  reviews[me] = {calls, notes};
  persist();
  render();
  alert(`Restored ${Object.keys(calls).length} call(s).` +
        (skipped ? `\\n${skipped} entr(ies) skipped, label not recognised.` : ''));
});

render();
"""


def corpus_stamp(inputs: list[str]) -> dict:
    """
    Identify the classifications these rows came from.

    Labels are only comparable across reviewers when they were made against the
    same classifications, and the prompt version and model say which those were.
    A save file carries this so a later merge can tell two reviewers who read the
    same corpus from two who read different ones.
    """
    versions, models = set(), set()
    for path in inputs:
        data = json.loads(Path(path).read_text())
        for r in data.get('classifications', []):
            if r.get('prompt_version') is not None:
                versions.add(r['prompt_version'])
            if r.get('model'):
                models.add(r['model'])
    return {
        'inputs': [Path(p).name for p in inputs],
        'prompt_versions': sorted(versions),
        'models': sorted(models),
    }


def build(rows: list[dict], heading: str = '', corpus: dict | None = None) -> str:
    payload = json.dumps(rows, ensure_ascii=False).replace('</', r'<\/')
    corpus_json = json.dumps(corpus or {}, ensure_ascii=False).replace('</', r'<\/')
    n = len(rows)
    located = sum(1 for r in rows
                  if any(q['tier'] != 'not_found' for q in r['quotes']))
    unsupported = n - located
    assessed = sum(1 for r in rows if r.get('same_lab') is not None)
    same = sum(1 for r in rows if r.get('same_lab') is True)
    neuro = sum(1 for r in rows if r.get('neurophys') is True)
    not_neuro = sum(1 for r in rows if r.get('neurophys') is False)

    lab_stat = (f'<div class="stat"><b>{same}</b><span>same-lab reuse</span></div>'
                if assessed else
                '<div class="stat"><b>&mdash;</b><span>lab not yet assessed</span></div>')
    both = sum(1 for r in rows if len(r.get('pathways') or []) > 1)
    title_text = heading or 'Data reuse claims awaiting a human check'
    reasons = Counter(r.get('dandi_reason') for r in rows if r.get('dandi_reason'))
    reason_bits = ''.join(
        f'<div class="stat"><b>{n}</b><span>{k}</span></div>'
        for k, n in reasons.most_common())

    return f"""<title>DANDI Reuse Verification &mdash; {n} cases</title>
<style>{CSS}</style>

<div class="wrap">
  <header class="page">
    <div class="eyebrow">Manual verification worksheet</div>
    <h1>{title_text}</h1>
    <p class="lede">Every case the full-text classifier labelled <b>REUSE</b>, shown with the
      passage it judged from. A quote is only worth trusting if it was actually found in the
      paper, so each one carries the tier at which it matched.</p>
    <div class="stats">
      <div class="stat"><b>{n}</b><span>reuse claims</span></div>
      <div class="stat"><b>{neuro}</b><span>reuse neurophysiology</span></div>
      {reason_bits}
      <div class="stat"><b>{both}</b><span>found by both pathways</span></div>
      <div class="stat"><b>{unsupported}</b><span>quote not in the paper</span></div>
    </div>
    <label class="who">Reviewing as
      <input id="reviewer" type="text" autocomplete="off" spellcheck="false"
             placeholder="your name"></label>
  </header>

  <div class="note">
    <b>Why each paper is here.</b> Every row reuses neurophysiology data, was judged to
    come from a lab other than the one that produced it, and carries textual evidence that
    the data came from DANDI rather than another repository. The provenance quotes below are
    that evidence &mdash; check them first, since the whole cohort rests on them.
  </div>

  <div class="note">
    <b>Modality is the decisive field.</b> DANDI hosts neurophysiology and the behavior
    recorded alongside it. Morphological reconstructions and transcriptomics live in other
    repositories, so a Patch-seq paper that reuses only gene expression or only
    reconstructions cites the same study without touching DANDI data. Green modality chips
    are the parts DANDI actually holds.
  </div>

  <div class="note">
    <b>How to read the tiers.</b> <em>Exact</em> means the quote appears character for
    character. <em>Normalized</em>, <em>case</em> and <em>spacing</em> mean it appears once
    curly quotes, dashes, capitalization or whitespace are folded &mdash; the passage is real,
    the transcription is loose. <em>Not found</em> means it does not appear in the paper at
    all; treat any claim resting only on those as unsupported.
  </div>

  <div class="toolbar">
    <div class="filters" role="group" aria-label="Filter cases">
      <button class="btn" data-f="all" aria-pressed="true">All {n}</button>
      <button class="btn" data-f="neuro" aria-pressed="false">Neurophysiology</button>
      <button class="btn" data-f="notneuro" aria-pressed="false">Other modality only</button>
      <button class="btn" data-f="nosource" aria-pressed="false">No provenance quote</button>
      <button class="btn" data-f="flagged" aria-pressed="false">Unsupported quote</button>
      <button class="btn" data-f="samelab" aria-pressed="false">Same lab</button>
      <button class="btn" data-f="todo" aria-pressed="false">Not yet checked</button>
    </div>
    <div class="spacer"></div>
    <div class="progress" id="prog">0 of {n} checked</div>
    <button class="btn" id="save">Save file</button>
    <button class="btn" id="load">Load file</button>
    <input type="file" id="loadFile" accept="application/json,.json" hidden>
    <button class="btn" id="reset">Clear all</button>
  </div>

  <div class="tablewrap">
    <table>
      <thead>
        <tr>
          <th style="width:30px">#</th>
          <th style="width:19%">Citing paper</th>
          <th style="width:150px">Dataset &amp; provenance</th>
          <th>Evidence quoted by the model</th>
          <th style="width:190px">Your call</th>
        </tr>
      </thead>
      <tbody id="body"></tbody>
    </table>
    <div class="empty" id="empty" hidden>No cases match this filter.</div>
  </div>

  <footer class="page">
    Generated from <code>output/fulltext_classifications.json</code> by
    <code>src/analysis/build_reuse_verification_page.py</code>. Your calls and notes are stored
    in this browser only and survive regeneration of this page; use <b>Copy results</b> to take
    them with you as CSV.
  </footer>
</div>

<script>
const ROWS = {payload};
const CORPUS = {corpus_json};
{JS}
</script>
"""


DANDI_MARKER = re.compile(r'dandiarchive\.org|10\.48324|\bDANDI\b|dandiset', re.I)

# Version suffixes. '/vN' is unambiguous. '.N' is not: 10.1002/brx2.47 and
# brx2.65 are different articles, not versions of one, so it only counts as a
# version when the unversioned DOI is also present or the base ends in a long
# article number.
_VERSION_SLASH = re.compile(r'^(?P<base>.+?)/v\d{1,2}$', re.I)
_VERSION_DOT = re.compile(r'^(?P<base>.+?)\.\d{1,2}$')
_ARTICLE_NUMBER = re.compile(r'\d{4,}$')


def canonical_doi(doi: str, known: set) -> str:
    """
    Collapse a versioned DOI onto the work it is a version of.

    eLife's reviewed-preprint model mints .1, .2 and .3 alongside the base DOI,
    so one paper can appear five times and be counted five times. Research
    Square, F1000Research, Authorea and Qeios do the same with other suffixes.
    """
    m = _VERSION_SLASH.match(doi)
    if m:
        return m.group('base')
    m = _VERSION_DOT.match(doi)
    if m:
        base = m.group('base')
        if base in known or _ARTICLE_NUMBER.search(base.split('/')[-1]):
            return base
    return doi


def merge_by_pair(inputs: list[str]) -> dict:
    """
    One row per (paper, dataset), which is the unit the classifier answers about.

    A paper reusing several datasets stands in a separate relationship to each,
    supported by its own passage, so each is judged on its own. The direct and
    citing pathways can both reach the same pair, so their fields are unioned:
    every archive named, every modality, every quote.
    """
    loaded = [(Path(p), json.loads(Path(p).read_text())) for p in inputs]
    known = {r['citing_doi'] for _, d in loaded for r in d['classifications']}

    merged: dict = {}
    for path, data in loaded:
        pathway = 'direct' if 'direct' in path.name else 'citing'
        for r in data['classifications']:
            if r.get('classification') != 'REUSE':
                continue
            doi = canonical_doi(r['citing_doi'], known)
            dandiset = r.get('dandiset_id') or ''
            row = merged.setdefault((doi, dandiset), {
                'key': f'{doi}\t{dandiset}', 'doi': doi, 'dandiset': dandiset,
                'title': '', 'confidence': 0,
                'archives': [], 'same_lab_vals': set(), 'same_lab_confidence': None,
                'modalities': [], 'neurophys': False, 'pathways': set(),
                'reasoning': '', 'quotes': [], 'source_quotes': [],
            })
            row['pathways'].add(pathway)
            if r.get('title') and len(r['title']) > len(row['title']):
                row['title'] = r['title'].strip()
            if r.get('source_archive') and r['source_archive'] not in row['archives']:
                row['archives'].append(r['source_archive'])
            if r.get('same_lab') is not None:
                row['same_lab_vals'].add(bool(r['same_lab']))
                if row['same_lab_confidence'] is None:
                    row['same_lab_confidence'] = r.get('same_lab_confidence')
            for m in r.get('reused_modalities') or []:
                if m not in row['modalities']:
                    row['modalities'].append(m)
            if r.get('reused_neurophysiology'):
                row['neurophys'] = True
            row['confidence'] = max(row['confidence'], r.get('confidence') or 0)
            if len(r.get('reasoning') or '') > len(row['reasoning']):
                row['reasoning'] = r.get('reasoning') or ''
            for key, field in (('quotes', 'evidence_quotes'),
                               ('source_quotes', 'source_quotes')):
                for q in r.get(field, []):
                    rec = {'q': q['quote'], 'tier': q['match_type']}
                    if rec not in row[key]:
                        row[key].append(rec)
    return merged


def finalize(row: dict) -> dict:
    """Derive the display fields the page needs from a merged row."""
    vals = row.pop('same_lab_vals')
    row['same_lab'] = (True if vals == {True} else
                       False if vals == {False} else
                       'mixed' if vals == {True, False} else None)
    row['pathways'] = sorted(row.pop('pathways'))

    # Why this paper counts as DANDI-sourced, which is what a reviewer checks.
    if 'direct' in row['pathways']:
        row['dandi_reason'] = 'names a DANDI identifier in its text'
    elif 'DANDI Archive' in row['archives']:
        row['dandi_reason'] = 'names DANDI Archive as the source'
    else:
        hit = next((q for q in row['source_quotes'] + row['quotes']
                    if q['tier'] != 'not_found' and DANDI_MARKER.search(q['q'])), None)
        row['dandi_reason'] = ('quotes DANDI in the text' if hit else None)
    row['archive'] = ', '.join(row['archives']) or None
    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('-i', '--input', action='append', required=True,
                        help='Classification JSON; repeat to merge both pathways.')
    parser.add_argument('-o', '--output', default='/tmp/reuse_verify.html')
    parser.add_argument('--neuro-only', action='store_true',
                        help='Keep only papers that reused neurophysiology.')
    parser.add_argument('--lab', choices=['different', 'same', 'any'], default='any',
                        help="'different' keeps outside reuse, and mixed cases with it.")
    parser.add_argument('--dandi-evidenced', action='store_true',
                        help='Keep only papers with textual evidence of DANDI as source.')
    parser.add_argument('--title', default='')
    args = parser.parse_args()

    rows = [finalize(r) for r in merge_by_pair(args.input).values()]

    if args.neuro_only:
        rows = [r for r in rows if r['neurophys']]
    if args.lab == 'different':
        rows = [r for r in rows if r['same_lab'] in (False, 'mixed')]
    elif args.lab == 'same':
        rows = [r for r in rows if r['same_lab'] in (True, 'mixed')]
    if args.dandi_evidenced:
        rows = [r for r in rows if r['dandi_reason']]

    rows.sort(key=lambda r: (-(r['confidence'] or 0), r['doi'], r['dandiset']))
    Path(args.output).write_text(
        build(rows, args.title, corpus_stamp(args.input)))
    papers = len({r['doi'] for r in rows})
    print(f"{len(rows)} pairs across {papers} papers -> {args.output}")


if __name__ == '__main__':
    main()
