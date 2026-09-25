#!/usr/bin/env python3
"""
Run a review session over the reuse candidates.

Two views onto one list of (paper, dataset) pairs. The worksheet asks one
question about one pair at a time: answer, and the next pair comes up. The
overview lays the pairs out grouped by dataset or by paper, which is what a
second pass needs -- every pair you called unsure, or every paper that touched
one dandiset, read together rather than one screen at a time.

A session holds both pathways, because a dandiset's pairs are split between
them and neither half is the whole story. Which pathway a pair came down is a
property of the pair, so the labels are chosen per pair and nothing is ever
offered a label its classifier could not have produced: a paper found by naming
the dandiset in its own text might have deposited that dataset, so a direct pair
offers PRIMARY and shows no cited paper; a paper found by citing the dandiset's
publication might only be mentioning that work, so an indirect pair offers
MENTION and leads with the paper it cited.

A third kind of pair is the reviewer's own. Reading a paper is what turns up a
dandiset it cited that the pipeline never proposed, so the overview grouped by
paper carries a box that takes an identifier, asks DANDI what the dataset is
called, and puts the pair on the list. Those pairs come down the ADDED pathway
and carry REUSE: adding one is the reviewer saying the paper reused that
dataset, so the call arrives with the pair and stands as long as it does.
Removing the pair is how the call is taken back.

Answers are written to reuse_confirmation/<reviewer>/<reviewer>-reviews.json as
they are made, and the pairs the reviewer added alongside them. That file is the
durable artifact of a review round and belongs in version control; nothing about
the model, the prompt or the run that produced the classification goes into it,
because none of that changes what the right answer is.

Usage:
    python -m src.review.run_review --reviewer rly
"""

from __future__ import annotations

import argparse
import html
import json
import re
import webbrowser
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from functools import lru_cache
from itertools import zip_longest
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import requests

from fetch_paper import TextCache

from src.review.build_candidates import CANDIDATES_FILE
from src.review.reviewers import (REUSE_CONFIRMATION_DIR, REVIEWERS_FILE,
                                  assignment_path, assignment_paths,
                                  load_reviewers, reviews_path, reviews_paths,
                                  select_reviewers)

REPO = Path(__file__).resolve().parents[2]
PAPER_CACHE = REPO / '.paper_cache'

DANDI_API = 'https://api.dandiarchive.org/api'

PALETTE = """
  :root{
    --ground:#F4F6F7; --surface:#FFFFFF; --raise:#EDF1F3;
    --line:#DCE3E7; --line-strong:#C3CED4;
    --ink:#0F171C; --muted:#5C6F7C;
    --accent:#16697A; --accent-soft:#E1EFF2; --on-accent:#FFFFFF;
    --ok:#2C7358; --ok-soft:#E0F0E8;
    --warn:#8A5E0C; --warn-soft:#F6EBD5; --on-warn:#FFFFFF;
    --bad:#A22F3D; --bad-soft:#F7E2E4;
    --mention:#1C5D9B; --mention-soft:#E1ECF7;
    --primary:#6D3D9B; --primary-soft:#EEE6F7;
    --ambiguous-reuse:#4E6070; --ambiguous-reuse-soft:#D9E2EA;
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
      --warn:#D9A63C; --warn-soft:#33280F; --on-warn:#1B1403;
      --bad:#EF8390; --bad-soft:#3A1B1F;
      --mention:#6DB3F2; --mention-soft:#10263A;
      --primary:#BE96E8; --primary-soft:#251B36;
      --ambiguous-reuse:#9DB2C2; --ambiguous-reuse-soft:#273846;
    }
  }
"""

