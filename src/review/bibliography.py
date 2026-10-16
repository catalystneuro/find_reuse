"""
What a numbered citation in a quoted passage points at.

A quote the classifier pulled often ends in a bracketed number: "data were
downloaded from the CELLxGENE repository [28]". Whether reference 28 is the
dataset's paper is what the card is asking, and a number does not say. The entry
it stands for is looked up here so the card can show it under the quote.

Two places hold a paper's reference list. The text the classifier read comes
first, because the quote was taken from that text and its list is numbered the
way the quote is. Most fetched text carries no list in a form that parses, so
the list the publisher deposited with Crossref fills in for the rest, and each
entry says which of the two it came from.

Crossref's order is not always the article's. In one paper the deposited list
put a spike sorter at the number the article gives the dataset's paper, and in
others it runs one off. A deposited entry is shown only where the paper's text
has the same entry under the same number: its wording found in the text, with
that number the last one standing before it as a reference list numbers its
entries. The fetcher appends Crossref's own list to the text, so that copy is
set aside first, or every entry would confirm itself.

A preprint gets no entry from Crossref. Crossref holds the list of one posted
version, which need not be the version that was fetched: where a preprint's text
had its own list to compare against, Crossref named a different paper for about
four citations in ten. An eLife article gets none either, because the text
fetched for one is usually the reviewed preprint, which numbers its citations,
and the list deposited is the version of record's, which is alphabetical.

Reference lists are cached, since every session over the same candidates asks
for the same ones.
"""

from __future__ import annotations

import json
import re
import time
from bisect import bisect_left
from itertools import accumulate
from pathlib import Path
from urllib.parse import quote

import requests

from src.indirect_pipeline.citation_context import parse_numbered_bibliography
from src.review.paper_metadata import USER_AGENT

REPO = Path(__file__).resolve().parents[2]
REFERENCES_CACHE = REPO / '.paper_references_cache.json'

CROSSREF_WORKS = 'https://api.crossref.org/works/'
CROSSREF_INTERVAL = 0.2  # seconds between requests; Crossref refuses a faster run
ELIFE = '10.7554/'

# [28], [12, 14] and [3-7], with either kind of dash. Europe PMC sets the
# numbers off from the brackets, as in [ 12 , 14 ].
CITATION = re.compile(r'\[\s*(\d{1,3}(?:\s*[,\-–]\s*\d{1,3})*)\s*\]')

YEAR = re.compile(r'\b(?:19|20)\d{2}\b')

# A numbered run of text is taken for a reference list when this share of its
# entries carry a year. Numbered section headings parse the same way and carry
# none.
MIN_DATED = 0.7

# The link labels a rendered reference list trails after each entry.
LINK_LABELS = re.compile(
    r'(?:\s*(?:↵|OpenUrl|Cross[Rr]ef|PubMed|Google Scholar|Web of Science'
    r'|Abstract/FREE Full Text|FREE Full Text|Scopus \(\d+\)))+\s*$')

# Long enough for a consortium's author list to reach the title.
MAX_ENTRY = 700

WORD = re.compile(r'[^\W_]+')

# A deposited entry is placed in the text by the first run of this many of its
# words found there. The run is tried from each word of the entry in turn,
# because the article and Crossref often write the author list differently.
RUN = 6
# A run this short or shorter is common enough to be found by chance.
MIN_RUN_CHARS = 20

# A reference list's number for an entry: [12], 12. or 12 ahead of the first
# author's name. Years are four digits and are passed over.
ANCHOR = re.compile(r'(?:^|[\s\[(])\[?(\d{1,3})\]?\.?\s+(?=[A-ZÀ-ÖØ-Þ])')

# How far back from an entry's wording its number is looked for.
LOOKBACK = 600

# The fetcher writes each Crossref entry as its own paragraph led by [N].
APPENDED = re.compile(r'\s*\[\d+\] ')


def cited_numbers(passage: str) -> list[int]:
    """The reference numbers a passage cites in brackets, in the order it cites them."""
    numbers = []
    for citation in CITATION.finditer(passage):
        for part in citation.group(1).split(','):
            ends = [int(number) for number in re.findall(r'\d+', part)]
            numbers.extend(range(ends[0], ends[-1] + 1))
    return list(dict.fromkeys(numbers))


def tidy(entry: str) -> str:
    """One reference as a single line, without what the page hung on it."""
    # The last entry of a list runs on into whatever follows the list.
    entry = entry.replace('​', '').split('\n\n')[0]
    entry = LINK_LABELS.sub('', ' '.join(entry.split()))
    return entry if len(entry) <= MAX_ENTRY else entry[:MAX_ENTRY].rstrip() + '…'


