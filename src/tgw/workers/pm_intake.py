"""
tgw.workers.pm_intake — Plan-Manager intake worker.

Watches inbox/ for dropped Markdown notes. When a note appears:
    1. Move it to inbox/queued/<filename> (only after submission-delay gate)
    2. Enqueue a pm_intake job referencing the file
    3. On claim: read file, call LLM to classify the change,
       dispatch action (append_to_section | file_document | flag_for_review | no_change)
    4. Archive to inbox/processed/

Submission-delay gate: files must age N hours (configurable via pm_intake_delay_hours,
default 4) before being enqueued. `tgw admin-file --now` bypasses the gate.

Action vocabulary:
  no_change       — note already reflected in plan; archive with no writes
  append_to_section — append content to a named plan section (append-only)
  file_document   — move file to reference/ | perplexity/ | dev-workflow/research/;
                    append FILING-LOG.md entry; optional one-line plan pointer
  flag_for_review — move to inbox/review/ + create a claude/admin todo
  new_section     — DEMOTED: treated as flag_for_review (plan writes append-only)

Queue name: pm_intake
"""

from __future__ import annotations

import logging
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import psycopg2
import psycopg2.errors

import tgw.logging as tgw_logging
from tgw.apis.llm import call_model, get_task_model
from tgw.apis.ollama import extract_json
from tgw.config import DEFAULT_CONFIG, load_config
from tgw.queue import state_machine
from tgw.queue.worker_base import HardFailure, QueueWorker
from tgw.todo import todo_add

log = logging.getLogger(__name__)

QUEUE_NAME = 'pm_intake'

_VALID_DESTINATIONS = frozenset({'reference', 'perplexity', 'dev-workflow/research'})

_SYSTEM_PROMPT = """\
You are the TGW project manager assistant. TGW is Trader Grim's Warehouse, a \
Python-based inventory and eBay listing management system. Your job is to \
integrate inbox notes into the living TGW Master Plan.

Rules:
- Be conservative: only add content genuinely not already captured in the plan.
- If the note describes something already fully reflected in the plan, respond with action "no_change".
- Match the existing writing style: concise bullet points, present-tense, no padding.
- For "append_to_section": section_heading must be the exact text of a heading line in the plan,
  including the # prefix. Content is appended inside that section before the next sibling heading.
  Never create new sections — if new section content is needed, use "flag_for_review" instead.
- For "flag_for_review": use when the note is uncertain, ambiguous, requires human judgement,
  or would require creating a new plan section. Provide review_todo_agent ("claude" for technical
  matters, "admin" for operator/business tasks) and a short review_todo_body.
- For "file_document": the note IS a research/reference document to be filed, not an action item.
  Choose destination: "reference" (technical reference docs), "perplexity" (Perplexity research
  outputs), or "dev-workflow/research" (other research and workflow notes).
  Provide destination_filename (e.g. "RESEARCH-topic.md") without directory prefix.
  Optionally provide a one-line plan_pointer (markdown) and plan_pointer_section (exact heading).
- Respond with valid JSON only — no prose, no markdown fences.
"""

_USER_TEMPLATE = """\
## TGW Master Plan — Structure (headings only)
{plan_headings}

---

## Inbox Note: {filename}{truncation_note}
{note}

---

## Task
Analyze this inbox note. Determine the appropriate action.

Respond with JSON only:
{{
  "action": "no_change" | "append_to_section" | "flag_for_review" | "file_document",
  "section_heading": "<exact heading from plan including # prefix — required for append_to_section>",
  "content": "<markdown lines to insert — required for append_to_section>",
  "destination": "reference" | "perplexity" | "dev-workflow/research",
  "destination_filename": "<filename in destination dir — required for file_document>",
  "related_pp": "<PP-*-001 item or empty string>",
  "plan_pointer": "<one-line markdown to append to plan — optional, file_document only>",
  "plan_pointer_section": "<exact heading — required if plan_pointer given>",
  "review_todo_agent": "claude" | "admin",
  "review_todo_body": "<short todo description — required for flag_for_review>",
  "confidence": 0.85,
  "rationale": "<1-2 sentences explaining the decision>"
}}
"""

_NOTE_MAX_CHARS = 8000


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _archive(note_path: Path, processed_dir: Path) -> None:
    ts = datetime.now(tz=timezone.utc).strftime('%Y%m%dT%H%M%S')
    archive_path = processed_dir / f'{ts}-{note_path.name}'
    shutil.move(str(note_path), archive_path)
    log.info('archived %s → processed/%s', note_path.name, archive_path.name)


