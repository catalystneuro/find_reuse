"""Render a flowchart showing the minimal pipeline's LLM classifications
and the human review corrections.

Reads counts dynamically from:
  - output/minimal/dandi/classifications.json (LLM classification counts)
  - output/minimal/dandi/datasets.json        (dandiset -> citing-paper counts)
  - review_state.json                         (human review decisions)

Writes: output/minimal/dandi/review_flow.png
"""
import json
from collections import Counter, defaultdict
from pathlib import Path

import graphviz

REPO_ROOT = Path(__file__).parent
CLASSIFICATIONS_PATH = REPO_ROOT / "output" / "minimal" / "dandi" / "classifications.json"
DATASETS_PATH = REPO_ROOT / "output" / "minimal" / "dandi" / "datasets.json"
REVIEW_STATE_PATH = REPO_ROOT / "review_state.json"
OUTPUT_BASE = REPO_ROOT / "output" / "minimal" / "dandi" / "review_flow"

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

total_dandisets = datasets_data["count"]
dandisets_with_citations = sum(
    1 for dandiset in datasets_data["results"] if len(dandiset["citing_papers"]) > 0
)
dandisets_dropped = total_dandisets - dandisets_with_citations

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

reuse_reviewed = sum(reuse_review.values())
mention_reviewed = sum(mention_review.values())
mention_unreviewed = mention_count - mention_reviewed

# Confusion matrix for the decisive (non-unsure) reviewed pool.
# REUSE is the positive class; MENTION is the negative class.
# NEITHER, unreviewed MENTION, and "unsure" reviews are excluded.
true_positive = reuse_review["yes"]    # LLM REUSE, confirmed REUSE
false_positive = reuse_review["no"]    # LLM REUSE, actually MENTION
false_negative = mention_review["no"]  # LLM MENTION, actually REUSE
true_negative = mention_review["yes"]  # LLM MENTION, confirmed MENTION
decisive_total = true_positive + false_positive + false_negative + true_negative

reuse_excluded_unsure = reuse_review["unsure"]
mention_excluded_unsure = mention_review["unsure"]


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
# Graphviz setup (style matches render_citation_pipeline_flow.py)
# ------------------------------------------------------------------
dot = graphviz.Digraph("review_flow", format="png")
dot.attr(rankdir="TB", fontname="Helvetica", fontsize="12", bgcolor="white",
         dpi="150", pad="0.5", ranksep="0.6", nodesep="0.5")
dot.attr("node", fontname="Helvetica", fontsize="10", margin="0.15,0.1")
dot.attr("edge", fontname="Helvetica", fontsize="9", penwidth="1.5")


def api_node(name, label):
    dot.node(name, label, shape="box", style="filled,rounded,bold",
             fillcolor="#e8d5f5", color="#7b1fa2", fontcolor="#4a148c",
             penwidth="2")


def process_node(name, label):
    dot.node(name, label, shape="box", style="filled,rounded",
             fillcolor="#e3f2fd", color="#1565c0", fontcolor="#0d47a1")


def decision_node(name, label):
    dot.node(name, label, shape="diamond", style="filled",
             fillcolor="#fff3e0", color="#f57c00", fontcolor="#e65100",
             width="2.4", height="1.2")


def result_node(name, label):
    dot.node(name, label, shape="box", style="filled,rounded",
             fillcolor="#c8e6c9", color="#2e7d32", fontcolor="#1b5e20",
             penwidth="2")


def note_node(name, label):
    dot.node(name, label, shape="note", style="filled",
             fillcolor="#fffde7", color="#f9a825", fontcolor="#827717",
             fontsize="9")


def output_node(name, label):
    dot.node(name, label, shape="box", style="filled,rounded,bold",
             fillcolor="#a5d6a7", color="#1b5e20", fontcolor="#1b5e20",
             penwidth="2.5", fontsize="11")


def source_node(name, label):
    dot.node(name, label, shape="box", style="filled,rounded",
             fillcolor="#fff3e0", color="#f57c00", fontcolor="#e65100")


# ------------------------------------------------------------------
# Row 0: DANDI stub input
# ------------------------------------------------------------------
api_node("DANDI",
         f"DANDI Archive (stub)\n{total_dandisets} dandisets")

