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
# Per-actor inbox topology (2026-07-15, PP-HERMES-EA-001 #1435/#1436):
# root/dave/tigwa are intake sources; claude/queued/archive/review are not.
# ---------------------------------------------------------------------------

def _mk_note(path: Path, content: str = '# Test', age_hours: float = 10.0) -> Path:
    path.write_text(content)
    ts = time.time() - (age_hours * 3600)
    os.utime(path, (ts, ts))
    return path


def test_scan_and_enqueue_discovers_dave_and_tigwa(tmp_path):
    from tgw.workers.pm_intake import scan_and_enqueue

    inbox = tmp_path / 'inbox'
    (inbox / 'dave').mkdir(parents=True)
    (inbox / 'tigwa').mkdir(parents=True)
    _mk_note(inbox / 'dave' / 'note-a.md')
    _mk_note(inbox / 'tigwa' / 'note-b.md')

    cfg = {'plan_inbox_path': inbox, 'pm_intake_delay_hours': 4.0,
           'postgres_dsn': 'dbname=state_machine user=tgw'}
    mock_sm = MagicMock()
    mock_sm.enqueue_job.return_value = 'job-uuid'

    with patch('tgw.workers.pm_intake.state_machine', mock_sm):
        result = scan_and_enqueue(cfg, bypass_delay=False)

    assert sorted(result) == ['dave/note-a.md', 'tigwa/note-b.md']
    assert (inbox / 'queued' / 'dave' / 'note-a.md').exists()
    assert (inbox / 'queued' / 'tigwa' / 'note-b.md').exists()


def test_scan_and_enqueue_excludes_claude_queued_archive_review(tmp_path):
    from tgw.workers.pm_intake import scan_and_enqueue

    inbox = tmp_path / 'inbox'
    for sub in ('claude', 'queued', 'archive', 'review'):
        (inbox / sub).mkdir(parents=True)
    _mk_note(inbox / 'claude' / 'handoff.md')
    _mk_note(inbox / 'queued' / 'already-queued.md')
    _mk_note(inbox / 'archive' / '20260101-old.md')
    _mk_note(inbox / 'review' / 'flagged.md')

    cfg = {'plan_inbox_path': inbox, 'pm_intake_delay_hours': 4.0,
           'postgres_dsn': 'dbname=state_machine user=tgw'}

    with patch('tgw.workers.pm_intake.state_machine'):
        result = scan_and_enqueue(cfg, bypass_delay=False)

    assert result == []
    # nothing was touched
    assert (inbox / 'claude' / 'handoff.md').exists()
    assert (inbox / 'queued' / 'already-queued.md').exists()


def test_scan_and_enqueue_same_filename_different_owners_no_collision(tmp_path):
    from tgw.workers.pm_intake import scan_and_enqueue

    inbox = tmp_path / 'inbox'
    (inbox / 'dave').mkdir(parents=True)
    (inbox / 'tigwa').mkdir(parents=True)
    _mk_note(inbox / 'dave' / 'shared-name.md', content='dave content')
    _mk_note(inbox / 'tigwa' / 'shared-name.md', content='tigwa content')

    cfg = {'plan_inbox_path': inbox, 'pm_intake_delay_hours': 4.0,
           'postgres_dsn': 'dbname=state_machine user=tgw'}
    mock_sm = MagicMock()
    mock_sm.enqueue_job.return_value = 'job-uuid'

    with patch('tgw.workers.pm_intake.state_machine', mock_sm):
        result = scan_and_enqueue(cfg, bypass_delay=False)

    assert sorted(result) == ['dave/shared-name.md', 'tigwa/shared-name.md']
    dave_queued = (inbox / 'queued' / 'dave' / 'shared-name.md').read_text()
    tigwa_queued = (inbox / 'queued' / 'tigwa' / 'shared-name.md').read_text()
    assert dave_queued == 'dave content'
    assert tigwa_queued == 'tigwa content'
    # dedupe keys differ (owner-qualified path), both jobs actually enqueued
    call_keys = [c.kwargs['dedupe_key'] for c in mock_sm.enqueue_job.call_args_list]
    assert len(set(call_keys)) == 2


