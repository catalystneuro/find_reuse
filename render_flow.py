"""Render the paper fetching flow diagram using Graphviz, with counts from results JSON."""
import graphviz
import json
from collections import Counter

# --- Load and analyze results ---
with open('output/all_dandiset_papers.json') as f:
    data = json.load(f)

# Deduplicate by DOI
papers = {}
for ds in data['results']:
    for paper in ds.get('citing_papers', []):
        doi = paper.get('doi', '')
        if doi and doi not in papers:
            papers[doi] = paper

total = len(papers)
preprints = {d: p for d, p in papers.items() if d.startswith('10.1101/')}
non_preprints = {d: p for d, p in papers.items() if not d.startswith('10.1101/')}

# Preprint analysis
pre_total = len(preprints)
pre_bio = pre_epmc = pre_crossref_only = pre_no_text = 0
for doi, paper in preprints.items():
    source = paper.get('text_source', '')
    tlen = int(paper.get('text_length', 0))
    parts = source.split('+') if source else []
    if tlen == 0 or not source:
        pre_no_text += 1
    elif 'playwright_biorxiv' in parts:
        pre_bio += 1
    elif 'europe_pmc' in parts:
        pre_epmc += 1
    else:
        pre_crossref_only += 1

# Non-preprint analysis
np_total = len(non_preprints)
np_epmc = np_ncbi = np_pmc_pw_supplement = np_unpaywall = 0
np_elsevier = np_publisher = np_pmc_pw_fallback = np_crossref_only = np_no_text = 0
for doi, paper in non_preprints.items():
    source = paper.get('text_source', '')
    tlen = int(paper.get('text_length', 0))
    parts = source.split('+') if source else []
    if tlen == 0 or not source:
        np_no_text += 1
    elif parts[0] == 'europe_pmc':
        np_epmc += 1
    elif parts[0] == 'ncbi_pmc':
        np_ncbi += 1
    elif parts[0] == 'pmc_playwright':
        np_pmc_pw_supplement += 1
    elif 'elsevier' in parts:
        np_elsevier += 1
    elif 'unpaywall' in parts:
        np_unpaywall += 1
    elif 'publisher_html' in parts:
        np_publisher += 1
    elif parts[0] == 'crossref' and 'pmc_playwright' in parts:
        np_pmc_pw_fallback += 1
    elif source == 'crossref':
        np_crossref_only += 1
    else:
        np_no_text += 1

# Papers that had PMC text but fell through to supplement
# (pmc_pw_supplement had PMC text but it was short)
np_had_pmc = np_epmc + np_ncbi + np_pmc_pw_supplement
np_no_pmc = np_total - np_had_pmc  # fell through to Unpaywall/publisher path
np_epmc_failed = np_total - np_epmc  # went to NCBI
np_ncbi_failed = np_epmc_failed - np_ncbi - np_pmc_pw_supplement  # no PMC at all (approx)

# Overall
full_text = sum(1 for d, p in papers.items()
                if p.get('text_source', '') and p.get('text_source', '') != 'crossref'
                and int(p.get('text_length', 0)) > 0)
crossref_only_total = sum(1 for d, p in papers.items() if p.get('text_source', '') == 'crossref')
no_text_total = sum(1 for d, p in papers.items() if int(p.get('text_length', 0)) == 0)

def pct(n, d):
    return f"{n*100/d:.1f}%" if d > 0 else "0%"

# --- Build diagram ---
dot = graphviz.Digraph('paper_fetching', format='png')
dot.attr(rankdir='TB', fontname='Helvetica', fontsize='14', bgcolor='white',
         dpi='150', pad='0.5', ranksep='0.4', nodesep='1.2')
dot.attr('node', fontname='Helvetica', fontsize='12', margin='0.2,0.12',
         width='2.5')
dot.attr('edge', fontname='Helvetica', fontsize='10')

def endpoint(name, label):
    dot.node(name, label, shape='box', style='filled,rounded',
             fillcolor='#e8d5f5', color='#7b1fa2', fontcolor='#4a148c')

def decision(name, label):
    dot.node(name, label, shape='diamond', style='filled',
             fillcolor='#fff3e0', color='#f57c00', fontcolor='#e65100',
             width='2', height='1.2')

def source_node(name, label):
    dot.node(name, label, shape='box', style='filled,rounded',
             fillcolor='#e1f5fe', color='#0288d1', fontcolor='#01579b')

def result_node(name, label):
    dot.node(name, label, shape='box', style='filled,rounded',
             fillcolor='#e8f5e9', color='#388e3c', fontcolor='#1b5e20')

