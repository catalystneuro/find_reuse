"""
Tests for full-text reuse classification.

The bulk of these cover one rule: a failure must never be reported as a
classification. Every transport error, malformed body, and unrecognized label
has to come back as ERROR, because a failure quietly recorded as REUSE would
manufacture false positives, and one recorded as MENTION would be
indistinguishable from a real negative.

No network access; the API call is stubbed.
"""

import json

import pytest
import requests

from src.shared import classify_fulltext_reuse as C


class FakeResponse:
    def __init__(self, status_code, text='', payload=None):
        self.status_code = status_code
        self.text = text
        self._payload = payload

    def json(self):
        if self._payload is None:
            raise ValueError('not json')
        return self._payload


def envelope(content, finish_reason='stop'):
    return {'choices': [{'message': {'content': content},
                         'finish_reason': finish_reason}],
            'usage': {'total_tokens': 1}}


def stub_post(monkeypatch, response=None, exception=None):
    def fake(*args, **kwargs):
        if exception is not None:
            raise exception
        return response
    monkeypatch.setattr(requests, 'post', fake)


def assert_is_error(result, kind=None):
    assert result['classification'] == 'ERROR'
    assert result['confidence'] == 0
    assert result['evidence_quotes'] == []
    assert result['error']
    if kind:
        assert result['error_kind'] == kind


# --------------------------------------------------------------------------- #
# Failures must never become classifications
# --------------------------------------------------------------------------- #

class TestFailuresNeverClassify:
    def test_empty_input(self):
        assert_is_error(C.classify_paper_reuse(''), 'empty_input')

    def test_whitespace_only_input(self):
        assert_is_error(C.classify_paper_reuse('   \n\t '), 'empty_input')

    def test_missing_api_key(self, monkeypatch):
        monkeypatch.delenv('DEEPSEEK_API_KEY', raising=False)
        monkeypatch.delenv('OPENROUTER_API_KEY', raising=False)
        monkeypatch.setattr(C, 'get_api_key_for', lambda model: None)
        assert_is_error(C.classify_paper_reuse('text'), 'no_api_key')

    @pytest.mark.parametrize('exc,kind', [
        (requests.Timeout('timed out'), 'network_error'),
        (requests.ConnectionError('refused'), 'network_error'),
        (requests.RequestException('bad'), 'request_error'),
        (ValueError('boom'), 'unexpected_error'),
    ])
    def test_transport_exceptions(self, monkeypatch, exc, kind):
        stub_post(monkeypatch, exception=exc)
        assert_is_error(
            C.classify_paper_reuse('text', api_key='k', max_retries=1), kind)

    @pytest.mark.parametrize('status', [400, 401, 403, 404, 429, 500, 503])
    def test_http_error_statuses(self, monkeypatch, status):
        stub_post(monkeypatch, FakeResponse(status, 'failure body'))
        assert_is_error(
            C.classify_paper_reuse('text', api_key='k', max_retries=1))

    def test_body_that_is_not_json(self, monkeypatch):
        stub_post(monkeypatch, FakeResponse(200, '<html>REUSE</html>'))
        assert_is_error(
            C.classify_paper_reuse('text', api_key='k', max_retries=1))

    def test_error_envelope(self, monkeypatch):
        stub_post(monkeypatch,
                  FakeResponse(200, payload={'error': {'message': 'nope'}}))
        assert_is_error(
            C.classify_paper_reuse('text', api_key='k'), 'api_error')

    def test_no_choices(self, monkeypatch):
        stub_post(monkeypatch, FakeResponse(200, payload={'choices': []}))
        assert_is_error(C.classify_paper_reuse('text', api_key='k'), 'no_choices')

    def test_empty_content_after_retries_is_an_error(self, monkeypatch):
        stub_post(monkeypatch, FakeResponse(200, payload=envelope('')))
        assert_is_error(
            C.classify_paper_reuse('text', api_key='k', max_retries=1), 'parse_error')

    def test_output_hit_token_cap(self, monkeypatch):
        stub_post(monkeypatch,
                  FakeResponse(200, payload=envelope('{"classi', 'length')))
        assert_is_error(C.classify_paper_reuse('text', api_key='k'),
                        'truncated_response')

    @pytest.mark.parametrize('content', [
        '<html>REUSE everywhere</html>',      # error page containing the word
        '"REUSE"',                            # bare JSON string, not an object
        'REUSE',                              # bare token
        '[]',                                 # JSON, wrong type
        '{"classification": "DEFINITELY"}',   # unrecognized label
        '{"classification": 5}',              # wrong type
        '{"confidence": 9}',                  # label missing entirely
        '{classification: REUSE}',            # malformed JSON
        'The answer is REUSE because...',     # prose mentioning the label
    ])
    def test_untrustworthy_content_never_classifies(self, monkeypatch, content):
        stub_post(monkeypatch, FakeResponse(200, payload=envelope(content)))
        assert_is_error(C.classify_paper_reuse('text', api_key='k'))