def test_scan_and_enqueue_idempotent_same_content_not_reenqueued(tmp_path):
    """Unique-violation on an unchanged dedupe key should still count as
    'already queued' (idempotent rerun), not silently dropped."""
    import psycopg2.errors

    from tgw.workers.pm_intake import scan_and_enqueue

    inbox = tmp_path / 'inbox'
    (inbox / 'tigwa').mkdir(parents=True)
    _mk_note(inbox / 'tigwa' / 'note.md', content='same content')

    cfg = {'plan_inbox_path': inbox, 'pm_intake_delay_hours': 4.0,
           'postgres_dsn': 'dbname=state_machine user=tgw'}
    mock_sm = MagicMock()
    mock_sm.enqueue_job.side_effect = psycopg2.errors.UniqueViolation('already exists')

    with patch('tgw.workers.pm_intake.state_machine', mock_sm):
        result = scan_and_enqueue(cfg, bypass_delay=False)

    assert result == ['tigwa/note.md']
    # file still moved into queued/ even though the job already existed
    assert (inbox / 'queued' / 'tigwa' / 'note.md').exists()


def test_scan_and_enqueue_payload_has_provenance(tmp_path):
    from tgw.workers.pm_intake import scan_and_enqueue

    inbox = tmp_path / 'inbox'
    (inbox / 'dave').mkdir(parents=True)
    _mk_note(inbox / 'dave' / 'note.md', content='provenance test')

    cfg = {'plan_inbox_path': inbox, 'pm_intake_delay_hours': 4.0,
           'postgres_dsn': 'dbname=state_machine user=tgw'}
    mock_sm = MagicMock()
    mock_sm.enqueue_job.return_value = 'job-uuid'

    with patch('tgw.workers.pm_intake.state_machine', mock_sm):
        scan_and_enqueue(cfg, bypass_delay=False)

    payload = mock_sm.enqueue_job.call_args.kwargs['payload']
    assert payload['filename'] == 'dave/note.md'
    assert payload['owner'] == 'dave'
    assert payload['source_path'] == 'dave/note.md'
    assert len(payload['sha256']) == 64
    assert 'intake_ts' in payload


def test_build_intake_manifest_dry_run_no_mutation(tmp_path):
    from tgw.workers.pm_intake import build_intake_manifest

    inbox = tmp_path / 'inbox'
    (inbox / 'dave').mkdir(parents=True)
    (inbox / 'tigwa').mkdir(parents=True)
    _mk_note(inbox / 'dave' / 'ready.md', age_hours=10.0)
    _mk_note(inbox / 'tigwa' / 'too-fresh.md', age_hours=0.1)

    cfg = {'plan_inbox_path': inbox, 'pm_intake_delay_hours': 4.0}

    manifest = build_intake_manifest(cfg, bypass_delay=False)

    by_path = {m['source_path']: m for m in manifest}
    assert by_path['dave/ready.md']['eligible'] is True
    assert by_path['dave/ready.md']['owner'] == 'dave'
    assert by_path['dave/ready.md']['planned_queue_path'] == 'queued/dave/ready.md'
    assert 'sha256' in by_path['dave/ready.md']
    assert by_path['tigwa/too-fresh.md']['eligible'] is False

    # nothing was moved or created
    assert (inbox / 'dave' / 'ready.md').exists()
    assert (inbox / 'tigwa' / 'too-fresh.md').exists()
    assert not (inbox / 'queued').exists()


def test_build_intake_manifest_surfaces_non_markdown_as_deferred(tmp_path):
    """Tigwa review #1438 / knowledge-first policy: non-Markdown direct
    sources must be visible in --dry-run as preserved/deferred candidates,
    not silently invisible, even though staging stays Markdown-only."""
    from tgw.workers.pm_intake import build_intake_manifest

    inbox = tmp_path / 'inbox'
    (inbox / 'dave').mkdir(parents=True)
    _mk_note(inbox / 'dave' / 'note.md', age_hours=10.0)
    (inbox / 'dave' / 'research.pdf').write_bytes(b'%PDF-1.4 fake pdf bytes')
    (inbox / 'dave' / 'notes.html').write_text('<html>fake</html>')
    # control files stay excluded, even from this all-files enumeration
    (inbox / 'README.md').write_text('# control file')
    (inbox / 'Untitled.base').write_text('control')

    cfg = {'plan_inbox_path': inbox, 'pm_intake_delay_hours': 4.0}

    manifest = build_intake_manifest(cfg, bypass_delay=False)
    by_path = {m['source_path']: m for m in manifest}

    assert by_path['dave/note.md']['eligible'] is True

    pdf = by_path['dave/research.pdf']
    assert pdf['eligible'] is False
    assert pdf['reason'] == 'seen but not actionable: unsupported source type pending supervised normalization'
    assert pdf['owner'] == 'dave'
    assert pdf['file_type'] == 'pdf'
    assert 'sha256' in pdf
    assert 'size_bytes' in pdf
    assert 'mtime' in pdf
    assert 'planned_queue_path' not in pdf

    html = by_path['dave/notes.html']
    assert html['eligible'] is False
    assert html['reason'] == 'seen but not actionable: unsupported source type pending supervised normalization'

    assert 'README.md' not in by_path
    assert 'Untitled.base' not in by_path

    # still fully non-mutating
    assert (inbox / 'dave' / 'research.pdf').exists()
    assert (inbox / 'dave' / 'notes.html').exists()
    assert not (inbox / 'queued').exists()


