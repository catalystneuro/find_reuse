"""Render a flowchart showing the minimal pipeline's LLM classifications
and the human review corrections.

Reads counts dynamically from:
  - output/minimal/<archive>/classifications.json (LLM classification counts)
  - output/minimal/<archive>/datasets.json        (dataset -> citing-paper counts)
  - <review_state>                                (human review decisions)

Writes: output/minimal/<archive>/review_flow.png

Usage:
    python render_review_flow.py --archive dandi --review-state review_state.json
    python render_review_flow.py --archive crcns --review-state review_state_crcns.json \
        --max-citing-papers 100 --include-neither false
"""
import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

import graphviz


def _str_to_bool(value):
    normalized = value.strip().lower()
    if normalized in ("true", "1", "yes", "y"):
        return True
    if normalized in ("false", "0", "no", "n"):
        return False
    raise argparse.ArgumentTypeError(f"Expected true/false, got {value!r}")


parser = argparse.ArgumentParser(description="Render review flow diagram for a given archive.")
parser.add_argument("--archive", required=True, help="Archive name (e.g. dandi, crcns)")
parser.add_argument("--review-state", required=True, help="Path to review_state JSON file")
parser.add_argument(
    "--max-citing-papers", type=int, default=None,
    help="Per-dataset citing-paper cap used during the run. "
         "If set, the diagram labels the run as a stub with 'fetch ≤ N per dataset'. "
         "Default: read from datasets.json metadata; otherwise treat as no cap.",
)
parser.add_argument(
    "--include-neither", type=_str_to_bool, default=True,
    help="Whether NEITHER pairs were manually reviewed (true/false). "
         "When false, the SAMPLED_NEITHER set is routed to Unreviewed and the "
         "NEITHER row is dropped from the confusion matrix. Default: true.",
)
parser.add_argument(
    "--total-datasets", type=int, default=None,
    help="Override for the pre-filter dataset count (before dropping datasets with "
         "0 citing papers). Default: read from datasets.json (total_before_filter), "
         "falling back to the post-filter count.",
)
args = parser.parse_args()

REPO_ROOT = Path(__file__).parent
CLASSIFICATIONS_PATH = REPO_ROOT / "output" / "minimal" / args.archive / "classifications.json"
DATASETS_PATH = REPO_ROOT / "output" / "minimal" / args.archive / "datasets.json"
CITATION_CONTEXTS_PATH = REPO_ROOT / "output" / "minimal" / args.archive / "citation_contexts.json"
REVIEW_STATE_PATH = Path(args.review_state)
OUTPUT_BASE = REPO_ROOT / "output" / "minimal" / args.archive / "review_flow"

# ------------------------------------------------------------------
# Load data
# ------------------------------------------------------------------
with open(CLASSIFICATIONS_PATH) as file_handle:
    classifications_data = json.load(file_handle)
with open(DATASETS_PATH) as file_handle:
    datasets_data = json.load(file_handle)
with open(CITATION_CONTEXTS_PATH) as file_handle:
    citation_contexts_data = json.load(file_handle)
with open(REVIEW_STATE_PATH) as file_handle:
    review_data = json.load(file_handle)

extraction_stats = citation_contexts_data["stats"]
input_pairs = extraction_stats["input_pairs"]
pre_extraction_failures = extraction_stats["pre_extraction_failures"]
attempted_extractions = extraction_stats["attempted_extractions"]
with_citations = extraction_stats["with_citations"]
no_citations_found = extraction_stats["no_citations_found"]
low_quality_text = extraction_stats["low_quality_text"]
extraction_exceptions = extraction_stats["extraction_exceptions"]
eligible_pairs = with_citations + no_citations_found
failed_pairs_count = pre_extraction_failures + low_quality_text + extraction_exceptions

classifications = classifications_data["classifications"]
llm_counts = classifications_data["metadata"]["classification_counts"]
mention_count = llm_counts["MENTION"]
reuse_count = llm_counts["REUSE"]
neither_count = llm_counts["NEITHER"]
total_pairs = mention_count + reuse_count + neither_count