CSS = PALETTE + """
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
  /* Nothing left to take back reads as nothing to press. */
  .btn:disabled{opacity:.45;cursor:default}
  .btn:disabled:hover{border-color:var(--line-strong);color:var(--muted)}
  .btn[aria-pressed="true"]{background:var(--accent);border-color:var(--accent);
                            color:var(--on-accent)}
  /* The way back to the pair the reviewer left. It appears only while there is
     somewhere to go back to, and it is the one amber thing in a toolbar that
     fills a control to mean selected, so it is found at a glance and reads as
     an action rather than a state. */
  .btn.back{background:var(--warn);border-color:var(--warn);
            color:var(--on-warn);font-weight:660}
  .btn.back:hover{background:color-mix(in srgb,var(--warn) 85%,black);
                  border-color:color-mix(in srgb,var(--warn) 85%,black);
                  color:var(--on-warn)}
  .filters{display:flex;gap:6px}
  /* A control the view has no use for is hidden, and a display of its own would
     otherwise outrank the browser's rule for that. */
  .filters[hidden]{display:none}
  .btn:focus-visible,a:focus-visible,textarea:focus-visible,
  input:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
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
  /* Two panels where the pair has no cited paper to open: a direct pair was
     found in the paper's own text, and an added one in a reviewer's reading. */
  .subject.direct,.subject.added{grid-template-columns:repeat(2,1fr)}
  @media (max-width:980px){
    .subject,.subject.direct,.subject.added{grid-template-columns:1fr}
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
  .byline{font-size:14px;color:var(--ink);opacity:.82}
  .shared-paper .byline{font-size:inherit;color:inherit;opacity:1}
  .shared-paper .byline::before{content:'\\2014  '}
  .links{margin-top:auto;display:flex;flex-wrap:wrap;align-items:baseline;gap:6px 14px}
  a.doi{font-family:var(--mono);font-size:11.5px;color:var(--accent);text-decoration:none;
        border-bottom:1px solid transparent;word-break:break-all}
  a.doi:hover{border-bottom-color:var(--accent)}
  /* The way into a paywalled paper: the text we fetched, which is the text the
     classifier was given. */
  a.rawtext{font-size:11.5px;font-weight:600;color:var(--accent);text-decoration:none;
            padding:2px 9px;border-radius:999px;background:var(--accent-soft);
            white-space:nowrap}
  a.rawtext:hover{text-decoration:underline}

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

  /* Where the dataset's paper came from, worn by the cited paper. The same chip
     as a quote's tier because it answers the same kind of question: how far to
     trust what is on the card. The label carries the provenance, so the colour
     carries only whether a person put it there. */
  .origin{display:inline-flex;align-items:center;font-family:var(--mono);
          font-size:10.5px;letter-spacing:.05em;text-transform:uppercase;
          padding:2px 7px;border-radius:5px;font-weight:600;
          background:var(--raise);color:var(--muted)}
  .origin.unvouched{background:var(--bad-soft);color:var(--bad)}

  /* One paper naming several dandisets, worn by the dataset. Amber rather than
     red: red says nothing stands behind the link, and this link can be DANDI's
     own and still leave the reuse unattributable to the dataset on the card. */
  .shared{display:inline-flex;align-items:center;font-family:var(--mono);
          font-size:10.5px;letter-spacing:.05em;text-transform:uppercase;
          padding:2px 7px;border-radius:5px;font-weight:600;
          background:var(--warn-soft);color:var(--warn)}
  .shared-paper{margin:0 0 12px;font-size:13.5px;color:var(--muted)}
  ul.siblings{margin:0 0 16px;padding:0;list-style:none;display:flex;
              flex-direction:column;gap:8px}
  ul.siblings li{display:flex;flex-wrap:wrap;align-items:baseline;gap:5px 11px}
  ul.siblings a.dsid{font-size:13px;font-weight:700}
  .sibname{font-size:13.5px;color:var(--muted);text-wrap:pretty}

  .decide{flex:0 0 auto;display:flex;flex-direction:column;align-items:center;gap:11px}
  /* The call in the middle of the screen, and the actions ending where the
     dataset panel above them ends, so they keep one place whatever the pathway
     offers and however wide its calls run. The side columns are held equal,
     which is what centres the calls on the page. */
  .choices{display:grid;align-items:center;gap:10px 28px;width:100%;
           grid-template-columns:minmax(0,1fr) auto minmax(0,1fr)}
  .choices .calls{grid-column:2}
  .actions{grid-column:3;justify-self:end;display:flex;flex-wrap:wrap;
           align-items:center;gap:10px}
  /* Narrower than this the side columns no longer hold the actions, so the
     actions take a line of their own under the calls. The calls keep the middle
     of the screen, which is where the answer is made. */
  @media (max-width:1260px){
    .choices .calls,.actions{grid-column:1/-1}
  }
  .btn.addcited{font-size:13px;padding:11px 16px;border-radius:11px;
                color:var(--accent);
                border-color:color-mix(in srgb,var(--accent) 40%,transparent)}
  .btn.addcited:hover{background:var(--accent-soft)}
  /* Taking back a pair the reviewer put on the list, offered wherever that pair
     is on screen. */
  .btn.drop{font-size:13px;padding:11px 16px;border-radius:11px;
            color:var(--muted);border-color:var(--line-strong)}
  .btn.drop:hover{border-color:var(--bad);color:var(--bad)}
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
     its colour rather than read off its text. The same rules dress the
     worksheet's buttons, the overview's and the chips that filter to a call,
     because all three name the same thing. */
  .call.reuse{color:var(--ok);
      border-color:color-mix(in srgb,var(--ok) 40%,transparent)}
  .call.ambiguous_reuse{color:var(--ambiguous-reuse);
      border-color:color-mix(in srgb,var(--ambiguous-reuse) 40%,transparent)}
  .call.mention{color:var(--mention);
      border-color:color-mix(in srgb,var(--mention) 40%,transparent)}
  .call.primary{color:var(--primary);
      border-color:color-mix(in srgb,var(--primary) 40%,transparent)}
  .call.neither{color:var(--bad);
      border-color:color-mix(in srgb,var(--bad) 40%,transparent)}
  .call.unsure{color:var(--warn);
      border-color:color-mix(in srgb,var(--warn) 40%,transparent)}
  .call[aria-pressed="true"].reuse{background:var(--ok-soft)}
  .call[aria-pressed="true"].ambiguous_reuse{background:var(--ambiguous-reuse-soft)}
  .call[aria-pressed="true"].mention{background:var(--mention-soft)}
  .call[aria-pressed="true"].primary{background:var(--primary-soft)}
  .call[aria-pressed="true"].neither{background:var(--bad-soft)}
  .call[aria-pressed="true"].unsure{background:var(--warn-soft)}
  .btn.call[aria-pressed="true"]{color:var(--ink);font-weight:700}
  /* A call the pair arrived with is shown made and not asked again, so it keeps
     the colour of the answer it is and takes no pointer. */
  .call:disabled{cursor:default}
  .empty{margin:auto;color:var(--muted);font-size:14px}

  /* The second toolbar row: what you are looking at, under where you are. The
     two read as one band, so only the lower one carries the rule beneath. */
  .toolbar.controls{border-bottom:1px solid var(--line);padding-top:0;gap:8px}
  .toolbar:not(.controls){border-bottom:0;padding-bottom:8px}
  /* Each group asks a different question of the list, so they are told apart by
     a rule rather than by a wider gap that wrapping would swallow. */
  .toolbar.controls .filters + .filters{padding-left:9px;
                                        border-left:1px solid var(--line)}
  .sep{width:1px;align-self:stretch;margin:0 3px;background:var(--line-strong)}
  .fold{margin-left:9px}
  /* Takes what the row has left rather than a width of its own, so the chips
     keep one line and the box is as wide as that leaves it. */
  .search{font:inherit;font-size:12.5px;padding:6px 13px;border-radius:999px;
          border:1px solid var(--line-strong);background:var(--surface);
          color:var(--ink);flex:1 1 13ch;min-width:11ch;max-width:30ch;
          margin-left:9px}
  .search::placeholder{color:var(--muted)}
  .mode.quiet{background:var(--raise);color:var(--muted);font-weight:500}
  .dsid.small{font-family:var(--mono);font-size:12px;font-weight:700;
              color:var(--accent);letter-spacing:0}

  /* The overview is the only thing that scrolls in its view, the way the
     evidence box is the only one on the worksheet. */
  .overview{flex:1 1 auto;min-height:0;overflow-y:auto;display:flex;
            flex-direction:column;gap:14px}
  /* A group keeps its full height and the overview scrolls past it. Letting it
     shrink to fit instead would clip its rows away behind the rounded corner,
     with nothing to scroll to reach them. */
  .group{flex:0 0 auto;background:var(--surface);border:1px solid var(--line);
         border-radius:14px;overflow:hidden}
  .group h3{position:sticky;top:0;z-index:1;display:flex;align-items:baseline;
            gap:11px;margin:0;padding:10px 18px;background:var(--raise);
            border-bottom:1px solid var(--line);font-size:14px;font-weight:640;
            cursor:pointer;user-select:none}
  .group h3:hover .caret{color:var(--accent)}
  /* A group folded shut keeps its heading, and the tally on it stands in for
     the rows being held back. */
  .group.shut h3{border-bottom:0}
  .caret{display:inline-block;color:var(--muted);font-size:12px;
         transition:transform .12s ease}
  .group.shut .caret{transform:rotate(-90deg)}
  .groupid{font-family:var(--mono);font-size:14px;font-weight:700;
           color:var(--accent);white-space:nowrap}
  .groupname{font-weight:400;color:var(--muted);min-width:0;overflow:hidden;
             text-overflow:ellipsis;white-space:nowrap}
  .tally{margin-left:auto;font-family:var(--mono);font-size:11.5px;
         font-variant-numeric:tabular-nums;color:var(--muted);white-space:nowrap}

  /* A row is the way into its pair, so the whole of it is the target and
     nothing inside it is a link of its own. */
  .entry{display:flex;align-items:center;gap:16px;padding:9px 18px;
         border-top:1px solid var(--line);cursor:pointer}
  .group h3 + .entry,.group .entry:first-child{border-top:0}
  .entry:hover{background:var(--raise)}
  .entry .what{flex:1 1 auto;min-width:0}
  .entry .line{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .entry .sub{display:flex;align-items:center;gap:9px;margin-top:2px}
  .entry .sub:empty{display:none}
  .doitext{font-family:var(--mono);font-size:11.5px;color:var(--muted)}
  /* A note is written where it is read, so the list is where a second pass can
     say what it found as well as what it called. It sizes to what it holds and
     keeps out of the way until the pointer reaches it, so forty rows of empty
     notes still read as a list. */
  .entry textarea.note{display:block;width:100%;margin-top:3px;font:inherit;
        font-size:12px;font-style:italic;line-height:1.45;color:var(--muted);
        padding:3px 7px;border-radius:7px;border:1px solid transparent;
        background:transparent;resize:none;overflow:hidden;cursor:text}
  .entry textarea.note:hover{border-color:var(--line)}
  .entry textarea.note:focus{border-color:var(--accent);background:var(--surface);
        color:var(--ink);font-style:normal}
  .entry textarea.note::placeholder{color:var(--muted);opacity:.5}
  .rowcalls{flex:0 0 auto;display:flex;gap:6px}
  .rowcalls button{font:inherit;font-size:11.5px;font-weight:560;padding:5px 12px;
                   border-radius:8px;cursor:pointer;border:1px solid;
                   background:var(--surface);white-space:nowrap}
  .rowcalls button:hover{border-color:currentColor}
  .rowcalls button[aria-pressed="true"]{font-weight:760;
                                        box-shadow:inset 0 0 0 1px currentColor}
  /* Only a pair the reviewer put on the list can be taken off it, so only those
     rows carry this. A mistyped identifier is what makes it necessary. */
  .rowcalls .drop{font-size:15px;line-height:1;padding:5px 11px;
                  border-color:var(--line-strong);color:var(--muted)}
  .rowcalls .drop:hover{border-color:var(--bad);color:var(--bad)}
  .mode.quiet.added{background:var(--warn-soft);color:var(--warn);font-weight:600}
  /* A pair no round dealt out, standing in a paper's datasets beside the ones
     somebody owes an answer on. Outlined rather than filled, because it says
     what nobody was asked rather than what the pair is. */
  .mode.quiet.outside{background:transparent;color:var(--muted);
                      box-shadow:inset 0 0 0 1px var(--line-strong)}

  /* Adding a dataset the pipeline missed, under the ones it found, so a paper
     reads as one list with a way to extend it. */
  .addbox{display:flex;flex-wrap:wrap;align-items:center;gap:10px;
          padding:11px 18px;border-top:1px solid var(--line);background:var(--raise)}
  .addbox .what{font-size:12.5px;color:var(--muted)}
  .addbox input{font:inherit;font-family:var(--mono);font-size:13px;width:12ch;
                padding:6px 11px;border-radius:8px;
                border:1px solid var(--line-strong);background:var(--surface);
                color:var(--ink)}
  .addstate{font-size:12px;color:var(--muted)}
  .addstate.ok{color:var(--ok)}
  .addstate.bad{color:var(--bad);font-weight:600}
  @media (max-width:760px){
    .entry{flex-wrap:wrap;align-items:flex-start}
    .rowcalls{width:100%}
  }
  @media (prefers-reduced-motion:reduce){*{transition:none!important;animation:none!important}}
"""