def text_entries(text: str) -> dict[int, str]:
    """The paper's own numbered reference list, or nothing where its text has none."""
    entries = parse_numbered_bibliography(text)
    dated = sum(1 for entry in entries.values() if YEAR.search(entry[:500]))
    if not entries or dated < MIN_DATED * len(entries):
        return {}
    return {number: tidy(entry) for number, entry in entries.items()}


def crossref_entry(reference: dict) -> str:
    """One deposited reference, as the publisher wrote it or from what it itemized."""
    body = reference.get('unstructured') or '. '.join(
        str(piece) for piece in (
            reference.get('author'), reference.get('year'),
            reference.get('article-title') or reference.get('volume-title'),
            reference.get('journal-title')) if piece)
    doi = reference.get('DOI') or ''
    if doi and doi.lower() not in body.lower():
        body = f'{body} {doi}'
    return tidy(body)


def fetch(session: requests.Session, doi: str) -> list[str]:
    """The reference list Crossref holds for one published paper, in its order."""
    if doi.startswith(ELIFE):
        return []
    response = session.get(CROSSREF_WORKS + quote(doi, safe=''), timeout=30)
    if response.status_code == 404:
        return []
    response.raise_for_status()
    work = response.json()['message']
    if work.get('type') == 'posted-content':
        return []
    return [crossref_entry(reference) for reference in work.get('reference') or []]


def resolve(dois: set[str], cache_path: Path) -> dict[str, list[str]]:
    """
    Every paper's deposited reference list, fetching the ones not cached.

    A paper Crossref does not answer for is left out and asked about again next
    time, so a session can start without it.
    """
    cache = json.loads(cache_path.read_text()) if cache_path.exists() else {}
    missing = sorted(set(dois) - set(cache))
    if missing:
        session = requests.Session()
        session.headers['User-Agent'] = USER_AGENT
        try:
            for i, doi in enumerate(missing, 1):
                print(f'Fetching reference list {i}/{len(missing)}: {doi}')
                try:
                    cache[doi] = fetch(session, doi)
                except requests.RequestException as failure:
                    print(f'  Crossref did not answer: {failure}')
                time.sleep(CROSSREF_INTERVAL)
        finally:
            cache_path.write_text(json.dumps(cache, sort_keys=True,
                                             ensure_ascii=False) + '\n')
    return cache


def words(text: str) -> list[str]:
    return [word.lower() for word in WORD.findall(text)]


def own_text(text: str, source: str, deposited: list[str] = ()) -> str:
    """
    The paper's text without the copy of Crossref's list the fetcher appended.

    The fetcher's paragraphs are led by [N]. Texts fetched before it numbered
    them hold each entry as a paragraph of its own, which is set aside when its
    wording is a deposited entry's.
    """
    if 'crossref' not in source.split('+'):
        return text
    copies = {' '.join(words(entry)) for entry in deposited} - {''}
    return '\n\n'.join(
        paragraph for paragraph in text.split('\n\n')
        if not APPENDED.match(paragraph)
        and ' '.join(words(paragraph)) not in copies)


def placed(entries: list[str], text: str) -> dict[int, int]:
    """Where each entry's wording first appears in the text, by its number."""
    matches = list(WORD.finditer(text))
    lowered = [match.group().lower() for match in matches]
    joined = ' ' + ' '.join(lowered) + ' '
    starts = list(accumulate((len(word) + 1 for word in lowered), initial=1))
    found = {}
    for number, entry in enumerate(entries, 1):
        tokens = words(entry)
        for k in range(len(tokens) - RUN + 1):
            run = tokens[k:k + RUN]
            if sum(map(len, run)) <= MIN_RUN_CHARS:
                continue
            at = joined.find(' ' + ' '.join(run) + ' ')
            if at >= 0:
                found[number] = matches[bisect_left(starts, at + 1)].start()
                break
    return found


def checked(entries: list[str], text: str) -> list[str]:
    """
    The deposited entries the text numbers the same way, the rest left blank.

    Each entry's number is looked for between where the entry's wording starts
    and where the wording of the entry before it in the text does.
    """
    found = placed(entries, text)
    starts = sorted(found.values())
    agreed = set()
    for number, at in found.items():
        before = bisect_left(starts, at)
        floor = max(starts[before - 1] if before else 0, at - LOOKBACK)
        anchors = ANCHOR.findall(text[floor:at + 1])
        if anchors and int(anchors[-1]) == number:
            agreed.add(number)
    return [entry if number in agreed else ''
            for number, entry in enumerate(entries, 1)]


def lookup(numbers: list[int], own: dict[int, str],
           deposited: list[str]) -> list[dict]:
    """The entries behind the numbers a quote cites, for the ones that can be found."""
    found = []
    for number in numbers:
        if number in own:
            found.append({'n': number, 'entry': own[number], 'source': 'text'})
        elif 0 < number <= len(deposited) and deposited[number - 1]:
            found.append({'n': number, 'entry': deposited[number - 1],
                          'source': 'crossref'})
    return found