datasets_with_citations = sum(
    1 for dataset in datasets_data["results"] if len(dataset["citing_papers"]) > 0
)
# Pre-filter total: CLI override > datasets.json metadata > post-filter count.
if args.total_datasets is not None:
    total_datasets = args.total_datasets
else:
    total_datasets = datasets_data.get("total_before_filter", datasets_data["count"])
datasets_dropped = total_datasets - datasets_with_citations

# Per-dataset cap: CLI override > datasets.json metadata > unset (no cap).
if args.max_citing_papers is not None:
    max_citing_papers = args.max_citing_papers
else:
    max_citing_papers = datasets_data.get("max_citing_papers")
is_stubbed = max_citing_papers is not None

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

# Full 3x3 confusion matrix counts (rows = LLM class, columns = human label).
# confirmed values are now explicit: "reuse", "mention", "unsure".
reuse_human_reuse = reuse_review["reuse"]
reuse_human_mention = reuse_review["mention"]
reuse_human_unsure = reuse_review["unsure"]

mention_human_reuse = mention_review["reuse"]
mention_human_mention = mention_review["mention"]
mention_human_unsure = mention_review["unsure"]

neither_human_reuse = neither_review["reuse"]
neither_human_mention = neither_review["mention"]
neither_human_unsure = neither_review["unsure"]

# 2x2 metrics matrix excludes NEITHER rows and UNSURE columns.
true_positive = reuse_human_reuse
false_positive = reuse_human_mention
false_negative = mention_human_reuse
true_negative = mention_human_mention
decisive_total = true_positive + false_positive + false_negative + true_negative


def _safe_ratio(numerator, denominator):
    return numerator / denominator if denominator else 0.0


def _wald_ci(numerator, denominator, z=1.959964):
    """95% Wald binomial confidence interval, clamped to [0, 1].

    Returns a formatted "[lo%, hi%]" string, or "n/a" when denominator is 0.
    Wald collapses to a degenerate point interval at p=0 or p=1; that's a
    known property of the method.
    """
    if denominator == 0:
        return "n/a"
    proportion = numerator / denominator
    standard_error = (proportion * (1 - proportion) / denominator) ** 0.5
    lower = max(0.0, proportion - z * standard_error)
    upper = min(1.0, proportion + z * standard_error)
    return f"[{lower:.0%}, {upper:.0%}]"


reuse_predicted = true_positive + false_positive
mention_predicted = true_negative + false_negative
true_reuse_total = true_positive + false_negative
true_mention_total = true_negative + false_positive

reuse_precision = _safe_ratio(true_positive, reuse_predicted)
reuse_recall = _safe_ratio(true_positive, true_reuse_total)
mention_precision = _safe_ratio(true_negative, mention_predicted)
mention_recall = _safe_ratio(true_negative, true_mention_total)
accuracy = _safe_ratio(true_positive + true_negative, decisive_total)

reuse_precision_ci = _wald_ci(true_positive, reuse_predicted)
mention_precision_ci = _wald_ci(true_negative, mention_predicted)
reuse_recall_ci = _wald_ci(true_positive, true_reuse_total)
mention_recall_ci = _wald_ci(true_negative, true_mention_total)
accuracy_ci = _wald_ci(true_positive + true_negative, decisive_total)

# ------------------------------------------------------------------
# Graphviz setup — top-to-bottom so main flow is vertical
# ------------------------------------------------------------------
dot = graphviz.Digraph("review_flow", format="png")
dot.attr(rankdir="TB", fontname="Helvetica", fontsize="12", bgcolor="white",
         dpi="150", pad="0.5", ranksep="0.8", nodesep="0.4", compound="true")
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
archive_label_suffix = " (stub)" if is_stubbed else ""
api_node("ARCHIVE",
         f"{args.archive.upper()} Archive{archive_label_suffix}\n{total_datasets} datasets")

