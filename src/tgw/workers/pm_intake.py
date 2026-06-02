"""
tgw.workers.pm_intake — Plan-Manager intake worker.

Watches inbox/ for dropped Markdown notes. When a note appears:
    1. Move it to inbox/queued/<filename>
    2. Enqueue a pm_intake job referencing the file
    3. On claim: read file, ask Ollama to classify the change,
       patch TGW-Master-Plan.md, archive to inbox/processed/

Flow: scan → enqueue → claim → ask Ollama → patch plan → archive
Failures before Ollama: transient retry (network, DB).
Failures in Ollama response: logged, job dead-lettered after max attempts.

Queue name: pm_intake
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

import psycopg2

from tgw.apis.ollama import chat, extract_json, is_available
from tgw.config import DEFAULT_CONFIG, load_config
from tgw.queue import state_machine
from tgw.queue.worker_base import HardFailure, QueueWorker
import tgw.logging as tgw_logging

log = logging.getLogger(__name__)

QUEUE_NAME  = 'pm_intake'
OLLAMA_MODEL = 'Qwen2.5:latest'

_SYSTEM_PROMPT = """\
You are the TGW project manager assistant. TGW is Trader Grim's Warehouse, a \
Python-based inventory and eBay listing management system. Your job is to \
integrate inbox notes into the living TGW Master Plan.

Rules:
- Be conservative: only add content genuinely not already captured in the plan.
- If the note describes something already fully reflected in the plan, respond with action "no_change".
- Match the existing writing style: concise bullet points, present-tense, no padding.
- section_heading must be the exact text of a heading line in the plan, including the # prefix.
- For "append_to_section": content is appended inside that section before the next sibling heading.
- For "new_section": a new top-level section is added at the end of the plan.
- Respond with valid JSON only — no prose, no fences.
"""

_USER_TEMPLATE = """\
## TGW Master Plan — Structure (headings only)
{plan_headings}

---

## Inbox Note: {filename}{truncation_note}
{note}

---

## Task
Analyze this inbox note. Determine what, if anything, should be added to the plan.