JS = """
// An answer is one pair's, so the record nests: paper, then dataset, then the
// call and the note made about that pair.
let reviews = {};
let index = 0;
const entry = r => (reviews[r.doi] || {})[r.dandiset] || {};
const callFor = r => entry(r).call || '';
const noteFor = r => entry(r).note || '';

// A pair with neither a call nor a note was never answered, so it is dropped
// rather than left behind as an empty branch.
function record(r, field, value){
  const datasets = reviews[r.doi] || (reviews[r.doi] = {});
  const answer = datasets[r.dandiset] || (datasets[r.dandiset] = {});
  if (value) answer[field] = value; else delete answer[field];
  if (!Object.keys(answer).length) delete datasets[r.dandiset];
  if (!Object.keys(datasets).length) delete reviews[r.doi];
  save();
}

// Two ways of reading the same list. The worksheet asks about one pair at a
// time, which is how a first pass is made; the overview lays the pairs out in
// groups, which is how you go back over the answers you already gave and see
// what a dataset or a paper came to as a whole.
let view = 'worksheet';

function setView(next){
  view = next;
  document.querySelectorAll('[data-view]').forEach(b =>
    b.setAttribute('aria-pressed', String(b.dataset.view === next)));
}

// A call is one click, and so is the wrong call -- the chip beside the one you
// meant, or the right one on the row above. Under a filter the pair leaves the
// list the moment it is answered, so what it was called before is kept here
// along with where it was called from, and both can be put back.
const undoStack = [];

// What the session is looking at. Every one of these narrows what is shown
// rather than what was loaded -- a session always holds every candidate there
// is -- so you can step outside your own queue, or into the other pathway, and
// back without restarting.
const controls = {
  // Take the pairs still owed an answer, look back over the ones already given
  // one, or ask for a single call. A session opens on the work still to do.
  filter: 'todo',
  pathway: 'all',
  scope: MINE ? 'mine' : 'candidates',
  grouping: 'dandiset',
  search: '',
};

// A pair is a paper and a dataset, and no two pairs are the same one, so that
// is the key everything is looked up by.
const keyOf = r => r.doi + '\\t' + r.dandiset;
const ROW_BY_KEY = new Map(ROWS.map(r => [keyOf(r), r]));
// Searching is done over a string built once rather than over the fields each
// time: this runs on every keystroke across every pair.
const searchTextOf = r => [r.doi, r.fetched_doi, r.title, r.dandiset,
                           r.dandiset_name, r.cited_doi, r.cited_title,
                           r.cited_citation]
                          .join(' ').toLowerCase();
for (const r of ROWS) r.searchText = searchTextOf(r);

// The order the candidates arrive in, which stands a paper's datasets together.
// A pair added to a paper is sorted into that paper's run of them.
const byPair = (a, b) => a.doi < b.doi ? -1 : a.doi > b.doi ? 1
                       : a.dandiset < b.dandiset ? -1 : 1;

// Pairs the reviewer found by reading the paper. Nested paper to dataset the
// way the reviews are, and holding the dataset's name, which for these is
// something only DANDI can say and nothing else on the page carries.
let added = {};
const isAdded = r => r.pathway === 'added';

// An added pair takes its paper from a pair already on the list: the paper is
// one the pipeline reached and only the dataset is new. Where nothing else
// reached it, the DOI stands in for the title.
function enrol(doi, dandiset, name){
  const key = doi + '\\t' + dandiset;
  if (ROW_BY_KEY.has(key)) return ROW_BY_KEY.get(key);
  const paper = ROWS.find(r => r.doi === doi) || {};
  const row = {doi, dandiset, dandiset_name: name || '', pathway: 'added',
               title: paper.title || '', fetched_doi: paper.fetched_doi || doi,
               has_text: Boolean(paper.has_text), reasoning: '', quotes: [],
               cited_doi: '', cited_title: '', cited_citation: '', cited_role: '',
               cited_source: '', shared_paper: null};
  row.searchText = searchTextOf(row);
  ROWS.push(row);
  ROWS.sort(byPair);
  ROW_BY_KEY.set(key, row);
  // The call comes with the pair: a reviewer adds a dandiset because they read
  // the paper as reusing it. It goes into the reviews as it is made, so that
  // what the file holds is what the screen says.
  if (callFor(row) !== 'reuse') record(row, 'call', 'reuse');
  return row;
}

const esc = s => String(s ?? '').replace(/[&<>"']/g, c =>
  ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const tierLabel = t => ({exact:'exact', normalized:'normalized',
  case_insensitive:'case only', spacing_insensitive:'spacing only',
  punctuation_insensitive:'punctuation only',
  not_found:'not in paper'}[t] || t);

// How the dataset came to name the paper the pair was built from. DANDI names
// one for a minority of these dandisets; for the rest a model picked it, and a
// wrong pick puts a paper about something else in front of the reviewer.
const originLabel = o => ({
  'dcite:IsDescribedBy':  'described by',
  'dcite:Describes':      'describes',
  'dcite:IsSupplementTo': 'supplement to',
  'dcite:IsPublishedIn':  'published in',
  description:            'in description',
  override:               'hand-set',
  llm_identified:         'LLM-identified \\u2014 verify',
  unknown:                'unknown',
}[o] || o);

// A candidate list built before this field existed says nothing, rather than
// guessing which kind of link it has.
function originChip(source){
  if (!source) return '';
  const unvouched = source === 'llm_identified' || source === 'unknown';
  return `<span class="origin ${unvouched ? 'unvouched' : ''}"
            >${esc(originLabel(source))}</span>`;
}

// The paper the pair was built from, where other dandisets name it too. A work
// citing a paper that describes four datasets has said nothing about which of
// them it touched, and the pair in front of you is only one of the four.
function sharedChip(r){
  if (!r.shared_paper) return '';
  const n = r.shared_paper.dandisets.length;
  return `<span class="shared">shared with ${n} dandiset${n === 1 ? '' : 's'}</span>`;
}

// Each sibling wears how it came to name the paper, because that is what says
// how much the sharing is worth: DANDI claiming the paper describes both is an
// ambiguity to resolve, a model picking it twice is a pairing to distrust.
function sharedBlock(r){
  if (!r.shared_paper) return '';
  const shared = r.shared_paper;
  const siblings = shared.dandisets.map(d => `<li>
      <a class="dsid" href="https://dandiarchive.org/dandiset/${esc(d.dandiset)}"
         target="_blank" rel="noopener">${esc(d.dandiset)}</a>
      <span class="sibname">${esc(d.dandiset_name)}</span>
      ${originChip(d.relation)}
    </li>`).join('');
  return `<h4>Dandisets Sharing This Paper</h4>
    <p class="shared-paper">
      <a class="doi" href="https://doi.org/${encodeURI(shared.doi)}"
         target="_blank" rel="noopener">${esc(shared.doi)}</a>
      ${esc(shared.title)}
      ${shared.citation ? `<span class="byline">${esc(shared.citation)}</span>` : ''}
    </p>
    <ul class="siblings">${siblings}</ul>`;
}

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

// Answers land in reuse_confirmation/<reviewer>/<reviewer>-reviews.json as they
// are made.
let saveTimer = null;

// `manual` only changes what the indicator says afterwards: a write the
// reviewer asked for reads back differently from one that happened on its own.
function saveNow(manual){
  clearTimeout(saveTimer);
  setSaveState('Saving\\u2026', '');
  return fetch('/save', {method: 'POST', headers: {'Content-Type': 'application/json'},
                         body: JSON.stringify({reviewer: REVIEWER, reviews, added})})
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

// A pair the reviewer added is theirs by the act of adding it, so narrowing to
// your own queue does not take it off the screen the moment you put it there.
const isMine = r => isAdded(r) || MINE.has(keyOf(r));

// Whether any round reached this pair. Rounds are cut with filters and most of
// the candidate list was never dealt to anybody, so a paper's datasets hold
// pairs somebody owes an answer on beside pairs nobody was ever asked about.
// A session that was given no queues to read says nothing about either.
const inRound = r => !DEALT || DEALT.has(keyOf(r));

// A pair nobody was ever asked about. What wears the chip and what sinks to the
// bottom of a group are the same question, so both read it off here. A pair the
// reviewer added is not one of these: no round dealt it either, but they put it
// there themselves a moment ago and it belongs with the work.
const unasked = r => !isAdded(r) && !inRound(r);

// Commas separate alternatives and spaces are all-of, so a group of datasets is
// one query -- "000128, 000129" -- and so is one paper, "mc_maze reach".
function matchesSearch(r){
  const query = controls.search.trim().toLowerCase();
  if (!query) return true;
  return query.split(',').some(alternative => {
    const terms = alternative.split(/\\s+/).filter(Boolean);
    return terms.length && terms.every(t => r.searchText.includes(t));
  });
}

// The work the session is looking at, before anything is asked about the call.
// Progress is measured over this, so that narrowing to one label does not read
// as having finished.
function inScope(){
  return ROWS.filter(r =>
    (controls.scope === 'candidates' || isMine(r)) &&
    (controls.pathway === 'all' || r.pathway === controls.pathway) &&
    matchesSearch(r));
}

function matchesCall(r){
  const call = callFor(r);
  if (controls.filter === 'all') return true;
  if (controls.filter === 'todo') return !call;
  if (controls.filter === 'done') return Boolean(call);
  return call === controls.filter;
}

const visible = () => inScope().filter(matchesCall);

// The pair goes with the citing paper's link so its quoted passages can be
// marked in the text; the cited paper has none of its own.
function textLink(doi, dandiset){
  let href = '/text?doi=' + encodeURIComponent(doi);
  if (dandiset) href += '&dandiset=' + encodeURIComponent(dandiset);
  return `<a class="rawtext" href="${href}" target="_blank"
             rel="noopener">Raw Text</a>`;
}

// The byline is who wrote the paper and when, which is how a citing paper's text
// refers to it.
function paperPanel(role, doi, title, byline, text, chip){
  const body = doi
    ? `<a class="name" href="https://doi.org/${encodeURI(doi)}"
          target="_blank" rel="noopener">${esc(title || doi)}</a>
       ${byline ? `<span class="byline">${esc(byline)}</span>` : ''}
       <div class="links">
         <a class="doi" href="https://doi.org/${encodeURI(doi)}"
            target="_blank" rel="noopener">${esc(doi)}</a>
         ${text || ''}
         ${chip || ''}
       </div>`
    : `<span class="absent">Not recorded for this pair.</span>`;
  return `<div class="party"><span class="role">${esc(role)}</span>${body}</div>`;
}

function datasetPanel(r){
  const chip = sharedChip(r);
  return `<div class="party dataset">
      <span class="role">Cited Dataset</span>
      <a class="dsid" href="https://dandiarchive.org/dandiset/${esc(r.dandiset)}"
         target="_blank" rel="noopener">${esc(r.dandiset)}</a>
      <span class="dsname">${esc(r.dandiset_name)}</span>
      ${chip ? `<div class="links">${chip}</div>` : ''}
    </div>`;
}

function quoteBlock(q){
  return `<figure class="q ${q.tier}">
      <blockquote>&ldquo;${esc(q.q)}&rdquo;</blockquote>
      <figcaption><span class="tier ${q.tier}">${tierLabel(q.tier)}</span></figcaption>
    </figure>`;
}

// A label is stored as a key and read as words, so 'ambiguous_reuse' is the
// answer recorded, ambiguous reuse the answer counted and Ambiguous Reuse the
// answer offered.
const callWords = label => label.replace('_', ' ');
const callName = label => callWords(label).split(' ')
  .map(word => word[0].toUpperCase() + word.slice(1)).join(' ');

// A pair is offered the labels of its own pathway, so a direct pair can be
// called primary and an indirect one mention, side by side in the same list.
// An added pair is offered reuse alone and wears it already: putting the pair
// on the list is the call, so the button reports it rather than asking.
function callButtons(r){
  const settled = isAdded(r);
  return LABELS[r.pathway].map(label =>
    `<button class="call ${label}" data-v="${label}"
       aria-pressed="${callFor(r) === label}"
       ${settled ? 'disabled title="Added pairs are reuse"' : ''}
       >${callName(label)}</button>`).join('');
}

function renderProgress(rows, scoped){
  const done = scoped.filter(callFor).length;
  document.getElementById('position').textContent =
    rows.length ? (view === 'overview' ? `${rows.length} pairs`
                                       : `Pair ${index + 1} of ${rows.length}`)
                : 'No pairs';
  document.getElementById('progress').textContent =
    `${done} of ${scoped.length} reviewed`;
  document.getElementById('bar').style.width =
    (scoped.length ? 100 * done / scoped.length : 0) + '%';
}

// The controls say what the session is looking at, so what they say has to keep
// up with it: a label no pathway in view can produce is not a filter worth
// offering, and stepping through pairs is not what the overview does.
function syncControls(rows){
  // Every chip reads off what the session is looking at, so a control moved by
  // opening a paper leaves the toolbar saying where that put you.
  document.querySelectorAll('.btn[data-control]').forEach(b =>
    b.setAttribute('aria-pressed',
                   String(controls[b.dataset.control] === b.dataset.value)));
  // Offered only while there is somewhere to go back to, which is only after
  // the reviewer left a pair to go and add a dandiset to its paper.
  document.getElementById('back').hidden = !returnTo;
  document.querySelectorAll('.step').forEach(b =>
    b.hidden = view === 'overview');
  document.querySelector('.grouping').hidden = view === 'worksheet';
  document.getElementById('undo').disabled = !undoStack.length;
  const fold = document.getElementById('foldall');
  fold.hidden = view === 'worksheet' || controls.grouping === 'none';
  if (!fold.hidden)
    fold.textContent = groupsOf(rows).some(g => !shut.has(shutKey(g.id)))
      ? 'Collapse All' : 'Expand All';
  document.querySelectorAll('[data-control="filter"][data-pathways]')
    .forEach(b => b.hidden = controls.pathway !== 'all'
                             && !b.dataset.pathways.split(' ')
                                  .includes(controls.pathway));
  // Which pathway a pair came down decides what it can be called, so on the
  // worksheet the chip says which one is on screen. In the overview each entry
  // carries its own.
  const chip = document.getElementById('pathwaychip');
  const r = rows[index];
  chip.textContent = view === 'worksheet' && r ? r.pathway : '';
  chip.hidden = !chip.textContent;
}

function render(){
  const scoped = inScope();
  const rows = visible();
  index = Math.min(index, Math.max(rows.length - 1, 0));
  renderProgress(rows, scoped);
  syncControls(rows);
  if (view === 'overview') renderOverview(rows); else renderWorksheet(rows);
}

// An empty list says which control emptied it, which is not always the one you
// last touched: a reviewer dealt one pathway has nothing on the other, and a
// call chip hides itself when the pathway that can produce it goes out of view.
function emptyMessage(){
  const pathway = controls.pathway === 'all' ? '' : controls.pathway + ' ';
  if (!inScope().length){
    if (controls.search.trim()) return 'Nothing matches that search.';
    if (controls.scope === 'mine')
      return `No ${pathway}pairs are assigned to you \\u2014 try All Candidates.`;
    return `No ${pathway}pairs here.`;
  }
  if (controls.filter === 'todo') return 'Every pair has an answer.';
  if (controls.filter === 'done') return 'Nothing answered yet.';
  return `Nothing called ${callWords(controls.filter)}.`;
}

function renderWorksheet(rows){
  const r = rows[index];
  if (!r){
    document.getElementById('card').innerHTML =
      `<p class="empty">${emptyMessage()}</p>`;
    return;
  }

  const quotes = r.quotes.length ? r.quotes.map(quoteBlock).join('')
    : `<figure class="q"><blockquote><em>No quote returned.</em></blockquote></figure>`;
  // Where discovery held no pairing, the panel shows the dataset's own declared
  // paper, and says so rather than claiming this paper cited it.
  const citedRole = r.cited_role === 'Cited' ? 'Cited Paper' : 'Dataset Paper';
  // A pair the reviewer added has a classifier record nowhere to be read, so
  // the box that holds one says where the call has to come from.
  const evidence = isAdded(r)
    ? `<h4>Added By Hand</h4>
       <p class="reasoning">You put this pair on the list while reading the
         paper, so it carries no model reasoning and no quoted passage.
         Adding it is the reuse call; removing the pair takes that back.</p>`
    : `${sharedBlock(r)}
       <h4>Model Reasoning</h4>
       <p class="reasoning">${esc(r.reasoning)}</p>
       <h4>Quoted Evidence</h4>
       ${TIER_KEY}
       ${quotes}`;

  document.getElementById('card').innerHTML = `
    <div class="subject ${r.pathway}">
      ${paperPanel('Citing Paper', r.fetched_doi, r.title, '',
                   r.has_text ? textLink(r.fetched_doi, r.dandiset) : '')}
      ${r.pathway === 'indirect'
        ? paperPanel(citedRole, r.cited_doi, r.cited_title, r.cited_citation,
                     r.cited_has_text ? textLink(r.cited_doi, '') : '',
                     originChip(r.cited_source)) : ''}
      ${datasetPanel(r)}
    </div>

    <div class="decide ${r.pathway}">
      <div class="choices">
        <div class="calls">${callButtons(r)}</div>
        <div class="actions">
          ${isAdded(r)
            ? `<button class="btn drop" data-drop="${esc(keyOf(r))}"
                 >\\u00d7 Remove Pair</button>` : ''}
          <button class="btn addcited" id="addcited">+ Add Reused Dandiset</button>
        </div>
      </div>
      <textarea class="note" placeholder="Why \\u2014 optional"
        >${esc(noteFor(r))}</textarea>
    </div>

    <div class="evidence">
      <div class="inner">${evidence}</div>
    </div>`;
}

// Groups folded shut, so that one dataset or one paper can be read with the
// rest out of the way. Keyed by the grouping they were shut under, since the
// datasets and the papers gather the same pairs into different groups.
const shut = new Set();
const shutKey = id => controls.grouping + '\\t' + id;

// Which pairs stand together. A dandiset's pairs are spread over the papers that
// used it and a paper's over the datasets it touched, so the two groupings are
// the same question asked from either end of the pair.
// The pairs a round dealt out stand above the ones nobody was asked about, so a
// group opens on the work there is to do and the rest is context under it. The
// sort is stable, so each band keeps the paper-then-dataset order it arrived in.
const roundFirst = rows => [...rows].sort((a, b) => unasked(a) - unasked(b));

function groupsOf(rows){
  if (controls.grouping === 'none')
    return [{id: '', name: '', rows: roundFirst(rows)}];
  const byPaper = controls.grouping === 'paper';
  const groups = new Map();
  for (const r of rows){
    const id = byPaper ? r.doi : r.dandiset;
    if (!groups.has(id))
      groups.set(id, {id, name: byPaper ? r.title : r.dandiset_name, rows: []});
    groups.get(id).rows.push(r);
  }
  for (const group of groups.values()) group.rows = roundFirst(group.rows);
  const order = byPaper ? (a, b) => a.name.toLowerCase() < b.name.toLowerCase() ? -1 : 1
                        : (a, b) => a.id < b.id ? -1 : 1;
  return [...groups.values()].sort(order);
}

// What a group came to, which is the thing a second pass is reading for.
function tally(rows){
  const counts = {};
  for (const r of rows){
    const call = callFor(r) || 'left';
    counts[call] = (counts[call] || 0) + 1;
  }
  const named = Object.keys(counts).filter(c => c !== 'left').sort()
    .map(c => `${counts[c]} ${callWords(c)}`);
  if (counts.left) named.push(`${counts.left} left`);
  return `${rows.length} pair${rows.length === 1 ? '' : 's'}`
         + (named.length ? ' \\u00b7 ' + named.join(' \\u00b7 ') : '');
}

// An entry shows the other side of the grouping: under a dataset you are
// reading papers, under a paper you are reading datasets. Nothing in it is a
// link, because the whole row is already the way into the pair -- the links are
// on the card a click away.
function entryRow(r){
  const byPaper = controls.grouping === 'paper';
  const what = byPaper
    ? `<div class="line"><span class="dsid small">${esc(r.dandiset)}</span>
         ${esc(r.dandiset_name)}</div>`
    : `<div class="line">${esc(r.title || r.fetched_doi)}</div>`;
  // A row the reviewer put here says so whatever the pathway filter is set to,
  // and is the only kind that can be taken back off the list.
  const drop = isAdded(r)
    ? `<button class="drop" data-drop="${esc(keyOf(r))}" title="Remove this pair"
         aria-label="Remove ${esc(r.dandiset)} from this paper">\\u00d7</button>`
    : '';
  // The group heading already carries whichever side the pairs were gathered on,
  // so the entry says the other one and does not repeat it.
  return `<div class="entry" data-key="${esc(keyOf(r))}">
      <div class="what">
        ${what}
        <div class="line sub">
          ${byPaper ? '' : `<span class="doitext">${esc(r.fetched_doi)}</span>`}
          ${controls.grouping === 'none'
            ? `<span class="dsid small">${esc(r.dandiset)}</span>` : ''}
          ${controls.pathway === 'all' || isAdded(r)
            ? `<span class="mode quiet ${esc(r.pathway)}"
                 >${esc(r.pathway)}</span>` : ''}
          ${unasked(r)
            ? `<span class="mode quiet outside">not in round</span>` : ''}
        </div>
        <textarea class="note" rows="1"
          placeholder="Note \\u2014 optional">${esc(noteFor(r))}</textarea>
      </div>
      <div class="rowcalls">${callButtons(r)}${drop}</div>
    </div>`;
}

// The box that puts a dataset on a paper the pipeline paired it with nothing
// for. It belongs under that paper's datasets, which is the only grouping where
// the rows are datasets and the heading is the paper they would join.
function addBox(doi){
  if (controls.grouping !== 'paper') return '';
  return `<form class="addbox" data-doi="${esc(doi)}">
      <span class="what">Add a dandiset this paper reused</span>
      <input class="dsinput" inputmode="numeric" autocomplete="off"
             placeholder="000001" aria-label="Dandiset identifier">
      <button class="btn" type="submit">Look Up &amp; Add</button>
      <span class="addstate" role="status"></span>
    </form>`;
}

// A note sizes to what it holds, so a row grows only for the ones that have
// something to say. An empty box is already one line and is left unmeasured:
// reading a height forces layout, and most rows carry no note.
function autosize(box){
  box.style.height = 'auto';
  box.style.height = box.scrollHeight + 'px';
}

function renderOverview(rows){
  const card = document.getElementById('card');
  if (!rows.length){
    card.innerHTML = `<p class="empty">${emptyMessage()}</p>`;
    return;
  }
  // Calling a pair from the list redraws it, and a list that jumped back to the
  // top on every call could not be worked down.
  const held = card.querySelector('.overview');
  const scrollTop = held ? held.scrollTop : 0;
  card.innerHTML = `<div class="overview">${groupsOf(rows).map(group => {
    const folded = shut.has(shutKey(group.id));
    return `<section class="group${folded ? ' shut' : ''}">
      ${group.id ? `<h3 data-group="${esc(group.id)}" aria-expanded="${!folded}">
        <span class="caret">\\u25be</span>
        <span class="groupid">${esc(group.id)}</span>
        <span class="groupname">${esc(group.name)}</span>
        <span class="tally">${tally(group.rows)}</span>
      </h3>` : ''}
      ${folded ? '' : group.rows.map(entryRow).join('') + addBox(group.id)}
    </section>`;
  }).join('')}</div>`;
  card.querySelector('.overview').scrollTop = scrollTop;
  for (const box of card.querySelectorAll('.entry textarea.note'))
    if (box.value) autosize(box);
  restoreAddState(card);
}

// What the add box last said and whether it is owed the cursor. Adding a pair
// redraws the whole list, so both have to outlive the redraw that the add
// itself causes, and adding one more is then a matter of typing.
let addState = {doi: '', message: '', tone: '', focus: false};

function restoreAddState(card){
  if (!addState.doi) return;
  const box = card.querySelector(
    `.addbox[data-doi="${CSS.escape(addState.doi)}"]`);
  if (!box) return;
  const state = box.querySelector('.addstate');
  state.textContent = addState.message;
  state.className = 'addstate ' + (addState.tone || '');
  if (addState.focus){
    box.querySelector('.dsinput').focus();
    addState.focus = false;
  }
}

// One press to put every group away, the next to bring them all back, so that
// reading one group on its own does not start with shutting forty.
function foldAll(){
  const groups = groupsOf(visible());
  const shutting = groups.some(g => !shut.has(shutKey(g.id)));
  for (const group of groups)
    if (shutting) shut.add(shutKey(group.id)); else shut.delete(shutKey(group.id));
  render();
}

function go(next){
  index = Math.min(Math.max(next, 0), Math.max(visible().length - 1, 0));
  render();
}

// A pair clicked in the overview came out of visible(), so it is already in the
// list the worksheet steps through; opening it is finding where it stands.
function openPair(r){
  setView('worksheet');
  index = visible().indexOf(r);
  render();
}

// Where the reviewer stood when they left to add a dandiset: the pair in front
// of them and every control that was narrowing the list. Going to a paper moves
// several of those at once, and this is what puts them all back.
let returnTo = null;

// One paper on its own: every dataset it was paired with, what each was called,
// and the box that adds one more. Search is what narrows the list, so the
// toolbar says why it is short and clearing the box gives the overview back.
// Scope widens to the whole candidate list, because the question here is what
// the paper cited, which its datasets answer whether or not a round took them.
// The ones no round took wear a chip saying so. Pathway widens to all of them
// for the same reason, and because a pair added here comes down the added
// pathway and has to stay on screen once it is.
function openPaper(doi){
  returnTo = {view, row: visible()[index], scope: controls.scope,
              grouping: controls.grouping, filter: controls.filter,
              pathway: controls.pathway, search: controls.search};
  setView('overview');
  controls.grouping = 'paper';
  controls.filter = 'all';
  controls.pathway = 'all';
  controls.scope = 'candidates';
  controls.search = doi;
  document.getElementById('search').value = doi;
  shut.delete(shutKey(doi));
  addState = {doi, message: '', tone: '', focus: true};
  render();
}

// Back to the pair the reviewer left, with the scope, the grouping, the filter
// and the pathway they left it under. The pair is found again rather than
// stepped back to, since adding one moves everything after it along by a place.
function goBack(){
  if (!returnTo) return;
  const back = returnTo;
  returnTo = null;
  setView(back.view);
  controls.scope = back.scope;
  controls.grouping = back.grouping;
  controls.filter = back.filter;
  controls.pathway = back.pathway;
  controls.search = back.search;
  document.getElementById('search').value = back.search;
  index = Math.max(visible().indexOf(back.row), 0);
  render();
}

// A pair the reviewer put on the list is theirs to take off it, which is what a
// mistyped identifier needs. The note is the part they wrote by hand, and undo
// does not reach it, so a pair carrying one asks before it goes.
function dropPair(r){
  if (noteFor(r) && !confirm(
      `Remove ${r.dandiset} from this paper? The note on it goes too.`))
    return;
  ROWS.splice(ROWS.indexOf(r), 1);
  ROW_BY_KEY.delete(keyOf(r));
  for (let i = undoStack.length - 1; i >= 0; i--)
    if (undoStack[i].row === r) undoStack.splice(i, 1);
  for (const held of [added, reviews]){
    delete (held[r.doi] || {})[r.dandiset];
    if (held[r.doi] && !Object.keys(held[r.doi]).length) delete held[r.doi];
  }
  saveNow(false);
  render();
}

// Adding a pair asks DANDI what the dataset is called: six digits are not
// something a call can be made against, and an identifier the archive does not
// know is a typo the reviewer can still see the source of.
function addDandiset(form){
  const doi = form.dataset.doi;
  const typed = form.querySelector('.dsinput').value;
  const state = form.querySelector('.addstate');
  const say = (message, tone) => {
    state.textContent = message;
    state.className = 'addstate ' + (tone || '');
  };
  say('Looking up\\u2026', '');
  fetch('/dandiset?id=' + encodeURIComponent(typed))
    .then(response => response.json().then(body => {
      if (!response.ok) throw new Error(body.error || 'DANDI lookup failed.');
      return body;
    }))
    .then(found => {
      if (ROW_BY_KEY.has(doi + '\\t' + found.dandiset))
        throw new Error(found.dandiset + ' is already on this paper.');
      const row = enrol(doi, found.dandiset, found.name);
      (added[doi] || (added[doi] = {}))[found.dandiset] =
        {dandiset_name: row.dandiset_name};
      saveNow(false);
      // The typed identifier goes with the redraw, which is what leaves the box
      // ready for the next one.
      addState = {doi, focus: true, tone: 'ok',
                  message: `Added ${found.dandiset} \\u2014 ${row.dandiset_name}`};
      render();
    })
    // Said on the box the reviewer is standing at, which leaves what they typed
    // in front of them, so a corrected digit is all it takes.
    .catch(failure => say(failure.message, 'bad'));
}

// Answering advances, but only on the worksheet: in the overview the list is
// what you are reading and the next pair is already on screen. Under a filter
// the answered pair drops out of the list and the next one slides into its
// place, so holding position is the advance.
function mark(r, value){
  // An added pair holds the call that put it there, and it stands as long as
  // the pair does. Removing the pair is how that call is taken back.
  if (isAdded(r)) return;
  undoStack.push({row: r, call: callFor(r), view, index});
  record(r, 'call', callFor(r) === value ? '' : value);
  if (view === 'worksheet'){
    const after = visible();
    if (after[index] === r && callFor(r) && index < after.length - 1) index++;
  }
  render();
}

// Puts the last call back and returns to where it was made, since the pair may
// have left the list on being answered and the list has moved on since.
function undo(){
  const last = undoStack.pop();
  if (!last) return;
  record(last.row, 'call', last.call);
  setView(last.view);
  index = Math.min(last.index, Math.max(visible().length - 1, 0));
  render();
}

document.getElementById('card').addEventListener('click', e => {
  if (e.target.id === 'addcited'){
    openPaper(visible()[index].doi);
    return;
  }
  // A heading is the handle its group is folded by; a row is the way into its
  // pair.
  const heading = e.target.closest('.group h3');
  if (heading){
    const key = shutKey(heading.dataset.group);
    if (!shut.delete(key)) shut.add(key);
    render();
    return;
  }
  const remove = e.target.closest('button[data-drop]');
  if (remove){
    dropPair(ROW_BY_KEY.get(remove.dataset.drop));
    return;
  }
  const entry = e.target.closest('.entry');
  const row = entry ? ROW_BY_KEY.get(entry.dataset.key) : visible()[index];
  const button = e.target.closest('button[data-v]');
  if (button) mark(row, button.dataset.v);
  // The row is the way into its pair, except where the click landed on
  // something of its own: writing a note there is not asking to leave.
  else if (entry && !e.target.closest('textarea, input, button')) openPair(row);
});

document.getElementById('card').addEventListener('submit', e => {
  const form = e.target.closest('.addbox');
  if (!form) return;
  e.preventDefault();
  addDandiset(form);
});

// A note is held as it is typed, in the row it sits in, but the card is not
// redrawn: that would take the cursor out of the box mid-word.
document.getElementById('card').addEventListener('input', e => {
  const box = e.target.closest('textarea.note');
  if (!box) return;
  const entry = box.closest('.entry');
  record(entry ? ROW_BY_KEY.get(entry.dataset.key) : visible()[index],
         'note', box.value.trim() ? box.value : '');
  if (entry) autosize(box);
});

document.getElementById('back').addEventListener('click', goBack);
document.getElementById('save').addEventListener('click', () => saveNow(true));
document.getElementById('undo').addEventListener('click', undo);
document.getElementById('foldall').addEventListener('click', foldAll);
document.getElementById('prev').addEventListener('click', () => go(index - 1));
document.getElementById('next').addEventListener('click', () => go(index + 1));

document.querySelectorAll('.filters:not(.views)').forEach(group => {
  group.addEventListener('click', e => {
    const button = e.target.closest('.btn');
    if (!button) return;
    controls[button.dataset.control] = button.dataset.value;
    index = 0;
    render();
  });
});

// Switching views holds your place, so that reading a pair in the list and
// going to the card and back is one movement rather than a restart.
document.querySelector('.views').addEventListener('click', e => {
  const button = e.target.closest('.btn');
  if (!button) return;
  setView(button.dataset.view);
  render();
});

document.addEventListener('keydown', e => {
  if (e.key !== 'z' || !(e.metaKey || e.ctrlKey)) return;
  if (e.target.closest('textarea, input')) return;
  e.preventDefault();
  undo();
});

document.getElementById('search').addEventListener('input', e => {
  controls.search = e.target.value;
  index = 0;
  render();
});

fetch('/load')
  .then(r => r.json())
  .then(data => {
    reviews = data.reviews || {};
    added = data.added || {};
    for (const [doi, datasets] of Object.entries(added))
      for (const [dandiset, held] of Object.entries(datasets))
        enrol(doi, dandiset, held.dandiset_name);
    render();
  })
  .catch(e => { setSaveState('Load failed \\u2014 ' + e.message, 'bad'); render(); });
"""