def test_scan_and_enqueue_never_overwrites_existing_queued_file(tmp_path):
    """Tigwa review #1438: same owner+filename with different content and a
    pre-existing queued destination must never be silently clobbered."""
    from tgw.workers.pm_intake import scan_and_enqueue

    inbox = tmp_path / 'inbox'
    (inbox / 'tigwa').mkdir(parents=True)
    queued_dir = inbox / 'queued' / 'tigwa'
    queued_dir.mkdir(parents=True)
    (queued_dir / 'note.md').write_text('OLD queued content — must survive')

    _mk_note(inbox / 'tigwa' / 'note.md', content='NEW incoming content, different from queued')

    cfg = {'plan_inbox_path': inbox, 'pm_intake_delay_hours': 4.0,
           'postgres_dsn': 'dbname=state_machine user=tgw'}
    mock_sm = MagicMock()
    mock_sm.enqueue_job.return_value = 'job-uuid'

    with patch('tgw.workers.pm_intake.state_machine', mock_sm):
        result = scan_and_enqueue(cfg, bypass_delay=False)

    # original queued artifact untouched
    assert (queued_dir / 'note.md').read_text() == 'OLD queued content — must survive'
    # incoming file routed to a distinct, content-addressed destination
    siblings = sorted(p.name for p in queued_dir.iterdir())
    assert 'note.md' in siblings
    assert len(siblings) == 2
    new_name = [n for n in siblings if n != 'note.md'][0]
    assert new_name.startswith('note__') and new_name.endswith('.md')
    assert (queued_dir / new_name).read_text() == 'NEW incoming content, different from queued'
    assert any(r.endswith(new_name) for r in result)
    # source file was moved (not left behind, not duplicated)
    assert not (inbox / 'tigwa' / 'note.md').exists()


def test_scan_and_enqueue_identical_content_collision_is_idempotent_noop(tmp_path):
    """Same owner+filename+content already queued: skip as a duplicate, do
    not move (nothing to move to) and do not re-enqueue a fresh job."""
    from tgw.workers.pm_intake import scan_and_enqueue

    inbox = tmp_path / 'inbox'
    (inbox / 'tigwa').mkdir(parents=True)
    queued_dir = inbox / 'queued' / 'tigwa'
    queued_dir.mkdir(parents=True)
    (queued_dir / 'note.md').write_text('identical content')

    _mk_note(inbox / 'tigwa' / 'note.md', content='identical content')

    cfg = {'plan_inbox_path': inbox, 'pm_intake_delay_hours': 4.0,
           'postgres_dsn': 'dbname=state_machine user=tgw'}
    mock_sm = MagicMock()

    with patch('tgw.workers.pm_intake.state_machine', mock_sm):
        result = scan_and_enqueue(cfg, bypass_delay=False)

    assert result == ['tigwa/note.md']
    mock_sm.enqueue_job.assert_not_called()
    # queued artifact unchanged, and only one file sits in queued/tigwa/
    assert (queued_dir / 'note.md').read_text() == 'identical content'
    assert [p.name for p in queued_dir.iterdir()] == ['note.md']
    # source left in place (not silently deleted) — data preservation
    assert (inbox / 'tigwa' / 'note.md').exists()


