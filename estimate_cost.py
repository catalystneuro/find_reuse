#!/usr/bin/env python3
"""
estimate_cost.py - Back-of-the-envelope cost for whole-paper LLM classification

Motivation
----------
The citation-context extraction stage (citation_context.py / extract_citation_contexts.py)
is brittle and hard-coded. An alternative is to skip excerpt extraction entirely and feed
each citing paper's full text into the LLM to classify data reuse. The main objection is
cost. This script answers "how much would that actually cost?" by measuring the real text
already sitting in the paper cache and applying current model pricing.

It is intentionally a rough estimate:
  - Token counts are approximated from character counts (chars / chars_per_token).
  - Pricing is supplied via flags (defaults to Gemini 3 Flash Preview on OpenRouter).

Both the per-paper output size AND the current excerpt-approach input size are
measured from real data rather than assumed:
  - Output tokens are averaged over the model's actual responses in a prior run's
    classifications.json (each entry carries the classification/confidence/
    reasoning fields the model emitted).
  - Excerpt input tokens are averaged over the real prompts reconstructed from the
    same classifications.json (fixed template + the actual context_excerpts that
    were fed in), using the live build_classification_prompt().

Usage
-----
    python estimate_cost.py
    python estimate_cost.py --cache-dir .paper_cache
    python estimate_cost.py --examples output/indirect/dandi/classifications.json
    python estimate_cost.py --input-price 0.30 --output-price 2.50
    python estimate_cost.py --json
"""

import argparse
import json
import statistics
from pathlib import Path

from src.indirect_pipeline.classify_citing_papers import build_classification_prompt


# Defaults: Gemini 3 Flash Preview on OpenRouter, USD per 1M tokens (June 2026).
DEFAULT_INPUT_PRICE_PER_MILLION = 0.50
DEFAULT_OUTPUT_PRICE_PER_MILLION = 3.00

# Rough English-text heuristic used by most tokenizers.
DEFAULT_CHARS_PER_TOKEN = 4.0

# Real classification examples to measure excerpt-input and output sizes from.
DEFAULT_EXAMPLES_FILE = Path("output/indirect/crcns/classifications.json")

# Fields the model emits, in schema order (RESPONSE_SCHEMA in classify_citing_papers).
OUTPUT_FIELDS = [
    "classification",
    "confidence",
    "same_lab",
    "same_lab_confidence",
    "source_archive",
    "reasoning",
]


def collect_text_lengths(cache_dir: Path) -> list[int]:
    """Return the character length of every cached paper that has text."""
    lengths = []
    for cache_file in sorted(cache_dir.glob("*.json")):
        with open(cache_file) as f:
            paper = json.load(f)
        text = paper.get("text", "")
        if text:
            lengths.append(len(text))
    return lengths


def measure_examples(examples_file: Path, chars_per_token: float) -> dict:
    """
    Measure real input and output sizes from a prior run's classifications.json.

    Each classification entry carries the excerpts that were fed in
    (context_excerpts) and the fields the model emitted (classification,
    confidence, reasoning, ...). We reconstruct the actual prompt with the live
    build_classification_prompt() and serialize the emitted fields back into the
    JSON shape the model returned, then average the character lengths.

    Note: the reconstructed prompt omits a few optional clauses that aren't stored
    per-entry (dataset description, author-year string, deposit DOI), so the
    excerpt input is a slight under-estimate. The dominant fixed template and the
    real excerpts are captured exactly.
    """
    with open(examples_file) as f:
        data = json.load(f)
    classifications = data["classifications"]

    input_chars = []
    output_chars = []
    no_excerpt_count = 0
    for entry in classifications:
        excerpts = entry.get("context_excerpts", [])
        if not excerpts:
            no_excerpt_count += 1
        contexts = [
            {
                "context": excerpt.get("text", ""),
                "method": excerpt.get("method", ""),
                "reference_number": excerpt.get("reference_number"),
            }
            for excerpt in excerpts
        ]
        prompt = build_classification_prompt(
            contexts=contexts,
            dandiset_id=entry["dandiset_id"],
            dandiset_name=entry.get("dandiset_name", ""),
            cited_doi=entry["cited_doi"],
            citing_doi=entry["citing_doi"],
        )
        input_chars.append(len(prompt))

        emitted = {field: entry.get(field) for field in OUTPUT_FIELDS}
        output_chars.append(len(json.dumps(emitted)))

    return {
        "example_count": len(classifications),
        "excerpt_input_chars_mean": statistics.mean(input_chars),
        "excerpt_input_tokens_mean": statistics.mean(input_chars) / chars_per_token,
        "output_chars_mean": statistics.mean(output_chars),
        "output_tokens_mean": statistics.mean(output_chars) / chars_per_token,
        "no_excerpt_fraction": no_excerpt_count / len(classifications),
    }