# --------------------------------------------------------------------------- #
# Strict parsing
# --------------------------------------------------------------------------- #

class TestProviderRouting:
    """
    OpenRouter serves the pinned snapshot from 28 providers at prices spanning
    3.5x and picks one per request. Unpinned, consecutive calls landed on
    different backends, which cost more and destroyed prefix caching because
    each provider keeps its own cache.
    """

    def test_openrouter_slug_is_detected_by_namespace(self):
        assert C.uses_openrouter('deepseek/deepseek-v4-flash-0731') is True
        assert C.uses_openrouter('deepseek-v4-flash') is False

    def test_openrouter_request_pins_the_provider(self, monkeypatch):
        captured = {}

        def fake(url, headers=None, json=None, timeout=None):
            captured['url'] = url
            captured['json'] = json
            return FakeResponse(200, payload=envelope(
                '{"classification": "MENTION", "confidence": 5, '
                '"evidence_quotes": [], "reasoning": "r"}'))

        monkeypatch.setattr(requests, 'post', fake)
        C.classify_paper_reuse(PAPER, api_key='k',
                               model='deepseek/deepseek-v4-flash-0731',
                               provider='DeepInfra')
        assert captured['url'] == C.OPENROUTER_API_URL
        assert captured['json']['provider'] == {
            'order': ['DeepInfra'], 'allow_fallbacks': False}

    def test_direct_deepseek_request_sends_no_provider_block(self, monkeypatch):
        captured = {}

        def fake(url, headers=None, json=None, timeout=None):
            captured['url'] = url
            captured['json'] = json
            return FakeResponse(200, payload=envelope(
                '{"classification": "MENTION", "confidence": 5, '
                '"evidence_quotes": [], "reasoning": "r"}'))

        monkeypatch.setattr(requests, 'post', fake)
        C.classify_paper_reuse(PAPER, api_key='k', model='deepseek-v4-flash')
        assert captured['url'] == C.DEEPSEEK_API_URL
        assert 'provider' not in captured['json']


class TestPromptLayout:
    def test_paper_precedes_the_instructions(self):
        """Prefix caching only works if the bulk of the prompt comes first."""
        prompt = C.build_prompt('UNIQUEPAPERBODY', dataset_id='000003')
        assert prompt.index('UNIQUEPAPERBODY') < 100
        assert prompt.count('UNIQUEPAPERBODY') == 1
        # The task must still be present, after the paper.
        assert 'CLASSIFICATIONS' in prompt
        assert prompt.index('CLASSIFICATIONS') > prompt.index('UNIQUEPAPERBODY')


class TestEmptyContentRetry:
    """
    A reasoning model can spend its thinking budget and emit no content, in an
    envelope that is otherwise a normal 200. Retrying gets an answer; not
    retrying manufactured 91 of the 122 errors in a full corpus run.
    """

    def test_retries_and_succeeds_when_content_arrives(self, monkeypatch):
        good = json.dumps({'classification': 'MENTION', 'confidence': 6,
                           'evidence_quotes': [], 'reasoning': 'r'})
        responses = [FakeResponse(200, payload=envelope('')),
                     FakeResponse(200, payload=envelope('')),
                     FakeResponse(200, payload=envelope(good))]
        calls = {'n': 0}

        def fake(*args, **kwargs):
            r = responses[min(calls['n'], len(responses) - 1)]
            calls['n'] += 1
            return r

        monkeypatch.setattr(requests, 'post', fake)
        monkeypatch.setattr(C.time, 'sleep', lambda *_: None)
        result = C.classify_paper_reuse('text', api_key='k', max_retries=3)
        assert result['classification'] == 'MENTION'
        assert calls['n'] == 3

    def test_does_not_retry_when_content_is_present(self, monkeypatch):
        bad = FakeResponse(200, payload=envelope('not json at all'))
        calls = {'n': 0}

        def fake(*args, **kwargs):
            calls['n'] += 1
            return bad

        monkeypatch.setattr(requests, 'post', fake)
        monkeypatch.setattr(C.time, 'sleep', lambda *_: None)
        assert_is_error(C.classify_paper_reuse('text', api_key='k', max_retries=3))
        # Content arrived; it was simply unusable. Retrying would not help.
        assert calls['n'] == 1