# The labels a reviewer picks from, which are the ones the classifier chose
# between in that pathway. Both sides naming a class is what a confusion matrix
# is built from, so offering a label the classifier could not have produced puts
# the answer off the matrix: only the direct pathway can say a paper is the one
# that deposited the dataset, and only the indirect pathway distinguishes a
# mention from a bare citation. 'unsure' and 'ambiguous_reuse' are the
# reviewer's alone.
#
# A session holds both pathways, so the page picks the list by the pathway of
# the pair in front of it rather than by anything about the session.
CLASSIFIER_LABELS = {
    'direct': ['reuse', 'ambiguous_reuse', 'primary', 'neither', 'unsure'],
    'indirect': ['reuse', 'ambiguous_reuse', 'mention', 'neither', 'unsure'],
}

# Every label any pair can be given, for the filter that asks for one. Taken a
# position at a time across the pathways rather than a list at a time, so that
# the two labels only one pathway can produce stand together where those
# pathways put them, between the reuse they qualify and the answers that deny
# it. Reading them off CLASSIFIER_LABELS at all is what keeps a label added to a
# pathway from being one you then cannot go looking for.
ALL_LABELS = [label for label in dict.fromkeys(
    label for column in zip_longest(*CLASSIFIER_LABELS.values()) for label in column)
    if label]

