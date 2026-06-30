"""Tests for AI usage ledger — PP-MULTIMODEL-001 / Phase 5 #2.

All tests are offline. DB calls are mocked so no real PostgreSQL connection
is needed. LLM calls are mocked via unittest.mock.patch.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_cfg():
    return {
        'models': {},
        'openrouter_credentials_path': None,
        'postgres_dsn': 'dbname=state_machine user=tgw',
    }


# ---------------------------------------------------------------------------
# ollama.chat_full
# ---------------------------------------------------------------------------

def test_chat_full_returns_text_and_tokens():
    from tgw.apis.ollama import chat_full

    mock_resp = {
        'message': {'role': 'assistant', 'content': 'hello world'},
        'prompt_eval_count': 42,
        'eval_count': 10,
    }
    with patch('tgw.apis.ollama._post', return_value=mock_resp):
        text, prompt_tok, comp_tok = chat_full('mymodel', [{'role': 'user', 'content': 'hi'}])

    assert text == 'hello world'
    assert prompt_tok == 42
    assert comp_tok == 10


def test_chat_full_handles_missing_token_counts():
    from tgw.apis.ollama import chat_full

    mock_resp = {'message': {'role': 'assistant', 'content': 'hi'}}
    with patch('tgw.apis.ollama._post', return_value=mock_resp):
        text, prompt_tok, comp_tok = chat_full('mymodel', [{'role': 'user', 'content': 'hi'}])

    assert text == 'hi'
    assert prompt_tok is None
    assert comp_tok is None


def test_chat_backward_compat():
    """chat() must still return a plain string."""
    from tgw.apis.ollama import chat

    mock_resp = {
        'message': {'role': 'assistant', 'content': 'backward compat'},
        'prompt_eval_count': 5,
        'eval_count': 3,
    }
    with patch('tgw.apis.ollama._post', return_value=mock_resp):
        result = chat('mymodel', [{'role': 'user', 'content': 'hi'}])

    assert result == 'backward compat'
    assert isinstance(result, str)


# ---------------------------------------------------------------------------
# call_model records usage
# ---------------------------------------------------------------------------

def _mock_ollama_text_call(text='response text', prompt_tokens=100, comp_tokens=50):
    """Patch the Ollama lock + chat_full to return a fake response."""
    mock_lock = MagicMock()
    mock_lock.__enter__ = MagicMock(return_value=None)
    mock_lock.__exit__ = MagicMock(return_value=False)

    mock_chat_full = MagicMock(return_value=(text, prompt_tokens, comp_tokens))
    return (
        patch('tgw.apis.llm.acquire_ollama_lock', return_value=mock_lock),
        patch('tgw.apis.ollama.chat_full', mock_chat_full),
    )


def test_call_model_ollama_records_usage():
    cfg = _make_cfg()
    cfg['models'] = {'my_task': {'provider': 'ollama', 'model': 'Qwen2.5:latest'}}

    recorded = []

    with patch('tgw.apis.llm._record_usage', side_effect=lambda *a, **kw: recorded.append((a, kw))):
        lock_patch, chat_patch = _mock_ollama_text_call()
        with lock_patch, chat_patch:
            result = __import__('tgw.apis.llm', fromlist=['call_model']).call_model(
                'my_task', 'system', 'user', cfg
            )

    assert result == 'response text'
    assert len(recorded) == 1
    args, kwargs = recorded[0]
    assert args[0] == 'my_task'
    assert args[1] == 'ollama'
    assert args[2] == 'Qwen2.5:latest'
    assert isinstance(args[3], int)  # duration_ms
    assert kwargs['success'] is True
    assert kwargs['output_chars'] == len('response text')


def test_call_model_openrouter_records_usage():
    from tgw.apis.llm import call_model

    cfg = _make_cfg()
    cfg['models'] = {'pm_intake': {'provider': 'openrouter', 'model': 'google/gemini-2.5-flash'}}

    or_resp = {
        'choices': [{'message': {'content': 'the answer'}}],
        'usage': {'prompt_tokens': 200, 'completion_tokens': 80, 'total_tokens': 280},
    }

    recorded = []
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = or_resp

    with patch('tgw.apis.llm._record_usage', side_effect=lambda *a, **kw: recorded.append((a, kw))):
        with patch('tgw.apis.llm._load_openrouter_key', return_value='fakekey'):
            with patch('requests.post', return_value=mock_resp):
                result = call_model('pm_intake', 'sys', 'user', cfg)

    assert result == 'the answer'
    assert len(recorded) == 1
    args, kwargs = recorded[0]
    assert kwargs['usage'].get('prompt_tokens') == 200
    assert kwargs['usage'].get('completion_tokens') == 80
    assert kwargs['usage'].get('total_tokens') == 280
    assert kwargs['success'] is True


def test_call_model_records_failure_on_exception():
    from tgw.apis.llm import call_model

    cfg = _make_cfg()
    cfg['models'] = {'fail_task': {'provider': 'ollama', 'model': 'Qwen2.5:latest'}}

    recorded = []
    mock_lock = MagicMock()
    mock_lock.__enter__ = MagicMock(return_value=None)
    mock_lock.__exit__ = MagicMock(return_value=False)

    with patch('tgw.apis.llm._record_usage', side_effect=lambda *a, **kw: recorded.append((a, kw))):
        with patch('tgw.apis.llm.acquire_ollama_lock', return_value=mock_lock):
            with patch('tgw.apis.ollama.chat_full', side_effect=ConnectionError('ollama down')):
                with pytest.raises(ConnectionError):
                    call_model('fail_task', 'sys', 'user', cfg)

    assert len(recorded) == 1
    args, kwargs = recorded[0]
    assert kwargs['success'] is False
    assert 'ollama down' in kwargs['error_msg']


def test_call_model_record_usage_never_raises_on_db_error():
    """A DB failure in _record_usage must never bubble up to the caller."""
    from tgw.apis.llm import call_model

    cfg = _make_cfg()
    cfg['models'] = {'my_task': {'provider': 'ollama', 'model': 'Qwen2.5:latest'}}

    mock_lock = MagicMock()
    mock_lock.__enter__ = MagicMock(return_value=None)
    mock_lock.__exit__ = MagicMock(return_value=False)

    with patch('tgw.apis.llm.acquire_ollama_lock', return_value=mock_lock):
        with patch('tgw.apis.ollama.chat_full', return_value=('ok text', None, None)):
            # record_ai_usage raises — must NOT propagate
            with patch('tgw.queue.state_machine.record_ai_usage', side_effect=RuntimeError('db down')):
                result = call_model('my_task', 'sys', 'user', cfg)

    assert result == 'ok text'


# ---------------------------------------------------------------------------
# state_machine.record_ai_usage (unit — no real DB)
# ---------------------------------------------------------------------------

def test_record_ai_usage_fails_silently_when_db_unavailable():
    """record_ai_usage must never raise even when DB is down."""
    from tgw.queue import state_machine as sm

    # Force an unavailable DSN so connection fails
    original_dsn = sm._DSN
    sm._DSN = 'dbname=nonexistent_db user=nobody'
    sm._ai_usage_table_ready = False

    try:
        # Should not raise
        sm.record_ai_usage(
            'test_task', 'ollama', 'Qwen2.5:latest', 1234,
            input_chars=100, output_chars=50,
        )
    finally:
        sm._DSN = original_dsn
        sm._ai_usage_table_ready = False  # reset so real DB works later


# ---------------------------------------------------------------------------
# query_ai_usage
# ---------------------------------------------------------------------------

def test_query_ai_usage_returns_rows():
    """query_ai_usage queries the right SQL with the since_days parameter."""
    from tgw.queue import state_machine as sm

    fake_rows = [
        {'day': '2026-06-12', 'task': 'ai_identify', 'provider': 'openrouter',
         'model': 'google/gemini-2.5-flash', 'calls': 10, 'total_ms': 5000,
         'prompt_tokens': 1000, 'completion_tokens': 200, 'total_tokens': 1200,
         'input_chars': 3000, 'output_chars': 500, 'errors': 0},
    ]

    mock_cur = MagicMock()
    mock_cur.__enter__ = MagicMock(return_value=mock_cur)
    mock_cur.__exit__ = MagicMock(return_value=False)
    mock_cur.execute = MagicMock()
    mock_cur.fetchall = MagicMock(return_value=fake_rows)

    mock_con = MagicMock()
    mock_con.__enter__ = MagicMock(return_value=mock_con)
    mock_con.__exit__ = MagicMock(return_value=False)
    mock_con.cursor = MagicMock(return_value=mock_cur)

    sm._ai_usage_table_ready = True
    with patch('tgw.queue.state_machine._conn', return_value=mock_con):
        rows = sm.query_ai_usage(since_days=7)

    assert rows == fake_rows
    # Verify since_days was passed to the query
    call_args = mock_cur.execute.call_args[0]
    assert '7' in call_args[1]


# ---------------------------------------------------------------------------
# cmd_ai_usage
# ---------------------------------------------------------------------------

def test_cmd_ai_usage_returns_ok_with_rows():
    from tgw.api import cmd_ai_usage

    fake_rows = [
        {'day': '2026-06-12', 'task': 'ai_identify', 'provider': 'openrouter',
         'model': 'google/gemini-2.5-flash', 'calls': 5, 'total_ms': 2500,
         'prompt_tokens': 500, 'completion_tokens': 100, 'total_tokens': 600,
         'input_chars': 1500, 'output_chars': 250, 'errors': 0},
    ]

    with patch('tgw.queue.state_machine.query_ai_usage', return_value=fake_rows):
        with patch('tgw.queue.state_machine.init'):
            with patch('tgw.queue.state_machine._ensure_ai_usage_table'):
                result = cmd_ai_usage({'postgres_dsn': 'dbname=state_machine user=tgw'}, since_days=7)

    assert result['ok'] is True
    assert result['rows'] == fake_rows
    assert result['since_days'] == 7


def test_cmd_ai_usage_handles_empty_table():
    from tgw.api import cmd_ai_usage

    with patch('tgw.queue.state_machine.query_ai_usage', return_value=[]):
        with patch('tgw.queue.state_machine.init'):
            with patch('tgw.queue.state_machine._ensure_ai_usage_table'):
                result = cmd_ai_usage({'postgres_dsn': 'dbname=state_machine user=tgw'}, since_days=30)

    assert result['ok'] is True
    assert result['rows'] == []


def test_cmd_ai_usage_handles_db_error():
    from tgw.api import cmd_ai_usage

    with patch('tgw.queue.state_machine.query_ai_usage', side_effect=RuntimeError('db down')):
        with patch('tgw.queue.state_machine.init'):
            with patch('tgw.queue.state_machine._ensure_ai_usage_table'):
                result = cmd_ai_usage({'postgres_dsn': 'dbname=state_machine user=tgw'})

    assert result['ok'] is False
    assert 'db down' in result['error']


# ---------------------------------------------------------------------------
# _print_ai_usage_table (smoke test)
# ---------------------------------------------------------------------------

def test_print_ai_usage_table_no_crash(capsys):
    from tgw.api import _print_ai_usage_table

    rows = [
        {'day': '2026-06-12', 'task': 'ai_identify', 'provider': 'openrouter',
         'model': 'google/gemini-2.5-flash', 'calls': 42, 'total_ms': 83000,
         'prompt_tokens': 245123, 'completion_tokens': None, 'total_tokens': 245123,
         'input_chars': 10000, 'output_chars': 3000, 'errors': 0},
        {'day': '2026-06-12', 'task': 'ebay_draft', 'provider': 'ollama',
         'model': 'Qwen2.5:latest', 'calls': 18, 'total_ms': 492000,
         'prompt_tokens': None, 'completion_tokens': None, 'total_tokens': None,
         'input_chars': 8000, 'output_chars': 1500, 'errors': 2},
    ]
    _print_ai_usage_table(rows, since_days=7)
    out = capsys.readouterr().out
    assert 'ai_identify' in out
    assert 'openrouter' in out
    assert 'ebay_draft' in out
    assert '⚠' in out  # error indicator


def test_print_ai_usage_table_empty(capsys):
    from tgw.api import _print_ai_usage_table

    _print_ai_usage_table([], since_days=7)
    out = capsys.readouterr().out
    assert 'No AI usage' in out


# ---------------------------------------------------------------------------
# record_ai_usage — sku parameter
# ---------------------------------------------------------------------------

def test_record_ai_usage_accepts_sku():
    """record_ai_usage with sku= must not raise even if DB is down."""
    from tgw.queue import state_machine as sm

    original_dsn = sm._DSN
    sm._DSN = 'dbname=nonexistent_db user=nobody'
    sm._ai_usage_table_ready = False

    try:
        sm.record_ai_usage(
            'ai_identify', 'openrouter', 'google/gemini-2.5-flash-lite', 1234,
            input_chars=100, output_chars=50,
            sku='tgw20260613120000000',
        )
    finally:
        sm._DSN = original_dsn
        sm._ai_usage_table_ready = False


def test_call_model_passes_sku_to_record_usage():
    """call_model(sku=) threads SKU through to _record_usage."""
    from tgw.apis.llm import call_model

    cfg = _make_cfg()
    cfg['models'] = {'ai_identify': {'provider': 'openrouter', 'model': 'google/gemini-2.5-flash-lite'}}

    or_resp = {
        'choices': [{'message': {'content': 'the answer'}}],
        'usage': {'prompt_tokens': 10, 'completion_tokens': 5, 'total_tokens': 15},
    }
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = or_resp

    recorded = []
    with patch('tgw.apis.llm._record_usage', side_effect=lambda *a, **kw: recorded.append((a, kw))):
        with patch('tgw.apis.llm._load_openrouter_key', return_value='fakekey'):
            with patch('requests.post', return_value=mock_resp):
                call_model('ai_identify', 'sys', 'user', cfg, sku='tgw20260613120000000')

    assert len(recorded) == 1
    _, kwargs = recorded[0]
    assert kwargs.get('sku') == 'tgw20260613120000000'


def test_call_model_sku_none_by_default():
    """call_model without sku= passes sku=None to _record_usage."""
    from tgw.apis.llm import call_model

    cfg = _make_cfg()
    cfg['models'] = {'pm_intake': {'provider': 'openrouter', 'model': 'deepseek/deepseek-v4-flash'}}

    or_resp = {
        'choices': [{'message': {'content': 'ok'}}],
        'usage': {},
    }
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = or_resp

    recorded = []
    with patch('tgw.apis.llm._record_usage', side_effect=lambda *a, **kw: recorded.append((a, kw))):
        with patch('tgw.apis.llm._load_openrouter_key', return_value='fakekey'):
            with patch('requests.post', return_value=mock_resp):
                call_model('pm_intake', 'sys', 'user', cfg)

    _, kwargs = recorded[0]
    assert kwargs.get('sku') is None


# ---------------------------------------------------------------------------
# query_ai_usage_by_sku
# ---------------------------------------------------------------------------

def test_query_ai_usage_by_sku_returns_rows():
    from tgw.queue import state_machine as sm

    sku = 'tgw20260613120000000'
    fake_rows = [
        {'sku': sku, 'task': 'ai_identify', 'provider': 'openrouter',
         'model': 'google/gemini-2.5-flash-lite', 'calls': 3, 'total_ms': 9000,
         'prompt_tokens': 600, 'completion_tokens': 90, 'total_tokens': 690,
         'input_chars': 1800, 'output_chars': 300, 'errors': 0},
        {'sku': sku, 'task': 'alt_text', 'provider': 'openrouter',
         'model': 'google/gemini-2.5-flash-lite', 'calls': 1, 'total_ms': 3000,
         'prompt_tokens': 200, 'completion_tokens': 30, 'total_tokens': 230,
         'input_chars': 600, 'output_chars': 100, 'errors': 0},
    ]

    mock_cur = MagicMock()
    mock_cur.__enter__ = MagicMock(return_value=mock_cur)
    mock_cur.__exit__ = MagicMock(return_value=False)
    mock_cur.execute = MagicMock()
    mock_cur.fetchall = MagicMock(return_value=fake_rows)

    mock_con = MagicMock()
    mock_con.__enter__ = MagicMock(return_value=mock_con)
    mock_con.__exit__ = MagicMock(return_value=False)
    mock_con.cursor = MagicMock(return_value=mock_cur)

    sm._ai_usage_table_ready = True
    with patch('tgw.queue.state_machine._conn', return_value=mock_con):
        rows = sm.query_ai_usage_by_sku(sku, since_days=30)

    assert rows == fake_rows
    call_args = mock_cur.execute.call_args[0]
    assert sku in call_args[1]
    assert '30' in call_args[1]


def test_query_ai_usage_by_sku_passes_sku_as_first_param():
    """Verify SKU is the first bind parameter in the WHERE clause."""
    from tgw.queue import state_machine as sm

    mock_cur = MagicMock()
    mock_cur.__enter__ = MagicMock(return_value=mock_cur)
    mock_cur.__exit__ = MagicMock(return_value=False)
    mock_cur.fetchall = MagicMock(return_value=[])

    mock_con = MagicMock()
    mock_con.__enter__ = MagicMock(return_value=mock_con)
    mock_con.__exit__ = MagicMock(return_value=False)
    mock_con.cursor = MagicMock(return_value=mock_cur)

    sm._ai_usage_table_ready = True
    with patch('tgw.queue.state_machine._conn', return_value=mock_con):
        sm.query_ai_usage_by_sku('tgwSKU123', since_days=14)

    params = mock_cur.execute.call_args[0][1]
    assert params[0] == 'tgwSKU123'
    assert params[1] == '14'


# ---------------------------------------------------------------------------
# cmd_ai_usage_by_sku
# ---------------------------------------------------------------------------

def test_cmd_ai_usage_by_sku_returns_ok():
    from tgw.api import cmd_ai_usage_by_sku

    sku = 'tgw20260613120000000'
    fake_rows = [
        {'sku': sku, 'task': 'ai_identify', 'provider': 'openrouter',
         'model': 'google/gemini-2.5-flash-lite', 'calls': 2, 'total_ms': 6000,
         'prompt_tokens': 400, 'completion_tokens': 60, 'total_tokens': 460,
         'input_chars': 1200, 'output_chars': 200, 'errors': 0},
    ]

    with patch('tgw.queue.state_machine.query_ai_usage_by_sku', return_value=fake_rows):
        with patch('tgw.queue.state_machine.init'):
            with patch('tgw.queue.state_machine._ensure_ai_usage_table'):
                result = cmd_ai_usage_by_sku(
                    {'postgres_dsn': 'dbname=state_machine user=tgw'},
                    sku=sku,
                    since_days=30,
                )

    assert result['ok'] is True
    assert result['sku'] == sku
    assert result['rows'] == fake_rows
    assert result['since_days'] == 30


def test_cmd_ai_usage_by_sku_handles_empty():
    from tgw.api import cmd_ai_usage_by_sku

    with patch('tgw.queue.state_machine.query_ai_usage_by_sku', return_value=[]):
        with patch('tgw.queue.state_machine.init'):
            with patch('tgw.queue.state_machine._ensure_ai_usage_table'):
                result = cmd_ai_usage_by_sku(
                    {'postgres_dsn': 'dbname=state_machine user=tgw'},
                    sku='tgwNONE',
                )

    assert result['ok'] is True
    assert result['rows'] == []


def test_cmd_ai_usage_by_sku_handles_db_error():
    from tgw.api import cmd_ai_usage_by_sku

    with patch('tgw.queue.state_machine.query_ai_usage_by_sku', side_effect=RuntimeError('db down')):
        with patch('tgw.queue.state_machine.init'):
            with patch('tgw.queue.state_machine._ensure_ai_usage_table'):
                result = cmd_ai_usage_by_sku(
                    {'postgres_dsn': 'dbname=state_machine user=tgw'},
                    sku='tgwANY',
                )

    assert result['ok'] is False
    assert 'db down' in result['error']


# ---------------------------------------------------------------------------
# _print_ai_usage_by_sku_table (smoke test)
# ---------------------------------------------------------------------------

def test_print_ai_usage_by_sku_table_no_crash(capsys):
    from tgw.api import _print_ai_usage_by_sku_table

    sku = 'tgw20260613120000000'
    rows = [
        {'sku': sku, 'task': 'ai_identify', 'provider': 'openrouter',
         'model': 'google/gemini-2.5-flash-lite', 'calls': 3, 'total_ms': 9000,
         'prompt_tokens': 600, 'completion_tokens': 90, 'total_tokens': 690,
         'input_chars': 1800, 'output_chars': 300, 'errors': 0},
        {'sku': sku, 'task': 'alt_text', 'provider': 'openrouter',
         'model': 'google/gemini-2.5-flash-lite', 'calls': 1, 'total_ms': 3000,
         'prompt_tokens': 200, 'completion_tokens': 30, 'total_tokens': 230,
         'input_chars': 600, 'output_chars': 100, 'errors': 1},
    ]
    _print_ai_usage_by_sku_table(sku, rows, since_days=30)
    out = capsys.readouterr().out
    assert sku in out
    assert 'ai_identify' in out
    assert 'alt_text' in out
    assert 'TOTAL' in out
    assert '⚠' in out


def test_print_ai_usage_by_sku_table_empty(capsys):
    from tgw.api import _print_ai_usage_by_sku_table

    _print_ai_usage_by_sku_table('tgwNONE', [], since_days=30)
    out = capsys.readouterr().out
    assert 'No AI usage' in out
    assert 'tgwNONE' in out


def test_print_ai_usage_by_sku_table_no_tokens(capsys):
    from tgw.api import _print_ai_usage_by_sku_table

    sku = 'tgw20260613120000000'
    rows = [
        {'sku': sku, 'task': 'ai_identify', 'provider': 'ollama',
         'model': 'Qwen2.5:latest', 'calls': 1, 'total_ms': 18000,
         'prompt_tokens': None, 'completion_tokens': None, 'total_tokens': None,
         'input_chars': 500, 'output_chars': 100, 'errors': 0},
    ]
    _print_ai_usage_by_sku_table(sku, rows, since_days=30)
    out = capsys.readouterr().out
    assert 'n/a' in out