if is_stubbed:
    fetch_citing_label = f"Fetch citing papers\n(≤ {max_citing_papers} per dataset)"
else:
    fetch_citing_label = "Fetch citing papers\n(no cap per dataset)"
process_node("FETCH_CITING", fetch_citing_label)
dot.edge("ARCHIVE", "FETCH_CITING", label=f"  {total_datasets}  ", color="#1565c0")

result_node("PAIRS",
            f"{input_pairs} paper × dataset pairs")
dot.edge("FETCH_CITING", "PAIRS",
         label=f"  {datasets_with_citations}  ", color="#2e7d32", penwidth="2")

process_node("EXTRACT",
             "Fetch full text &\nextract citation contexts")
dot.edge("PAIRS", "EXTRACT", label=f"  {input_pairs}  ", color="#1565c0")

result_node("ELIGIBLE",
            f"{eligible_pairs} pairs eligible\n"
            f"for classification")
dot.edge("EXTRACT", "ELIGIBLE",
         label=f"  {eligible_pairs}  ", color="#2e7d32", penwidth="2")

with dot.subgraph(name="cluster_extract_failed") as failed_cluster:
    failed_cluster.attr(
        label=f"{failed_pairs_count} pair(s) excluded",
        style="filled,rounded",
        color="#f9a825",
        fillcolor="#fffde7",
        fontcolor="#827717",
        fontsize="10",
        penwidth="2",
        margin="12",
    )
    failed_cluster.node(
        "FAIL_PRE",
        f"Pre-extraction failures\n{pre_extraction_failures}\n"
        f"(missing DOI / fetch failed / no cache)",
        shape="box", style="filled,rounded",
        fillcolor="white", color="#f9a825", fontcolor="#827717",
        fontsize="9",
    )
    failed_cluster.node(
        "FAIL_LOW",
        f"Low-quality text\n{low_quality_text}\n"
        f"(only refs/metadata)",
        shape="box", style="filled,rounded",
        fillcolor="white", color="#f9a825", fontcolor="#827717",
        fontsize="9",
    )
    failed_cluster.node(
        "FAIL_EXC",
        f"Extraction exceptions\n{extraction_exceptions}",
        shape="box", style="filled,rounded",
        fillcolor="white", color="#f9a825", fontcolor="#827717",
        fontsize="9",
    )

dot.edge("EXTRACT", "FAIL_PRE", style="dashed",
         color="#e64a19", fontcolor="#e64a19",
         label=f"  {failed_pairs_count}  ",
         lhead="cluster_extract_failed")

process_node("LLM", "Gemini 3 Flash\nclassification")
dot.edge("ELIGIBLE", "LLM", label=f"  {total_pairs}  ", color="#1565c0")

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

# Sample sizes per class come from how many entries of each LLM class
# actually appear in the review state file.
sampled_per_class = Counter()
for key in review_data:
    llm_class = llm_label_by_key.get(key)
    if llm_class is not None:
        sampled_per_class[llm_class] += 1

reuse_sampled = sampled_per_class["REUSE"]
mention_sampled = sampled_per_class["MENTION"]
neither_sampled = sampled_per_class["NEITHER"]
total_sampled = reuse_sampled + mention_sampled + neither_sampled
total_unreviewed = total_pairs - total_sampled

# SAMPLE diverges: sampled subsets for review + unreviewed remainder
with dot.subgraph() as sampled_classes:
    sampled_classes.attr(rank="same")
    source_node("SAMPLED_REUSE", f"REUSE\n{reuse_sampled}")
    source_node("SAMPLED_MENTION", f"MENTION\n{mention_sampled}")
    source_node("SAMPLED_NEITHER", f"NEITHER\n{neither_sampled}")

dot.edge("SAMPLE", "SAMPLED_REUSE", label=f"  {reuse_sampled}  ",
         color="#f57c00", fontcolor="#e65100")