# A pair a reviewer found by reading the paper. Adding one is the reviewer
# saying the paper reused that dataset, so reuse is the call it carries and the
# only one it offers.
LABELS = {**CLASSIFIER_LABELS, 'added': ['reuse']}


def call_filters() -> str:
    """The chips that ask for one call, coloured as that call is everywhere."""
    return ''.join(
        f'\n    <button class="btn call {label}" data-control="filter" '
        f'data-value="{label}" aria-pressed="false" data-pathways='
        f'"{" ".join(p for p in LABELS if label in LABELS[p])}"'
        f'>{label.replace("_", " ").title()}</button>'
        for label in ALL_LABELS)


def build(rows: list[dict], reviewer: str,
          mine: list[tuple[str, str]] | None = None,
          dealt: set[tuple[str, str]] | None = None) -> str:
    """
    Render one reviewer's session over every candidate there is.

    Both pathways are on board and the session opens on both. `mine` is the
    subset assigned to this reviewer, which the page filters down to rather than
    being built from, so stepping outside your own queue and back costs nothing:
    a second pass over your own answers is exactly the time you need to see a
    pair that was never dealt to you.

    `dealt` is every pair any round reached, which is what marks the rest as
    pairs nobody was asked about. None leaves them unmarked: a session with no
    queues to read has nothing to say about which pairs a round took, and
    guessing would put the mark on all of them.
    """
    payload = json.dumps(rows, ensure_ascii=False).replace('</', r'<\/')
    n = len(rows)
    scope_buttons = '' if mine is None else """
  <div class="filters" role="group" aria-label="Whose">
    <button class="btn" data-control="scope" data-value="mine"
            aria-pressed="true">Assigned Only</button>
    <button class="btn" data-control="scope" data-value="candidates"
            aria-pressed="false">All Candidates</button>
  </div>"""
    mine_js = ('null' if mine is None else
               'new Set(%s)' % json.dumps([f'{doi}\t{dandiset}'
                                           for doi, dandiset in mine]))
    dealt_js = ('null' if dealt is None else
                'new Set(%s)' % json.dumps(sorted(f'{doi}\t{dandiset}'
                                                  for doi, dandiset in dealt)))
    pathway_buttons = ''.join(
        f'\n    <button class="btn" data-control="pathway" data-value="{value}" '
        f'aria-pressed="{str(value == "all").lower()}">{name}</button>'
        for value, name in [('all', 'All'), ('indirect', 'Indirect'),
                            ('direct', 'Direct'), ('added', 'Added')])
    return f"""<title>DANDI reuse review &mdash; {n} pairs</title>
<style>{CSS}</style>

<div class="toolbar">
  <button class="btn back" id="back" hidden>&larr; Return to Worksheet</button>
  <span class="who">Reviewing as <b>{reviewer}</b></span>
  <span class="mode" id="pathwaychip"></span>
  <div class="filters views" role="group" aria-label="View">
    <button class="btn" data-view="worksheet" aria-pressed="true">Worksheet</button>
    <button class="btn" data-view="overview" aria-pressed="false">Overview</button>
  </div>
  <button class="btn step" id="prev">&larr; Prev</button>
  <button class="btn step" id="next">Next &rarr;</button>
  <span class="readout" id="position">Pair 1 of {n}</span>
  <div class="spacer"></div>
  <div class="bar"><i id="bar"></i></div>
  <span class="readout" id="progress">0 of {n} reviewed</span>
  <button class="btn" id="undo" disabled>&#8630; Undo</button>
  <button class="btn" id="save">Save</button>
  <span class="savestate" id="savestate"></span>
</div>

<div class="toolbar controls">
  <div class="filters" role="group" aria-label="Pathway">{pathway_buttons}
  </div>
  <div class="filters" role="group" aria-label="Call">
    <button class="btn" data-control="filter" data-value="all"
            aria-pressed="false">All</button>
    <button class="btn" data-control="filter" data-value="todo"
            aria-pressed="true">Unreviewed</button>
    <button class="btn" data-control="filter" data-value="done"
            aria-pressed="false">Reviewed</button>
    <span class="sep"></span>{call_filters()}
  </div>{scope_buttons}
  <div class="filters grouping" role="group" aria-label="Grouping">
    <button class="btn" data-control="grouping" data-value="dandiset"
            aria-pressed="true">By Dandiset</button>
    <button class="btn" data-control="grouping" data-value="paper"
            aria-pressed="false">By Citing Paper</button>
    <button class="btn" data-control="grouping" data-value="none"
            aria-pressed="false">Flat</button>
  </div>
  <button class="btn fold" id="foldall" hidden>Collapse All</button>
  <input class="search" id="search" type="search" autocomplete="off"
         placeholder="Search &mdash; commas for any">
</div>

<div class="card" id="card"></div>

<script>
const ROWS = {payload};
const REVIEWER = {json.dumps(reviewer)};
const LABELS = {json.dumps(LABELS)};
const MINE = {mine_js};
const DEALT = {dealt_js};
{JS}
</script>
"""


