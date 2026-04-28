"""Render a flowchart showing the minimal pipeline's LLM classifications
and the human review corrections.

Reads counts dynamically from:
  - output/minimal/<archive>/classifications.json (LLM classification counts)
  - output/minimal/<archive>/datasets.json        (dataset -> citing-paper counts)
  - <review_state>                                (human review decisions)

Writes: output/minimal/<archive>/review_flow.png

Usage:
    python render_review_flow.py --archive dandi --review-state review_state.json
    python render_review_flow.py --archive crcns --review-state review_state_crcns.json
"""
import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

import graphviz

parser = argparse.ArgumentParser(description="Render review flow diagram for a given archive.")
parser.add_argument("--archive", required=True, help="Archive name (e.g. dandi, crcns)")
parser.add_argument("--review-state", required=True, help="Path to review_state JSON file")
args = parser.parse_args()

REPO_ROOT = Path(__file__).parent
CLASSIFICATIONS_PATH = REPO_ROOT / "output" / "minimal" / args.archive / "classifications.json"
DATASETS_PATH = REPO_ROOT / "output" / "minimal" / args.archive / "datasets.json"
REVIEW_STATE_PATH = Path(args.review_state)
OUTPUT_BASE = REPO_ROOT / "output" / "minimal" / args.archive / "review_flow"

# ------------------------------------------------------------------
# Load data
# ------------------------------------------------------------------
with open(CLASSIFICATIONS_PATH) as file_handle:
    classifications_data = json.load(file_handle)
with open(DATASETS_PATH) as file_handle:
    datasets_data = json.load(file_handle)
with open(REVIEW_STATE_PATH) as file_handle:
    review_data = json.load(file_handle)

classifications = classifications_data["classifications"]
llm_counts = classifications_data["metadata"]["classification_counts"]
mention_count = llm_counts["MENTION"]
reuse_count = llm_counts["REUSE"]
neither_count = llm_counts["NEITHER"]
total_pairs = mention_count + reuse_count + neither_count

total_datasets = datasets_data["count"]
datasets_with_citations = sum(
    1 for dataset in datasets_data["results"] if len(dataset["citing_papers"]) > 0
)
datasets_dropped = total_datasets - datasets_with_citations

# Build lookup from (citing_doi|dandiset_id) -> LLM classification
llm_label_by_key = {
    f"{entry['citing_doi']}|{entry['dandiset_id']}": entry["classification"]
    for entry in classifications
}

# Group human review outcomes by LLM classification
review_outcomes_by_class = defaultdict(Counter)
for key, review_entry in review_data.items():
    confirmation = review_entry["confirmed"]
    if confirmation is None:
        continue
    llm_class = llm_label_by_key.get(key)
    if llm_class is not None:
        review_outcomes_by_class[llm_class][confirmation] += 1

reuse_review = review_outcomes_by_class["REUSE"]
mention_review = review_outcomes_by_class["MENTION"]
neither_review = review_outcomes_by_class["NEITHER"]

reuse_reviewed = sum(reuse_review.values())
mention_reviewed = sum(mention_review.values())
neither_reviewed = sum(neither_review.values())
reuse_unreviewed = reuse_count - reuse_reviewed
mention_unreviewed = mention_count - mention_reviewed
neither_unreviewed = neither_count - neither_reviewed

# Confusion matrix for the decisive (non-unsure) reviewed pool.
# REUSE is the positive class; MENTION is the negative class.
# Unreviewed and "unsure" reviews are excluded.
# NEITHER reviews are also excluded (true class unknown for "no" entries).
true_positive = reuse_review["yes"]    # LLM REUSE, confirmed REUSE
false_positive = reuse_review["no"]    # LLM REUSE, actually MENTION
false_negative = mention_review["no"]  # LLM MENTION, actually REUSE
true_negative = mention_review["yes"]  # LLM MENTION, confirmed MENTION
decisive_total = true_positive + false_positive + false_negative + true_negative

