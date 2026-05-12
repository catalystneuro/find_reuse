"""Render a flowchart for a review round, showing the pipeline → sampling → manual
review flow plus a chain of classification rounds with confusion matrices.

Reads from a review round directory:
  pipeline_snapshot/classifications.json  (initial LLM classification counts)
  pipeline_snapshot/datasets.json         (dataset → citing-paper counts)
  pipeline_snapshot/citation_contexts.json (extraction stats)
  sample.json                             (sampled pair keys + per-class counts)
  review_state.json                       (human ground truth)
  classification_rounds/<id>/{classifications,metadata}.json  (one card per round)

Writes: <review_round_dir>/review_flow.png

Usage:
    python -m src.indirect_review.render_review_flow \\
        --review-round-dir output/indirect/crcns/review_rounds/review_round_1
    python -m src.indirect_review.render_review_flow \\
        --review-round-dir output/indirect/crcns/review_rounds/review_round_1 \\
        --include-neither false
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


def _llm_label_index(classifications: list[dict]) -> dict[str, str]:
    return {
        f"{entry['citing_doi']}|{entry['dandiset_id']}": entry["classification"]
        for entry in classifications
    }


CLASS_ORDER = ("REUSE", "MENTION", "NEITHER")


def _build_human_transition_breakdown(parent_labels, current_labels, review_data):
    """For each (human_bucket, parent_class, current_class), count pairs.

    human_bucket is one of: 'reuse', 'mention', 'unsure', 'unreviewed'.
    Returns dict[human_bucket] -> Counter[(parent_class, current_class)] -> count.
    """
    breakdown = defaultdict(Counter)
    for key, current_class in current_labels.items():
        parent_class = parent_labels.get(key)
        if parent_class is None:
            continue
        confirmed = review_data.get(key, {}).get("confirmed")
        if confirmed is None:
            human_bucket = "unreviewed"
        elif confirmed == "unsure":
            human_bucket = "unsure"
        else:
            human_bucket = confirmed
        breakdown[human_bucket][(parent_class, current_class)] += 1
    return breakdown


def _transition_summary_html(breakdown, parent_id, current_id):
    """Deltas-only callouts: one line per cohort listing off-diagonal movements.

    For each human cohort (REUSE, MENTION), summarize pairs whose LLM label
    changed between rounds, classified as fixed/regressed/drifted relative to
    the human ground truth. Diagonal cells (no change) are omitted. A net
    correct-count delta is shown in the header.
    """
    fixed_color = "#2e7d32"
    regressed_color = "#c62828"
    drifted_color = "#ef6c00"
    muted_color = "#616161"
    header_color = "#e0e0e0"

    net_correct = 0
    for human_bucket in ("reuse", "mention"):
        for (parent_class, current_class), count in breakdown.get(human_bucket, Counter()).items():
            parent_correct = parent_class.lower() == human_bucket
            current_correct = current_class.lower() == human_bucket
            if current_correct and not parent_correct:
                net_correct += count
            elif parent_correct and not current_correct:
                net_correct -= count

    if net_correct > 0:
        net_label, net_color = f"+{net_correct}", fixed_color
    elif net_correct < 0:
        net_label, net_color = f"{net_correct}", regressed_color
    else:
        net_label, net_color = "0", muted_color

    rows_html = (
        f'  <TR><TD ALIGN="LEFT" BGCOLOR="{header_color}">'
        f'<B>{current_id} vs {parent_id}</B>  '
        f'<FONT COLOR="{net_color}"><B>net: {net_label} correct</B></FONT>'
        f'</TD></TR>\n'
    )

    cohort_label_by_bucket = {"reuse": "HUMAN = REUSE", "mention": "HUMAN = MENTION"}
    for human_bucket in ("reuse", "mention"):
        cohort_data = breakdown.get(human_bucket, Counter())
        cohort_total = sum(cohort_data.values())
        if cohort_total == 0:
            continue
        movements = []
        for (parent_class, current_class), count in sorted(cohort_data.items()):
            if parent_class == current_class:
                continue
            parent_correct = parent_class.lower() == human_bucket
            current_correct = current_class.lower() == human_bucket
            if current_correct and not parent_correct:
                label, color = "fixed", fixed_color
            elif parent_correct and not current_correct:
                label, color = "regressed", regressed_color
            else:
                label, color = "drifted", drifted_color
            movements.append(
                f'<FONT COLOR="{color}"><B>{count}</B> {label}</FONT>'
                f' <FONT POINT-SIZE="9" COLOR="{muted_color}">'
                f'({parent_class} → {current_class})</FONT>'
            )
        if movements:
            cohort_body = ",  ".join(movements)
        else:
            cohort_body = f'<FONT COLOR="{muted_color}"><I>no changes</I></FONT>'
        rows_html += (
            f'  <TR><TD ALIGN="LEFT">'
            f'<B>{cohort_label_by_bucket[human_bucket]}</B> ({cohort_total}):  '
            f'{cohort_body}</TD></TR>\n'
        )

    extras = []
    for bucket in ("unsure", "unreviewed"):
        count = sum(breakdown.get(bucket, Counter()).values())
        if count:
            extras.append(f"{count} {bucket}")
    if extras:
        rows_html += (
            f'  <TR><TD ALIGN="LEFT">'
            f'<FONT POINT-SIZE="9" COLOR="{muted_color}">'
            f'({", ".join(extras)} not shown)</FONT></TD></TR>\n'
        )

    return f"""<
