"""Render a diagram showing how papers reference dandisets, with counts."""
import graphviz
import json

# --- Load data ---
with open('output/all_classifications.json') as f:
    classif = json.load(f)
with open('output/direct_ref_classifications.json') as f:
    direct_classif = json.load(f)
with open('output/results_dandi.json') as f:
    direct_data = json.load(f)
with open('output/ref_list_analysis.json') as f:
    ref_analysis = json.load(f)

cite_reuse_dois = set(c['citing_doi'] for c in classif['classifications']
                      if c.get('classification') == 'REUSE'
                      and c.get('source_type') in ('citation', 'both', ''))
direct_reuse_dois = set()
direct_reuse_pairs = set()
for c in direct_classif['classifications']:
    if c.get('classification') == 'REUSE':
        direct_reuse_dois.add(c['citing_doi'])
        direct_reuse_pairs.add((c['citing_doi'], c['dandiset_id']))

paper_patterns = {}
for paper in direct_data['results']:
    doi = paper['doi']
    dandi = paper.get('archives', {}).get('DANDI Archive', {})
    for m in dandi.get('matches', []):
        ds_id = m.get('id', '')
        if (doi, ds_id) in direct_reuse_pairs:
            paper_patterns.setdefault(doi, set()).add(m.get('pattern_type', 'unknown'))

both = cite_reuse_dois & direct_reuse_dois
only_cite = cite_reuse_dois - direct_reuse_dois
only_direct = direct_reuse_dois - cite_reuse_dois
all_reuse = cite_reuse_dois | direct_reuse_dois
total = len(all_reuse)
has_direct = both | only_direct
has_citation = both | only_cite

doi_refs = set(d for d in has_direct if 'doi' in paper_patterns.get(d, set()))
url_refs = set(d for d in has_direct
               if 'url' in paper_patterns.get(d, set()) or 'gui_url' in paper_patterns.get(d, set()))
other_refs = set(d for d in has_direct
                 if paper_patterns.get(d, set()) - {'doi', 'url', 'gui_url'})

in_refs_all = set(ref_analysis['in_refs_papers']) | set(ref_analysis.get('both_papers', []))
inline_only = has_direct - in_refs_all

def pct(n, d=total):
    return f"{n * 100 / d:.1f}%" if d > 0 else "0%"

# --- Build diagram ---
dot = graphviz.Digraph('dandiset_references', format='png')
dot.attr(fontname='Helvetica', fontsize='12', bgcolor='white',
         dpi='150', pad='0.4', ranksep='0.6', nodesep='0.5')
dot.attr('node', fontname='Helvetica', fontsize='10', margin='0.2,0.1')
dot.attr('edge', fontname='Helvetica', fontsize='9', penwidth='1.5')

# ── Row 0: Root ──
dot.node('ROOT',
         f'Data Reuse Papers\n{total} unique papers',
         shape='box', style='filled,rounded,bold',
         fillcolor='#e8d5f5', color='#7b1fa2', fontcolor='#4a148c',
         fontsize='14', penwidth='2.5')

# ── Row 1: Two main branches ──
with dot.subgraph() as s:
    s.attr(rank='same')
    s.node('CITE_PAPER',
           f'Cite associated paper\n{len(has_citation)} ({pct(len(has_citation))})',
           shape='box', style='filled,rounded',
           fillcolor='#e3f2fd', color='#1565c0', fontcolor='#0d47a1',
           penwidth='2')
    s.node('DIRECT_REF',
           f'Reference dandiset in text\n{len(has_direct)} ({pct(len(has_direct))})',
           shape='box', style='filled,rounded',
           fillcolor='#fff9c4', color='#f9a825', fontcolor='#f57f17',
           penwidth='2')

dot.edge('ROOT', 'CITE_PAPER',
         label=f'  {len(has_citation)}  ',
         color='#1565c0', fontcolor='#1565c0',
         penwidth=str(max(2, len(has_citation) * 4 / total)))
dot.edge('ROOT', 'DIRECT_REF',
         label=f'  {len(has_direct)}  ',
         color='#f9a825', fontcolor='#f9a825',
         penwidth=str(max(1.5, len(has_direct) * 4 / total)))

