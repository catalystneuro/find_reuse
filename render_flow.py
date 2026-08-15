"""Render the paper fetching flow diagram using Graphviz, with counts from results JSON.

Counts are keyed off the three-way retrieval status (full_text, metadata_only,
unavailable) rather than off "did we get any string at all". The distinction is
the point of the diagram: a CrossRef record or a publisher landing page can run
to tens of thousands of characters without containing any of the paper.
"""
import json
from collections import Counter
from pathlib import Path

import graphviz

from paper_text_fetcher import cache_filename, is_full_text, legacy_cache_filename

# Prefer the refreshed corpus when a search-and-fetch run has produced one.
CANDIDATES = [
    Path('output/all_dandiset_papers_refreshed.json'),
    Path('output/all_dandiset_papers.json'),
]
RESULTS_PATH = next((p for p in CANDIDATES if p.exists()), CANDIDATES[-1])
CACHE_DIR = Path('.paper_cache')

with open(RESULTS_PATH) as f:
    data = json.load(f)
print(f"Reading counts from {RESULTS_PATH}")

# Deduplicate by DOI
papers = {}
for ds in data['results']:
    for paper in ds.get('citing_papers', []):
        doi = paper.get('doi', '')
        if doi and doi not in papers:
            papers[doi] = paper


def status_of(doi, paper):
    """
    Retrieval status for a paper, recomputed from the cache when the corpus
    entry predates the status field.
    """
    if paper.get('text_status'):
        return paper['text_status']
    # Ask the library for the filename rather than rebuilding it here: the
    # scheme changed to percent-encoding, and hand-rolling the old one would
    # silently report every newer entry as unavailable.
    cache_path = CACHE_DIR / cache_filename(doi)
    if not cache_path.exists():
        cache_path = CACHE_DIR / legacy_cache_filename(doi)
    if not cache_path.exists():
        return 'unavailable'
    try:
        entry = json.load(open(cache_path))
    except Exception:
        return 'unavailable'
    if is_full_text(entry.get('text'), entry.get('source', '')):
        return 'full_text'
    return 'metadata_only' if entry.get('text') else 'unavailable'


def body_source(paper):
    """The source that supplied the article body, or None."""
    source = paper.get('text_source') or ''
    parts = source.split('+') if source else []
    for part in parts:
        if part != 'crossref':
            return part
    return None


statuses = {doi: status_of(doi, p) for doi, p in papers.items()}

total = len(papers)
preprints = {d: p for d, p in papers.items() if d.startswith('10.1101/')}
non_preprints = {d: p for d, p in papers.items() if not d.startswith('10.1101/')}

overall = Counter(statuses.values())
full_text_total = overall['full_text']
metadata_only_total = overall['metadata_only']
no_text_total = overall['unavailable']

# Which source supplied the body, among papers where we actually got one
source_wins = Counter()
for doi, p in papers.items():
    if statuses[doi] == 'full_text':
        source_wins[body_source(p) or 'unknown'] += 1

# Preprint path
pre_total = len(preprints)
pre_bio = sum(1 for d, p in preprints.items()
              if statuses[d] == 'full_text' and body_source(p) == 'playwright_biorxiv')
pre_epmc = sum(1 for d, p in preprints.items()
               if statuses[d] == 'full_text' and body_source(p) == 'europe_pmc')
pre_other = sum(1 for d in preprints if statuses[d] == 'full_text') - pre_bio - pre_epmc
pre_meta = sum(1 for d in preprints if statuses[d] == 'metadata_only')
pre_none = sum(1 for d in preprints if statuses[d] == 'unavailable')

# Non-preprint path
np_total = len(non_preprints)


def np_count(source):
    return sum(1 for d, p in non_preprints.items()
               if statuses[d] == 'full_text' and body_source(p) == source)


np_epmc = np_count('europe_pmc')
np_ncbi = np_count('ncbi_pmc')
np_elsevier = np_count('elsevier')
np_unpaywall = np_count('unpaywall')
np_publisher = np_count('publisher_html')