class TestRetryPreservesCorpus:
    """
    In retry mode the work list is only the failures. The output file must still
    describe the whole corpus: writing it from the work list alone silently
    replaced 11,578 results with the 122 that were retried.
    """

    def _entry(self, doi, classification):
        return json.dumps({'citing_doi': doi, 'dandiset_id': '000003',
                           'classification': classification, 'confidence': 5,
                           'evidence_quotes': [], 'source_quotes': []})

    def test_load_cached_results_can_exclude_errors(self, tmp_path):
        from src.shared import run_fulltext_classification as R
        (tmp_path / 'a.json').write_text(self._entry('10.1/a', 'REUSE'))
        (tmp_path / 'b.json').write_text(self._entry('10.1/b', 'ERROR'))
        (tmp_path / 'c.json').write_text(self._entry('10.1/c', 'MENTION'))

        everything = R.load_cached_results(tmp_path)
        survivors = R.load_cached_results(tmp_path, skip_errors=True)
        assert len(everything) == 3
        assert len(survivors) == 2
        assert all(r['classification'] != 'ERROR' for r in survivors)

    def test_retry_worklist_holds_only_errors(self, tmp_path):
        from src.shared import run_fulltext_classification as R
        (tmp_path / 'a.json').write_text(self._entry('10.1/a', 'REUSE'))
        (tmp_path / 'b.json').write_text(self._entry('10.1/b', 'ERROR'))

        work = R.build_retry_worklist(tmp_path)
        assert [w['doi'] for w in work] == ['10.1/b']

    def test_carried_and_retried_together_cover_everything(self, tmp_path):
        from src.shared import run_fulltext_classification as R
        (tmp_path / 'a.json').write_text(self._entry('10.1/a', 'REUSE'))
        (tmp_path / 'b.json').write_text(self._entry('10.1/b', 'ERROR'))
        (tmp_path / 'c.json').write_text(self._entry('10.1/c', 'MENTION'))

        carried = R.load_cached_results(tmp_path, skip_errors=True)
        work = R.build_retry_worklist(tmp_path)
        assert len(carried) + len(work) == 3


class TestParseStrict:
    def test_accepts_plain_object(self):
        assert C.parse_strict('{"classification": "REUSE"}')['classification'] == 'REUSE'

    def test_accepts_markdown_fenced(self):
        fenced = '```json\n{"classification": "MENTION"}\n```'
        assert C.parse_strict(fenced)['classification'] == 'MENTION'

    def test_normalizes_case(self):
        assert C.parse_strict('{"classification": "reuse"}')['classification'] == 'REUSE'

    @pytest.mark.parametrize('bad', [
        '', '   ', 'REUSE', '"REUSE"', '[]',
        '{"classification": "MAYBE"}', '{"foo": 1}',
    ])
    def test_rejects_untrustworthy(self, bad):
        with pytest.raises(C.ClassificationError):
            C.parse_strict(bad)


# --------------------------------------------------------------------------- #
# Quote verification
# --------------------------------------------------------------------------- #

PAPER = (
    'We recorded from mouse hippocampus. '
    'The dataset used here is publicly available on the CRCNS website as hc-26. '
    'We thank the Buzsaki lab.'
)