Respond with JSON:
{{
  "action": "no_change" | "append_to_section" | "new_section",
  "section_heading": "<exact heading line from plan — required for append_to_section>",
  "new_section_heading": "<heading for new section — required for new_section>",
  "content": "<markdown lines to insert — omit for no_change>",
  "rationale": "<1-2 sentences explaining the decision>"
}}
"""

_NOTE_MAX_CHARS = 4000


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _archive(note_path: Path, processed_dir: Path) -> None:
    ts = datetime.now(tz=timezone.utc).strftime('%Y%m%dT%H%M%S')
    archive_path = processed_dir / f'{ts}-{note_path.name}'
    shutil.move(str(note_path), archive_path)
    log.info('archived %s → processed/%s', note_path.name, archive_path.name)


# ---------------------------------------------------------------------------
# Plan patching helpers
# ---------------------------------------------------------------------------

def _patch_plan_append(plan_text: str, section_heading: str, content: str) -> str:
    """Append content inside a named section, before the next sibling heading."""
    lines = plan_text.splitlines(keepends=True)

    # Find target heading
    target_idx: Optional[int] = None
    for i, line in enumerate(lines):
        if line.rstrip('\n') == section_heading.rstrip():
            target_idx = i
            break
    if target_idx is None:
        raise ValueError(f'section not found in plan: {section_heading!r}')

    # Heading level of the target
    level = len(section_heading) - len(section_heading.lstrip('#'))

    # Find where this section ends: next heading of level <= current
    end_idx = len(lines)
    for i in range(target_idx + 1, len(lines)):
        stripped = lines[i].strip()
        if stripped.startswith('#'):
            next_level = len(stripped) - len(stripped.lstrip('#'))
            if next_level <= level:
                end_idx = i
                break

    content_lines = (content.rstrip('\n') + '\n').splitlines(keepends=True)
    # Blank separator if section doesn't already end with a blank line
    if end_idx > 0 and lines[end_idx - 1].strip():
        content_lines = ['\n'] + content_lines

    return ''.join(lines[:end_idx] + content_lines + lines[end_idx:])


def _patch_plan_new_section(plan_text: str, heading: str, content: str) -> str:
    """Append a new top-level section at the end of the plan."""
    if not heading.startswith('#'):
        heading = f'## {heading}'
    return plan_text.rstrip('\n') + f'\n\n{heading}\n{content.rstrip()}\n'


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
            self._scan_inbox()
            job = self._claim_one()
            if job is None:
                time.sleep(self.poll_interval)
                continue
            self._process(job)

        tgw_logging.log_event('worker_stop', queue=QUEUE_NAME, owner=self.owner)
        log.info('pm_intake worker stopped')

    # ------------------------------------------------------------------
    # Inbox scanning
    # ------------------------------------------------------------------

    def _scan_inbox(self) -> None:
        inbox_dir: Path = self.config['plan_inbox_path']
        queued_dir = inbox_dir / 'queued'
        queued_dir.mkdir(parents=True, exist_ok=True)

        for md_file in inbox_dir.glob('*.md'):
            if md_file.name.lower() == 'readme.md':
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
                tgw_logging.log_event('pm_intake_enqueued',
                                      filename=md_file.name, job_id=jid)
            except psycopg2.errors.UniqueViolation:
                log.info('pm_intake job already exists for %s — skipping', md_file.name)
            except Exception:
                log.exception('failed to enqueue pm_intake job for %s', md_file.name)
                # Move back so we don't lose the file
                dest.rename(md_file)

    # ------------------------------------------------------------------
    # Job handler
    # ------------------------------------------------------------------

    def handle(self, job: Dict[str, Any]) -> None:
        payload = job.get('payload_json') or {}
        filename: str = payload.get('filename', '')
        if not filename:
            raise HardFailure('pm_intake job missing filename in payload')

        inbox_dir: Path  = self.config['plan_inbox_path']
        queued_dir       = inbox_dir / 'queued'
        processed_dir    = inbox_dir / 'processed'
        master_plan_path: Path = self.config['plan_master_path']
        processed_dir.mkdir(parents=True, exist_ok=True)

        note_path = queued_dir / filename
        if not note_path.exists():
            # Already processed (idempotency) or lost — treat as done
            log.info('note file not found (already processed?): %s', filename)
            tgw_logging.log_event('pm_intake_note_missing', filename=filename)
            return

        note_text    = note_path.read_text(encoding='utf-8').strip()
        plan_text    = master_plan_path.read_text(encoding='utf-8')

        # Empty note — nothing to do
        if not note_text:
            log.info('note %s is empty — archiving with no_change', filename)
            tgw_logging.log_event('pm_intake_empty_note', filename=filename)
            _archive(note_path, processed_dir)
            return

        # Build a compact prompt: headings-only plan + truncated note
        plan_headings = '\n'.join(
            line for line in plan_text.splitlines() if line.startswith('#')
        )
        truncated = len(note_text) > _NOTE_MAX_CHARS
        note_excerpt = note_text[:_NOTE_MAX_CHARS]
        truncation_note = f' (truncated to {_NOTE_MAX_CHARS} chars of {len(note_text)} total)' if truncated else ''

        if not is_available(OLLAMA_MODEL):
            raise RuntimeError(f'Ollama unavailable or model {OLLAMA_MODEL!r} not found')

        log.info('calling Ollama (%s) for %s (%d chars)', OLLAMA_MODEL, filename, len(note_excerpt))
        tgw_logging.log_event('pm_intake_ollama_call', filename=filename, model=OLLAMA_MODEL,
                              note_chars=len(note_excerpt), truncated=truncated)

        prompt = _USER_TEMPLATE.format(
            plan_headings=plan_headings,
            filename=filename,
            truncation_note=truncation_note,
            note=note_excerpt,
        )
        raw_response = chat(
            model=OLLAMA_MODEL,
            messages=[{'role': 'user', 'content': prompt}],
            system=_SYSTEM_PROMPT,
        )

        try:
            result = extract_json(raw_response)
        except Exception as exc:
            raise HardFailure(
                f'Ollama returned non-JSON for {filename}: {raw_response[:200]}'
            ) from exc

        action    = result.get('action', 'no_change')
        rationale = result.get('rationale', '')
        log.info('Ollama decision for %s: action=%s rationale=%s',
                 filename, action, rationale)
        tgw_logging.log_event('pm_intake_ollama_decision',
                              filename=filename, action=action, rationale=rationale)

        if action == 'no_change':
            log.info('no plan changes needed for %s', filename)

        elif action == 'append_to_section':
            section  = result.get('section_heading', '')
            content  = result.get('content', '')
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
            tgw_logging.log_event('pm_intake_plan_updated',
                                  filename=filename, action=action, section=section)

        elif action == 'new_section':
            heading = result.get('new_section_heading', '')
            content = result.get('content', '')
            if not heading or not content:
                raise HardFailure(
                    f'new_section missing new_section_heading or content for {filename}'
                )
            new_plan = _patch_plan_new_section(plan_text, heading, content)
            master_plan_path.write_text(new_plan, encoding='utf-8')
            log.info('plan updated: new section %r', heading)
            tgw_logging.log_event('pm_intake_plan_updated',
                                  filename=filename, action=action, section=heading)

        else:
            raise HardFailure(f'unknown action {action!r} from Ollama for {filename}')

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
