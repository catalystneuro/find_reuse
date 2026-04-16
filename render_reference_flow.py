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

both_dois = cite_reuse_dois & direct_reuse_dois
only_cite = cite_reuse_dois - direct_reuse_dois
only_direct = direct_reuse_dois - cite_reuse_dois
all_reuse = cite_reuse_dois | direct_reuse_dois
total = len(all_reuse)
has_direct = both_dois | only_direct
has_citation = both_dois | only_cite


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

# ── Row 1: Three branches ──
with dot.subgraph() as s:
    s.attr(rank='same')
    s.node('CITE_ONLY',
           f'Cite associated paper only\n{len(only_cite)} ({pct(len(only_cite))})',
           shape='box', style='filled,rounded',
           fillcolor='#e3f2fd', color='#1565c0', fontcolor='#0d47a1',
           penwidth='2')
    s.node('BOTH_REF',
           f'Both citation and\ndirect dandiset reference\n{len(both_dois)} ({pct(len(both_dois))})',
           shape='box', style='filled,rounded',
           fillcolor='#c8e6c9', color='#2e7d32', fontcolor='#1b5e20',
           penwidth='2')
    s.node('DIRECT_ONLY',
           f'Direct dandiset reference only\n{len(only_direct)} ({pct(len(only_direct))})',
           shape='box', style='filled,rounded',
           fillcolor='#fff9c4', color='#f9a825', fontcolor='#f57f17',
           penwidth='2')

dot.edge('ROOT', 'CITE_ONLY',
         label=f'  {len(only_cite)}  ',
         color='#1565c0', fontcolor='#1565c0',
         penwidth=str(max(2, len(only_cite) * 4 / total)))
dot.edge('ROOT', 'BOTH_REF',
         label=f'  {len(both_dois)}  ',
         color='#2e7d32', fontcolor='#2e7d32',
         penwidth=str(max(1.5, len(both_dois) * 4 / total)))
dot.edge('ROOT', 'DIRECT_ONLY',
         label=f'  {len(only_direct)}  ',
         color='#f9a825', fontcolor='#f9a825',
         penwidth=str(max(1.5, len(only_direct) * 4 / total)))



dot.render('output/dandiset_reference_flow', cleanup=True)
print(f"Rendered to output/dandiset_reference_flow.png")