class TestVerifyQuote:
    def test_exact_match(self):
        r = C.verify_quote('publicly available on the CRCNS website', PAPER)
        assert r['match_type'] == 'exact' and r['verbatim'] is True
        assert r['offset'] > 0

    def test_curly_quotes_and_dashes_still_match(self):
        paper = 'The authors said “we downloaded the data” from the archive—twice.'
        quote = 'we downloaded the data" from the archive-twice.'
        r = C.verify_quote(quote, paper)
        assert r['match_type'] == 'normalized'
        assert r['verbatim'] is False

    def test_whitespace_differences_still_match(self):
        paper = 'Data   were\n\ndownloaded   from the archive.'
        r = C.verify_quote('Data were downloaded from the archive.', paper)
        assert r['match_type'] in ('normalized', 'spacing_insensitive')

    def test_recapitalized_sentence_start_is_not_a_fabrication(self):
        # Quoting from mid-sentence, the model drops the lead-in and
        # capitalizes: "Notably, the dataset..." becomes "The dataset...".
        paper = 'Notably, the dataset used here is on CRCNS as hc-26.'
        r = C.verify_quote('The dataset used here is on CRCNS as hc-26.', paper)
        assert r['match_type'] == 'case_insensitive'
        assert r['verbatim'] is False

    def test_fabricated_quote_is_not_found(self):
        r = C.verify_quote('We downloaded everything from DANDI.', PAPER)
        assert r['match_type'] == 'not_found'
        assert r['verbatim'] is False

    def test_abridged_quote_is_not_found(self):
        # Words present but a clause spliced out: reordering/omission must fail.
        r = C.verify_quote('The dataset used here is available as hc-26.', PAPER)
        assert r['match_type'] == 'not_found'

    def test_empty_inputs(self):
        assert C.verify_quote('', PAPER)['match_type'] == 'not_found'
        assert C.verify_quote('x', '')['match_type'] == 'not_found'


class TestQuoteReporting:
    def test_fabricated_quotes_are_counted_and_flagged(self, monkeypatch):
        payload = envelope(json.dumps({
            'classification': 'REUSE',
            'confidence': 9,
            'evidence_quotes': ['We downloaded everything from DANDI.',
                                'publicly available on the CRCNS website'],
            'source_archive': 'CRCNS',
            'reasoning': 'because',
        }))
        stub_post(monkeypatch, FakeResponse(200, payload=payload))
        result = C.classify_paper_reuse(PAPER, api_key='k')

        assert result['classification'] == 'REUSE'
        assert result['hallucinated_quote_count'] == 1
        assert any('fabrication' in w for w in result['quote_warnings'])
        kinds = [q['match_type'] for q in result['evidence_quotes']]
        assert 'not_found' in kinds and 'exact' in kinds

    def test_non_list_quotes_are_tolerated(self, monkeypatch):
        payload = envelope(json.dumps({
            'classification': 'MENTION',
            'confidence': 5,
            'evidence_quotes': 'We recorded from mouse hippocampus.',
            'reasoning': 'r',
        }))
        stub_post(monkeypatch, FakeResponse(200, payload=payload))
        result = C.classify_paper_reuse(PAPER, api_key='k')
        assert result['classification'] == 'MENTION'
        assert len(result['evidence_quotes']) == 1

    def test_out_of_range_confidence_is_flagged_not_silently_kept(self, monkeypatch):
        payload = envelope(json.dumps({
            'classification': 'MENTION', 'confidence': 99,
            'evidence_quotes': [], 'reasoning': 'r',
        }))
        stub_post(monkeypatch, FakeResponse(200, payload=payload))
        result = C.classify_paper_reuse(PAPER, api_key='k')
        assert any('confidence' in w for w in result['quote_warnings'])


# --------------------------------------------------------------------------- #
# Truncation
# --------------------------------------------------------------------------- #

