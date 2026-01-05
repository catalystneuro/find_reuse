import json
from collections import defaultdict
from datetime import datetime
import matplotlib.pyplot as plt


def load_results(filepath='results_dandi.json'):
    """Load the results JSON file."""
    with open(filepath, 'r') as f:
        return json.load(f)


EXCLUDED_DOIS = [
    '10.7554/elife.78362',  # NWB ecosystem paper with 70 datasets
]


def count_citations_by_quarter(data):
    """Count DANDI dataset citations by quarter."""
    quarterly_counts = defaultdict(int)

    for paper in data['results']:
        if 'archives' not in paper or 'DANDI Archive' not in paper['archives']:
            continue

        # Skip excluded papers
        if paper.get('doi', '').lower() in EXCLUDED_DOIS:
            continue

        dandi_data = paper['archives']['DANDI Archive']
        dataset_count = len(dandi_data.get('dataset_ids', []))

        if dataset_count == 0:
            continue

        date_str = paper.get('date')
        if not date_str:
            continue

        try:
            pub_date = datetime.strptime(date_str, '%Y-%m-%d')
            quarter = (pub_date.month - 1) // 3 + 1
            quarter_key = f"{pub_date.year}-Q{quarter}"
            quarterly_counts[quarter_key] += dataset_count
        except ValueError:
            continue

    return quarterly_counts


def plot_citations(quarterly_counts, output_path='dandi_citations_quarterly.png'):
    """Create and save a cumulative line plot of citations by quarter."""
    sorted_quarters = sorted(quarterly_counts.keys())
    counts = [quarterly_counts[q] for q in sorted_quarters]

    # Calculate cumulative counts
    cumulative_counts = []
    total = 0
    for c in counts:
        total += c
        cumulative_counts.append(total)

    fig, ax = plt.subplots(figsize=(8, 8))
    ax.plot(range(len(sorted_quarters)), cumulative_counts, color='#2196F3',
            linewidth=2, marker='o', markersize=6)

    ax.set_xlabel('Quarter', fontsize=12)
    ax.set_ylabel('Cumulative DANDI Datasets Cited', fontsize=12)
    ax.set_title('Cumulative DANDI Dataset Citations Over Time (by Quarter)', fontsize=14)
    ax.set_xticks(range(len(sorted_quarters)))
    ax.set_xticklabels(sorted_quarters, rotation=45, ha='right')

    # Add major grid lines at year boundaries (Q1 of each year)
    year_indices = [i for i, q in enumerate(sorted_quarters) if q.endswith('-Q1')]
    for idx in year_indices:
        ax.axvline(x=idx, color='gray', linestyle='--', linewidth=1, alpha=0.7)

    plt.tight_layout()
    plt.savefig(output_path, dpi=150)
    print(f"Plot saved to {output_path}")

    return fig, ax


def main():
    data = load_results()
    quarterly_counts = count_citations_by_quarter(data)

    print("Quarter data:")
    for q in sorted(quarterly_counts.keys()):
        print(f"  {q}: {quarterly_counts[q]} datasets cited")

    plot_citations(quarterly_counts)

    total_papers = len([p for p in data['results'] if 'DANDI Archive' in p.get('archives', {})])
    total_citations = sum(quarterly_counts.values())
    print(f"\nTotal papers with DANDI datasets: {total_papers}")
    print(f"Total dataset citations: {total_citations}")


if __name__ == '__main__':
    main()