TEXT_CSS = PALETTE + """
  *{box-sizing:border-box}
  body{margin:0;background:var(--ground);color:var(--ink);font-family:var(--sans);
       font-size:15px;line-height:1.5;-webkit-font-smoothing:antialiased}
  .head{position:sticky;top:0;z-index:1;display:flex;flex-wrap:wrap;align-items:baseline;
        gap:6px 16px;padding:11px clamp(12px,2vw,26px);background:var(--surface);
        border-bottom:1px solid var(--line)}
  .head a{font-family:var(--mono);font-size:12.5px;color:var(--accent);
          text-decoration:none;word-break:break-all}
  .head a:hover{text-decoration:underline}
  .head span{font-size:12px;color:var(--muted)}
  /* Fetched text is one long flow with the source's own line breaks kept, so it
     is set as a reading column rather than reflowed into paragraphs. */
  article{max-width:82ch;margin:0 auto;padding:26px clamp(12px,2vw,26px) 90px;
          font-family:var(--serif);font-size:16.5px;line-height:1.62;
          white-space:pre-wrap;overflow-wrap:break-word}
  mark{background:var(--warn-soft);color:inherit;
       box-shadow:inset 0 -2px 0 var(--warn);padding:1px 0}
  .absent{max-width:82ch;margin:0 auto;padding:40px clamp(12px,2vw,26px);
          color:var(--muted);font-size:14px}
"""