def test_cmd_admin_file_dry_run_does_not_touch_state_machine(tmp_path):
    from tgw.workers.pm_intake import cmd_admin_file

    inbox = tmp_path / 'inbox'
    (inbox / 'dave').mkdir(parents=True)
    _mk_note(inbox / 'dave' / 'note.md')

    cfg = {'plan_inbox_path': inbox, 'pm_intake_delay_hours': 4.0,
           'postgres_dsn': 'dbname=state_machine user=tgw'}

    with patch('tgw.workers.pm_intake.state_machine') as mock_sm:
        result = cmd_admin_file(cfg, dry_run=True)

    assert result['ok'] is True
    assert result['dry_run'] is True
    assert len(result['candidates']) == 1
    mock_sm.init.assert_not_called()
    assert not (inbox / 'queued').exists()


# ---------------------------------------------------------------------------
# Action handlers via handle() with mocked worker
# ---------------------------------------------------------------------------

def _make_cfg(tmp_path: Path) -> dict:
    vault = tmp_path / 'vault'
    (vault / 'inbox' / 'queued').mkdir(parents=True)
    (vault / 'inbox' / 'archive').mkdir(parents=True)
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
        # PM intake writes only this explicit authoring target. It is not the
        # clean approved Plan materialization consumed by operational readers.
        'plan_update_master_path': plan,
        'pm_intake_delay_hours': 4.0,
        'models': {'pm_intake': {'provider': 'openrouter', 'model': 'google/gemini-2.5-flash'}},
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
    processed = list((cfg['plan_inbox_path'] / 'archive').glob('*test.md'))
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

    plan_text = cfg['plan_update_master_path'].read_text()
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
    processed = list((cfg['plan_inbox_path'] / 'archive').glob('*research.md'))
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

    plan_text = cfg['plan_update_master_path'].read_text()
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
    processed = list((cfg['plan_inbox_path'] / 'archive').glob('*ambiguous.md'))
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
    plan_text = cfg['plan_update_master_path'].read_text()
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
    processed = list((cfg['plan_inbox_path'] / 'archive').glob('*empty.md'))
    assert processed


# ---------------------------------------------------------------------------
# URL/URI submissions (PP-DOCFLOW-001 Phase 3)
# ---------------------------------------------------------------------------

def test_is_url_submission_detects_http():
    from tgw.workers.pm_intake import is_url_submission
    assert is_url_submission('https://example.com/article') == 'https://example.com/article'
    assert is_url_submission('  http://foo.bar/baz  ') == 'http://foo.bar/baz'


def test_is_url_submission_rejects_non_url():
    from tgw.workers.pm_intake import is_url_submission
    assert is_url_submission('# This is a note') is None
    assert is_url_submission('some plain text') is None
    assert is_url_submission('') is None
    assert is_url_submission('ftp://not-http.example.com') is None


def test_html_to_text_strips_script_and_nav():
    from tgw.workers.pm_intake import _html_to_text
    html = (
        '<html><head><title>T</title></head><body>'
        '<nav>Menu</nav>'
        '<main><h1>Article</h1><p>Content here.</p></main>'
        '<script>alert("x")</script>'
        '</body></html>'
    )
    text = _html_to_text(html)
    assert 'Content here.' in text
    assert 'Article' in text
    assert 'Menu' not in text
    assert 'alert' not in text


def test_html_to_text_collapses_whitespace():
    from tgw.workers.pm_intake import _html_to_text
    html = '<p>  lots   of   spaces  </p>'
    text = _html_to_text(html)
    assert '  ' not in text.strip()


def _make_mock_response(text, content_type='text/html; charset=utf-8',
                        status_code=200, url='https://example.com/',
                        content_length=None):
    """Build a fake httpx streamed-Response-like object.

    fetch_url() now uses client.stream(...).iter_bytes() rather than a
    buffering client.get(...), so the mock exposes .iter_bytes() and is
    itself usable as a context manager (matching what client.stream(...)
    returns from real httpx).
    """
    resp = MagicMock()
    resp.status_code = status_code
    headers = {'content-type': content_type}
    if content_length is not None:
        headers['content-length'] = str(content_length)
    resp.headers = headers
    resp.text = text
    resp.url = url
    resp.encoding = 'utf-8'
    body = text if isinstance(text, bytes) else text.encode('utf-8')
    resp.iter_bytes = MagicMock(return_value=iter([body]) if body else iter([]))
    resp.__enter__ = MagicMock(return_value=resp)
    resp.__exit__ = MagicMock(return_value=False)
    return resp


