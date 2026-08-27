"""Tests for the batch-mode classification runner."""
import json

from src.shared import run_batch_classification as B


class TestChunkGroups:
    """
    A paper's pairs must stay in one chunk: prompt-cache hits depend on the
    repeated full text landing in the same batch file.
    """

    def test_group_is_never_split_across_chunks(self):
        groups = [['a' * 60, 'b' * 60], ['c' * 60]]
        chunks = B.chunk_groups(groups, max_bytes=100)
        assert chunks == [['a' * 60, 'b' * 60], ['c' * 60]]

    def test_small_groups_share_a_chunk(self):
        groups = [['a' * 30], ['b' * 30], ['c' * 30]]
        chunks = B.chunk_groups(groups, max_bytes=100)
        assert chunks == [['a' * 30, 'b' * 30, 'c' * 30]]

    def test_order_is_preserved(self):
        groups = [[f'line-{n}' * 20] for n in range(10)]
        chunks = B.chunk_groups(groups, max_bytes=200)
        flat = [line for chunk in chunks for line in chunk]
        assert flat == [line for group in groups for line in group]

    def test_oversized_group_still_gets_a_chunk(self):
        groups = [['x' * 500]]
        assert B.chunk_groups(groups, max_bytes=100) == [['x' * 500]]

    def test_empty_input_yields_no_chunks(self):
        assert B.chunk_groups([], max_bytes=100) == []


class TestRequestLine:
    def test_shape_matches_openai_batch_contract(self):
        row = json.loads(B.request_line('10.1_x__000003', 'PROMPT', 16384, 'max'))
        assert row['custom_id'] == '10.1_x__000003'
        assert row['method'] == 'POST'
        assert row['url'] == '/v1/chat/completions'
        body = row['body']
        assert body['model'] == B.MODEL_OPENAI
        assert body['messages'] == [{'role': 'user', 'content': 'PROMPT'}]
        assert body['max_completion_tokens'] == 16384
        assert body['reasoning_effort'] == 'max'
        assert body['response_format'] == {'type': 'json_object'}
        # OpenAI's reasoning models reject any non-default temperature, and
        # chat completions rejects max_tokens for them.
        assert 'temperature' not in body
        assert 'max_tokens' not in body