def percentile(sorted_values: list[int], fraction: float) -> int:
    """Nearest-rank percentile of an already-sorted list."""
    index = min(int(len(sorted_values) * fraction), len(sorted_values) - 1)
    return sorted_values[index]


def _breakdown(
    total_input_tokens: float,
    input_tokens_per_call: float,
    output_tokens_per_call: float,
    paper_count: int,
    input_price_per_million: float,
    output_price_per_million: float,
) -> dict:
    """Cost breakdown for one approach across the whole corpus."""
    total_output_tokens = output_tokens_per_call * paper_count
    input_cost = total_input_tokens / 1_000_000 * input_price_per_million
    output_cost = total_output_tokens / 1_000_000 * output_price_per_million
    per_paper_cost = (
        input_tokens_per_call / 1_000_000 * input_price_per_million
        + output_tokens_per_call / 1_000_000 * output_price_per_million
    )
    return {
        "input_tokens_per_call": input_tokens_per_call,
        "output_tokens_per_call": output_tokens_per_call,
        "total_input_tokens": total_input_tokens,
        "total_output_tokens": total_output_tokens,
        "input_cost": input_cost,
        "output_cost": output_cost,
        "total_cost": input_cost + output_cost,
        "per_paper_cost": per_paper_cost,
    }


def estimate(
    char_lengths: list[int],
    chars_per_token: float,
    output_tokens: float,
    excerpt_input_tokens: float,
    no_excerpt_fraction: float,
    input_price_per_million: float,
    output_price_per_million: float,
) -> dict:
    """
    Compute side-by-side cost estimates for three approaches:
      - excerpt:     every paper classified from its extracted excerpts
      - whole_paper: every paper classified from its full text
      - hybrid:      excerpts when available, full text only as a fallback for the
                     no_excerpt_fraction of papers where extraction found nothing

    output_tokens and excerpt_input_tokens are measured per-call averages from a
    prior run (see measure_examples); the whole-paper input comes from the cached
    full-text character lengths. All approaches run over the same paper_count
    papers and emit the same classification JSON, so they share output_tokens.

    The hybrid fallback subset is priced at the mean full-text length, since the
    examples don't link a no-excerpt classification to its cached text length.
    """
    sorted_lengths = sorted(char_lengths)
    paper_count = len(sorted_lengths)
    mean_tokens = statistics.mean(sorted_lengths) / chars_per_token
    whole_paper_total_input = sum(sorted_lengths) / chars_per_token

    whole_paper = _breakdown(
        total_input_tokens=whole_paper_total_input,
        input_tokens_per_call=mean_tokens,
        output_tokens_per_call=output_tokens,
        paper_count=paper_count,
        input_price_per_million=input_price_per_million,
        output_price_per_million=output_price_per_million,
    )
    excerpt = _breakdown(
        total_input_tokens=excerpt_input_tokens * paper_count,
        input_tokens_per_call=excerpt_input_tokens,
        output_tokens_per_call=output_tokens,
        paper_count=paper_count,
        input_price_per_million=input_price_per_million,
        output_price_per_million=output_price_per_million,
    )

    fallback_papers = paper_count * no_excerpt_fraction
    excerpt_papers = paper_count - fallback_papers
    hybrid_total_input = (
        excerpt_papers * excerpt_input_tokens + fallback_papers * mean_tokens
    )
    hybrid = _breakdown(
        total_input_tokens=hybrid_total_input,
        input_tokens_per_call=hybrid_total_input / paper_count,
        output_tokens_per_call=output_tokens,
        paper_count=paper_count,
        input_price_per_million=input_price_per_million,
        output_price_per_million=output_price_per_million,
    )

    return {
        "paper_count": paper_count,
        "chars_per_token": chars_per_token,
        "no_excerpt_fraction": no_excerpt_fraction,
        "input_price_per_million": input_price_per_million,
        "output_price_per_million": output_price_per_million,
        "char_length": {
            "median": statistics.median(sorted_lengths),
            "mean": statistics.mean(sorted_lengths),
            "p10": percentile(sorted_lengths, 0.10),
            "p90": percentile(sorted_lengths, 0.90),
            "max": sorted_lengths[-1],
        },
        "approaches": {
            "excerpt": excerpt,
            "hybrid": hybrid,
            "whole_paper": whole_paper,
        },
        "cost_multiplier_vs_excerpt": whole_paper["total_cost"] / excerpt["total_cost"],
    }