def _patch_resolve_safe(safe=True):
    """Patch the SSRF hostname-resolution check so fetch_url tests that are
    exercising unrelated behavior don't depend on real DNS resolution."""
    return patch('tgw.workers.pm_intake._resolve_is_safe', return_value=safe)


def _mock_httpx_ok(html, url='https://example.com/article'):
    """Context manager that patches httpx.Client to stream a successful
    response, and treats the target host as SSRF-safe (real DNS not used)."""
    from unittest.mock import MagicMock, patch
    mock_resp = _make_mock_response(html, url=url)
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.stream = MagicMock(return_value=mock_resp)

    class _Combined:
        def __enter__(self):
            self._p1 = patch('httpx.Client', return_value=mock_client)
            self._p2 = _patch_resolve_safe(True)
            self._p1.__enter__()
            self._p2.__enter__()
            return mock_client

        def __exit__(self, *exc):
            self._p2.__exit__(*exc)
            self._p1.__exit__(*exc)
            return False

    return _Combined()


def test_fetch_url_success(tmp_path):
    from tgw.workers.pm_intake import fetch_url
    html = '<html><head><title>My Article</title></head><body><p>Great content.</p></body></html>'
    with _mock_httpx_ok(html, url='https://example.com/article'):
        result = fetch_url('https://example.com/article')
    assert result['ok'] is True
    assert result['title'] == 'My Article'
    assert 'Great content.' in result['text']
    assert result['url'] == 'https://example.com/article'


def test_fetch_url_http_error():
    from tgw.workers.pm_intake import fetch_url
    resp = _make_mock_response('', status_code=404)
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.stream = MagicMock(return_value=resp)
    with patch('httpx.Client', return_value=mock_client), _patch_resolve_safe(True):
        result = fetch_url('https://example.com/missing')
    assert result['ok'] is False
    assert '404' in result['error']


def test_fetch_url_unsupported_content_type():
    from tgw.workers.pm_intake import fetch_url
    resp = _make_mock_response(b'binary', content_type='application/pdf')
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.stream = MagicMock(return_value=resp)
    with patch('httpx.Client', return_value=mock_client), _patch_resolve_safe(True):
        result = fetch_url('https://example.com/file.pdf')
    assert result['ok'] is False
    assert 'unsupported' in result['error']


def test_fetch_url_network_error():
    import httpx

    from tgw.workers.pm_intake import fetch_url
    with patch('httpx.Client') as mock_cls, _patch_resolve_safe(True):
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.stream = MagicMock(side_effect=httpx.RequestError('connection refused'))
        mock_cls.return_value = mock_client
        result = fetch_url('https://down.example.com/')
    assert result['ok'] is False
    assert 'request error' in result['error']


def test_fetch_url_plaintext():
    from tgw.workers.pm_intake import fetch_url
    resp = _make_mock_response('plain text content', content_type='text/plain')
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.stream = MagicMock(return_value=resp)
    with patch('httpx.Client', return_value=mock_client), _patch_resolve_safe(True):
        result = fetch_url('https://example.com/file.txt')
    assert result['ok'] is True
    assert result['text'] == 'plain text content'


# ---------------------------------------------------------------------------
# SSRF protection (#1278) and response-size cap (#1279)
# ---------------------------------------------------------------------------

def test_fetch_url_blocks_loopback():
    """fetch_url() must refuse http://127.0.0.1/ with no network call made."""
    from tgw.workers.pm_intake import fetch_url
    with patch('httpx.Client') as mock_cls:
        result = fetch_url('http://127.0.0.1/')
    assert result['ok'] is False
    assert 'blocked' in result['error']
    mock_cls.assert_not_called()


def test_fetch_url_blocks_link_local_metadata():
    """fetch_url() must refuse the cloud-metadata link-local address."""
    from tgw.workers.pm_intake import fetch_url
    with patch('httpx.Client') as mock_cls:
        result = fetch_url('http://169.254.169.254/')
    assert result['ok'] is False
    assert 'blocked' in result['error']
    mock_cls.assert_not_called()