# PMC Playwright is used at two different points, and the source string tells
# them apart by position. When it supplements short PMC text it replaces
# text_parts[0], so it leads the source string. When it runs as the last resort
# after the publisher blocked us, it is appended, so CrossRef leads.
np_pmc_pw_supplement = sum(
    1 for d, p in non_preprints.items()
    if statuses[d] == 'full_text'
    and (p.get('text_source') or '').split('+')[0] == 'pmc_playwright'
)
np_pmc_pw_fallback = sum(
    1 for d, p in non_preprints.items()
    if statuses[d] == 'full_text'
    and body_source(p) == 'pmc_playwright'
    and (p.get('text_source') or '').split('+')[0] != 'pmc_playwright'
)
np_pmc_pw = np_pmc_pw_supplement + np_pmc_pw_fallback
np_meta = sum(1 for d in non_preprints if statuses[d] == 'metadata_only')
np_none = sum(1 for d in non_preprints if statuses[d] == 'unavailable')
np_pmc_any = np_epmc + np_ncbi + np_pmc_pw_supplement
np_no_pmc = np_total - np_pmc_any


def pct(n, d):
    return f"{n*100/d:.1f}%" if d > 0 else "0%"


# --- Build diagram ---
dot = graphviz.Digraph('paper_fetching', format='png')
dot.attr(rankdir='TB', fontname='Helvetica', fontsize='14', bgcolor='white',
         dpi='150', pad='0.5', ranksep='0.4', nodesep='1.1')
dot.attr('node', fontname='Helvetica', fontsize='12', margin='0.2,0.12', width='2.5')
dot.attr('edge', fontname='Helvetica', fontsize='10')


# Each helper draws into `g`, which is the enclosing cluster when one is active.
# Passing the graph explicitly is what makes cluster membership work: calling
# dot.node() from inside a `with dot.subgraph()` block would attach the node to
# the top-level graph and leave the cluster empty.
def endpoint(name, label, g=None):
    (g or dot).node(name, label, shape='box', style='filled,rounded',
                    fillcolor='#e8d5f5', color='#7b1fa2', fontcolor='#4a148c')


def decision(name, label, g=None):
    (g or dot).node(name, label, shape='diamond', style='filled',
                    fillcolor='#fff3e0', color='#f57c00', fontcolor='#e65100',
                    width='2', height='1.2')


def source_node(name, label, g=None):
    (g or dot).node(name, label, shape='box', style='filled,rounded',
                    fillcolor='#e1f5fe', color='#0288d1', fontcolor='#01579b')


def result_node(name, label, g=None):
    (g or dot).node(name, label, shape='box', style='filled,rounded',
                    fillcolor='#e8f5e9', color='#388e3c', fontcolor='#1b5e20')


def process_node(name, label, g=None):
    (g or dot).node(name, label, shape='box', style='filled,rounded',
                    fillcolor='#f5f5f5', color='#9e9e9e', fontcolor='#212121')


def fail_node(name, label, g=None):
    (g or dot).node(name, label, shape='box', style='filled,rounded',
                    fillcolor='#ffebee', color='#d32f2f', fontcolor='#b71c1c')


def gate_node(name, label, g=None):
    (g or dot).node(name, label, shape='box', style='filled,rounded',
                    fillcolor='#fff8e1', color='#f9a825', fontcolor='#f57f17')


# --- Nodes ---
endpoint('START', f'get_paper_text_detailed(doi)\n{total:,} unique papers')
decision('CACHE', 'Cache\nhit?')
gate_node('CACHE_VALIDATE', 'Re-judge legacy entry\n(source name is not trusted)')
result_node('RETURN_CACHED', 'Return cached text\n+ status')
decision('PREPRINT', 'Is preprint?\n(10.1101/...)')

with dot.subgraph(name='cluster_preprint') as c:
    c.attr(label=f'Preprint Path — {pre_total:,} papers ({pct(pre_total, total)})',
           style='dashed', color='#7b1fa2', fontname='Helvetica', fontsize='11',
           fontcolor='#7b1fa2', labeljust='l')
    source_node('BIO', f'bioRxiv/medRxiv Playwright\n✓ {pre_bio:,} ({pct(pre_bio, pre_total)})', c)
    source_node('CROSSREF_P', 'CrossRef (references)\nmetadata only', c)
    decision('PRE_CHECK', 'Have article\nbody?', c)
    source_node('EPMC_PRE', f'Europe PMC (fallback)\n✓ {pre_epmc:,} ({pct(pre_epmc, pre_total)})', c)