class TestSameLabAndArchive:
    def reuse_payload(self, **over):
        body = {'classification': 'REUSE', 'confidence': 9,
                'evidence_quotes': ['publicly available on the CRCNS website'],
                'same_lab': False, 'same_lab_confidence': 8,
                'source_archive': 'CRCNS', 'reasoning': 'r'}
        body.update(over)
        return envelope(json.dumps(body))

    def test_reuse_carries_lab_and_archive(self, monkeypatch):
        stub_post(monkeypatch, FakeResponse(200, payload=self.reuse_payload()))
        r = C.classify_paper_reuse(PAPER, api_key='k')
        assert r['same_lab'] is False
        assert r['same_lab_confidence'] == 8
        assert r['source_archive'] == 'CRCNS'

    def test_archive_alias_is_normalized(self, monkeypatch):
        stub_post(monkeypatch, FakeResponse(
            200, payload=self.reuse_payload(source_archive='DANDI')))
        r = C.classify_paper_reuse(PAPER, api_key='k')
        assert r['source_archive'] == 'DANDI Archive'

    @pytest.mark.parametrize('value', ['unclear', '', '  ', None, 'n/a'])
    def test_unusable_archive_becomes_none(self, monkeypatch, value):
        stub_post(monkeypatch, FakeResponse(
            200, payload=self.reuse_payload(source_archive=value)))
        assert C.classify_paper_reuse(PAPER, api_key='k')['source_archive'] is None

    def test_unknown_archive_is_kept_and_flagged(self, monkeypatch):
        stub_post(monkeypatch, FakeResponse(
            200, payload=self.reuse_payload(source_archive='Wombat Data Bank')))
        r = C.classify_paper_reuse(PAPER, api_key='k')
        assert r['source_archive'] == 'Wombat Data Bank'
        assert any('canonical vocabulary' in w for w in r['quote_warnings'])

    @pytest.mark.parametrize('label', ['MENTION', 'NEITHER'])
    def test_non_reuse_never_carries_lab_or_archive(self, monkeypatch, label):
        # The model may fill these in anyway; they must not survive, or they
        # would be counted later as though the paper reused something.
        stub_post(monkeypatch, FakeResponse(200, payload=self.reuse_payload(
            classification=label, same_lab=True, source_archive='CRCNS')))
        r = C.classify_paper_reuse(PAPER, api_key='k')
        assert r['classification'] == label
        assert r['same_lab'] is None
        assert r['same_lab_confidence'] is None
        assert r['source_archive'] is None

    def test_non_boolean_same_lab_is_flagged_not_coerced(self, monkeypatch):
        stub_post(monkeypatch, FakeResponse(
            200, payload=self.reuse_payload(same_lab='probably')))
        r = C.classify_paper_reuse(PAPER, api_key='k')
        assert r['same_lab'] is None
        assert any('same_lab' in w for w in r['quote_warnings'])

    def test_out_of_range_lab_confidence_dropped(self, monkeypatch):
        stub_post(monkeypatch, FakeResponse(
            200, payload=self.reuse_payload(same_lab_confidence=99)))
        assert C.classify_paper_reuse(PAPER, api_key='k')['same_lab_confidence'] is None

    def test_result_records_prompt_version(self, monkeypatch):
        stub_post(monkeypatch, FakeResponse(200, payload=self.reuse_payload()))
        assert C.classify_paper_reuse(PAPER, api_key='k')['prompt_version'] == C.PROMPT_VERSION
        assert C._error_result('x', 'y')['prompt_version'] == C.PROMPT_VERSION


