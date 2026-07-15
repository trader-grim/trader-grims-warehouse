"""#1393 — extract_json() must handle an open-only markdown fence (no
closing ```), not just the closed-fence case.

Root cause: 95 ebay_draft dead-letters, all `HardFailure('...model
returned non-JSON...')`, were the model returning a complete JSON object
wrapped in a leading ```json fence with NO closing fence. The old regex
required both an opening and closing ``` marker via a single re.search;
when there was no closing fence, `fenced` was None and the raw text
(including the literal ```json prefix) was handed to json.loads(),
which always failed immediately.

This does NOT relax genuine truncation detection: a response that is
open-fenced AND genuinely cut off mid-object must still raise
json.JSONDecodeError after fence-stripping — silently masking real
truncation as success would hide a real per-item data quality problem.
"""

from __future__ import annotations

import json

import pytest

from tgw.apis.ollama import extract_json


class TestExtractJsonFences:
    def test_closed_fence_still_works(self):
        # Regression guard: pre-existing closed-fence behavior must not change.
        text = '```json\n{"a": 1, "b": 2}\n```'
        assert extract_json(text) == {'a': 1, 'b': 2}

    def test_open_fence_no_closing_marker_complete_json(self):
        # The actual #1393 bug: complete JSON, fence never closed.
        text = '```json\n{"a": 1, "b": 2}'
        assert extract_json(text) == {'a': 1, 'b': 2}

    def test_open_fence_no_closing_marker_genuinely_truncated_still_raises(self):
        # Must NOT silently swallow real truncation as success.
        text = '```json\n{"a": 1, "b":'
        with pytest.raises(json.JSONDecodeError):
            extract_json(text)

    def test_no_fence_at_all_still_works(self):
        text = '{"a": 1}'
        assert extract_json(text) == {'a': 1}

    def test_bare_open_fence_no_json_marker(self):
        # ``` with no "json" language tag, still no closing fence.
        text = '```\n{"a": 1}'
        assert extract_json(text) == {'a': 1}

    def test_open_fence_with_nested_backticks_in_string_value_not_confused(self):
        # Sanity: a value containing a lone backtick shouldn't break
        # fence-stripping (there's no closing ``` triple-marker here).
        text = '```json\n{"note": "use `pip install` first"}'
        assert extract_json(text) == {'note': 'use `pip install` first'}
