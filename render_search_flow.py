"""Render a flowchart showing how papers referencing dandisets are discovered
via search engines and text filtering."""
import graphviz

dot = graphviz.Digraph('search_flow', format='png')
dot.attr(rankdir='TB', fontname='Helvetica', fontsize='12', bgcolor='white',
         dpi='150', pad='0.5', ranksep='0.55', nodesep='0.5')
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
             width='2.2', height='1.2')

def result_node(name, label):
    dot.node(name, label, shape='box', style='filled,rounded',
             fillcolor='#c8e6c9', color='#2e7d32', fontcolor='#1b5e20',
             penwidth='2')

def note_node(name, label):
    dot.node(name, label, shape='note', style='filled',
             fillcolor='#fffde7', color='#f9a825', fontcolor='#827717',
             fontsize='9')

def fail_node(name, label):
    dot.node(name, label, shape='box', style='filled,rounded',
             fillcolor='#ffebee', color='#d32f2f', fontcolor='#b71c1c')

def output_node(name, label):
    dot.node(name, label, shape='box', style='filled,rounded,bold',
             fillcolor='#a5d6a7', color='#1b5e20', fontcolor='#1b5e20',
             penwidth='2.5', fontsize='11')

# ====================================================================
# Row 0: Search terms per archive
# ====================================================================
process_node('SEARCH_TERMS',
             'Build search queries per archive\n'
             'from ARCHIVE_SEARCH_TERMS\n'
             '(URLs, DOI prefixes, keywords)')

note_node('ARCHIVES',
          'Supported archives:\n'
          'DANDI Archive, OpenNeuro,\n'
          'CRCNS, Allen Brain Observatory,\n'
          'International Brain Laboratory, …')

dot.edge('SEARCH_TERMS', 'ARCHIVES', style='dashed', arrowhead='none',
         color='#f9a825')

# ====================================================================
# Row 1: Two search engines
# ====================================================================
with dot.subgraph() as s:
    s.attr(rank='same')
    api_node('EPMC',
             'Europe PMC\n(full-text search)\n'
             'search_europe_pmc()')
    api_node('OPENALEX',
             'OpenAlex\n(fulltext.search)\n'
             'search_openalex()')

dot.edge('SEARCH_TERMS', 'EPMC', label='  per archive  ', color='#7b1fa2')
dot.edge('SEARCH_TERMS', 'OPENALEX', label='  per archive  ', color='#7b1fa2')

note_node('EPMC_DETAIL',
          'Query: "dandiarchive.org"\n'
          'OR "DANDI:NNNNNN"\n'
          'OR "dandi/dandisets"\n'
          'Returns DOI, title, PMID')

note_node('OA_DETAIL',
          'fulltext.search for each term\n'
          'Catches preprints not in\n'
          'Europe PMC')

dot.edge('EPMC', 'EPMC_DETAIL', style='dashed', arrowhead='none',
         color='#f9a825')
dot.edge('OPENALEX', 'OA_DETAIL', style='dashed', arrowhead='none',
         color='#f9a825')

# ====================================================================
# Row 2: Merge + deduplicate
# ====================================================================
process_node('MERGE',
             'Merge results by DOI\n'
             'Deduplicate (prefer published\n'
             'over preprint)')

dot.edge('EPMC', 'MERGE', color='#1565c0')
dot.edge('OPENALEX', 'MERGE', color='#1565c0')

# ====================================================================
# Row 3: Fetch paper text
# ====================================================================
process_node('FETCH_TEXT',
             'PaperFetcher: get full text\n'
             'for each candidate paper\n'
             '(fallback chain)')

dot.edge('MERGE', 'FETCH_TEXT', label='  candidate DOIs  ', color='#1565c0')

# ====================================================================
# Row 4: Decision — has enough text?
# ====================================================================
decision_node('HAS_TEXT', 'Full text\navailable?\n(> 3K chars)')

dot.edge('FETCH_TEXT', 'HAS_TEXT', color='#1565c0')

fail_node('NO_TEXT', 'Skip paper\n(insufficient text)')
dot.edge('HAS_TEXT', 'NO_TEXT', label=' No ', color='#e64a19',
         fontcolor='#e64a19')

# ====================================================================
# Row 5: Regex pattern matching
# ====================================================================
process_node('REGEX',
             'find_all_archive_references(text)\n'
             'Regex pattern matching against\n'
             'full paper text')

dot.edge('HAS_TEXT', 'REGEX', label=' Yes ', color='#2e7d32',
         fontcolor='#2e7d32', penwidth='2')

note_node('PATTERNS',
          'Pattern types matched:\n'
          '• URL: dandiarchive.org/dandiset/NNNNNN\n'
          '• DOI: 10.48324/dandi.NNNNNN\n'
          '• GUI URL: gui.dandiarchive.org\n'
          '• API URL: api.dandiarchive.org\n'
          '• ID: DANDI:NNNNNN\n'
          '→ Extracts dandiset ID from each match')

dot.edge('REGEX', 'PATTERNS', style='dashed', arrowhead='none',
         color='#f9a825')

# ====================================================================
# Row 6: Resolve unlinked citations
# ====================================================================
process_node('UNLINKED',
             'Resolve unlinked citations\n'
             '(e.g. "Dataset Name"\n'
             'DANDI Archive, without ID)')

dot.edge('REGEX', 'UNLINKED', color='#1565c0')

note_node('UNLINKED_DETAIL',
          'Searches DANDI API for titles\n'
          'mentioned near "DANDI" keyword\n'
          'without a dataset identifier')
dot.edge('UNLINKED', 'UNLINKED_DETAIL', style='dashed', arrowhead='none',
         color='#f9a825')

# ====================================================================
# Row 7: Follow data descriptor chain
# ====================================================================
process_node('FOLLOW_REFS',
             'Follow data descriptor chain\n'
             '(optional: check references for\n'
             'links to data descriptors)')

dot.edge('UNLINKED', 'FOLLOW_REFS', color='#1565c0')

# ====================================================================
# Row 8: Output
# ====================================================================
output_node('OUTPUT',
            'Per paper: DOI, text_source,\n'
            'matched dataset IDs by archive,\n'
            'pattern types, match strings')

dot.edge('FOLLOW_REFS', 'OUTPUT', color='#1b5e20', penwidth='2.5')

# ====================================================================
# Render
# ====================================================================
dot.render('output/search_reference_flow', cleanup=True)
print("Rendered to output/search_reference_flow.png")
