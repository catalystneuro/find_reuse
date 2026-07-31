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
        monkeypatch.setattr(C, 'get_deepseek_api_key', lambda: None)
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

    def test_empty_content(self, monkeypatch):
        stub_post(monkeypatch, FakeResponse(200, payload=envelope('')))
        assert_is_error(C.classify_paper_reuse('text', api_key='k'), 'parse_error')

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