<TABLE BORDER="0" CELLBORDER="1" CELLSPACING="0" CELLPADDING="6" BGCOLOR="white">
{rows_html}</TABLE>>"""


def _build_confusion(llm_label_by_key, review_data):
    """3x3 confusion matrix: matrix[llm_class][human_label] -> count."""
    matrix = defaultdict(Counter)
    for key, entry in review_data.items():
        confirmed = entry.get("confirmed")
        if confirmed is None:
            continue
        llm_class = llm_label_by_key.get(key)
        if llm_class is None:
            continue
        matrix[llm_class][confirmed] += 1
    return matrix


def _metrics_from_confusion(matrix):
    true_positive = matrix["REUSE"]["reuse"]
    false_positive = matrix["REUSE"]["mention"]
    false_negative = matrix["MENTION"]["reuse"]
    true_negative = matrix["MENTION"]["mention"]
    reuse_predicted = true_positive + false_positive
    mention_predicted = true_negative + false_negative
    true_reuse_total = true_positive + false_negative
    true_mention_total = true_negative + false_positive
    decisive_total = true_positive + false_positive + false_negative + true_negative
    return {
        "tp": true_positive, "fp": false_positive,
        "fn": false_negative, "tn": true_negative,
        "reuse_predicted": reuse_predicted,
        "mention_predicted": mention_predicted,
        "true_reuse_total": true_reuse_total,
        "true_mention_total": true_mention_total,
        "decisive_total": decisive_total,
        "reuse_precision": _safe_ratio(true_positive, reuse_predicted),
        "mention_precision": _safe_ratio(true_negative, mention_predicted),
        "reuse_recall": _safe_ratio(true_positive, true_reuse_total),
        "mention_recall": _safe_ratio(true_negative, true_mention_total),
        "accuracy": _safe_ratio(true_positive + true_negative, decisive_total),
        "reuse_precision_ci": _wald_ci(true_positive, reuse_predicted),
        "mention_precision_ci": _wald_ci(true_negative, mention_predicted),
        "reuse_recall_ci": _wald_ci(true_positive, true_reuse_total),
        "mention_recall_ci": _wald_ci(true_negative, true_mention_total),
        "accuracy_ci": _wald_ci(true_positive + true_negative, decisive_total),
    }


def _confusion_matrix_html(matrix, include_neither):
    header_color = "#e0e0e0"
    correct_color = "#c8e6c9"
    error_color = "#ffcdd2"
    unknown_color = "#fff9c4"

    reuse_human_reuse = matrix["REUSE"]["reuse"]
    reuse_human_mention = matrix["REUSE"]["mention"]
    reuse_human_unsure = matrix["REUSE"]["unsure"]
    mention_human_reuse = matrix["MENTION"]["reuse"]
    mention_human_mention = matrix["MENTION"]["mention"]
    mention_human_unsure = matrix["MENTION"]["unsure"]
    neither_human_reuse = matrix["NEITHER"]["reuse"]
    neither_human_mention = matrix["NEITHER"]["mention"]
    neither_human_unsure = matrix["NEITHER"]["unsure"]

    neither_row_html = f"""  <TR>
    <TD BGCOLOR="{header_color}"><B>NEITHER</B></TD>
    <TD BGCOLOR="{error_color}">{neither_human_reuse}</TD>
    <TD BGCOLOR="{error_color}">{neither_human_mention}</TD>
    <TD BGCOLOR="{correct_color}">{neither_human_unsure}</TD>
  </TR>