def test_fetch_url_blocks_redirect_to_loopback():
    """A public-looking URL that 302-redirects to a loopback target must be
    blocked mid-redirect by the event_hooks guard, not followed.

    Uses a real httpx.Client with an httpx.MockTransport (only the
    transport is mocked) so this actually exercises follow_redirects=True
    + event_hooks for this httpx version, not just fetch_url's own logic.
    """
    import httpx

    from tgw.workers.pm_intake import fetch_url

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == 'redirect-source.example.com':
            return httpx.Response(302, headers={'Location': 'http://127.0.0.1:9/'})
        # Should never be reached — the redirect target must be blocked first.
        return httpx.Response(200, text='should not be reached')

    transport = httpx.MockTransport(handler)
    real_client_cls = httpx.Client

    def patched_client(*args, **kwargs):
        kwargs['transport'] = transport
        return real_client_cls(*args, **kwargs)

    from tgw.workers.pm_intake import _resolve_is_safe as _real_resolve_is_safe

    def fake_resolve_is_safe(hostname):
        # The "public" test host is treated as safe without real DNS; the
        # redirect target (a real loopback literal) is judged for real —
        # this is what actually proves the mid-redirect check fires.
        if hostname == 'redirect-source.example.com':
            return True
        return _real_resolve_is_safe(hostname)

    with patch('httpx.Client', side_effect=patched_client), \
         patch('tgw.workers.pm_intake._resolve_is_safe', side_effect=fake_resolve_is_safe):
        result = fetch_url('http://redirect-source.example.com/')

    assert result['ok'] is False
    assert 'blocked' in result['error']


def test_fetch_url_normal_case_no_regression():
    """A real public URL (mocked) still returns ok with extracted text."""
    from tgw.workers.pm_intake import fetch_url
    html = '<html><head><title>Hi</title></head><body><p>Normal page.</p></body></html>'
    with _mock_httpx_ok(html, url='https://example.com/normal'):
        result = fetch_url('https://example.com/normal')
    assert result['ok'] is True
    assert result['title'] == 'Hi'
    assert 'Normal page.' in result['text']


def test_fetch_url_rejects_oversized_content_length():
    """A Content-Length header over the cap must be rejected before any
    body bytes are read — the iterator must never be fully consumed."""
    from tgw.workers.pm_intake import _MAX_RESPONSE_BYTES, fetch_url
    resp = _make_mock_response(
        'x' * 100, url='https://example.com/huge', content_length=_MAX_RESPONSE_BYTES + 1,
    )
    consumed = MagicMock(side_effect=AssertionError('body iterator must not be consumed'))
    resp.iter_bytes = consumed
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.stream = MagicMock(return_value=resp)
    with patch('httpx.Client', return_value=mock_client), _patch_resolve_safe(True):
        result = fetch_url('https://example.com/huge')
    assert result['ok'] is False
    assert 'too large' in result['error']
    consumed.assert_not_called()