with dot.subgraph(name='cluster_nonpreprint') as c:
    c.attr(label=f'Non-Preprint Path — {np_total:,} papers ({pct(np_total, total)})',
           style='dashed', color='#0288d1', fontname='Helvetica', fontsize='11',
           fontcolor='#0288d1', labeljust='l')
    source_node('EPMC', f'Europe PMC (JATS XML)\n✓ {np_epmc:,} ({pct(np_epmc, np_total)})', c)
    source_node('NCBI', f'NCBI PMC (DOI→PMCID→efetch)\n✓ {np_ncbi:,} ({pct(np_ncbi, np_total)})', c)
    gate_node('XML_BODY', 'Require <body>\n(<front>-only = abstract)', c)
    source_node('CROSSREF_NP', 'CrossRef (always, for refs)\nmetadata only', c)
    decision('SHORT', 'PMC text\n< 15K chars?', c)
    source_node('PW_SHORT', f'PMC Playwright (supplement)\n✓ {np_pmc_pw_supplement:,} ({pct(np_pmc_pw_supplement, np_total)})', c)
    decision('FULL_CHECK', 'Have article\nbody?', c)
    source_node('ELSEVIER', f'Elsevier ScienceDirect API\n✓ {np_elsevier:,} ({pct(np_elsevier, np_total)})', c)
    source_node('UNPAYWALL', f'Unpaywall (OA PDF → PyMuPDF)\n✓ {np_unpaywall:,} ({pct(np_unpaywall, np_total)})', c)
    source_node('PUB', f'Publisher HTML (doi.org)\n✓ {np_publisher:,} ({pct(np_publisher, np_total)})', c)
    gate_node('PAYWALL_GATE', 'Reject paywall / landing page\n≥6K chars + body sections', c)
    decision('HAS_PMCID', 'Have\nPMCID?', c)
    source_node('PW_FALLBACK', f'PMC Playwright (last resort)\n✓ {np_pmc_pw_fallback:,} ({pct(np_pmc_pw_fallback, np_total)})', c)

# Final assembly
process_node('COMBINE', 'Combine text parts\n(source1 + source2 + ...)')
decision('IS_FULL', 'Any source gave\nthe body?')
result_node('SAVE', f'status = full_text\n{full_text_total:,} papers ({pct(full_text_total, total)})')
fail_node('META', f'status = metadata_only\n{metadata_only_total:,} papers ({pct(metadata_only_total, total)})')
fail_node('NO_TEXT', f'status = unavailable\n{no_text_total:,} papers ({pct(no_text_total, total)})')
endpoint('RETURN', 'Return (text, source,\nstatus, reason)')

# --- Edges ---
dot.edge('START', 'CACHE')
dot.edge('CACHE', 'CACHE_VALIDATE', label='  Yes', color='#388e3c', fontcolor='#388e3c')
dot.edge('CACHE_VALIDATE', 'RETURN_CACHED')
dot.edge('CACHE', 'PREPRINT', label='  No', color='#d32f2f', fontcolor='#d32f2f')

# Preprint branch
dot.edge('PREPRINT', 'BIO', label=f'  Yes\n  {pre_total:,}', color='#388e3c', fontcolor='#388e3c')
dot.edge('BIO', 'CROSSREF_P')
dot.edge('CROSSREF_P', 'PRE_CHECK')
dot.edge('PRE_CHECK', 'COMBINE', label=f'  Yes ({pre_bio:,})', color='#388e3c', fontcolor='#388e3c')
dot.edge('PRE_CHECK', 'EPMC_PRE', label=f'  No ({pre_total - pre_bio:,})',
         color='#d32f2f', fontcolor='#d32f2f')
dot.edge('EPMC_PRE', 'COMBINE')