""" if include_neither else ""

    return f"""<
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


def _metrics_matrix_html(metrics):
    header_color = "#e0e0e0"
    correct_color = "#c8e6c9"
    error_color = "#ffcdd2"
    return f"""<
<TABLE BORDER="0" CELLBORDER="1" CELLSPACING="0" CELLPADDING="8" BGCOLOR="white">
  <TR>
    <TD BGCOLOR="{header_color}"><B>LLM \\ Human</B></TD>
    <TD BGCOLOR="{header_color}"><B>REUSE</B></TD>
    <TD BGCOLOR="{header_color}"><B>MENTION</B></TD>
    <TD BGCOLOR="{header_color}"><B>Precision</B></TD>
  </TR>
  <TR>
    <TD BGCOLOR="{header_color}"><B>REUSE</B></TD>
    <TD BGCOLOR="{correct_color}">{metrics['tp']}</TD>
    <TD BGCOLOR="{error_color}">{metrics['fp']}</TD>
    <TD BGCOLOR="#a5d6a7"><B>{metrics['tp']}/{metrics['reuse_predicted']} = {metrics['reuse_precision']:.0%}<BR/><FONT POINT-SIZE="9">{metrics['reuse_precision_ci']}</FONT></B></TD>
  </TR>
  <TR>
    <TD BGCOLOR="{header_color}"><B>MENTION</B></TD>
    <TD BGCOLOR="{error_color}">{metrics['fn']}</TD>
    <TD BGCOLOR="{correct_color}">{metrics['tn']}</TD>
    <TD BGCOLOR="#a5d6a7"><B>{metrics['tn']}/{metrics['mention_predicted']} = {metrics['mention_precision']:.0%}<BR/><FONT POINT-SIZE="9">{metrics['mention_precision_ci']}</FONT></B></TD>
  </TR>
  <TR>
    <TD BGCOLOR="{header_color}"><B>Recall</B></TD>
    <TD BGCOLOR="#a5d6a7"><B>{metrics['tp']}/{metrics['true_reuse_total']} = {metrics['reuse_recall']:.0%}<BR/><FONT POINT-SIZE="9">{metrics['reuse_recall_ci']}</FONT></B></TD>
    <TD BGCOLOR="#a5d6a7"><B>{metrics['tn']}/{metrics['true_mention_total']} = {metrics['mention_recall']:.0%}<BR/><FONT POINT-SIZE="9">{metrics['mention_recall_ci']}</FONT></B></TD>
    <TD BGCOLOR="#66bb6a"><B>Acc: {metrics['tp'] + metrics['tn']}/{metrics['decisive_total']} = {metrics['accuracy']:.0%}<BR/><FONT POINT-SIZE="9">{metrics['accuracy_ci']}</FONT></B></TD>
  </TR>
</TABLE>>"""