class TestModalities:
    """
    The study counts reuse of DANDI holdings, and DANDI hosts neurophysiology and
    the behavior recorded with it, not morphology or transcriptomics. A Patch-seq
    paper reusing only gene expression or only reconstructions has not touched
    DANDI data, so the modality list is what separates a real positive from a
    citation that merely looks like one.
    """

    def payload(self, modalities, label='REUSE'):
        return envelope(json.dumps({
            'classification': label, 'confidence': 9,
            'evidence_quotes': ['publicly available on the CRCNS website'],
            'source_quotes': ['publicly available on the CRCNS website'],
            'same_lab': False, 'same_lab_confidence': 7,
            'source_archive': 'CRCNS', 'reused_modalities': modalities,
            'reasoning': 'r'}))

    def test_neurophysiology_counts(self, monkeypatch):
        stub_post(monkeypatch, FakeResponse(200, payload=self.payload(['neurophysiology'])))
        r = C.classify_paper_reuse(PAPER, api_key='k')
        assert r['reused_modalities'] == ['neurophysiology']
        assert r['reused_neurophysiology'] is True
        assert r['reused_dandi_hosted'] is True

    def test_behavior_is_dandi_hosted_but_not_neurophysiology(self, monkeypatch):
        stub_post(monkeypatch, FakeResponse(200, payload=self.payload(['behavior'])))
        r = C.classify_paper_reuse(PAPER, api_key='k')
        assert r['reused_neurophysiology'] is False
        assert r['reused_dandi_hosted'] is True

    @pytest.mark.parametrize('modality', ['transcriptomics', 'morphology'])
    def test_modalities_dandi_does_not_host(self, monkeypatch, modality):
        stub_post(monkeypatch, FakeResponse(200, payload=self.payload([modality])))
        r = C.classify_paper_reuse(PAPER, api_key='k')
        assert r['reused_neurophysiology'] is False
        assert r['reused_dandi_hosted'] is False

    def test_patchseq_mixed_reuse_still_counts(self, monkeypatch):
        stub_post(monkeypatch, FakeResponse(
            200, payload=self.payload(['transcriptomics', 'neurophysiology'])))
        r = C.classify_paper_reuse(PAPER, api_key='k')
        assert r['reused_neurophysiology'] is True

    @pytest.mark.parametrize('raw,expected', [
        (['electrophysiology'], ['neurophysiology']),
        (['ephys'], ['neurophysiology']),
        (['Gene_Expression'], ['transcriptomics']),
        (['genetics'], ['transcriptomics']),
        (['behaviour'], ['behavior']),
        ('neurophysiology', ['neurophysiology']),
        (['neurophysiology', 'neurophysiology'], ['neurophysiology']),
    ])
    def test_synonyms_and_dedup(self, monkeypatch, raw, expected):
        stub_post(monkeypatch, FakeResponse(200, payload=self.payload(raw)))
        assert C.classify_paper_reuse(PAPER, api_key='k')['reused_modalities'] == expected

    def test_unrecognized_modality_dropped_and_flagged(self, monkeypatch):
        stub_post(monkeypatch, FakeResponse(200, payload=self.payload(['telepathy'])))
        r = C.classify_paper_reuse(PAPER, api_key='k')
        assert r['reused_modalities'] == []
        assert any('telepathy' in w for w in r['quote_warnings'])

    def test_reuse_without_modality_is_flagged(self, monkeypatch):
        stub_post(monkeypatch, FakeResponse(200, payload=self.payload([])))
        r = C.classify_paper_reuse(PAPER, api_key='k')
        assert r['reused_neurophysiology'] is False
        assert any('no usable modality' in w for w in r['quote_warnings'])

    @pytest.mark.parametrize('label', ['MENTION', 'NEITHER'])
    def test_non_reuse_leaves_modality_unasked(self, monkeypatch, label):
        stub_post(monkeypatch, FakeResponse(
            200, payload=self.payload(['neurophysiology'], label=label)))
        r = C.classify_paper_reuse(PAPER, api_key='k')
        assert r['reused_modalities'] == []
        # None, not False: "not asked" must stay distinct from "asked, answer no".
        assert r['reused_neurophysiology'] is None
        assert r['reused_dandi_hosted'] is None