def _patch_plan_append(plan_text: str, section_heading: str, content: str) -> str:
    """Append content inside a named section, before the next sibling heading."""
    lines = plan_text.splitlines(keepends=True)

    target_idx: Optional[int] = None
    for i, line in enumerate(lines):
        if line.rstrip('\n') == section_heading.rstrip():
            target_idx = i
            break
    if target_idx is None:
        raise ValueError(f'section not found in plan: {section_heading!r}')

    level = len(section_heading) - len(section_heading.lstrip('#'))

    end_idx = len(lines)
    for i in range(target_idx + 1, len(lines)):
        stripped = lines[i].strip()
        if stripped.startswith('#'):
            next_level = len(stripped) - len(stripped.lstrip('#'))
            if next_level <= level:
                end_idx = i
                break

    content_lines = (content.rstrip('\n') + '\n').splitlines(keepends=True)
    if end_idx > 0 and lines[end_idx - 1].strip():
        content_lines = ['\n'] + content_lines

    return ''.join(lines[:end_idx] + content_lines + lines[end_idx:])


def _write_filing_log(
    vault_path: Path,
    source_filename: str,
    destination: str,
    destination_filename: str,
    related_pp: str,
    provider: str,
    model: str,
    confidence: float,
    rationale: str,
) -> None:
    """Append an entry to reference/FILING-LOG.md."""
    log_path = vault_path / 'reference' / 'FILING-LOG.md'
    ts = datetime.now(tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    entry = (
        f'\n## {ts} — {source_filename}\n\n'
        f'- **Filed to:** `{destination}/{destination_filename}`\n'
        f'- **Related PP:** {related_pp or "none"}\n'
        f'- **Model:** {provider} / {model}\n'
        f'- **Confidence:** {confidence:.2f}\n'
        f'- **Rationale:** {rationale}\n'
    )
    if not log_path.exists():
        log_path.write_text(
            '# TGW Filing Log\n\nDocument intake audit trail — auto-generated by pm_intake.\n',
            encoding='utf-8',
        )
    with log_path.open('a', encoding='utf-8') as f:
        f.write(entry)


# ---------------------------------------------------------------------------
# Shared scan-and-enqueue logic (used by worker loop and tgw admin-file)
# ---------------------------------------------------------------------------

def scan_and_enqueue(cfg: Dict[str, Any], bypass_delay: bool = False) -> List[str]:
    """Scan inbox for eligible .md files, move to queued/, enqueue jobs.

    Files that have not yet aged past pm_intake_delay_hours are skipped unless
    bypass_delay is True. Returns list of filenames enqueued (or already queued).
    """
    inbox_dir: Path = cfg['plan_inbox_path']
    queued_dir = inbox_dir / 'queued'
    queued_dir.mkdir(parents=True, exist_ok=True)

    delay_hours: float = float(cfg.get('pm_intake_delay_hours', 4.0))
    enqueued: List[str] = []

    for md_file in sorted(inbox_dir.glob('*.md')):
        if md_file.name.lower() == 'readme.md':
            continue

        if not bypass_delay:
            try:
                age_hours = (time.time() - md_file.stat().st_mtime) / 3600
            except OSError:
                continue
            if age_hours < delay_hours:
                log.debug(
                    'skipping %s: %.1fh old, delay gate is %.0fh',
                    md_file.name, age_hours, delay_hours,
                )
                continue

        dest = queued_dir / md_file.name
        try:
            md_file.rename(dest)
        except OSError as exc:
            log.warning('could not move %s to queued/: %s', md_file.name, exc)
            continue

        dedupe_key = f'pm_intake:{md_file.name}'
        try:
            jid = state_machine.enqueue_job(
                queue_name=QUEUE_NAME,
                payload={'filename': md_file.name},
                dedupe_key=dedupe_key,
                max_attempts=3,
            )
            log.info('enqueued pm_intake job for %s (job %s)', md_file.name, jid)
            tgw_logging.log_event('pm_intake_enqueued', filename=md_file.name, job_id=jid)
            enqueued.append(md_file.name)
        except psycopg2.errors.UniqueViolation:
            log.info('pm_intake job already exists for %s — skipping', md_file.name)
            enqueued.append(md_file.name)
        except Exception:
            log.exception('failed to enqueue pm_intake job for %s', md_file.name)
            dest.rename(md_file)

    return enqueued


# ---------------------------------------------------------------------------
# CLI command: tgw admin-file [--now]
# ---------------------------------------------------------------------------

def cmd_admin_file(cfg: Dict[str, Any], bypass_delay: bool = False) -> Dict[str, Any]:
    """Scan inbox and enqueue eligible notes for pm_intake processing.

    Called by `tgw admin-file [--now]`. Returns {'ok', 'enqueued', 'files'}.
    """
    state_machine.init(cfg.get('postgres_dsn', 'dbname=state_machine user=tgw'))
    files = scan_and_enqueue(cfg, bypass_delay=bypass_delay)
    if files:
        print(f'Enqueued {len(files)} file(s) for pm_intake:')
        for f in files:
            print(f'  {f}')
    else:
        gate_note = ' (delay gate bypassed)' if bypass_delay else f' (delay gate: {cfg.get("pm_intake_delay_hours", 4)}h)'
        print(f'No eligible inbox files found{gate_note}.')
    return {'ok': True, 'enqueued': len(files), 'files': files}


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------

class PMIntakeWorker(QueueWorker):

    def run(self) -> None:
        self.install_signal_handlers()
        tgw_logging.log_event('worker_start', queue=QUEUE_NAME, owner=self.owner)
        log.info('pm_intake worker started: owner=%s', self.owner)

        while not self._stop:
            self._maybe_recover()
            scan_and_enqueue(self.config)
            job = self._claim_one()
            if job is None:
                time.sleep(self.poll_interval)
                continue
            self._process(job)

        tgw_logging.log_event('worker_stop', queue=QUEUE_NAME, owner=self.owner)
        log.info('pm_intake worker stopped')

    def handle(self, job: Dict[str, Any]) -> None:
        payload = job.get('payload_json') or {}
        filename: str = payload.get('filename', '')
        if not filename:
            raise HardFailure('pm_intake job missing filename in payload')

        inbox_dir: Path = self.config['plan_inbox_path']
        queued_dir = inbox_dir / 'queued'
        processed_dir = inbox_dir / 'processed'
        vault_path: Path = self.config['plan_vault_path']
        master_plan_path: Path = self.config['plan_master_path']
        processed_dir.mkdir(parents=True, exist_ok=True)

        note_path = queued_dir / filename
        if not note_path.exists():
            log.info('note file not found (already processed?): %s', filename)
            tgw_logging.log_event('pm_intake_note_missing', filename=filename)
            return

        note_text = note_path.read_text(encoding='utf-8').strip()
        plan_text = master_plan_path.read_text(encoding='utf-8')

        if not note_text:
            log.info('note %s is empty — archiving with no_change', filename)
            tgw_logging.log_event('pm_intake_empty_note', filename=filename)
            _archive(note_path, processed_dir)
            return

        plan_headings = '\n'.join(
            line for line in plan_text.splitlines() if line.startswith('#')
        )
        truncated = len(note_text) > _NOTE_MAX_CHARS
        note_excerpt = note_text[:_NOTE_MAX_CHARS]
        truncation_note = (
            f' (truncated to {_NOTE_MAX_CHARS} chars of {len(note_text)} total)'
            if truncated else ''
        )

        provider, model = get_task_model(self.config, 'pm_intake')
        log.info('calling LLM (%s / %s) for %s (%d chars)', provider, model, filename, len(note_excerpt))
        tgw_logging.log_event(
            'pm_intake_llm_call',
            filename=filename, provider=provider, model=model,
            note_chars=len(note_excerpt), truncated=truncated,
        )

        prompt = _USER_TEMPLATE.format(
            plan_headings=plan_headings,
            filename=filename,
            truncation_note=truncation_note,
            note=note_excerpt,
        )
        raw_response = call_model('pm_intake', _SYSTEM_PROMPT, prompt, self.config)

        try:
            result = extract_json(raw_response)
        except Exception as exc:
            raise HardFailure(
                f'LLM returned non-JSON for {filename}: {raw_response[:200]}'
            ) from exc

        action: str = result.get('action', 'no_change')
        rationale: str = result.get('rationale', '')
        confidence: float = float(result.get('confidence', 0.5))
        related_pp: str = result.get('related_pp', '') or ''

        # Demote new_section → flag_for_review (plan writes are append-only)
        if action == 'new_section':
            log.info('new_section demoted to flag_for_review for %s', filename)
            action = 'flag_for_review'
            result.setdefault('review_todo_agent', 'claude')
            result.setdefault(
                'review_todo_body',
                f'Review inbox note for possible new plan section: {filename}',
            )

        log.info(
            'LLM decision for %s: action=%s confidence=%.2f rationale=%s',
            filename, action, confidence, rationale,
        )
        tgw_logging.log_event(
            'pm_intake_llm_decision',
            filename=filename, action=action, confidence=confidence, rationale=rationale,
        )

        if action == 'no_change':
            log.info('no plan changes needed for %s', filename)

        elif action == 'append_to_section':
            section = result.get('section_heading', '')
            content = result.get('content', '')
            if not section or not content:
                raise HardFailure(
                    f'append_to_section missing section_heading or content for {filename}'
                )
            try:
                new_plan = _patch_plan_append(plan_text, section, content)
            except ValueError as exc:
                raise HardFailure(str(exc)) from exc
            master_plan_path.write_text(new_plan, encoding='utf-8')
            log.info('plan updated: appended to %r', section)
            tgw_logging.log_event(
                'pm_intake_plan_updated',
                filename=filename, action=action, section=section,
            )

        elif action == 'file_document':
            destination = result.get('destination', '')
            if destination not in _VALID_DESTINATIONS:
                raise HardFailure(
                    f'invalid destination {destination!r} for file_document in {filename}'
                )
            dest_filename = (result.get('destination_filename') or '').strip() or filename
            dest_dir = vault_path / destination
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest_path = dest_dir / dest_filename
            shutil.copy2(str(note_path), str(dest_path))
            log.info('filed %s → %s/%s', filename, destination, dest_filename)
            tgw_logging.log_event(
                'pm_intake_filed',
                filename=filename, destination=destination,
                destination_filename=dest_filename, related_pp=related_pp,
            )

            try:
                _write_filing_log(
                    vault_path, filename, destination, dest_filename,
                    related_pp, provider, model, confidence, rationale,
                )
            except Exception:
                log.exception('failed to write FILING-LOG.md for %s', filename)

            plan_pointer = (result.get('plan_pointer') or '').strip()
            plan_pointer_section = (result.get('plan_pointer_section') or '').strip()
            if plan_pointer and plan_pointer_section:
                try:
                    refreshed_plan = master_plan_path.read_text(encoding='utf-8')
                    new_plan = _patch_plan_append(refreshed_plan, plan_pointer_section, plan_pointer)
                    master_plan_path.write_text(new_plan, encoding='utf-8')
                    log.info('plan pointer added to section %r', plan_pointer_section)
                    tgw_logging.log_event(
                        'pm_intake_plan_pointer',
                        filename=filename, section=plan_pointer_section,
                    )
                except ValueError as exc:
                    log.warning('plan_pointer section not found (%s) — skipping plan pointer', exc)
                    tgw_logging.log_event(
                        'pm_intake_plan_pointer_failed',
                        filename=filename, section=plan_pointer_section, error=str(exc),
                    )

        elif action == 'flag_for_review':
            review_dir = inbox_dir / 'review'
            review_dir.mkdir(parents=True, exist_ok=True)
            dest = review_dir / filename
            shutil.copy2(str(note_path), str(dest))
            log.info('flagged for review: %s → inbox/review/%s', filename, filename)

            todo_agent = (result.get('review_todo_agent') or 'claude').strip()
            todo_body = (result.get('review_todo_body') or f'Review inbox note: {filename}').strip()
            try:
                todo_result = todo_add(
                    todo_agent,
                    f'[pm_intake] {todo_body} → inbox/review/{filename}',
                    priority=40,
                    source='pm_intake',
                )
                log.info(
                    'created todo #%s for review of %s (agent=%s)',
                    todo_result.get('id'), filename, todo_agent,
                )
                tgw_logging.log_event(
                    'pm_intake_flagged',
                    filename=filename, todo_id=todo_result.get('id'), todo_agent=todo_agent,
                )
            except Exception:
                log.exception('failed to create todo for review of %s — review file still moved', filename)
                tgw_logging.log_event('pm_intake_flagged_no_todo', filename=filename)

        else:
            raise HardFailure(f'unknown action {action!r} from LLM for {filename}')

        _archive(note_path, processed_dir)
        tgw_logging.log_event('pm_intake_archived', filename=filename)


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(prog='tgw-pm-intake-worker')
    parser.add_argument('--config', default=str(DEFAULT_CONFIG))
    args = parser.parse_args()

    cfg = load_config(Path(args.config))
    worker = PMIntakeWorker(queue_name=QUEUE_NAME, config=cfg)
    worker.run()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