# ------------------------------------------------------------------
# Row 1: Fetch citing papers, drop dandisets with no citations
# ------------------------------------------------------------------
process_node("FETCH_CITING",
             "Fetch citing papers\n(≤ 10 per dandiset)")
dot.edge("DANDI", "FETCH_CITING", label=f"  {total_dandisets}  ", color="#1565c0")

note_node("DROPPED",
          f"{dandisets_dropped} dandiset with\n0 citing papers")
dot.edge("FETCH_CITING", "DROPPED", label=" dropped ", color="#e64a19",
         fontcolor="#e64a19", style="dashed")

# ------------------------------------------------------------------
# Row 2: Paper x dandiset pairs
# ------------------------------------------------------------------
result_node("PAIRS",
            f"{total_pairs} paper × dandiset pairs\n"
            f"({dandisets_with_citations} dandisets × 10 citations)")
dot.edge("FETCH_CITING", "PAIRS",
         label=f"  {dandisets_with_citations}  ", color="#2e7d32", penwidth="2")

# ------------------------------------------------------------------
# Row 3: LLM classification
# ------------------------------------------------------------------
process_node("LLM",
             "Gemini 3 Flash classification")
dot.edge("PAIRS", "LLM", label=f"  {total_pairs}  ", color="#1565c0")

# ------------------------------------------------------------------
# Row 4: LLM label buckets
# ------------------------------------------------------------------
with dot.subgraph() as row:
    row.attr(rank="same")
    source_node("LLM_MENTION", f"MENTION\n{mention_count}")
    source_node("LLM_REUSE", f"REUSE\n{reuse_count}")
    source_node("LLM_NEITHER", f"NEITHER\n{neither_count}")

dot.edge("LLM", "LLM_MENTION", label=f"  {mention_count}  ",
         color="#f57c00", fontcolor="#e65100")
dot.edge("LLM", "LLM_REUSE", label=f"  {reuse_count}  ",
         color="#f57c00", fontcolor="#e65100")
dot.edge("LLM", "LLM_NEITHER", label=f"  {neither_count}  ",
         color="#f57c00", fontcolor="#e65100")

# ------------------------------------------------------------------
# Row 5: Human review + excluded buckets
# ------------------------------------------------------------------
# NEITHER excluded entirely (semantics unclear for the REUSE vs MENTION task)
note_node("EXCLUDED_NEITHER",
          f"NEITHER: {neither_count} excluded\n(semantics unclear)")
dot.edge("LLM_NEITHER", "EXCLUDED_NEITHER", style="dashed",
         color="#e64a19", fontcolor="#e64a19",
         label=f"  {neither_count}  ")

# Unreviewed MENTION dropped
if mention_unreviewed > 0:
    note_node("DROPPED_MENTION_UNREVIEWED",
              f"{mention_unreviewed} MENTION unreviewed\n(dropped)")
    dot.edge("LLM_MENTION", "DROPPED_MENTION_UNREVIEWED", style="dashed",
             color="#e64a19", fontcolor="#e64a19",
             label=f"  {mention_unreviewed}  ")

decision_node("REVIEW_MENTION",
              f"Convenience sample\n(first {mention_reviewed} MENTION\non dashboard)")
dot.edge("LLM_MENTION", "REVIEW_MENTION",
         label=f"  {mention_reviewed}  ", color="#1565c0", penwidth="2")

decision_node("REVIEW_REUSE",
              f"Manual review of\nall {reuse_reviewed} REUSE entries")
dot.edge("LLM_REUSE", "REVIEW_REUSE",
         label=f"  {reuse_reviewed}  ", color="#1565c0", penwidth="2")

# ------------------------------------------------------------------
# Row 6: Review outcomes as confusion-matrix cells
# ------------------------------------------------------------------
def matrix_cell(name, title, count, fill, border, font):
    dot.node(name,
             f"{title}\n{count}",
             shape="box", style="filled,rounded,bold",
             fillcolor=fill, color=border, fontcolor=font,
             penwidth="2", fontsize="11")