@lru_cache(maxsize=None)
def text_cache(cache_dir: Path) -> TextCache:
    """
    The paper text cache the classification run filled, read without expiry.

    Its entries are what the classifier was given, so they are what a reviewer
    checking a quote should be reading. Expiry exists to make the fetcher try a
    paper again; here it would only take a paper away.
    """
    return TextCache(cache_dir, metadata_ttl_days=None)


def paper_text(cache_dir: Path, doi: str) -> tuple[str, str]:
    """The cached text of a paper and the source it was fetched from."""
    cached = text_cache(cache_dir).get(doi)
    return (cached[0], cached[1]) if cached else ('', '')


def mark_quotes(text: str, quotes: list[str]) -> str:
    """
    The paper's text as HTML, with each quoted passage marked where it stands.

    A quote the classifier could only match after folding case, spacing or
    punctuation is not here character for character, and is left unmarked rather
    than approximated; the tier beside it on the card already says so.
    """
    spans = []
    for quote in quotes:
        start = text.find(quote) if quote else -1
        if start >= 0:
            spans.append((start, start + len(quote)))
    spans.sort()

    marked, cursor = [], 0
    for start, end in spans:
        if start < cursor:
            continue
        marked.append(html.escape(text[cursor:start]))
        marked.append(f'<mark>{html.escape(text[start:end])}</mark>')
        cursor = end
    marked.append(html.escape(text[cursor:]))
    return ''.join(marked)


def text_page(doi: str, text: str, source: str, quotes: list[str]) -> str:
    """
    Render one paper's fetched text, for when the DOI leads to a paywall.

    This is the text the classification was made from, not the published
    article: it carries the export's own mangling of citations, captions and
    line numbers, which is why a quote can be sound and still not match.
    """
    body = (f'<article>{mark_quotes(text, quotes)}</article>' if text else
            '<p class="absent">No text for this paper is in the cache.</p>')
    return f"""<title>Fetched text &mdash; {html.escape(doi)}</title>
<style>{TEXT_CSS}</style>

<div class="head">
  <a href="https://doi.org/{html.escape(doi)}" target="_blank"
     rel="noopener">{html.escape(doi)}</a>
  <span>Text as fetched for classification{f' &middot; {html.escape(source)}'
                                           if source else ''}</span>
</div>

{body}

<script>
// Land on the quoted passage rather than the top of a 100,000-character paper.
document.querySelector('mark')?.scrollIntoView({{block: 'center'}});
</script>
"""


def attach_paper_texts(rows: list[dict], cache_dir: Path) -> None:
    """
    Say which papers the fetched text is on hand for.

    A DOI resolves to the publisher, and behind a paywall that is where a
    reviewer stops. The text the classifier was given is already on disk, so the
    card offers it for the papers it covers, and says nothing for the rest.

    The cache is keyed by `fetched_doi`, the version the text came from. `doi` is
    collapsed onto the work a preprint's versions share, and Research Square
    mints nothing under that collapsed form.
    """
    cache = text_cache(cache_dir)
    for row in rows:
        row['has_text'] = bool(cache.get(row['fetched_doi']))
        if 'cited_doi' in row:
            row['cited_has_text'] = bool(row['cited_doi']
                                         and cache.get(row['cited_doi']))


def quotes_by_pair(rows: list[dict]) -> dict:
    """
    The passages to mark in a paper's text, for each pair asked about it.

    Keyed by the DOI the card asks for the text by, which is the one it was
    fetched under.
    """
    return {(row['fetched_doi'], row['dandiset']): [q['q'] for q in row['quotes']]
            for row in rows}


# What the add box takes: a dandiset identifier, which is six digits.
DANDISET_ID = re.compile(r'^\d{6}$')


def dandiset_id(typed: str) -> str:
    """
    The dandiset an entry names, or nothing where it names none.

    Space around it is dropped, since an identifier is usually pasted.
    """
    text = (typed or '').strip()
    return text if DANDISET_ID.match(text) else ''