def process_node(name, label):
    dot.node(name, label, shape='box', style='filled,rounded',
             fillcolor='#f5f5f5', color='#9e9e9e', fontcolor='#212121')

def fail_node(name, label):
    dot.node(name, label, shape='box', style='filled,rounded',
             fillcolor='#ffebee', color='#d32f2f', fontcolor='#b71c1c')

# --- Nodes ---
endpoint('START', f'get_paper_text(doi)\n{total:,} unique papers')
decision('CACHE', 'Cache\nhit?')
result_node('RETURN_CACHED', 'Return cached text')
decision('PREPRINT', f'Is preprint?\n(10.1101/...)')

# --- Preprint path ---
with dot.subgraph(name='cluster_preprint') as c:
    c.attr(label=f'Preprint Path — {pre_total:,} papers ({pct(pre_total, total)})',
           style='dashed', color='#7b1fa2', fontname='Helvetica', fontsize='11',
           fontcolor='#7b1fa2', labeljust='l')
    source_node('BIO', f'bioRxiv Playwright\n✓ {pre_bio:,} ({pct(pre_bio, pre_total)})')
    source_node('CROSSREF_P', 'CrossRef (references)')
    decision('PRE_CHECK', 'Have full\ntext?')
    source_node('EPMC_PRE', f'Europe PMC (fallback)\n✓ {pre_epmc} ({pct(pre_epmc, pre_total)})')

# --- Non-preprint path ---
with dot.subgraph(name='cluster_nonpreprint') as c:
    c.attr(label=f'Non-Preprint Path — {np_total:,} papers ({pct(np_total, total)})',
           style='dashed', color='#0288d1', fontname='Helvetica', fontsize='11',
           fontcolor='#0288d1', labeljust='l')
    source_node('EPMC', f'Europe PMC (full text XML)\n✓ {np_epmc:,} ({pct(np_epmc, np_total)})')
    source_node('NCBI', f'NCBI PMC (DOI→PMCID→efetch)\n✓ {np_ncbi} ({pct(np_ncbi, np_total)})')
    source_node('CROSSREF_NP', 'CrossRef (always, for refs)')
    decision('SHORT', 'PMC text\n< 15K chars?')
    source_node('PW_SHORT', f'PMC Playwright (supplement)\n✓ {np_pmc_pw_supplement} ({pct(np_pmc_pw_supplement, np_total)})')
    decision('FULL_CHECK', 'Have full\ntext?')
    source_node('ELSEVIER', f'Elsevier API (ScienceDirect)\n✓ {np_elsevier} ({pct(np_elsevier, np_total)})')
    source_node('UNPAYWALL', f'Unpaywall (OA PDF → PyMuPDF)\n✓ {np_unpaywall} ({pct(np_unpaywall, np_total)})')
    source_node('PUB', f'Publisher HTML (scrape doi.org)\n✓ {np_publisher:,} ({pct(np_publisher, np_total)})')
    decision('HAS_PMCID', 'Have\nPMCID?')
    source_node('PW_FALLBACK', f'PMC Playwright (last resort)\n✓ {np_pmc_pw_fallback} ({pct(np_pmc_pw_fallback, np_total)})')

# Final assembly
process_node('COMBINE', f'Combine text parts\n(source1 + source2 + ...)')
decision('CROSSREF_ONLY', 'Only\nCrossRef?')
result_node('SAVE', f'Save to cache\n{full_text:,} papers ({pct(full_text, total)})')
fail_node('SKIP', f'Skip cache (refs only)\n{crossref_only_total} papers ({pct(crossref_only_total, total)})')
endpoint('RETURN', 'Return (text, sources)')

# Also show no-text
fail_node('NO_TEXT', f'No text at all\n{no_text_total} papers ({pct(no_text_total, total)})')

# --- Edges ---
dot.edge('START', 'CACHE')
dot.edge('CACHE', 'RETURN_CACHED', label='  Yes', color='#388e3c', fontcolor='#388e3c')
dot.edge('CACHE', 'PREPRINT', label='  No', color='#d32f2f', fontcolor='#d32f2f')

# Preprint branch
dot.edge('PREPRINT', 'BIO', label=f'  Yes\n  {pre_total:,}', color='#388e3c', fontcolor='#388e3c')
dot.edge('BIO', 'CROSSREF_P')
dot.edge('CROSSREF_P', 'PRE_CHECK')
pre_had_text = pre_bio
pre_no_fulltext = pre_total - pre_bio
dot.edge('PRE_CHECK', 'COMBINE', label=f'  Yes ({pre_had_text:,})', color='#388e3c', fontcolor='#388e3c')
dot.edge('PRE_CHECK', 'EPMC_PRE', label=f'  No ({pre_no_fulltext})', color='#d32f2f', fontcolor='#d32f2f')
dot.edge('EPMC_PRE', 'COMBINE')