with dot.subgraph() as row:
    row.attr(rank="same")
    # TP (correct REUSE) - green
    matrix_cell("TP", "True Positive\n(LLM REUSE, actual REUSE)",
                true_positive, "#c8e6c9", "#2e7d32", "#1b5e20")
    # FP (wrong REUSE) - red
    matrix_cell("FP", "False Positive\n(LLM REUSE, actual MENTION)",
                false_positive, "#ffcdd2", "#c62828", "#b71c1c")
    # FN (missed REUSE) - red
    matrix_cell("FN", "False Negative\n(LLM MENTION, actual REUSE)",
                false_negative, "#ffcdd2", "#c62828", "#b71c1c")
    # TN (correct MENTION) - green
    matrix_cell("TN", "True Negative\n(LLM MENTION, actual MENTION)",
                true_negative, "#c8e6c9", "#2e7d32", "#1b5e20")

# REUSE review -> TP / FP / excluded-unsure
dot.edge("REVIEW_REUSE", "TP",
         label=f"  yes: {true_positive}  ",
         color="#1b5e20", penwidth="2")
if false_positive > 0:
    dot.edge("REVIEW_REUSE", "FP",
             label=f"  no: {false_positive}  ",
             color="#c62828", fontcolor="#b71c1c", penwidth="2")
else:
    dot.edge("REVIEW_REUSE", "FP", style="invis")
if reuse_excluded_unsure > 0:
    note_node("EXCLUDED_UNSURE_REUSE",
              f"{reuse_excluded_unsure} REUSE unsure\n(excluded)")
    dot.edge("REVIEW_REUSE", "EXCLUDED_UNSURE_REUSE", style="dashed",
             color="#f57c00", fontcolor="#e65100",
             label=f"  unsure: {reuse_excluded_unsure}  ")

# MENTION review -> TN / FN / excluded-unsure
dot.edge("REVIEW_MENTION", "TN",
         label=f"  yes: {true_negative}  ",
         color="#1b5e20", penwidth="2")
if false_negative > 0:
    dot.edge("REVIEW_MENTION", "FN",
             label=f"  no: {false_negative}  ",
             color="#c62828", fontcolor="#b71c1c", penwidth="2")
else:
    dot.edge("REVIEW_MENTION", "FN", style="invis")
if mention_excluded_unsure > 0:
    note_node("EXCLUDED_UNSURE_MENTION",
              f"{mention_excluded_unsure} MENTION unsure\n(excluded)")
    dot.edge("REVIEW_MENTION", "EXCLUDED_UNSURE_MENTION", style="dashed",
             color="#f57c00", fontcolor="#e65100",
             label=f"  unsure: {mention_excluded_unsure}  ")

# ------------------------------------------------------------------
# Row 7: Metrics
# ------------------------------------------------------------------
def metric_node(name, label):
    dot.node(name, label,
             shape="box", style="filled,rounded,bold",
             fillcolor="#a5d6a7", color="#1b5e20", fontcolor="#1b5e20",
             penwidth="2.5", fontsize="11")


with dot.subgraph() as row:
    row.attr(rank="same")
    metric_node("M_REUSE_PREC",
                f"REUSE precision\n{true_positive}/{reuse_predicted}"
                f" = {reuse_precision:.0%}")
    metric_node("M_REUSE_REC",
                f"REUSE recall\n{true_positive}/{true_reuse_total}"
                f" = {reuse_recall:.0%}")
    metric_node("M_MENTION_PREC",
                f"MENTION precision\n{true_negative}/{mention_predicted}"
                f" = {mention_precision:.0%}")
    metric_node("M_MENTION_REC",
                f"MENTION recall\n{true_negative}/{true_mention_total}"
                f" = {mention_recall:.0%}")
    metric_node("M_ACCURACY",
                f"Accuracy\n{true_positive + true_negative}/{decisive_total}"
                f" = {accuracy:.0%}")

# Anchor every metric node below the matrix row so they land in their own row
for matrix_source, metric_target in [
    ("TP", "M_REUSE_PREC"),
    ("TP", "M_REUSE_REC"),
    ("FP", "M_ACCURACY"),
    ("TN", "M_MENTION_PREC"),
    ("TN", "M_MENTION_REC"),
]:
    dot.edge(matrix_source, metric_target, style="invis")

# ------------------------------------------------------------------
# Render
# ------------------------------------------------------------------
output_path = dot.render(str(OUTPUT_BASE), cleanup=True)
print(f"Rendered to {output_path}")
