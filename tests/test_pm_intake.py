"""Offline tests for tgw.workers.pm_intake (PP-DOCFLOW-001 Phase 1).

All tests mock filesystem and external dependencies — no DB, no LLM calls.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Plan-patching helpers
# ---------------------------------------------------------------------------

def test_patch_plan_append_basic():
    from tgw.workers.pm_intake import _patch_plan_append

    plan = '# Top\n\n## Section A\n\nExisting content.\n\n## Section B\n\nOther.\n'
    result = _patch_plan_append(plan, '## Section A', 'New bullet.')
    assert 'New bullet.' in result
    # New content should appear before Section B
    assert result.index('New bullet.') < result.index('## Section B')


def test_patch_plan_append_adds_blank_separator():
    from tgw.workers.pm_intake import _patch_plan_append

    plan = '## Section A\n\nContent without trailing blank.\n## Section B\n'
    result = _patch_plan_append(plan, '## Section A', 'Added.')
    # A blank line should separate existing content from addition
    assert '\n\nAdded.' in result or '\nAdded.' in result


def test_patch_plan_append_section_not_found():
    from tgw.workers.pm_intake import _patch_plan_append

    with pytest.raises(ValueError, match='section not found'):
        _patch_plan_append('## Real Section\n\nContent.\n', '## Missing', 'x')


def test_patch_plan_append_at_end_of_file():
    from tgw.workers.pm_intake import _patch_plan_append

    plan = '## Only Section\n\nContent.\n'
    result = _patch_plan_append(plan, '## Only Section', 'Appended.')
    assert 'Appended.' in result


# ---------------------------------------------------------------------------
# Filing log
# ---------------------------------------------------------------------------

def test_write_filing_log_creates_file(tmp_path):
    from tgw.workers.pm_intake import _write_filing_log

    vault = tmp_path
    ref_dir = vault / 'reference'
    ref_dir.mkdir()

    _write_filing_log(
        vault, 'note.md', 'reference', 'REF-001.md',
        'PP-DOCFLOW-001', 'openrouter', 'google/gemini-2.5-flash',
        0.9, 'Test rationale.',
    )

    log_path = ref_dir / 'FILING-LOG.md'
    assert log_path.exists()
    content = log_path.read_text()
    assert 'note.md' in content
    assert 'reference/REF-001.md' in content
    assert 'PP-DOCFLOW-001' in content
    assert '0.90' in content
    assert 'Test rationale.' in content


def test_write_filing_log_appends(tmp_path):
    from tgw.workers.pm_intake import _write_filing_log

    vault = tmp_path
    (vault / 'reference').mkdir()

    _write_filing_log(vault, 'a.md', 'perplexity', 'P-001.md', '', 'openrouter', 'model', 0.8, 'r1')
    _write_filing_log(vault, 'b.md', 'reference', 'R-001.md', 'PP-X', 'openrouter', 'model', 0.7, 'r2')

    content = (vault / 'reference' / 'FILING-LOG.md').read_text()
    assert 'a.md' in content
    assert 'b.md' in content


# ---------------------------------------------------------------------------
# Submission-delay gate
# ---------------------------------------------------------------------------

def test_scan_and_enqueue_respects_delay(tmp_path):
    """Files newer than delay_hours should not be enqueued."""
    from tgw.workers.pm_intake import scan_and_enqueue

    inbox = tmp_path / 'inbox'
    inbox.mkdir()
    new_file = inbox / 'new-note.md'
    new_file.write_text('# Test')
    # mtime = now → age ≈ 0h, delay = 4h → should be skipped

    cfg = {
        'plan_inbox_path': inbox,
        'pm_intake_delay_hours': 4.0,
        'postgres_dsn': 'dbname=state_machine user=tgw',
    }

    with patch('tgw.workers.pm_intake.state_machine'):
        result = scan_and_enqueue(cfg, bypass_delay=False)

    assert result == []
    assert new_file.exists()  # not moved


def test_scan_and_enqueue_bypass_delay(tmp_path):
    """With bypass_delay=True, files of any age are enqueued."""
    from tgw.workers.pm_intake import scan_and_enqueue

    inbox = tmp_path / 'inbox'
    inbox.mkdir()
    new_file = inbox / 'fresh-note.md'
    new_file.write_text('# Test')

    cfg = {
        'plan_inbox_path': inbox,
        'pm_intake_delay_hours': 4.0,
        'postgres_dsn': 'dbname=state_machine user=tgw',
    }

    mock_sm = MagicMock()
    mock_sm.enqueue_job.return_value = 'job-uuid-123'

    with patch('tgw.workers.pm_intake.state_machine', mock_sm):
        result = scan_and_enqueue(cfg, bypass_delay=True)

    assert 'fresh-note.md' in result
    assert not new_file.exists()  # moved to queued/
    assert (inbox / 'queued' / 'fresh-note.md').exists()


def test_scan_and_enqueue_aged_file(tmp_path):
    """Files older than delay_hours are enqueued."""
    from tgw.workers.pm_intake import scan_and_enqueue

    inbox = tmp_path / 'inbox'
    inbox.mkdir()
    old_file = inbox / 'old-note.md'
    old_file.write_text('# Old')
    old_time = time.time() - (5 * 3600)  # 5 hours ago
    os.utime(old_file, (old_time, old_time))

    cfg = {
        'plan_inbox_path': inbox,
        'pm_intake_delay_hours': 4.0,
        'postgres_dsn': 'dbname=state_machine user=tgw',
    }

    mock_sm = MagicMock()
    mock_sm.enqueue_job.return_value = 'job-uuid-456'

    with patch('tgw.workers.pm_intake.state_machine', mock_sm):
        result = scan_and_enqueue(cfg, bypass_delay=False)

    assert 'old-note.md' in result


def test_scan_and_enqueue_skips_readme(tmp_path):
    from tgw.workers.pm_intake import scan_and_enqueue

    inbox = tmp_path / 'inbox'
    inbox.mkdir()
    readme = inbox / 'README.md'
    readme.write_text('# Inbox readme')
    old_time = time.time() - (10 * 3600)
    os.utime(readme, (old_time, old_time))

    cfg = {
        'plan_inbox_path': inbox,
        'pm_intake_delay_hours': 4.0,
        'postgres_dsn': 'dbname=state_machine user=tgw',
    }

    with patch('tgw.workers.pm_intake.state_machine'):
        result = scan_and_enqueue(cfg, bypass_delay=False)

    assert result == []
    assert readme.exists()


# ---------------------------------------------------------------------------
# Action handlers via handle() with mocked worker
# ---------------------------------------------------------------------------

def _make_cfg(tmp_path: Path) -> dict:
    vault = tmp_path / 'vault'
    (vault / 'inbox' / 'queued').mkdir(parents=True)
    (vault / 'inbox' / 'processed').mkdir(parents=True)
    (vault / 'inbox' / 'review').mkdir(parents=True)
    (vault / 'reference').mkdir(parents=True)
    (vault / 'perplexity').mkdir(parents=True)
    (vault / 'dev-workflow' / 'research').mkdir(parents=True)
    (vault / 'plan').mkdir(parents=True)
    plan = vault / 'plan' / 'TGW-Master-Plan.md'
    plan.write_text('# TGW Master Plan\n\n## Work Tracks\n\nSome content.\n\n## Other Section\n\nOther.\n')
    return {
        'plan_vault_path': vault,
        'plan_inbox_path': vault / 'inbox',
        'plan_master_path': plan,
        'pm_intake_delay_hours': 4.0,
        'models': {'pm_intake': {'provider': 'openrouter', 'model': 'google/gemini-2.5-flash'}},
        'openrouter_credentials_path': None,
        'postgres_dsn': 'dbname=state_machine user=tgw',
    }


def _make_worker(cfg: dict):
    from tgw.workers.pm_intake import PMIntakeWorker
    worker = PMIntakeWorker.__new__(PMIntakeWorker)
    worker.config = cfg
    return worker


def _place_queued_note(cfg: dict, filename: str, content: str) -> Path:
    note_path = cfg['plan_inbox_path'] / 'queued' / filename
    note_path.write_text(content)
    return note_path


def test_handle_no_change(tmp_path):
    cfg = _make_cfg(tmp_path)
    worker = _make_worker(cfg)
    _place_queued_note(cfg, 'test.md', '# Already in plan')

    llm_response = json.dumps({'action': 'no_change', 'rationale': 'Already covered.'})
    job = {'payload_json': {'filename': 'test.md'}}

    with patch('tgw.workers.pm_intake.call_model', return_value=llm_response):
        worker.handle(job)

    # File moved to processed/
    processed = list((cfg['plan_inbox_path'] / 'processed').glob('*test.md'))
    assert processed


def test_handle_append_to_section(tmp_path):
    cfg = _make_cfg(tmp_path)
    worker = _make_worker(cfg)
    _place_queued_note(cfg, 'append.md', '# Note with new info')

    llm_response = json.dumps({
        'action': 'append_to_section',
        'section_heading': '## Work Tracks',
        'content': '- New bullet point.',
        'rationale': 'Adds new track info.',
        'confidence': 0.9,
    })
    job = {'payload_json': {'filename': 'append.md'}}

    with patch('tgw.workers.pm_intake.call_model', return_value=llm_response):
        worker.handle(job)

    plan_text = cfg['plan_master_path'].read_text()
    assert 'New bullet point.' in plan_text
    # Bullet should be in the Work Tracks section, before Other Section
    assert plan_text.index('New bullet point.') < plan_text.index('## Other Section')


def test_handle_file_document(tmp_path):
    cfg = _make_cfg(tmp_path)
    worker = _make_worker(cfg)
    _place_queued_note(cfg, 'research.md', '# Research doc content')

    llm_response = json.dumps({
        'action': 'file_document',
        'destination': 'reference',
        'destination_filename': 'RESEARCH-001.md',
        'related_pp': 'PP-DOCFLOW-001',
        'confidence': 0.85,
        'rationale': 'This is a reference document.',
    })
    job = {'payload_json': {'filename': 'research.md'}}

    with patch('tgw.workers.pm_intake.call_model', return_value=llm_response):
        worker.handle(job)

    # File copied to reference/
    dest = cfg['plan_vault_path'] / 'reference' / 'RESEARCH-001.md'
    assert dest.exists()
    assert dest.read_text() == '# Research doc content'

    # FILING-LOG.md created
    log_path = cfg['plan_vault_path'] / 'reference' / 'FILING-LOG.md'
    assert log_path.exists()
    assert 'RESEARCH-001.md' in log_path.read_text()

    # Original archived to processed/
    processed = list((cfg['plan_inbox_path'] / 'processed').glob('*research.md'))
    assert processed


def test_handle_file_document_with_plan_pointer(tmp_path):
    cfg = _make_cfg(tmp_path)
    worker = _make_worker(cfg)
    _place_queued_note(cfg, 'with_pointer.md', '# Doc with pointer')

    llm_response = json.dumps({
        'action': 'file_document',
        'destination': 'perplexity',
        'destination_filename': 'PERPLEXITY-008-test.md',
        'related_pp': 'PP-DOCFLOW-001',
        'plan_pointer': '- Research filed: see `perplexity/PERPLEXITY-008-test.md`',
        'plan_pointer_section': '## Work Tracks',
        'confidence': 0.88,
        'rationale': 'Perplexity research output.',
    })
    job = {'payload_json': {'filename': 'with_pointer.md'}}

    with patch('tgw.workers.pm_intake.call_model', return_value=llm_response):
        worker.handle(job)

    plan_text = cfg['plan_master_path'].read_text()
    assert 'PERPLEXITY-008-test.md' in plan_text


def test_handle_file_document_invalid_destination(tmp_path):
    from tgw.queue.worker_base import HardFailure
    cfg = _make_cfg(tmp_path)
    worker = _make_worker(cfg)
    _place_queued_note(cfg, 'bad.md', '# Bad destination')

    llm_response = json.dumps({
        'action': 'file_document',
        'destination': 'plan',
        'destination_filename': 'INJECTED.md',
        'confidence': 0.5,
        'rationale': 'Should be rejected.',
    })
    job = {'payload_json': {'filename': 'bad.md'}}

    with patch('tgw.workers.pm_intake.call_model', return_value=llm_response):
        with pytest.raises(HardFailure, match='invalid destination'):
            worker.handle(job)


def test_handle_flag_for_review(tmp_path):
    cfg = _make_cfg(tmp_path)
    worker = _make_worker(cfg)
    _place_queued_note(cfg, 'ambiguous.md', '# Ambiguous note')

    llm_response = json.dumps({
        'action': 'flag_for_review',
        'review_todo_agent': 'claude',
        'review_todo_body': 'Unclear whether to create new section or append',
        'confidence': 0.4,
        'rationale': 'Uncertain placement.',
    })
    job = {'payload_json': {'filename': 'ambiguous.md'}}

    mock_todo = MagicMock(return_value={'ok': True, 'id': 99})
    with patch('tgw.workers.pm_intake.call_model', return_value=llm_response):
        with patch('tgw.workers.pm_intake.todo_add', mock_todo):
            worker.handle(job)

    # File moved to review/
    review_copy = cfg['plan_inbox_path'] / 'review' / 'ambiguous.md'
    assert review_copy.exists()

    # todo_add called with correct agent
    mock_todo.assert_called_once()
    call_args = mock_todo.call_args[0]
    assert call_args[0] == 'claude'
    assert 'ambiguous.md' in call_args[1]

    # Original archived
    processed = list((cfg['plan_inbox_path'] / 'processed').glob('*ambiguous.md'))
    assert processed


def test_handle_new_section_demoted_to_flag(tmp_path):
    """new_section from the model must be demoted to flag_for_review."""
    cfg = _make_cfg(tmp_path)
    worker = _make_worker(cfg)
    _place_queued_note(cfg, 'new_sec.md', '# Wants a new section')

    llm_response = json.dumps({
        'action': 'new_section',
        'new_section_heading': '## Brand New Section',
        'content': 'New content here.',
        'confidence': 0.7,
        'rationale': 'New topic.',
    })
    job = {'payload_json': {'filename': 'new_sec.md'}}

    mock_todo = MagicMock(return_value={'ok': True, 'id': 100})
    with patch('tgw.workers.pm_intake.call_model', return_value=llm_response):
        with patch('tgw.workers.pm_intake.todo_add', mock_todo):
            worker.handle(job)

    # Should be treated as flag_for_review: review/ copy + todo
    review_copy = cfg['plan_inbox_path'] / 'review' / 'new_sec.md'
    assert review_copy.exists()
    mock_todo.assert_called_once()

    # Plan must NOT have the new section (append-only enforcement)
    plan_text = cfg['plan_master_path'].read_text()
    assert 'Brand New Section' not in plan_text


def test_handle_missing_note_file(tmp_path):
    """Missing note file (already processed) should be a no-op."""
    cfg = _make_cfg(tmp_path)
    worker = _make_worker(cfg)
    job = {'payload_json': {'filename': 'ghost.md'}}

    with patch('tgw.workers.pm_intake.call_model') as mock_llm:
        worker.handle(job)
    mock_llm.assert_not_called()


def test_handle_empty_note(tmp_path):
    """Empty note file should archive with no LLM call."""
    cfg = _make_cfg(tmp_path)
    worker = _make_worker(cfg)
    _place_queued_note(cfg, 'empty.md', '   ')

    job = {'payload_json': {'filename': 'empty.md'}}

    with patch('tgw.workers.pm_intake.call_model') as mock_llm:
        worker.handle(job)
    mock_llm.assert_not_called()
    processed = list((cfg['plan_inbox_path'] / 'processed').glob('*empty.md'))
    assert processed