def fetch_dandiset(identifier: str, api_base: str = DANDI_API) -> dict:
    """
    What DANDI calls this dandiset, or why it could not say.

    The published version's name is the one the archive shows for a dandiset
    that has one, and a dandiset still in draft has only the draft's. An
    identifier DANDI does not know comes back as an error, so that a typo reads
    as a typo rather than as a dataset with no name.
    """
    response = requests.get(f'{api_base}/dandisets/{identifier}/', timeout=15)
    if response.status_code == 404:
        return {'error': f'DANDI has no dandiset {identifier}.'}
    response.raise_for_status()
    versions = response.json()
    name = next((name for name in
                 ((versions.get(version) or {}).get('name')
                  for version in ('most_recent_published_version', 'draft_version'))
                 if name), '')
    return {'dandiset': identifier, 'name': name}


def make_handler(page: str, reviewer: str, save_path: Path,
                 paper_cache: Path = PAPER_CACHE, quotes: dict | None = None,
                 dandi_api: str = DANDI_API):
    """A request handler bound to one reviewer's page and answer file."""
    quotes = quotes or {}

    class ReviewHandler(BaseHTTPRequestHandler):
        def _send(self, status: int, body: bytes, content_type: str):
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_json(self, status: int, body: dict):
            self._send(status, json.dumps(body).encode(), 'application/json')

        def _lookup_dandiset(self, typed: str):
            """
            Name the dandiset a reviewer is adding, so the pair carries what a
            call is made against rather than six digits.

            An identifier that names nothing and an archive that does not answer
            are both reported as what they are: the first is a typo to correct,
            the second a reason to try again.
            """
            identifier = dandiset_id(typed)
            if not identifier:
                self._send_json(400, {
                    'error': f'{typed.strip() or "That"} is not a dandiset '
                             f'identifier; a dandiset is six digits.'})
                return
            try:
                found = fetch_dandiset(identifier, dandi_api)
            except requests.RequestException as failure:
                self._send_json(502, {
                    'error': f'DANDI did not answer for {identifier}: {failure}'})
                return
            self._send_json(404 if 'error' in found else 200, found)

        def do_GET(self):
            url = urlparse(self.path)
            if url.path == '/':
                self._send(200, page.encode(), 'text/html; charset=utf-8')
            elif url.path == '/load':
                saved = (json.loads(save_path.read_text()) if save_path.exists()
                         else {'reviewer': reviewer, 'reviews': {}})
                self._send(200, json.dumps(saved).encode(), 'application/json')
            elif url.path == '/text':
                query = parse_qs(url.query)
                doi = (query.get('doi') or [''])[0]
                dandiset = (query.get('dandiset') or [''])[0]
                text, source = paper_text(paper_cache, doi)
                page_text = text_page(doi, text, source,
                                      quotes.get((doi, dandiset), []))
                self._send(200, page_text.encode(), 'text/html; charset=utf-8')
            elif url.path == '/dandiset':
                self._lookup_dandiset(
                    (parse_qs(url.query).get('id') or [''])[0])
            else:
                self._send(404, b'not found', 'text/plain')

        def do_POST(self):
            if self.path != '/save':
                self._send(404, b'not found', 'text/plain')
                return
            length = int(self.headers.get('Content-Length', 0))
            incoming = json.loads(self.rfile.read(length))
            # Only the answers and the pairs the reviewer found, both nested
            # paper to dataset. Which model or prompt produced the
            # classification does not change what the right answer is, so it has
            # no place in the record of the answer.
            record = {
                'reviewer': reviewer,
                'reviews': incoming.get('reviews') or {},
            }
            # A reviewer who has added nothing gets a file that says nothing
            # about it, so the feature costs the committed reviews no diff.
            if incoming.get('added'):
                record['added'] = incoming['added']
            save_path.parent.mkdir(parents=True, exist_ok=True)
            save_path.write_text(
                json.dumps(record, indent=2, ensure_ascii=False) + '\n')
            self._send(200, b'{"ok":true}', 'application/json')

        def log_message(self, *args):
            """Quiet: autosave would otherwise print a line every few seconds."""

    return ReviewHandler


def serve(rows: list[dict], reviewer: str, port: int,
          base: Path = REUSE_CONFIRMATION_DIR, paper_cache: Path = PAPER_CACHE,
          open_browser: bool = True,
          mine: list[tuple[str, str]] | None = None,
          dandi_api: str = DANDI_API) -> None:
    if not rows:
        raise SystemExit('No candidates to review.')
    save_path = reviews_path(reviewer, base)
    handler = make_handler(build(rows, reviewer, mine, round_pairs(base)),
                           reviewer, save_path, paper_cache,
                           quotes_by_pair(rows), dandi_api)
    server = ThreadingHTTPServer(('127.0.0.1', port), handler)
    url = f'http://127.0.0.1:{server.server_address[1]}/'
    papers = len({r['doi'] for r in rows})
    counts = Counter(r['pathway'] for r in rows)
    breakdown = ', '.join(f'{counts[p]} {p}'
                          for p in CLASSIFIER_LABELS if counts[p])
    print(f'{len(rows)} pairs across {papers} papers — {breakdown}'
          + (f'; {len(mine)} assigned to you' if mine is not None else ''))
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


def all_pairs(candidates_path: Path = CANDIDATES_FILE) -> list[dict]:
    """
    Every candidate there is, both pathways together.

    A dandiset's pairs are split between the two pathways, so a session that
    loaded one of them could never show a dataset whole -- and sorting out what
    a dataset was used for is the question a second pass over the reviews is
    asking. What one reviewer was assigned narrows what is shown, not what was
    loaded, so stepping outside your own queue is a click rather than a restart,
    and a pair you reviewed before it was ever assigned to you is on screen
    either way.

    Sorting by paper then dataset stands a paper's direct pairs next to its
    indirect ones, so everything asked about one paper is passed through in one
    go.
    """
    pairs = json.loads(candidates_path.read_text())['pairs']
    pairs.sort(key=lambda r: (r['doi'], r['dandiset']))
    return pairs


def round_pairs(base: Path = REUSE_CONFIRMATION_DIR
                ) -> set[tuple[str, str]] | None:
    """
    Every pair a round has reached, dealt to somebody or already judged, or
    None where there are no queues and no reviews to read.

    A round is cut with filters, so most of the candidate list was never dealt
    to anyone. Those pairs stand in a paper's datasets beside the ones somebody
    owes an answer on, and a reviewer looking at the paper has no way to tell
    which is which. This is what the page marks them apart by.

    The reviews are read alongside the queues because reviewing a pair takes it
    out of the queue it came from: a pair somebody has answered is one the round
    reached, whatever is left in the queues.

    A tree holding neither says nothing about which pairs a round took, so the
    page is told nothing and marks no pair as one nobody was asked about.
    """
    if not assignment_paths(base) and not reviews_paths(base):
        return None
    pairs: set[tuple[str, str]] = set()
    for path in assignment_paths(base):
        assignment = json.loads(path.read_text())
        pairs |= {(doi, dandiset)
                  for doi, dandisets in assignment['pairs'].items()
                  for dandiset in dandisets}
    for path in reviews_paths(base):
        reviews = json.loads(path.read_text())['reviews']
        pairs |= {(doi, dandiset)
                  for doi, datasets in reviews.items()
                  for dandiset in datasets}
    return pairs


def read_assignment(assignment_path: Path) -> tuple[list[tuple[str, str]], str]:
    """One reviewer's queue: the pairs it names, and whose it is."""
    assignment = json.loads(assignment_path.read_text())
    pairs = [(doi, dandiset)
             for doi, dandisets in assignment['pairs'].items()
             for dandiset in dandisets]
    return pairs, assignment['reviewer']


def assignment_pairs(reviewer: str, base: Path = REUSE_CONFIRMATION_DIR
                     ) -> list[tuple[str, str]] | None:
    """
    The pairs dealt to this reviewer, across every queue they hold.

    Assignments are written one file per pathway and a session covers both, so
    both of the reviewer's own are opened, found where they are written rather
    than named. A reviewer a round dealt nothing in has no file for that pathway,
    which is a queue they are not in rather than a file that went missing. None
    means nothing was dealt, and the page then offers no filter for whose a pair
    is.
    """
    pairs = []
    for pathway in CLASSIFIER_LABELS:
        path = assignment_path(reviewer, pathway, base)
        if not path.exists():
            continue
        named, assigned_to = read_assignment(path)
        if assigned_to != reviewer:
            raise SystemExit(
                f'{path} is {assigned_to}\'s, not {reviewer}\'s.')
        pairs += named
    return pairs or None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reviewer', required=True,
                        help='Whose session this is; must be a registered '
                             'username, and names the file the reviews go to.')
    parser.add_argument('--paper-cache', default=str(PAPER_CACHE),
                        help='Fetched paper text, served for papers behind a paywall.')
    parser.add_argument('--port', type=int, default=8000)
    args = parser.parse_args()

    select_reviewers(load_reviewers(REVIEWERS_FILE), args.reviewer)

    mine = assignment_pairs(args.reviewer)
    rows = all_pairs()
    attach_paper_texts(rows, Path(args.paper_cache))
    serve(rows, args.reviewer, args.port,
          paper_cache=Path(args.paper_cache), mine=mine)



if __name__ == '__main__':
    main()