class TestDirectMode:
    """
    The direct pathway starts from a paper that names a dataset identifier, so
    the question is whether these authors published it or reused it. PRIMARY is
    a valid answer there and meaningless in the citing pathway; MENTION is the
    reverse. Mixing the vocabularies would silently corrupt either corpus.
    """

    def payload(self, label):
        return envelope(json.dumps({
            'classification': label, 'confidence': 9,
            'evidence_quotes': ['We recorded from mouse hippocampus.'],
            'source_quotes': [], 'same_lab': None, 'same_lab_confidence': None,
            'source_archive': None, 'reused_modalities': [], 'reasoning': 'r'}))

    def test_primary_accepted_in_direct_mode(self, monkeypatch):
        stub_post(monkeypatch, FakeResponse(200, payload=self.payload('PRIMARY')))
        r = C.classify_paper_reuse(PAPER, api_key='k', mode=C.MODE_DIRECT)
        assert r['classification'] == 'PRIMARY'
        assert r['mode'] == C.MODE_DIRECT

    def test_primary_rejected_in_citing_mode(self, monkeypatch):
        stub_post(monkeypatch, FakeResponse(200, payload=self.payload('PRIMARY')))
        assert_is_error(C.classify_paper_reuse(PAPER, api_key='k'), 'parse_error')

    def test_mention_rejected_in_direct_mode(self, monkeypatch):
        stub_post(monkeypatch, FakeResponse(200, payload=self.payload('MENTION')))
        assert_is_error(
            C.classify_paper_reuse(PAPER, api_key='k', mode=C.MODE_DIRECT),
            'parse_error')

    @pytest.mark.parametrize('label', ['REUSE', 'NEITHER'])
    def test_shared_labels_work_in_both_modes(self, monkeypatch, label):
        stub_post(monkeypatch, FakeResponse(200, payload=self.payload(label)))
        for mode in (C.MODE_CITING, C.MODE_DIRECT):
            r = C.classify_paper_reuse(PAPER, api_key='k', mode=mode)
            assert r['classification'] == label

    def test_unknown_mode_is_an_error(self):
        assert_is_error(
            C.classify_paper_reuse(PAPER, api_key='k', mode='sideways'), 'bad_mode')

    def test_direct_prompt_names_the_relationship_question(self):
        prompt = C.build_prompt('text', dataset_id='000003',
                                mode=C.MODE_DIRECT,
                                matched_patterns=['DANDI:000003'])
        assert 'PRIMARY' in prompt
        assert 'RELATIONSHIP' in prompt
        assert 'DANDI:000003' in prompt
        assert 'MENTION' not in prompt

    def test_citing_prompt_is_unchanged_by_the_branch(self):
        prompt = C.build_prompt('text', dataset_id='000003')
        assert 'MENTION' in prompt
        assert 'PRIMARY:' not in prompt


class TestSourceQuotes:
    def test_source_quotes_are_verified_like_evidence(self, monkeypatch):
        stub_post(monkeypatch, FakeResponse(200, payload=envelope(json.dumps({
            'classification': 'REUSE', 'confidence': 9,
            'evidence_quotes': ['We recorded from mouse hippocampus.'],
            'source_quotes': ['publicly available on the CRCNS website',
                              'downloaded from the DANDI Archive'],
            'same_lab': False, 'same_lab_confidence': 7, 'source_archive': 'CRCNS',
            'reused_modalities': ['neurophysiology'], 'reasoning': 'r'}))))
        r = C.classify_paper_reuse(PAPER, api_key='k')
        tiers = [q['match_type'] for q in r['source_quotes']]
        assert tiers == ['exact', 'not_found']
        # A fabricated provenance quote counts toward the same total.
        assert r['hallucinated_quote_count'] == 1
        assert any('source_quotes' in w for w in r['quote_warnings'])

    def test_missing_source_quotes_is_empty_not_error(self, monkeypatch):
        stub_post(monkeypatch, FakeResponse(200, payload=envelope(json.dumps({
            'classification': 'MENTION', 'confidence': 5,
            'evidence_quotes': [], 'reasoning': 'r'}))))
        r = C.classify_paper_reuse(PAPER, api_key='k')
        assert r['classification'] == 'MENTION'
        assert r['source_quotes'] == []


class TestNormalizeArchive:
    @pytest.mark.parametrize('raw,expected', [
        ('DANDI', 'DANDI Archive'),
        ('dandi archive', 'DANDI Archive'),
        ('CRCNS', 'CRCNS'),
        ('crcns', 'CRCNS'),
        ('  OSF  ', 'OSF'),
        ('unclear', None),
        ('none', None),
        ('', None),
        (None, None),
        (123, None),
    ])
    def test_normalization(self, raw, expected):
        assert C.normalize_archive(raw) == expected


class TestTruncation:
    def test_short_paper_is_untouched(self):
        text, info = C._truncate('short paper', 1000)
        assert text == 'short paper' and info is None

    def test_long_paper_keeps_head_and_tail(self):
        text = 'HEAD' + ('x' * 5000) + 'TAIL'
        trimmed, info = C._truncate(text, 1000)
        assert info['truncated'] is True
        assert info['dropped_chars'] > 0
        # Data availability statements live at the end, so the tail must survive.
        assert trimmed.startswith('HEAD')
        assert trimmed.endswith('TAIL')