dot.edge("SAMPLE", "SAMPLED_MENTION", label=f"  {mention_sampled}  ",
         color="#f57c00", fontcolor="#e65100")
dot.edge("SAMPLE", "SAMPLED_NEITHER", label=f"  {neither_sampled}  ",
         color="#f57c00", fontcolor="#e65100")

# When NEITHER wasn't reviewed, the sampled NEITHER set ends up unreviewed too.
unreviewed_label_total = total_unreviewed + (neither_sampled if not args.include_neither else 0)
note_node("UNREVIEWED", f"Unreviewed\n{unreviewed_label_total}")
dot.edge("SAMPLE", "UNREVIEWED", label=f"  {total_unreviewed}  ", style="dashed",
         color="#e64a19", fontcolor="#e64a19")

# Sampled subsets converge into manual review
process_node("REVIEW", "Manual review")
dot.edge("SAMPLED_REUSE", "REVIEW", color="#1565c0")
dot.edge("SAMPLED_MENTION", "REVIEW", color="#1565c0")
if args.include_neither:
    dot.edge("SAMPLED_NEITHER", "REVIEW", color="#1565c0")
else:
    dot.edge("SAMPLED_NEITHER", "UNREVIEWED",
             label=f"  {neither_sampled}  ", style="dashed",
             color="#e64a19", fontcolor="#e64a19")

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

neither_row_html = f"""  <TR>
    <TD BGCOLOR="{header_color}"><B>NEITHER</B></TD>
    <TD BGCOLOR="{error_color}">{neither_human_reuse}</TD>
    <TD BGCOLOR="{error_color}">{neither_human_mention}</TD>
    <TD BGCOLOR="{correct_color}">{neither_human_unsure}</TD>
  </TR>
""" if args.include_neither else ""

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
    <TD BGCOLOR="{correct_color}">{reuse_human_reuse}</TD>
    <TD BGCOLOR="{error_color}">{reuse_human_mention}</TD>
    <TD BGCOLOR="{unknown_color}">{reuse_human_unsure}</TD>
  </TR>
  <TR>
    <TD BGCOLOR="{header_color}"><B>MENTION</B></TD>
    <TD BGCOLOR="{error_color}">{mention_human_reuse}</TD>
    <TD BGCOLOR="{correct_color}">{mention_human_mention}</TD>
    <TD BGCOLOR="{unknown_color}">{mention_human_unsure}</TD>
  </TR>
{neither_row_html}</TABLE>>"""

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
    <TD BGCOLOR="#a5d6a7"><B>{true_positive}/{reuse_predicted} = {reuse_precision:.0%}<BR/><FONT POINT-SIZE="9">{reuse_precision_ci}</FONT></B></TD>
  </TR>
  <TR>
    <TD BGCOLOR="{header_color}"><B>MENTION</B></TD>
    <TD BGCOLOR="{error_color}">{false_negative}</TD>
    <TD BGCOLOR="{correct_color}">{true_negative}</TD>
    <TD BGCOLOR="#a5d6a7"><B>{true_negative}/{mention_predicted} = {mention_precision:.0%}<BR/><FONT POINT-SIZE="9">{mention_precision_ci}</FONT></B></TD>
  </TR>
  <TR>
    <TD BGCOLOR="{header_color}"><B>Recall</B></TD>
    <TD BGCOLOR="#a5d6a7"><B>{true_positive}/{true_reuse_total} = {reuse_recall:.0%}<BR/><FONT POINT-SIZE="9">{reuse_recall_ci}</FONT></B></TD>
    <TD BGCOLOR="#a5d6a7"><B>{true_negative}/{true_mention_total} = {mention_recall:.0%}<BR/><FONT POINT-SIZE="9">{mention_recall_ci}</FONT></B></TD>
    <TD BGCOLOR="#66bb6a"><B>Acc: {true_positive + true_negative}/{decisive_total} = {accuracy:.0%}<BR/><FONT POINT-SIZE="9">{accuracy_ci}</FONT></B></TD>
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