reuse_excluded_unsure = reuse_review["unsure"]
mention_excluded_unsure = mention_review["unsure"]
neither_excluded_unsure = neither_review["unsure"]
neither_confirmed = neither_review["yes"]
neither_misclassified = neither_review["no"]


def _safe_ratio(numerator, denominator):
    return numerator / denominator if denominator else 0.0


reuse_predicted = true_positive + false_positive
mention_predicted = true_negative + false_negative
true_reuse_total = true_positive + false_negative
true_mention_total = true_negative + false_positive

reuse_precision = _safe_ratio(true_positive, reuse_predicted)
reuse_recall = _safe_ratio(true_positive, true_reuse_total)
mention_precision = _safe_ratio(true_negative, mention_predicted)
mention_recall = _safe_ratio(true_negative, true_mention_total)
accuracy = _safe_ratio(true_positive + true_negative, decisive_total)

# ------------------------------------------------------------------
# Graphviz setup — top-to-bottom so main flow is vertical
# ------------------------------------------------------------------
dot = graphviz.Digraph("review_flow", format="png")
dot.attr(rankdir="TB", fontname="Helvetica", fontsize="12", bgcolor="white",
         dpi="150", pad="0.5", ranksep="0.8", nodesep="0.4")
dot.attr("node", fontname="Helvetica", fontsize="10", margin="0.15,0.1")
dot.attr("edge", fontname="Helvetica", fontsize="9", penwidth="1.5")


def api_node(name, label):
    dot.node(name, label, shape="box", style="filled,rounded,bold",
             fillcolor="#e8d5f5", color="#7b1fa2", fontcolor="#4a148c",
             penwidth="2")


def process_node(name, label):
    dot.node(name, label, shape="box", style="filled,rounded",
             fillcolor="#e3f2fd", color="#1565c0", fontcolor="#0d47a1")


def result_node(name, label):
    dot.node(name, label, shape="box", style="filled,rounded",
             fillcolor="#c8e6c9", color="#2e7d32", fontcolor="#1b5e20",
             penwidth="2")


def note_node(name, label):
    dot.node(name, label, shape="note", style="filled",
             fillcolor="#fffde7", color="#f9a825", fontcolor="#827717",
             fontsize="9")


def source_node(name, label):
    dot.node(name, label, shape="box", style="filled,rounded",
             fillcolor="#fff3e0", color="#f57c00", fontcolor="#e65100")


def matrix_cell(name, title, count, fill, border, font):
    dot.node(name, f"{title}\n{count}",
             shape="box", style="filled,rounded,bold",
             fillcolor=fill, color=border, fontcolor=font,
             penwidth="2", fontsize="11")


def metric_node(name, label):
    dot.node(name, label,
             shape="box", style="filled,rounded,bold",
             fillcolor="#a5d6a7", color="#1b5e20", fontcolor="#1b5e20",
             penwidth="2.5", fontsize="11")


# ------------------------------------------------------------------
# Main vertical flow (top to bottom)
# ------------------------------------------------------------------
api_node("ARCHIVE",
         f"{args.archive.upper()} Archive (stub)\n{total_datasets} datasets")

process_node("FETCH_CITING",
             "Fetch citing papers\n(≤ 100 per dataset)")
dot.edge("ARCHIVE", "FETCH_CITING", label=f"  {total_datasets}  ", color="#1565c0")

result_node("PAIRS",
            f"{total_pairs} paper × dataset pairs\n"
            f"({datasets_with_citations} datasets × 100 citations)")
dot.edge("FETCH_CITING", "PAIRS",
         label=f"  {datasets_with_citations}  ", color="#2e7d32", penwidth="2")

process_node("LLM", "Gemini 3 Flash\nclassification")
dot.edge("PAIRS", "LLM", label=f"  {total_pairs}  ", color="#1565c0")

# LLM diverges into 3 class nodes
with dot.subgraph() as llm_classes:
    llm_classes.attr(rank="same")
    source_node("LLM_REUSE", f"REUSE\n{reuse_count}")
    source_node("LLM_MENTION", f"MENTION\n{mention_count}")
    source_node("LLM_NEITHER", f"NEITHER\n{neither_count}")