def test_fetch_url_aborts_streaming_body_over_cap_without_content_length():
    """No Content-Length header, but the streamed body exceeds the cap —
    must still be aborted once the accumulated size crosses the limit
    (proves the fast-path header check isn't the only guard)."""
    from tgw.workers.pm_intake import _MAX_RESPONSE_BYTES, fetch_url
    chunk = b'a' * 1_000_000
    n_chunks = (_MAX_RESPONSE_BYTES // len(chunk)) + 3  # comfortably over the cap
    resp = _make_mock_response('placeholder', url='https://example.com/stream-huge')
    resp.iter_bytes = MagicMock(return_value=iter([chunk] * n_chunks))
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.stream = MagicMock(return_value=resp)
    with patch('httpx.Client', return_value=mock_client), _patch_resolve_safe(True):
        result = fetch_url('https://example.com/stream-huge')
    assert result['ok'] is False
    assert 'too large' in result['error']


def test_handle_url_submission_filed(tmp_path):
    """URL-only note: fetches content, files via LLM file_document action."""
    cfg = _make_cfg(tmp_path)
    worker = _make_worker(cfg)
    _place_queued_note(cfg, 'link.md', 'https://example.com/research')

    html = '<html><head><title>Research Article</title></head><body><p>Key findings.</p></body></html>'
    llm_response = json.dumps({
        'action': 'file_document',
        'destination': 'reference',
        'destination_filename': 'RESEARCH-URL.md',
        'related_pp': 'PP-DOCFLOW-001',
        'confidence': 0.9,
        'rationale': 'Reference research document.',
    })
    job = {'payload_json': {'filename': 'link.md'}}

    with _mock_httpx_ok(html):
        with patch('tgw.workers.pm_intake.call_model', return_value=llm_response):
            worker.handle(job)

    # Filed content should be the fetched markdown, not the URL stub
    dest = cfg['plan_vault_path'] / 'reference' / 'RESEARCH-URL.md'
    assert dest.exists()
    filed_text = dest.read_text()
    assert 'Research Article' in filed_text or 'Key findings.' in filed_text
    # Must NOT be the bare URL
    assert filed_text.strip() != 'https://example.com/research'

    # FILING-LOG must record source URL
    log_path = cfg['plan_vault_path'] / 'reference' / 'FILING-LOG.md'
    assert log_path.exists()
    log_text = log_path.read_text()
    assert 'https://example.com/research' in log_text

    # Original archived
    processed = list((cfg['plan_inbox_path'] / 'archive').glob('*link.md'))
    assert processed


def test_handle_url_submission_fetch_failure(tmp_path):
    """Failed URL fetch immediately flags for review without LLM call."""
    cfg = _make_cfg(tmp_path)
    worker = _make_worker(cfg)
    _place_queued_note(cfg, 'bad-link.md', 'https://down.example.com/')

    import httpx
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.get = MagicMock(side_effect=httpx.RequestError('connection refused'))

    mock_todo = MagicMock(return_value={'ok': True, 'id': 55})
    job = {'payload_json': {'filename': 'bad-link.md'}}

    with patch('httpx.Client', return_value=mock_client):
        with patch('tgw.workers.pm_intake.call_model') as mock_llm:
            with patch('tgw.workers.pm_intake.todo_add', mock_todo):
                worker.handle(job)

    # No LLM call — flagged immediately
    mock_llm.assert_not_called()

    # File moved to review
    review = cfg['plan_inbox_path'] / 'review' / 'bad-link.md'
    assert review.exists()

    # Todo created with admin agent + URL in body
    mock_todo.assert_called_once()
    call_args = mock_todo.call_args[0]
    assert call_args[0] == 'admin'
    assert 'https://down.example.com/' in call_args[1]

    # Original archived
    processed = list((cfg['plan_inbox_path'] / 'archive').glob('*bad-link.md'))
    assert processed


def test_handle_url_submission_no_change(tmp_path):
    """LLM returns no_change for URL submission — note archived, nothing filed."""
    cfg = _make_cfg(tmp_path)
    worker = _make_worker(cfg)
    _place_queued_note(cfg, 'old-link.md', 'https://example.com/already-known')

    html = '<html><body><p>Already known content.</p></body></html>'
    llm_response = json.dumps({'action': 'no_change', 'rationale': 'Already captured.'})
    job = {'payload_json': {'filename': 'old-link.md'}}

    with _mock_httpx_ok(html):
        with patch('tgw.workers.pm_intake.call_model', return_value=llm_response):
            worker.handle(job)

    processed = list((cfg['plan_inbox_path'] / 'archive').glob('*old-link.md'))
    assert processed
    # No filing log entry
    log_path = cfg['plan_vault_path'] / 'reference' / 'FILING-LOG.md'
    assert not log_path.exists()


def test_write_filing_log_records_source_url(tmp_path):
    """FILING-LOG entry includes Source URL when source_url is given."""
    from tgw.workers.pm_intake import _write_filing_log
    vault = tmp_path
    (vault / 'reference').mkdir()

    _write_filing_log(
        vault, 'link.md', 'reference', 'FILED.md',
        'PP-DOCFLOW-001', 'openrouter', 'gemini', 0.9, 'test',
        source_url='https://example.com/article',
    )
    log_text = (vault / 'reference' / 'FILING-LOG.md').read_text()
    assert 'https://example.com/article' in log_text
    assert 'Source URL' in log_text


def test_non_url_note_unchanged_behavior(tmp_path):
    """Regular (non-URL) notes are unaffected by the URL detection path."""
    cfg = _make_cfg(tmp_path)
    worker = _make_worker(cfg)
    _place_queued_note(cfg, 'normal.md', '# A regular note\n\nSome content here.')

    llm_response = json.dumps({'action': 'no_change', 'rationale': 'Already covered.'})
    job = {'payload_json': {'filename': 'normal.md'}}

    with patch('tgw.workers.pm_intake.call_model', return_value=llm_response) as mock_llm:
        with patch('httpx.Client') as mock_http:
            worker.handle(job)

    mock_llm.assert_called_once()   # LLM called for normal notes
    mock_http.assert_not_called()   # httpx NOT used for non-URL notes
