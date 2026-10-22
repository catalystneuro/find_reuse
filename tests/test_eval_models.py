"""Tests for scoring a model against the frozen eval set."""

import json

import pytest

import src.review.eval_models as E
from src.shared.run_fulltext_classification import build_worklist


def corpus(*pairs):
    """A corpus holding (citing DOI, dandiset) pairs, each citing 10.1/primary-<dandiset>."""
    records = {}
    for citing, dandiset in pairs:
        records.setdefault(dandiset, {'dandiset_id': dandiset, 'dandiset_name': f'set {dandiset}',
                                      'citing_papers': []})
        records[dandiset]['citing_papers'].append(
            {'doi': citing, 'title': f'paper {citing}', 'cited_paper_doi': f'10.1/primary-{dandiset}'})
    return {'results': list(records.values())}


def classification(doi, dandiset, label):
    return {'citing_doi': doi, 'dandiset_id': dandiset, 'classification': label}


@pytest.fixture
def eval_set():
    pairs = [(f'10.1/r{i}', '000001') for i in range(4)] + \
            [(f'10.1/n{i}', '000002') for i in range(3)] + \
            [('10.1/amb', '000003'), ('10.1/gone', '000004'), ('10.1/err', '000001')] + \
            [(f'10.1/m{i}', '000005') for i in range(5)]
    calls = {**{(f'10.1/r{i}', '000001'): 'reuse' for i in range(4)},
             **{(f'10.1/n{i}', '000002'): 'neither' for i in range(3)},
             ('10.1/amb', '000003'): 'ambiguous_reuse',
             ('10.1/gone', '000099'): 'reuse',
             ('10.1/err', '000001'): 'reuse'}
    labels = [classification(d, ds, 'REUSE') for d, ds in pairs[:11]]
    labels[-1] = classification('10.1/err', '000001', 'ERROR')
    labels += [classification(f'10.1/m{i}', '000005', 'MENTION') for i in range(5)]
    return E.build_eval_set(corpus(*pairs), labels, calls)


class TestBuildEvalSet:
    def test_keeps_reviewed_pairs_that_are_still_in_the_corpus(self, eval_set):
        kinds = {(p['doi'], p['kind']) for p in eval_set['pairs']}
        assert ('10.1/r0', 'reuse') in kinds
        assert ('10.1/n0', 'not_reuse') in kinds
        assert ('10.1/amb', 'ambiguous') in kinds
        assert not any(p['doi'] == '10.1/gone' for p in eval_set['pairs'])

    def test_leaves_out_pairs_the_baseline_failed_on(self, eval_set):
        assert not any(p['doi'] == '10.1/err' for p in eval_set['pairs'])

    def test_negatives_are_unreviewed_mention_or_neither_calls(self, eval_set):
        negatives = [p for p in eval_set['pairs'] if p['kind'] == 'negative']
        assert len(negatives) == 5
        assert all(p['review'] is None and p['baseline'] == 'MENTION' for p in negatives)

    def test_is_the_same_every_time_it_is_built(self, eval_set):
        pairs = [(f'10.1/r{i}', '000001') for i in range(4)] + \
                [(f'10.1/n{i}', '000002') for i in range(3)] + \
                [('10.1/amb', '000003'), ('10.1/gone', '000004'), ('10.1/err', '000001')] + \
                [(f'10.1/m{i}', '000005') for i in range(5)]
        calls = {(f'10.1/r{i}', '000001'): 'reuse' for i in range(4)}
        labels = [classification(d, ds, 'REUSE') for d, ds in pairs[:4]] + \
                 [classification(f'10.1/m{i}', '000005', 'MENTION') for i in range(5)]
        first = E.build_eval_set(corpus(*pairs), labels, calls)
        second = E.build_eval_set(corpus(*pairs), labels, calls)
        assert first['pairs'] == second['pairs']


class TestAsCorpus:
    def test_the_classifier_reads_back_every_pair(self, eval_set, tmp_path):
        path = tmp_path / 'corpus.json'
        path.write_text(json.dumps(E.as_corpus(eval_set['pairs'])))

        work = build_worklist(path)

        assert sorted((w['doi'], w['dandiset_id']) for w in work) == \
            sorted((p['doi'], p['dandiset_id']) for p in eval_set['pairs'])
        assert all(w['primary_paper_doi'] == f"10.1/primary-{w['dandiset_id']}" for w in work)


class TestScore:
    @staticmethod
    def pair(doi, kind):
        return {'doi': doi, 'dandiset_id': '000001', 'kind': kind}

    @staticmethod
    def result(doi, label, cost=0.01, bad_quotes=0):
        return {('10.1/' + doi, '000001'): {
            'classification': label, 'evidence_quotes': [{'quote': 'q'}, {'quote': 'q'}],
            'hallucinated_quote_count': bad_quotes,
            'usage': {'cost': cost, 'prompt_tokens': 1000, 'completion_tokens': 100}}}

    def test_recall_false_positives_and_precision(self):
        pairs = [self.pair('10.1/a', 'reuse'), self.pair('10.1/b', 'reuse'),
                 self.pair('10.1/c', 'not_reuse'), self.pair('10.1/d', 'not_reuse')]
        results = {**self.result('a', 'REUSE'), **self.result('b', 'MENTION'),
                   **self.result('c', 'REUSE'), **self.result('d', 'NEITHER')}

        s = E.score(pairs, results)

        assert s['reuse_recall'] == 0.5
        assert s['false_positive_rate'] == 0.5
        assert s['precision'] == 0.5

    def test_precision_is_weighted_to_the_whole_set(self):
        pairs = [self.pair('10.1/a', 'reuse'), self.pair('10.1/b', 'not_reuse')]
        results = {**self.result('a', 'REUSE'), **self.result('b', 'REUSE')}

        s = E.score(pairs, results, weights={'reuse': 3, 'not_reuse': 1})

        assert s['precision'] == 0.75

    def test_a_failed_or_missing_pair_counts_against_the_model(self):
        pairs = [self.pair('10.1/a', 'reuse'), self.pair('10.1/b', 'reuse')]
        results = self.result('a', 'ERROR')

        s = E.score(pairs, results)

        assert s['reuse_recall'] == 0.0
        assert s['error_rate'] == 1.0

    def test_lists_negatives_the_model_calls_reuse(self):
        pairs = [self.pair('10.1/a', 'negative'), self.pair('10.1/b', 'negative')]
        results = {**self.result('a', 'REUSE'), **self.result('b', 'MENTION')}

        s = E.score(pairs, results)

        assert s['negatives_called_reuse'] == 0.5
        assert s['negatives_flipped_to_reuse'] == ['10.1/a 000001']

    def test_cost_and_quotes_per_pair(self):
        pairs = [self.pair('10.1/a', 'reuse'), self.pair('10.1/b', 'reuse')]
        results = {**self.result('a', 'REUSE', cost=0.01, bad_quotes=1),
                   **self.result('b', 'REUSE', cost=0.03)}

        s = E.score(pairs, results)

        assert s['cost_per_pair'] == 0.02
        assert s['unverified_quote_rate'] == 0.25
        assert s['input_tokens_per_pair'] == 1000


class TestModelSlug:
    def test_is_a_safe_directory_name(self):
        assert E.model_slug('anthropic/claude-haiku-5.5', 'high') == 'anthropic_claude-haiku-5.5__high'