dot.edge("LLM", "LLM_REUSE", label=f"  {reuse_count}  ",
         color="#f57c00", fontcolor="#e65100")
dot.edge("LLM", "LLM_MENTION", label=f"  {mention_count}  ",
         color="#f57c00", fontcolor="#e65100")
dot.edge("LLM", "LLM_NEITHER", label=f"  {neither_count}  ",
         color="#f57c00", fontcolor="#e65100")

# All 3 class nodes converge into stratified random sampling
process_node("SAMPLE", "Stratified random\nsampling")
dot.edge("LLM_REUSE", "SAMPLE", color="#1565c0")
dot.edge("LLM_MENTION", "SAMPLE", color="#1565c0")
dot.edge("LLM_NEITHER", "SAMPLE", color="#1565c0")

sample_size_per_class = 10
total_sampled = 3 * sample_size_per_class
total_unreviewed = total_pairs - total_sampled

# SAMPLE diverges: sampled subsets for review + unreviewed remainder
with dot.subgraph() as sampled_classes:
    sampled_classes.attr(rank="same")
    source_node("SAMPLED_REUSE", f"REUSE\n{sample_size_per_class}")
    source_node("SAMPLED_MENTION", f"MENTION\n{sample_size_per_class}")
    source_node("SAMPLED_NEITHER", f"NEITHER\n{sample_size_per_class}")

dot.edge("SAMPLE", "SAMPLED_REUSE", label=f"  {sample_size_per_class}  ",
         color="#f57c00", fontcolor="#e65100")
dot.edge("SAMPLE", "SAMPLED_MENTION", label=f"  {sample_size_per_class}  ",
         color="#f57c00", fontcolor="#e65100")
dot.edge("SAMPLE", "SAMPLED_NEITHER", label=f"  {sample_size_per_class}  ",
         color="#f57c00", fontcolor="#e65100")

note_node("UNREVIEWED", f"Unreviewed\n{total_unreviewed}")
dot.edge("SAMPLE", "UNREVIEWED", label=f"  {total_unreviewed}  ", style="dashed",
         color="#e64a19", fontcolor="#e64a19")

# Sampled subsets converge into manual review
process_node("REVIEW", "Manual review")
dot.edge("SAMPLED_REUSE", "REVIEW", color="#1565c0")
dot.edge("SAMPLED_MENTION", "REVIEW", color="#1565c0")
dot.edge("SAMPLED_NEITHER", "REVIEW", color="#1565c0")

# Confusion matrix as an HTML table node.
# Rows = LLM prediction, Columns = human label.
# Human UNSURE column captures:
#   - human "unsure" responses for REUSE/MENTION rows
#   - human "yes" (confirmed NEITHER) + human "unsure" for the NEITHER row,
#     since both indicate the human found the case ambiguous.
# NEITHER misclassified (human said "no" to NEITHER) have unknown true class
# because the review process didn't ask which class they should be — shown as "?".
header_color = "#e0e0e0"
correct_color = "#c8e6c9"
error_color = "#ffcdd2"
unknown_color = "#fff9c4"
zero_color = "#f5f5f5"

neither_human_unsure = neither_confirmed + neither_excluded_unsure