# ── Row 2: Cite paper children + Proper citation / Inline split ──
with dot.subgraph() as s:
    s.attr(rank='same')
    s.node('CITE_ONLY',
           f'Paper only (no dandiset ref)\n{len(only_cite)} ({pct(len(only_cite))})',
           shape='box', style='filled,rounded',
           fillcolor='#bbdefb', color='#1565c0', fontcolor='#0d47a1')
    s.node('CITE_PLUS',
           f'+ dandiset ref\n{len(both)} ({pct(len(both))})',
           shape='box', style='filled,rounded',
           fillcolor='#90caf9', color='#1565c0', fontcolor='#0d47a1')
    s.node('IN_REFS',
           f'Proper citation\n(in reference list)\n{len(in_refs_all)} ({pct(len(in_refs_all))})',
           shape='box', style='filled,rounded',
           fillcolor='#c8e6c9', color='#2e7d32', fontcolor='#1b5e20',
           penwidth='2')
    s.node('INLINE_ONLY',
           f'Inline mention only\n{len(inline_only)} ({pct(len(inline_only))})',
           shape='box', style='filled,rounded',
           fillcolor='#ffccbc', color='#e64a19', fontcolor='#bf360c',
           penwidth='2')

dot.edge('CITE_PAPER', 'CITE_ONLY',
         label=f' {len(only_cite)}', color='#1565c0', fontcolor='#1565c0',
         penwidth=str(max(1, len(only_cite) * 4 / total)))
dot.edge('CITE_PAPER', 'CITE_PLUS',
         label=f' {len(both)}', color='#1565c0', fontcolor='#1565c0')
dot.edge('DIRECT_REF', 'IN_REFS',
         label=f' {len(in_refs_all)}',
         color='#2e7d32', fontcolor='#2e7d32')
dot.edge('DIRECT_REF', 'INLINE_ONLY',
         label=f' {len(inline_only)}',
         color='#e64a19', fontcolor='#e64a19')

# ── Row 3: Reference type sub-boxes under Inline mention only ──
dot.node('OVERLAP',
         f'{len(both)} papers ({pct(len(both))})\ndo both',
         shape='note', style='filled',
         fillcolor='#fffde7', color='#f9a825', fontcolor='#827717',
         fontsize='8')

with dot.subgraph() as s:
    s.attr(rank='same')
    s.node('OVERLAP')
    s.node('REF_DOI',
           f'DOI\n{len(doi_refs)} ({pct(len(doi_refs))})',
           shape='box', style='filled,rounded',
           fillcolor='#ffccbc', color='#e64a19', fontcolor='#bf360c',
           fontsize='9')
    s.node('REF_URL',
           f'URL\n{len(url_refs)} ({pct(len(url_refs))})',
           shape='box', style='filled,rounded',
           fillcolor='#ffccbc', color='#e64a19', fontcolor='#bf360c',
           fontsize='9')
    s.node('REF_OTHER',
           f'Other\n{len(other_refs)} ({pct(len(other_refs))})',
           shape='box', style='filled,rounded',
           fillcolor='#ffccbc', color='#e64a19', fontcolor='#bf360c',
           fontsize='9')

# Pull overlap to lower left
dot.edge('CITE_ONLY', 'OVERLAP', style='invis')
dot.edge('INLINE_ONLY', 'REF_DOI',
         label=f' {len(doi_refs)}', color='#e64a19', fontcolor='#e64a19')
dot.edge('INLINE_ONLY', 'REF_URL',
         label=f' {len(url_refs)}', color='#e64a19', fontcolor='#e64a19')
dot.edge('INLINE_ONLY', 'REF_OTHER',
         label=f' {len(other_refs)}', color='#e64a19', fontcolor='#e64a19')

# Dotted lines connecting overlap
dot.edge('CITE_PLUS', 'OVERLAP', style='dotted', color='#f9a825',
         arrowhead='none', constraint='false')

dot.render('output/dandiset_reference_flow', cleanup=True)
print(f"Rendered to output/dandiset_reference_flow.png")
