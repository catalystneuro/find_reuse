"""Render a flowchart showing the pipeline for finding primary publications
and gathering their citing papers from OpenAlex."""
import graphviz

dot = graphviz.Digraph('citation_pipeline', format='png')
dot.attr(rankdir='TB', fontname='Helvetica', fontsize='12', bgcolor='white',
         dpi='150', pad='0.5', ranksep='0.6', nodesep='0.5')
dot.attr('node', fontname='Helvetica', fontsize='10', margin='0.15,0.1')
dot.attr('edge', fontname='Helvetica', fontsize='9', penwidth='1.5')

# --- Color helpers ---
def api_node(name, label):
    dot.node(name, label, shape='box', style='filled,rounded,bold',
             fillcolor='#e8d5f5', color='#7b1fa2', fontcolor='#4a148c',
             penwidth='2')

def process_node(name, label):
    dot.node(name, label, shape='box', style='filled,rounded',
             fillcolor='#e3f2fd', color='#1565c0', fontcolor='#0d47a1')

def decision_node(name, label):
    dot.node(name, label, shape='diamond', style='filled',
             fillcolor='#fff3e0', color='#f57c00', fontcolor='#e65100',
             width='2.4', height='1.2')

def result_node(name, label):
    dot.node(name, label, shape='box', style='filled,rounded',
             fillcolor='#c8e6c9', color='#2e7d32', fontcolor='#1b5e20',
             penwidth='2')

def note_node(name, label):
    dot.node(name, label, shape='note', style='filled',
             fillcolor='#fffde7', color='#f9a825', fontcolor='#827717',
             fontsize='9')

def output_node(name, label):
    dot.node(name, label, shape='box', style='filled,rounded,bold',
             fillcolor='#a5d6a7', color='#1b5e20', fontcolor='#1b5e20',
             penwidth='2.5', fontsize='11')

def source_node(name, label):
    """Light orange for source breakdown."""
    dot.node(name, label, shape='box', style='filled,rounded',
             fillcolor='#fff3e0', color='#f57c00', fontcolor='#e65100')

# ====================================================================
# Row 0: DANDI API → dandisets
# ====================================================================
api_node('DANDI',
         'DANDI Archive API\n792 dandisets')

# ====================================================================
# Row 1: Check paper relations
# ====================================================================
process_node('CHECK_REL',
             'Check paper relations\nfor each dandiset')

dot.edge('DANDI', 'CHECK_REL', label='  792  ', color='#1565c0')

# ====================================================================
# Row 2: Decision — has primary paper?
# ====================================================================
decision_node('HAS_PAPER', 'Has ≥ 1 primary\npaper DOI?')

dot.edge('CHECK_REL', 'HAS_PAPER', color='#1565c0')

note_node('NO_PAPER', '576 dandisets (73%)\nno primary paper')
dot.edge('HAS_PAPER', 'NO_PAPER', label=' No ', color='#e64a19',
         fontcolor='#e64a19')

# ====================================================================
# Row 3: Source breakdown — relatedResource vs description
# ====================================================================
result_node('DS_WITH_PAPERS',
            '216 dandisets\nwith primary papers')

dot.edge('HAS_PAPER', 'DS_WITH_PAPERS', label=' Yes ', color='#2e7d32',
         fontcolor='#2e7d32', penwidth='2')

with dot.subgraph() as s:
    s.attr(rank='same')
    source_node('FROM_RELATED',
                'relatedResource\n141 dandisets (171 papers)\n'
                'IsDescribedBy: 110\n'
                'IsPublishedIn: 51\n'
                'IsSupplementTo: 10')
    source_node('FROM_DESC',
                'DOI in description\n80 dandisets (79 papers)')

note_node('OVERLAP', '5 dandisets\nhave both')

dot.edge('DS_WITH_PAPERS', 'FROM_RELATED',
         label='  136 only  ', color='#f57c00', fontcolor='#e65100')
dot.edge('DS_WITH_PAPERS', 'FROM_DESC',
         label='  75 only  ', color='#f57c00', fontcolor='#e65100')
dot.edge('FROM_RELATED', 'OVERLAP', style='dotted', color='#f9a825',
         arrowhead='none', constraint='false')
dot.edge('FROM_DESC', 'OVERLAP', style='dotted', color='#f9a825',
         arrowhead='none', constraint='false')

# ====================================================================
# Row 4: Merge into unique DOIs + CrossRef alternate lookup
# ====================================================================
process_node('ALT_LOOKUP',
             'CrossRef: look up alternate DOIs\n'
             '(preprint ↔ published)\n'
             '55 alternates found')

result_node('ALL_DOIS',
            '262 unique DOIs\n(214 + 55 − 7 overlaps)')

dot.edge('FROM_RELATED', 'ALT_LOOKUP', color='#1565c0')
dot.edge('FROM_DESC', 'ALT_LOOKUP', color='#1565c0')
dot.edge('ALT_LOOKUP', 'ALL_DOIS', color='#2e7d32', penwidth='2')

# ====================================================================
# Row 5: OpenAlex paper metadata
# ====================================================================
process_node('FETCH_OA',
             'OpenAlex: fetch paper metadata\n'
             '(publication date, citation count, ID)\n'
             'for each of 262 DOIs')

dot.edge('ALL_DOIS', 'FETCH_OA', color='#1565c0')

# ====================================================================
# Row 6: Fetch citing papers (combines count + full fetch)
# ====================================================================
process_node('FETCH_CITING',
             'OpenAlex: fetch citing papers\n'
             'filter: cites={id} AND\n'
             'publication_date > dandiset_created\n'
             'cursor pagination, max 1,000/dandiset')

dot.edge('FETCH_OA', 'FETCH_CITING', label='  216 dandisets  ',
         color='#1565c0')

result_node('CITING_RESULT',
            '9,910 unique citing papers\n(published after dandiset creation)')

dot.edge('FETCH_CITING', 'CITING_RESULT', color='#2e7d32', penwidth='2.5')

# ====================================================================
# Row 7: Fetch paper texts
# ====================================================================
process_node('FETCH_TEXT',
             'PaperFetcher: fetch full text\n'
             '(8 parallel workers)\n'
             'Europe PMC → NCBI PMC → CrossRef\n'
             '→ Unpaywall → Publisher → Playwright\n'
             '7,787 cached + 2,123 to fetch')

dot.edge('CITING_RESULT', 'FETCH_TEXT', label='  9,910 DOIs  ',
         color='#1565c0')

# ====================================================================
# Row 8: Output
# ====================================================================
output_node('OUTPUT',
            'output/dandi_results_crossversion.json\n'
            'Per dandiset: paper_relations,\n'
            'citing_papers (with text), citation counts')

dot.edge('FETCH_TEXT', 'OUTPUT', color='#1b5e20', penwidth='2.5')

# ====================================================================
# Render
# ====================================================================
dot.render('output/citation_pipeline_flow', cleanup=True)
print("Rendered to output/citation_pipeline_flow.png")