matrix_html = f"""<
<TABLE BORDER="0" CELLBORDER="1" CELLSPACING="0" CELLPADDING="8" BGCOLOR="white">
  <TR>
    <TD BGCOLOR="{header_color}"><B>LLM \\ Human</B></TD>
    <TD BGCOLOR="{header_color}"><B>REUSE</B></TD>
    <TD BGCOLOR="{header_color}"><B>MENTION</B></TD>
    <TD BGCOLOR="{header_color}"><B>UNSURE</B></TD>
  </TR>
  <TR>
    <TD BGCOLOR="{header_color}"><B>REUSE</B></TD>
    <TD BGCOLOR="{correct_color}">{true_positive}</TD>
    <TD BGCOLOR="{error_color}">{false_positive}</TD>
    <TD BGCOLOR="{unknown_color}">{reuse_excluded_unsure}</TD>
  </TR>
  <TR>
    <TD BGCOLOR="{header_color}"><B>MENTION</B></TD>
    <TD BGCOLOR="{error_color}">{false_negative}</TD>
    <TD BGCOLOR="{correct_color}">{true_negative}</TD>
    <TD BGCOLOR="{unknown_color}">{mention_excluded_unsure}</TD>
  </TR>
  <TR>
    <TD BGCOLOR="{header_color}"><B>NEITHER</B></TD>
    <TD BGCOLOR="{unknown_color}" COLSPAN="2">? (n={neither_misclassified})</TD>
    <TD BGCOLOR="{correct_color}">{neither_human_unsure}</TD>
  </TR>
</TABLE>>"""

dot.node("MATRIX", matrix_html, shape="plain")
dot.edge("REVIEW", "MATRIX", color="#1565c0")

# 2x2 matrix (REUSE vs MENTION only, NEITHER and UNSURE excluded)
# with precision, recall, and accuracy embedded in extra row/column.
metrics_html = f"""<
<TABLE BORDER="0" CELLBORDER="1" CELLSPACING="0" CELLPADDING="8" BGCOLOR="white">
  <TR>
    <TD BGCOLOR="{header_color}"><B>LLM \\ Human</B></TD>
    <TD BGCOLOR="{header_color}"><B>REUSE</B></TD>
    <TD BGCOLOR="{header_color}"><B>MENTION</B></TD>
    <TD BGCOLOR="{header_color}"><B>Precision</B></TD>
  </TR>
  <TR>
    <TD BGCOLOR="{header_color}"><B>REUSE</B></TD>
    <TD BGCOLOR="{correct_color}">{true_positive}</TD>
    <TD BGCOLOR="{error_color}">{false_positive}</TD>
    <TD BGCOLOR="#a5d6a7"><B>{true_positive}/{reuse_predicted} = {reuse_precision:.0%}</B></TD>
  </TR>
  <TR>
    <TD BGCOLOR="{header_color}"><B>MENTION</B></TD>
    <TD BGCOLOR="{error_color}">{false_negative}</TD>
    <TD BGCOLOR="{correct_color}">{true_negative}</TD>
    <TD BGCOLOR="#a5d6a7"><B>{true_negative}/{mention_predicted} = {mention_precision:.0%}</B></TD>
  </TR>
  <TR>
    <TD BGCOLOR="{header_color}"><B>Recall</B></TD>
    <TD BGCOLOR="#a5d6a7"><B>{true_positive}/{true_reuse_total} = {reuse_recall:.0%}</B></TD>
    <TD BGCOLOR="#a5d6a7"><B>{true_negative}/{true_mention_total} = {mention_recall:.0%}</B></TD>
    <TD BGCOLOR="#66bb6a"><B>Acc: {true_positive + true_negative}/{decisive_total} = {accuracy:.0%}</B></TD>
  </TR>
</TABLE>>"""

dot.node("METRICS_MATRIX", metrics_html, shape="plain")
dot.edge("MATRIX", "METRICS_MATRIX", label="  excluding NEITHER/UNSURE  ", color="#1565c0")

# ------------------------------------------------------------------
# Side branches off the main flow
# ------------------------------------------------------------------

# Datasets with no citations (drops off FETCH_CITING)
note_node("DROPPED_NO_CITATIONS",
          f"{datasets_dropped} dataset(s) with\n0 citing papers → dropped")
dot.edge("FETCH_CITING", "DROPPED_NO_CITATIONS", style="dashed",
         color="#e64a19", fontcolor="#e64a19", label=" dropped ")

# ------------------------------------------------------------------
# Render
# ------------------------------------------------------------------
output_path = dot.render(str(OUTPUT_BASE), cleanup=True)
print(f"Rendered to {output_path}")