# Non-preprint branch
dot.edge('PREPRINT', 'EPMC', label=f'  No\n  {np_total:,}', color='#d32f2f', fontcolor='#d32f2f')
dot.edge('EPMC', 'XML_BODY')
dot.edge('XML_BODY', 'CROSSREF_NP', label=f'  ✓ {np_epmc:,}', color='#388e3c', fontcolor='#388e3c')
dot.edge('XML_BODY', 'NCBI', label=f'  ✗ {np_total - np_epmc:,}', color='#d32f2f', fontcolor='#d32f2f')
dot.edge('NCBI', 'CROSSREF_NP')
dot.edge('CROSSREF_NP', 'SHORT')

dot.edge('SHORT', 'PW_SHORT', label=f'  Yes ({np_pmc_pw_supplement:,})',
         color='#f57c00', fontcolor='#f57c00')
dot.edge('SHORT', 'FULL_CHECK', label='  No / adequate', color='#388e3c', fontcolor='#388e3c')
dot.edge('PW_SHORT', 'FULL_CHECK')

dot.edge('FULL_CHECK', 'COMBINE', label=f'  Yes\n  {np_pmc_any:,} ({pct(np_pmc_any, np_total)})',
         color='#388e3c', fontcolor='#388e3c')
dot.edge('FULL_CHECK', 'ELSEVIER', label=f'  No ({np_no_pmc:,})', color='#d32f2f', fontcolor='#d32f2f')

dot.edge('ELSEVIER', 'COMBINE', label=f'  ✓ {np_elsevier:,}', color='#388e3c', fontcolor='#388e3c')
dot.edge('ELSEVIER', 'UNPAYWALL', label=f'  ✗ {np_no_pmc - np_elsevier:,}',
         color='#d32f2f', fontcolor='#d32f2f')
dot.edge('UNPAYWALL', 'COMBINE', label=f'  ✓ {np_unpaywall:,}', color='#388e3c', fontcolor='#388e3c')
dot.edge('UNPAYWALL', 'PUB', label=f'  ✗ {np_no_pmc - np_elsevier - np_unpaywall:,}',
         color='#d32f2f', fontcolor='#d32f2f')
dot.edge('PUB', 'PAYWALL_GATE')
dot.edge('PAYWALL_GATE', 'COMBINE', label=f'  ✓ {np_publisher:,}', color='#388e3c', fontcolor='#388e3c')
dot.edge('PAYWALL_GATE', 'HAS_PMCID', label='  ✗ rejected', color='#d32f2f', fontcolor='#d32f2f')
dot.edge('HAS_PMCID', 'PW_FALLBACK', label=f'  Yes ({np_pmc_pw_fallback:,})',
         color='#388e3c', fontcolor='#388e3c')
dot.edge('HAS_PMCID', 'COMBINE', label='  No', color='#d32f2f', fontcolor='#d32f2f')
dot.edge('PW_FALLBACK', 'COMBINE')

# Final assembly
dot.edge('COMBINE', 'IS_FULL')
dot.edge('IS_FULL', 'SAVE', label=f'  Yes ({full_text_total:,})', color='#388e3c', fontcolor='#388e3c')
dot.edge('IS_FULL', 'META', label=f'  No ({metadata_only_total:,})', color='#d32f2f', fontcolor='#d32f2f')
dot.edge('IS_FULL', 'NO_TEXT', label=f'  nothing ({no_text_total:,})', style='dashed',
         color='#d32f2f', fontcolor='#d32f2f')
dot.edge('SAVE', 'RETURN')
dot.edge('META', 'RETURN')
dot.edge('NO_TEXT', 'RETURN')

dot.render('output/paper_fetching_flow', cleanup=True)
print("Rendered to output/paper_fetching_flow.png")
print(f"\nTotal: {total:,}")
print(f"  full_text     : {full_text_total:,} ({pct(full_text_total, total)})")
print(f"  metadata_only : {metadata_only_total:,} ({pct(metadata_only_total, total)})")
print(f"  unavailable   : {no_text_total:,} ({pct(no_text_total, total)})")
print("\nBody supplied by:")
for src, n in source_wins.most_common():
    print(f"  {src:20} {n:>7,} ({pct(n, full_text_total)} of full text)")