def _trajectory_table_html(rounds_with_metrics, initial_metrics):
    """Compact per-round trajectory table used in summary mode.

    Each row: round id, description, correct/total, delta vs initial.
    Skips the first and last entries (those are rendered as full panels).
    """
    header_color = "#e0e0e0"
    fixed_color = "#2e7d32"
    regressed_color = "#c62828"
    muted_color = "#616161"

    rows_html = (
        f'  <TR>'
        f'<TD BGCOLOR="{header_color}"><B>Round</B></TD>'
        f'<TD BGCOLOR="{header_color}"><B>Change</B></TD>'
        f'<TD BGCOLOR="{header_color}"><B>Correct</B></TD>'
        f'<TD BGCOLOR="{header_color}"><B>Δ vs initial</B></TD>'
        f'</TR>\n'
    )
    initial_correct = initial_metrics["tp"] + initial_metrics["tn"]
    for entry in rounds_with_metrics[1:]:
        correct = entry["metrics"]["tp"] + entry["metrics"]["tn"]
        total = entry["metrics"]["decisive_total"]
        delta = correct - initial_correct
        if delta > 0:
            delta_label, delta_color = f"+{delta}", fixed_color
        elif delta < 0:
            delta_label, delta_color = f"{delta}", regressed_color
        else:
            delta_label, delta_color = "0", muted_color
        description = entry["metadata"].get("description", "")
        rows_html += (
            f'  <TR>'
            f'<TD ALIGN="LEFT"><B>{entry["id"]}</B></TD>'
            f'<TD ALIGN="LEFT">{description}</TD>'
            f'<TD ALIGN="RIGHT">{correct}/{total}</TD>'
            f'<TD ALIGN="RIGHT"><FONT COLOR="{delta_color}"><B>{delta_label}</B></FONT></TD>'
            f'</TR>\n'
        )

    return f"""<
<TABLE BORDER="0" CELLBORDER="1" CELLSPACING="0" CELLPADDING="6" BGCOLOR="white">
  <TR><TD COLSPAN="4" ALIGN="LEFT" BGCOLOR="{header_color}"><B>Iterative refinement</B></TD></TR>
{rows_html}</TABLE>>"""