def print_report(report: dict) -> None:
    char = report["char_length"]
    excerpt = report["approaches"]["excerpt"]
    hybrid = report["approaches"]["hybrid"]
    whole = report["approaches"]["whole_paper"]
    fallback_pct = report["no_excerpt_fraction"] * 100

    print("Excerpt vs hybrid vs whole-paper classification cost")
    print("=" * 74)
    print(f"Papers in corpus:             {report['paper_count']:,}")
    print(f"Measured from prior run:      {report['examples']['example_count']:,} classifications")
    print(f"No-excerpt fallback rate:     {fallback_pct:.1f}%  (hybrid sends these whole)")
    print(f"Chars per token assumed:      {report['chars_per_token']}")
    print(
        f"Pricing (per 1M tokens):      "
        f"${report['input_price_per_million']:.2f} in / "
        f"${report['output_price_per_million']:.2f} out"
    )
    print()
    print("Per-paper full-text length (chars / est. tokens):")
    print(f"  median:  {char['median']:>10,.0f}  /  {char['median'] / report['chars_per_token']:>9,.0f}")
    print(f"  mean:    {char['mean']:>10,.0f}  /  {char['mean'] / report['chars_per_token']:>9,.0f}")
    print(f"  p90:     {char['p90']:>10,.0f}  /  {char['p90'] / report['chars_per_token']:>9,.0f}")
    print(f"  max:     {char['max']:>10,.0f}")
    print()

    label_width = 26
    column = ">16"
    header = f"{'':<{label_width}}{'Excerpt':>16}{'Hybrid':>16}{'Whole-paper':>16}"
    print(header)
    print("-" * len(header))

    def row(label: str, key, formatter):
        print(
            f"{label:<{label_width}}"
            f"{formatter(excerpt[key]):{column}}"
            f"{formatter(hybrid[key]):{column}}"
            f"{formatter(whole[key]):{column}}"
        )

    tokens = lambda value: f"{value:,.0f}"
    dollars = lambda value: f"${value:,.2f}"
    cents = lambda value: f"${value:,.4f}"

    row("input tokens / call (avg)", "input_tokens_per_call", tokens)
    row("output tokens / call", "output_tokens_per_call", tokens)
    row("cost / paper (avg)", "per_paper_cost", cents)
    print("-" * len(header))
    row("total input tokens", "total_input_tokens", tokens)
    row("total output tokens", "total_output_tokens", tokens)
    row("input cost", "input_cost", dollars)
    row("output cost", "output_cost", dollars)
    row("TOTAL", "total_cost", dollars)
    print("-" * len(header))
    print()
    print(
        f"vs excerpt baseline (${excerpt['total_cost']:,.2f}):  "
        f"hybrid +${hybrid['total_cost'] - excerpt['total_cost']:,.2f} "
        f"({hybrid['total_cost'] / excerpt['total_cost']:.1f}x),  "
        f"whole-paper +${whole['total_cost'] - excerpt['total_cost']:,.2f} "
        f"({report['cost_multiplier_vs_excerpt']:.1f}x)."
    )


