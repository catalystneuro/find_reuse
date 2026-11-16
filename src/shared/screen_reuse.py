"""
The screener: a decision model that reads a paper and says how likely it is to
have reused a dataset, before the classifier is asked.

Nine pairs in ten are a paper that cites a dataset's publication and never
touches the data. A decision model answers that in one pass for a tenth of the
classifier's price, so pairs it puts below a cutoff are settled there and only
the rest go on. It quotes nothing and cannot be checked against the paper, so
it never decides that a pair IS reuse: everything at or above the cutoff gets
the classifier's reading.

Measured on reviewers' calls (src.review.eval_decision_model): with
perplexity/pplx-decider-v1.3 and a cutoff of 0.1 on P(REUSE), all 354 confirmed
reuse pairs went on, and 6.5% of the classifier's MENTION and NEITHER pairs. On
2,955 further pairs, 99.0% of the classifier's REUSE calls and 9.3% of the rest.

The direct pathway, where the paper prints the dataset's identifier, has its own
question and a lower cutoff (see DEFAULT_DIRECT_CUTOFF).

The model must read whole papers. A window under about 200K tokens means
excerpts, and on excerpts no cutoff kept the reuse and dropped much else.
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path

import requests

ENDPOINT = 'https://openrouter.ai/api/v1/systemone'
DEFAULT_SCREENER = 'perplexity/pplx-decider-v1.3:free'
DEFAULT_CUTOFF = 0.1
# The direct pathway's cutoff is lower. On every direct pair of DANDI (484) and
# of CRCNS with text on hand (348), 0.05 sent on all 431 pairs the classifier
# called REUSE, and 49% and 80% of the pairs; 0.1 dropped three CRCNS REUSE pairs.
DEFAULT_DIRECT_CUTOFF = 0.05
CHARS_PER_TOKEN = 3    # conservative; scientific text runs nearer four

QUESTION = {
    'type': 'choice',
    'instructions': (
        'How does the paper relate to the dataset and the work that published it? '
        'Data reuse is the rare case: most references to other work are background. '
        'Answer REUSE only when the passages show the authors actually obtained and '
        'analyzed the data. A review, perspective or commentary does not reuse data.'),
    'criteria': {
        'REUSE': ('The authors obtained and analyzed the actual data (recordings, images, '
                  'behavioral traces, scans, spike trains) that they did not collect '
                  'themselves: downloading, re-analyzing, re-processing, training on, or '
                  'benchmarking against it.'),
        'MENTION': ('The paper cites or refers to the work without reusing its data: as '
                    'background, prior findings, methods or comparison, or only as an '
                    'entry in the reference list.'),
        'NEITHER': 'The paper does not cite or refer to the work at all.',
    },
}

REUSED = {
    'type': 'noul',
    'instructions': (
        'Did the authors of the paper obtain and analyze the actual data of the dataset '
        '(or of the work that published it), data they did not collect themselves? '
        'Citing the work, discussing its findings or comparing with its results is not '
        'analyzing its data.'),
}


# The direct pathway's question. The paper prints the dataset's identifier, so
# what is open is who its authors are with respect to the dataset, in the
# classifier's own three terms. Worded for any archive.
DIRECT_QUESTION = {
    'type': 'choice',
    'instructions': (
        'The paper names this dataset by its identifier. Who are the paper\'s authors with '
        'respect to the dataset? Decide from whether the text shows the authors GENERATING '
        'the data or OBTAINING it. A paper can deposit its own data and also reuse other '
        'datasets; judge only the dataset named in the state.'),
    'criteria': {
        'PRIMARY': ('This paper produced and published the dataset. The authors collected the '
                    'data and deposited it; the identifier points readers to their own deposit '
                    '("data are available at", "we deposited").'),
        'REUSE': ('The authors obtained and analyzed data they did not collect: downloading, '
                  're-analyzing, re-processing, training on, or benchmarking against someone '
                  'else\'s deposit.'),
        'NEITHER': ('The identifier appears without either relationship: a bibliography entry, '
                    'a passing mention, related work the authors did not touch, a review or '
                    'commentary, or a string that is not really this dataset.'),
    },
}


def fit(text: str, context_tokens: int) -> str:
    """The text, with the middle cut out if it would overflow the model's window."""
    limit = int(context_tokens * 0.9) * CHARS_PER_TOKEN
    if len(text) <= limit:
        return text
    return text[:limit // 2] + '\n[...]\n' + text[-limit // 2:]


def context_length(model: str) -> int:
    models = requests.get('https://openrouter.ai/api/v1/models',
                          params={'output_modalities': 'decisions'}, timeout=60).json()['data']
    return next(m['context_length'] for m in models if m['id'] == model)


def new_session() -> requests.Session:
    session = requests.Session()
    session.headers['Authorization'] = f"Bearer {os.environ['OPENROUTER_API_KEY']}"
    return session


def ask(session: requests.Session, model: str, state: dict,
        questions: dict | None = None) -> dict:
    """One decision, retried while the endpoint is rate-limited or briefly down."""
    questions = questions or {'relation': QUESTION, 'reused': REUSED}
    for attempt in range(8):
        response = session.post(ENDPOINT, timeout=180, json={
            'model': model, 'state': state, 'questions': questions})
        if response.status_code in (429, 500, 502, 503, 504):
            time.sleep(min(60, 5 * 2 ** attempt))
            continue
        response.raise_for_status()
        return response.json()
    raise RuntimeError(f'{model} kept refusing: HTTP {response.status_code}')


def paper_state(passages: list[str], whole: bool, *, dandiset_id: str, dandiset_name: str,
                published_in: str, title: str, doi: str) -> dict:
    """What the model is shown. One shape for the eval and for production."""
    return {
        # Other archives' datasets come through the same fields: a CRCNS dataset
        # is 'hc-3', and calling it DANDI:hc-3 would point the model the wrong way.
        'dataset': {('dandiset' if dandiset_id.isdigit() else 'dataset'):
                    (f'DANDI:{dandiset_id}' if dandiset_id.isdigit() else dandiset_id),
                    'name': dandiset_name, 'published_in': published_in},
        'paper': {'title': title, 'doi': doi},
        ('text' if whole else 'passages'): passages,
    }


def direct_state(text: str, *, archive: str, dataset_id: str,
                 matched_patterns: list[str] | None, title: str, doi: str) -> dict:
    """What the model is shown for a paper that prints a dataset's identifier."""
    dataset = {'archive': archive, 'identifier': dataset_id}
    if matched_patterns:
        dataset['named_in_the_paper_as'] = sorted(set(matched_patterns))[:10]
    return {'dataset': dataset, 'paper': {'title': title, 'doi': doi}, 'text': [text]}


def screen(text: str, model: str, session: requests.Session, context_tokens: int,
           direct: bool = False, **pair) -> dict:
    """
    The screener's answer for one pair: its choice, P(REUSE) and what it cost.

    `direct` asks the direct pathway's question, about a paper that prints the
    dataset's identifier; otherwise the paper cites the dataset's publication.

    Raises on failure. The caller sends a pair the screener could not answer on
    to the classifier, since a pair dropped here is never looked at again.
    """
    text = fit(text, context_tokens)
    if direct:
        answer = ask(session, model, direct_state(text, **pair), {'relation': DIRECT_QUESTION})
    else:
        answer = ask(session, model, paper_state([text], True, **pair))
    relation = answer['answers']['relation']
    usage = answer.get('usage') or {}
    return {'choice': relation['choice'], 'probabilities': relation['probabilities'],
            'p_reuse': relation['probabilities'].get('REUSE', 0.0),
            'confidence': relation.get('confidence'),
            'reused': (answer['answers'].get('reused') or {}).get('noul'),
            'usage': {**usage, 'cost': usage.get('cost', 0),
                      'prompt_tokens': usage.get('input_tokens', 0), 'completion_tokens': 0}}


def slug(model: str) -> str:
    return re.sub(r'[^A-Za-z0-9._-]+', '_', model)


def screener_cache_dir(repo: Path, model: str, direct: bool = False, archive: str = '') -> Path:
    """Answers are kept per model, pathway and archive: the same paper and identifier
    is a different question in each."""
    name = slug(model) + (f'__direct_{slug(archive)}' if direct else '')
    return repo / '.screener_cache' / name