# Non-preprint branch
dot.edge('PREPRINT', 'EPMC', label=f'  No\n  {np_total:,}', color='#d32f2f', fontcolor='#d32f2f')

np_epmc_failed_count = np_total - np_epmc
dot.edge('EPMC', 'CROSSREF_NP', label=f'  ✓ {np_epmc:,}', color='#388e3c', fontcolor='#388e3c')
dot.edge('EPMC', 'NCBI', label=f'  ✗ {np_epmc_failed_count:,}', color='#d32f2f', fontcolor='#d32f2f')

dot.edge('NCBI', 'CROSSREF_NP')
dot.edge('CROSSREF_NP', 'SHORT')

np_had_pmc_text = np_epmc + np_ncbi + np_pmc_pw_supplement
dot.edge('SHORT', 'PW_SHORT', label=f'  Yes ({np_pmc_pw_supplement})', color='#f57c00', fontcolor='#f57c00')
dot.edge('SHORT', 'FULL_CHECK', label=f'  No / adequate', color='#388e3c', fontcolor='#388e3c')
dot.edge('PW_SHORT', 'FULL_CHECK')

np_had_fulltext = np_epmc + np_ncbi + np_pmc_pw_supplement
np_no_fulltext = np_total - np_had_fulltext
dot.edge('FULL_CHECK', 'COMBINE', label=f'  Yes\n  {np_had_fulltext:,} ({pct(np_had_fulltext, np_total)})',
         color='#388e3c', fontcolor='#388e3c')
dot.edge('FULL_CHECK', 'ELSEVIER', label=f'  No ({np_no_fulltext:,})', color='#d32f2f', fontcolor='#d32f2f')

np_elsevier_failed = np_no_fulltext - np_elsevier
dot.edge('ELSEVIER', 'COMBINE', label=f'  ✓ {np_elsevier}', color='#388e3c', fontcolor='#388e3c')
dot.edge('ELSEVIER', 'UNPAYWALL', label=f'  ✗ {np_elsevier_failed:,}', color='#d32f2f', fontcolor='#d32f2f')

np_unpaywall_failed = np_elsevier_failed - np_unpaywall
dot.edge('UNPAYWALL', 'COMBINE', label=f'  ✓ {np_unpaywall}', color='#388e3c', fontcolor='#388e3c')
dot.edge('UNPAYWALL', 'PUB', label=f'  ✗ {np_unpaywall_failed:,}', color='#d32f2f', fontcolor='#d32f2f')

np_pub_failed = np_unpaywall_failed - np_publisher
dot.edge('PUB', 'COMBINE', label=f'  ✓ {np_publisher:,}', color='#388e3c', fontcolor='#388e3c')
dot.edge('PUB', 'HAS_PMCID', label=f'  ✗ {np_pub_failed}', color='#d32f2f', fontcolor='#d32f2f')

np_no_pmcid = np_pub_failed - np_pmc_pw_fallback
dot.edge('HAS_PMCID', 'PW_FALLBACK', label=f'  Yes ({np_pmc_pw_fallback})', color='#388e3c', fontcolor='#388e3c')
dot.edge('HAS_PMCID', 'COMBINE', label=f'  No ({np_no_pmcid})', color='#d32f2f', fontcolor='#d32f2f')
dot.edge('PW_FALLBACK', 'COMBINE')

# Final assembly
dot.edge('COMBINE', 'CROSSREF_ONLY')
dot.edge('CROSSREF_ONLY', 'SKIP', label=f'  Yes ({crossref_only_total})', color='#d32f2f', fontcolor='#d32f2f')
dot.edge('CROSSREF_ONLY', 'SAVE', label=f'  No ({full_text:,})', color='#388e3c', fontcolor='#388e3c')
dot.edge('SKIP', 'RETURN')
dot.edge('SAVE', 'RETURN')

# No text edge
dot.edge('COMBINE', 'NO_TEXT', label=f'  empty ({no_text_total})', style='dashed',
         color='#d32f2f', fontcolor='#d32f2f')


dot.render('output/paper_fetching_flow', cleanup=True)
print("Rendered to output/paper_fetching_flow.png")
print(f"\nTotal: {total:,} | Full text: {full_text:,} ({pct(full_text, total)}) | CrossRef only: {crossref_only_total} | No text: {no_text_total}")