def format_markdown(report: dict) -> str:
    """Render the report as a standalone Markdown document."""
    char = report["char_length"]
    chars_per_token = report["chars_per_token"]
    excerpt = report["approaches"]["excerpt"]
    hybrid = report["approaches"]["hybrid"]
    whole = report["approaches"]["whole_paper"]
    fallback_pct = report["no_excerpt_fraction"] * 100

    def tokens(value):
        return f"{value:,.0f}"

    def dollars(value):
        return f"${value:,.2f}"

    def cents(value):
        return f"${value:,.4f}"

    def row(label, key, formatter):
        return (
            f"| {label} | {formatter(excerpt[key])} | "
            f"{formatter(hybrid[key])} | {formatter(whole[key])} |"
        )

    lines = [
        "# Citation-reuse classification: cost of whole-paper vs excerpts",
        "",
        "Back-of-the-envelope comparison of three ways to feed citing papers to the",
        "LLM reuse classifier. Generated by `estimate_cost.py`; re-run that script to",
        "refresh these numbers.",
        "",
        "- **Excerpt** — every paper classified from its extracted citation excerpts (current pipeline).",
        "- **Hybrid** — excerpts when available; full text only as a fallback for papers where extraction found nothing.",
        "- **Whole-paper** — every paper classified from its full text, skipping excerpt extraction entirely.",
        "",
        "## Parameters",
        "",
        f"- **Papers in corpus:** {report['paper_count']:,} (cached full text in `.paper_cache/`)",
        f"- **Measured from prior run:** {report['examples']['example_count']:,} classifications",
        f"- **No-excerpt fallback rate:** {fallback_pct:.1f}% (the share the hybrid sends whole)",
        f"- **Chars per token assumed:** {chars_per_token}",
        (
            f"- **Pricing (per 1M tokens):** ${report['input_price_per_million']:.2f} input / "
            f"${report['output_price_per_million']:.2f} output"
        ),
        "",
        "Per-call output size and excerpt input size are measured from the model's",
        "actual responses and reconstructed prompts in the prior run, not assumed.",
        "",
        "## Per-paper full-text length",
        "",
        "| Percentile | Characters | Est. tokens |",
        "| --- | ---: | ---: |",
        f"| median | {char['median']:,.0f} | {char['median'] / chars_per_token:,.0f} |",
        f"| mean | {char['mean']:,.0f} | {char['mean'] / chars_per_token:,.0f} |",
        f"| p90 | {char['p90']:,.0f} | {char['p90'] / chars_per_token:,.0f} |",
        f"| max | {char['max']:,.0f} | {char['max'] / chars_per_token:,.0f} |",
        "",
        "## Cost comparison",
        "",
        "| Metric | Excerpt | Hybrid | Whole-paper |",
        "| --- | ---: | ---: | ---: |",
        row("input tokens / call (avg)", "input_tokens_per_call", tokens),
        row("output tokens / call", "output_tokens_per_call", tokens),
        row("cost / paper (avg)", "per_paper_cost", cents),
        row("total input tokens", "total_input_tokens", tokens),
        row("total output tokens", "total_output_tokens", tokens),
        row("input cost", "input_cost", dollars),
        row("output cost", "output_cost", dollars),
        row("**TOTAL**", "total_cost", dollars),
        "",
        "## Bottom line",
        "",
        (
            f"Against the **{dollars(excerpt['total_cost'])}** excerpt baseline, the **hybrid** adds "
            f"**+{dollars(hybrid['total_cost'] - excerpt['total_cost'])}** "
            f"({hybrid['total_cost'] / excerpt['total_cost']:.1f}x) and **whole-paper** adds "
            f"**+{dollars(whole['total_cost'] - excerpt['total_cost'])}** "
            f"({report['cost_multiplier_vs_excerpt']:.1f}x) for one full pass over the corpus."
        ),
        "",
        "Output cost is identical across all three approaches: the classifier emits the",
        "same small JSON regardless of input size, so the entire difference is input tokens.",
        "",
        "### Caveats",
        "",
        "- Token counts are approximated as `characters / "
        f"{chars_per_token}`, not a real tokenizer.",
        "- The hybrid fallback subset is priced at the *mean* full-text length, since the",
        "  examples don't link a no-excerpt classification to its cached text length. If",
        "  no-excerpt papers skew shorter (often the truncated texts that yielded nothing),",
        "  the hybrid figure is a slight over-estimate.",
        "- The reconstructed excerpt prompt omits a few optional clauses not stored per-entry",
        "  (dataset description, author-year string, deposit DOI), so excerpt input is a",
        "  slight under-estimate.",
        "- This prices only tokens. The hybrid's quality risk — *bad* excerpts that get",
        "  retrieved and so never trigger the whole-paper fallback — is not a token cost and",
        "  does not appear here.",
        "",
    ]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="Back-of-the-envelope cost for whole-paper LLM classification"
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=Path(".paper_cache"),
        help="Directory of cached paper JSON files (default: .paper_cache)",
    )
    parser.add_argument(
        "--examples",
        type=Path,
        default=DEFAULT_EXAMPLES_FILE,
        help=(
            "Prior run's classifications.json to measure real per-call output and "
            f"excerpt-input sizes from (default: {DEFAULT_EXAMPLES_FILE})"
        ),
    )
    parser.add_argument(
        "--chars-per-token",
        type=float,
        default=DEFAULT_CHARS_PER_TOKEN,
        help=f"Characters per token heuristic (default: {DEFAULT_CHARS_PER_TOKEN})",
    )
    parser.add_argument(
        "--input-price",
        type=float,
        default=DEFAULT_INPUT_PRICE_PER_MILLION,
        help=f"USD per 1M input tokens (default: {DEFAULT_INPUT_PRICE_PER_MILLION})",
    )
    parser.add_argument(
        "--output-price",
        type=float,
        default=DEFAULT_OUTPUT_PRICE_PER_MILLION,
        help=f"USD per 1M output tokens (default: {DEFAULT_OUTPUT_PRICE_PER_MILLION})",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit the report as JSON instead of a formatted table",
    )
    parser.add_argument(
        "--markdown",
        action="store_true",
        help="Emit the report as a standalone Markdown document instead of a table",
    )
    args = parser.parse_args()

    measured = measure_examples(args.examples, args.chars_per_token)
    char_lengths = collect_text_lengths(args.cache_dir)
    report = estimate(
        char_lengths=char_lengths,
        chars_per_token=args.chars_per_token,
        output_tokens=measured["output_tokens_mean"],
        excerpt_input_tokens=measured["excerpt_input_tokens_mean"],
        no_excerpt_fraction=measured["no_excerpt_fraction"],
        input_price_per_million=args.input_price,
        output_price_per_million=args.output_price,
    )
    report["examples"] = measured

    if args.json:
        print(json.dumps(report, indent=2))
    elif args.markdown:
        print(format_markdown(report))
    else:
        print_report(report)


if __name__ == "__main__":
    main()