def _load_classification_rounds(review_round_dir: Path) -> list[dict]:
    rounds_root = review_round_dir / "classification_rounds"
    rounds = []
    for path in sorted(rounds_root.iterdir()):
        if not (path.is_dir() and path.name[:3].isdigit()):
            continue
        classifications_data = json.loads((path / "classifications.json").read_text())
        metadata = json.loads((path / "metadata.json").read_text())
        rounds.append({
            "id": path.name,
            "metadata": metadata,
            "llm_label_by_key": _llm_label_index(classifications_data["classifications"]),
        })
    return rounds


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--review-round-dir", required=True, type=Path,
                        help="Path to the review round directory.")
    parser.add_argument(
        "--mode", choices=("full", "summary"), default="full",
        help="full: render every classification round with confusion + metrics + transitions "
             "(default). summary: render only the initial and final rounds' confusion matrices, "
             "with a compact trajectory table for the intermediate rounds.",
    )
    parser.add_argument(
        "--include-neither", type=_str_to_bool, default=None,
        help="Whether NEITHER pairs were manually reviewed (true/false). "
             "Default: read from sample.json's include_neither field.",
    )
    parser.add_argument(
        "--total-datasets", type=int, default=None,
        help="Override for the pre-filter dataset count.",
    )
    args = parser.parse_args()

    review_round_dir = args.review_round_dir
    snapshot_dir = review_round_dir / "pipeline_snapshot"
    classifications_path = snapshot_dir / "classifications.json"
    datasets_path = snapshot_dir / "datasets.json"
    citation_contexts_path = snapshot_dir / "citation_contexts.json"
    sample_path = review_round_dir / "sample.json"
    review_state_path = review_round_dir / "review_state.json"
    output_base = review_round_dir / ("review_flow_summary" if args.mode == "summary" else "review_flow")

    classifications_data = json.loads(classifications_path.read_text())
    datasets_data = json.loads(datasets_path.read_text())
    citation_contexts_data = json.loads(citation_contexts_path.read_text())
    sample_data = json.loads(sample_path.read_text())
    review_data = json.loads(review_state_path.read_text()) if review_state_path.exists() else {}

    archive = sample_data.get("archive", "")
    include_neither = (
        sample_data.get("include_neither", True)
        if args.include_neither is None else args.include_neither
    )

    extraction_stats = citation_contexts_data["stats"]
    input_pairs = extraction_stats["input_pairs"]
    pre_extraction_failures = extraction_stats["pre_extraction_failures"]
    with_citations = extraction_stats["with_citations"]
    no_citations_found = extraction_stats["no_citations_found"]
    low_quality_text = extraction_stats["low_quality_text"]
    extraction_exceptions = extraction_stats["extraction_exceptions"]
    eligible_pairs = with_citations + no_citations_found
    failed_pairs_count = pre_extraction_failures + low_quality_text + extraction_exceptions

    # Pipeline-snapshot LLM counts (the classifier that drove the sampling).
    llm_counts = classifications_data["metadata"]["classification_counts"]
    mention_count = llm_counts["MENTION"]
    reuse_count = llm_counts["REUSE"]
    neither_count = llm_counts["NEITHER"]
    total_pairs = mention_count + reuse_count + neither_count

    datasets_with_citations = sum(
        1 for dataset in datasets_data["results"] if len(dataset["citing_papers"]) > 0
    )
    if args.total_datasets is not None:
        total_datasets = args.total_datasets
    else:
        total_datasets = datasets_data.get("total_before_filter", datasets_data["count"])
    datasets_dropped = total_datasets - datasets_with_citations
    max_citing_papers = datasets_data.get("max_citing_papers")
    is_stubbed = max_citing_papers is not None

    classification_rounds = _load_classification_rounds(review_round_dir)
    if not classification_rounds:
        raise RuntimeError(f"No classification rounds found under {review_round_dir}")

    # Sample sizes per class come from sample.json.
    sampled_per_class = Counter(entry["classification"] for entry in sample_data["sampled_pairs"])
    reuse_sampled = sampled_per_class["REUSE"]
    mention_sampled = sampled_per_class["MENTION"]
    neither_sampled = sampled_per_class["NEITHER"]
    total_sampled = reuse_sampled + mention_sampled + neither_sampled
    total_unreviewed = total_pairs - total_sampled

    # Graphviz setup
    dot = graphviz.Digraph("review_flow", format="png")
    dot.attr(rankdir="TB", fontname="Helvetica", fontsize="12", bgcolor="white",
             dpi="150", pad="0.5", ranksep="0.8", nodesep="0.4", compound="true")
    dot.attr("node", fontname="Helvetica", fontsize="10", margin="0.15,0.1")
    dot.attr("edge", fontname="Helvetica", fontsize="9", penwidth="1.5")

    def api_node(name, label):
        dot.node(name, label, shape="box", style="filled,rounded,bold",
                 fillcolor="#e8d5f5", color="#7b1fa2", fontcolor="#4a148c", penwidth="2")

    def process_node(name, label):
        dot.node(name, label, shape="box", style="filled,rounded",
                 fillcolor="#e3f2fd", color="#1565c0", fontcolor="#0d47a1")

    def result_node(name, label):
        dot.node(name, label, shape="box", style="filled,rounded",
                 fillcolor="#c8e6c9", color="#2e7d32", fontcolor="#1b5e20", penwidth="2")

    def note_node(name, label):
        dot.node(name, label, shape="note", style="filled",
                 fillcolor="#fffde7", color="#f9a825", fontcolor="#827717", fontsize="9")

    def source_node(name, label):
        dot.node(name, label, shape="box", style="filled,rounded",
                 fillcolor="#fff3e0", color="#f57c00", fontcolor="#e65100")

    archive_label_suffix = " (stub)" if is_stubbed else ""
    api_node("ARCHIVE", f"{archive.upper()} Archive{archive_label_suffix}\n{total_datasets} datasets")

    if is_stubbed:
        fetch_citing_label = f"Fetch citing papers\n(≤ {max_citing_papers} per dataset)"
    else:
        fetch_citing_label = "Fetch citing papers\n(no cap per dataset)"
    process_node("FETCH_CITING", fetch_citing_label)
    dot.edge("ARCHIVE", "FETCH_CITING", label=f"  {total_datasets}  ", color="#1565c0")

    result_node("PAIRS", f"{input_pairs} paper × dataset pairs")
    dot.edge("FETCH_CITING", "PAIRS",
             label=f"  {datasets_with_citations}  ", color="#2e7d32", penwidth="2")

    process_node("EXTRACT", "Fetch full text &\nextract citation contexts")
    dot.edge("PAIRS", "EXTRACT", label=f"  {input_pairs}  ", color="#1565c0")

    result_node("ELIGIBLE", f"{eligible_pairs} pairs eligible\nfor classification")
    dot.edge("EXTRACT", "ELIGIBLE", label=f"  {eligible_pairs}  ",
             color="#2e7d32", penwidth="2")

    with dot.subgraph(name="cluster_extract_failed") as failed_cluster:
        failed_cluster.attr(
            label=f"{failed_pairs_count} pair(s) excluded",
            style="filled,rounded", color="#f9a825", fillcolor="#fffde7",
            fontcolor="#827717", fontsize="10", penwidth="2", margin="12",
        )
        failed_cluster.node(
            "FAIL_PRE",
            f"Pre-extraction failures\n{pre_extraction_failures}\n"
            f"(missing DOI / fetch failed / no cache)",
            shape="box", style="filled,rounded",
            fillcolor="white", color="#f9a825", fontcolor="#827717", fontsize="9",
        )
        failed_cluster.node(
            "FAIL_LOW",
            f"Low-quality text\n{low_quality_text}\n(only refs/metadata)",
            shape="box", style="filled,rounded",
            fillcolor="white", color="#f9a825", fontcolor="#827717", fontsize="9",
        )
        failed_cluster.node(
            "FAIL_EXC", f"Extraction exceptions\n{extraction_exceptions}",
            shape="box", style="filled,rounded",
            fillcolor="white", color="#f9a825", fontcolor="#827717", fontsize="9",
        )

    dot.edge("EXTRACT", "FAIL_PRE", style="dashed",
             color="#e64a19", fontcolor="#e64a19",
             label=f"  {failed_pairs_count}  ", lhead="cluster_extract_failed")

    initial_round = classification_rounds[0]
    initial_model_label = initial_round["metadata"].get("model", "LLM")
    process_node("LLM", f"{initial_model_label}\nclassification\n(round {initial_round['id']})")
    dot.edge("ELIGIBLE", "LLM", label=f"  {total_pairs}  ", color="#1565c0")

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

    process_node("SAMPLE", "Stratified random\nsampling")
    dot.edge("LLM_REUSE", "SAMPLE", color="#1565c0")
    dot.edge("LLM_MENTION", "SAMPLE", color="#1565c0")
    if include_neither:
        dot.edge("LLM_NEITHER", "SAMPLE", color="#1565c0")

    with dot.subgraph() as sampled_classes:
        sampled_classes.attr(rank="same")
        source_node("SAMPLED_REUSE", f"REUSE\n{reuse_sampled}")
        source_node("SAMPLED_MENTION", f"MENTION\n{mention_sampled}")
        if include_neither:
            source_node("SAMPLED_NEITHER", f"NEITHER\n{neither_sampled}")

    dot.edge("SAMPLE", "SAMPLED_REUSE", label=f"  {reuse_sampled}  ",
             color="#f57c00", fontcolor="#e65100")
    dot.edge("SAMPLE", "SAMPLED_MENTION", label=f"  {mention_sampled}  ",
             color="#f57c00", fontcolor="#e65100")
    if include_neither:
        dot.edge("SAMPLE", "SAMPLED_NEITHER", label=f"  {neither_sampled}  ",
                 color="#f57c00", fontcolor="#e65100")

    note_node("UNREVIEWED", f"Unreviewed\n{total_unreviewed}")
    if include_neither:
        sample_to_unreviewed = total_unreviewed
    else:
        sample_to_unreviewed = total_unreviewed - neither_count
        dot.edge("LLM_NEITHER", "UNREVIEWED", label=f"  {neither_count}  ",
                 style="dashed", color="#e64a19", fontcolor="#e64a19")
    dot.edge("SAMPLE", "UNREVIEWED", label=f"  {sample_to_unreviewed}  ",
             style="dashed", color="#e64a19", fontcolor="#e64a19")

    process_node("REVIEW", "Manual review")
    dot.edge("SAMPLED_REUSE", "REVIEW", color="#1565c0")
    dot.edge("SAMPLED_MENTION", "REVIEW", color="#1565c0")
    if include_neither:
        dot.edge("SAMPLED_NEITHER", "REVIEW", color="#1565c0")

    if not review_data:
        note_node(
            "REVIEW_PENDING",
            "review_state.json not found —\nmanual review pending",
        )
        dot.edge("REVIEW", "REVIEW_PENDING", style="dashed", color="#e64a19")
    else:
        initial_round = classification_rounds[0]
        initial_id = initial_round["id"]
        final_index = len(classification_rounds) - 1
        rounds_with_metrics = []
        for classification_round in classification_rounds:
            matrix = _build_confusion(
                classification_round["llm_label_by_key"], review_data,
            )
            rounds_with_metrics.append({
                "id": classification_round["id"],
                "metadata": classification_round["metadata"],
                "llm_label_by_key": classification_round["llm_label_by_key"],
                "matrix": matrix,
                "metrics": _metrics_from_confusion(matrix),
            })
        initial_metrics = rounds_with_metrics[0]["metrics"]

        is_summary = args.mode == "summary"
        if is_summary:
            indices_to_render = sorted({0, final_index})
        else:
            indices_to_render = list(range(len(rounds_with_metrics)))

        previous_anchor = "REVIEW"
        previous_rendered_id = None
        for position, index in enumerate(indices_to_render):
            entry = rounds_with_metrics[index]
            round_id = entry["id"]
            metadata = entry["metadata"]
            matrix = entry["matrix"]
            metrics = entry["metrics"]
            is_initial = index == 0
            is_final = index == final_index

            # In summary mode, insert the trajectory table between initial and final.
            if is_summary and previous_rendered_id == initial_id and not is_initial:
                trajectory_node = "TRAJECTORY"
                dot.node(
                    trajectory_node,
                    _trajectory_table_html(rounds_with_metrics, initial_metrics),
                    shape="plain",
                )
                dot.edge(previous_anchor, trajectory_node, color="#5e35b1",
                         label="  iterative refinement  ")
                previous_anchor = trajectory_node

            header_node = f"ROUND_HEADER_{index}"
            description = metadata.get("description", "")
            model = metadata.get("model", "")
            parent = metadata.get("parent")
            parent_label = f"\nparent: {parent}" if parent else ""
            dot.node(
                header_node,
                f"{round_id}\nmodel: {model}\n{description}{parent_label}",
                shape="box", style="filled,rounded,bold",
                fillcolor="#ede7f6", color="#5e35b1", fontcolor="#311b92",
                penwidth="2", fontsize="11",
            )
            edge_label = ""
            if not is_initial:
                accuracy_delta = metrics["accuracy"] - initial_metrics["accuracy"]
                correct_delta = (
                    (metrics["tp"] + metrics["tn"])
                    - (initial_metrics["tp"] + initial_metrics["tn"])
                )
                edge_label = (
                    f"  Δ acc {accuracy_delta * 100:+.0f}pp vs {initial_id}"
                    f"  ({correct_delta:+d} correct)  "
                )
            dot.edge(previous_anchor, header_node, label=edge_label, color="#5e35b1")

            tail_anchor = header_node

            # Show confusion matrix on initial (always) and final (summary mode only).
            show_confusion_matrix = is_initial or (is_summary and is_final)
            if show_confusion_matrix:
                matrix_node = f"MATRIX_{index}"
                dot.node(matrix_node, _confusion_matrix_html(matrix, include_neither),
                         shape="plain")
                dot.edge(tail_anchor, matrix_node, color="#1565c0")
                tail_anchor = matrix_node

            metrics_node = f"METRICS_{index}"
            dot.node(metrics_node, _metrics_matrix_html(metrics), shape="plain")
            dot.edge(tail_anchor, metrics_node,
                     label="  excluding NEITHER/UNSURE  ", color="#1565c0")
            tail_anchor = metrics_node

            if not is_initial and not is_summary:
                breakdown = _build_human_transition_breakdown(
                    rounds_with_metrics[0]["llm_label_by_key"],
                    entry["llm_label_by_key"],
                    review_data,
                )
                transition_node = f"TRANSITION_{index}"
                dot.node(transition_node,
                         _transition_summary_html(breakdown, initial_id, round_id),
                         shape="plain")
                dot.edge(tail_anchor, transition_node,
                         label=f"  vs {initial_id}  ", color="#5e35b1")
                tail_anchor = transition_node

            previous_anchor = tail_anchor
            previous_rendered_id = round_id

    note_node("DROPPED_NO_CITATIONS",
              f"{datasets_dropped} dataset(s) with\n0 citing papers → dropped")
    dot.edge("FETCH_CITING", "DROPPED_NO_CITATIONS", style="dashed",
             color="#e64a19", fontcolor="#e64a19", label=" dropped ")

    output_path = dot.render(str(output_base), cleanup=True)
    print(f"Rendered to {output_path}")


if __name__ == "__main__":
    main()
