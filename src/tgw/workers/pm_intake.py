"""
tgw.workers.pm_intake — Plan-Manager intake worker.

Watches inbox/, inbox/dave/, and inbox/tigwa/ for dropped Markdown notes
(inbox/claude/, inbox/queued/, inbox/archive/, inbox/review/ are never
scanned — see the topology comment above scan_and_enqueue). When a note
appears:
    1. Move it to inbox/queued/<owner-qualified-name> (only after submission-delay gate)
    2. Enqueue a pm_intake job referencing the file, with owner/source_path/sha256
       recorded in the payload for provenance and idempotent dedupe
    3. On claim: read file, call LLM to classify the change,
       dispatch action (append_to_section | file_document | flag_for_review | no_change)
    4. Archive to inbox/archive/ (durable — included in vault sync)

Submission-delay gate: files must age N hours (configurable via pm_intake_delay_hours,
default 4) before being enqueued. `tgw admin-file --now` bypasses the gate.

Action vocabulary:
  no_change       — note already reflected in plan; archive with no writes
  append_to_section — append content to a named plan section (append-only)
  file_document   — move file to reference/ | perplexity/ | dev-workflow/research/;
                    append FILING-LOG.md entry; optional one-line plan pointer
  flag_for_review — move to inbox/review/ + create a claude/admin todo
  new_section     — DEMOTED: treated as flag_for_review (plan writes append-only)

URL/URI submissions (PP-DOCFLOW-001 Phase 3):
  A note whose entire content is a single HTTP/HTTPS URL triggers URL fetch mode:
  the worker fetches the URL, extracts readable text, synthesises a markdown note,
  and passes it through the same classify/file pipeline. The source URL is recorded
  in FILING-LOG.md. A fetch failure immediately flags for review.

Queue name: pm_intake
"""

from __future__ import annotations

import ipaddress
import logging
import re
import shutil
import socket
import time
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import psycopg2
import psycopg2.errors

import tgw.logging as tgw_logging
from tgw.apis.llm import call_model, get_task_model
from tgw.apis.ollama import extract_json
from tgw.config import DEFAULT_CONFIG, configured_ebay_environment
from tgw.items import atomic_write_text
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

PRESERVATION RULE (highest priority):
If the note contains ANY of the following, you MUST use "file_document" — not "append_to_section":
  - Code blocks (``` or indented code)
  - Python, bash, or other executable examples
  - Step-by-step procedures or runbook content
  - API call sequences or worked examples
  - Research findings with citations or URLs
  - External tool output (Perplexity, Gemini, Antigravity, etc.)
  - Technical specifications or architecture diagrams
  - More than ~500 words of substantive content
The original document is the primary asset. A paragraph summary in the plan is NOT a substitute.
file_document can ALSO add a plan pointer — choose it first, then add the pointer.

Action rules:
- "append_to_section": ONLY for brief operational notes, status updates, or one-line observations
  that are not primarily research documents. section_heading must be the exact heading text
  including the # prefix. Never create new sections.
- "no_change": note already fully reflected in plan AND the document has no preservation value.
- "file_document": the note should be preserved as a document. Choose destination:
  "reference" (technical reference docs), "perplexity" (Perplexity research outputs),
  or "dev-workflow/research" (all other research, procedures, and worked examples).
  Provide destination_filename (e.g. "RESEARCH-topic.md") without directory prefix.
  Optionally add a one-line plan_pointer and plan_pointer_section to also update the plan.
- "flag_for_review": use when the note is uncertain, ambiguous, or requires human judgement.
  Provide review_todo_agent ("claude" or "admin") and review_todo_body.
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

_NOTE_MAX_CHARS = 32000
_URL_FETCH_MAX_CHARS = 32000
_MAX_RESPONSE_BYTES = 5_000_000  # 5MB — generous for HTML/text pages, well above _URL_FETCH_MAX_CHARS's needs

# Matches a bare HTTP/HTTPS URL as the entire note content
_URL_ONLY_RE = re.compile(r'^https?://\S+$', re.IGNORECASE)


# ---------------------------------------------------------------------------
# URL fetch helpers (PP-DOCFLOW-001 Phase 3)
# ---------------------------------------------------------------------------

class _HTMLTextExtractor(HTMLParser):
    """Minimal HTML-to-text extractor (no third-party deps)."""

    _SKIP = frozenset({'script', 'style', 'head', 'noscript', 'iframe', 'svg', 'nav', 'footer'})
    _BLOCK = frozenset({
        'p', 'div', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
        'li', 'tr', 'blockquote', 'pre', 'br', 'hr',
        'article', 'section', 'header', 'aside', 'main',
    })

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._parts: list = []

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        tag = tag.lower()
        if tag in self._SKIP:
            self._skip_depth += 1
        elif tag in self._BLOCK:
            self._parts.append('\n')

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in self._SKIP:
            self._skip_depth = max(0, self._skip_depth - 1)
        elif tag in self._BLOCK:
            self._parts.append('\n')

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0:
            self._parts.append(data)

    def get_text(self) -> str:
        text = ''.join(self._parts)
        text = re.sub(r'\n{3,}', '\n\n', text)
        text = re.sub(r'[ \t]+', ' ', text)
        return text.strip()


def _html_to_text(html: str) -> str:
    extractor = _HTMLTextExtractor()
    extractor.feed(html)
    return extractor.get_text()


def is_url_submission(text: str) -> Optional[str]:
    """Return the URL if text is a URL-only inbox note, else None."""
    stripped = text.strip()
    return stripped if _URL_ONLY_RE.match(stripped) else None


def _resolve_is_safe(hostname: str) -> bool:
    """False if *hostname* resolves to any private/loopback/link-local/
    reserved/multicast address — blocks SSRF to internal network targets."""
    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror:
        return False  # can't resolve → can't safely proceed
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast):
            return False
    return True


class _SSRFBlocked(Exception):
    """Raised from the httpx request event hook to abort a request/redirect
    whose target resolves to a private/internal address."""


def _ssrf_guard_hook(request: 'Any') -> None:
    """httpx request event hook — fires for the initial request AND for
    every redirect hop when follow_redirects=True, so this re-checks each
    hop before it is sent."""
    host = request.url.host
    if not host or not _resolve_is_safe(host):
        raise _SSRFBlocked(f'blocked: url targets a private/internal address ({host!r})')


def fetch_url(url: str, timeout_s: float = 10.0) -> Dict[str, Any]:
    """Fetch a URL and return extracted text content.

    Returns::

        {
            'ok': True,
            'url': original_url,
            'final_url': url_after_redirects,
            'content_type': 'text/html',
            'text': extracted_text,
            'title': page_title_or_empty,
        }

    On failure::

        {'ok': False, 'url': url, 'error': 'description'}
    """
    try:
        import httpx
    except ImportError:
        return {'ok': False, 'url': url, 'error': 'httpx not installed'}

    parsed = urlparse(url)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname:
        return {'ok': False, 'url': url,
                'error': 'blocked: url targets a private/internal address (no valid hostname)'}
    if not _resolve_is_safe(parsed.hostname):
        return {'ok': False, 'url': url,
                'error': 'blocked: url targets a private/internal address'}

    try:
        with httpx.Client(
            follow_redirects=True,
            timeout=timeout_s,
            event_hooks={'request': [_ssrf_guard_hook]},
        ) as client:
            with client.stream(
                'GET', url, headers={'User-Agent': 'TGW-pm-intake/1.0'}
            ) as resp:
                content_length = resp.headers.get('content-length')
                if content_length is not None:
                    try:
                        if int(content_length) > _MAX_RESPONSE_BYTES:
                            return {
                                'ok': False, 'url': url,
                                'error': f'response too large (content-length {content_length} '
                                         f'exceeds {_MAX_RESPONSE_BYTES} bytes)',
                            }
                    except ValueError:
                        pass  # malformed header — fall through to the real streaming guard

                body = bytearray()
                for chunk in resp.iter_bytes():
                    body.extend(chunk)
                    if len(body) > _MAX_RESPONSE_BYTES:
                        return {
                            'ok': False, 'url': url,
                            'error': f'response too large (exceeded {_MAX_RESPONSE_BYTES} bytes '
                                     f'while streaming)',
                        }

                if resp.status_code >= 400:
                    return {'ok': False, 'url': url,
                            'error': f'HTTP {resp.status_code} from {resp.url}'}

                content_type = resp.headers.get('content-type', '').lower()
                final_url = str(resp.url)
                encoding = resp.encoding or 'utf-8'
                text = body.decode(encoding, errors='replace')
    except _SSRFBlocked as exc:
        return {'ok': False, 'url': url, 'error': str(exc)}
    except httpx.TimeoutException:
        return {'ok': False, 'url': url, 'error': f'timeout after {timeout_s}s'}
    except httpx.RequestError as exc:
        return {'ok': False, 'url': url, 'error': f'request error: {exc}'}

    if 'html' in content_type:
        raw_text = _html_to_text(text)
        title = _extract_title(text)
    elif content_type.startswith('text/'):
        raw_text = text.strip()
        title = ''
    else:
        return {
            'ok': False, 'url': url,
            'error': f'unsupported content type: {content_type} (use binaries path)',
        }

    return {
        'ok': True,
        'url': url,
        'final_url': final_url,
        'content_type': content_type,
        'text': raw_text,
        'title': title,
    }


def _extract_title(html: str) -> str:
    """Extract <title> text from HTML source."""
    m = re.search(r'<title[^>]*>(.*?)</title>', html, re.IGNORECASE | re.DOTALL)
    if not m:
        return ''
    # Strip tags inside <title> (uncommon but possible) and decode entities
    extractor = _HTMLTextExtractor()
    extractor.feed(m.group(1))
    return extractor.get_text()[:200]


def _build_url_note(fetch_result: Dict[str, Any]) -> str:
    """Synthesise a markdown note from fetched URL content."""
    title = fetch_result.get('title') or ''
    url = fetch_result['url']
    final_url = fetch_result.get('final_url', url)
    body = fetch_result.get('text', '')[:_URL_FETCH_MAX_CHARS]

    heading = f'# {title}' if title else '# (Fetched URL content)'
    url_line = f'Source URL: {url}'
    if final_url != url:
        url_line += f' → {final_url}'

    return f'{heading}\n\n{url_line}\n\n{body}'


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _archive(note_path: Path, processed_dir: Path, source_label: Optional[str] = None) -> None:
    """Move note_path into processed_dir with a timestamp prefix.

    source_label (e.g. 'tigwa/foo.md') is flattened into the archived filename
    when given, so provenance survives archiving and same-name notes from
    different owner queues never collide in the flat archive/ dir.
    """
    ts = datetime.now(tz=timezone.utc).strftime('%Y%m%dT%H%M%S')
    flat_name = source_label.replace('/', '__') if source_label else note_path.name
    archive_path = processed_dir / f'{ts}-{flat_name}'
    shutil.move(str(note_path), archive_path)
    log.info('archived %s → archive/%s', source_label or note_path.name, archive_path.name)


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
    source_url: str = '',
) -> None:
    """Append an entry to reference/FILING-LOG.md."""
    log_path = vault_path / 'reference' / 'FILING-LOG.md'
    ts = datetime.now(tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    url_line = f'- **Source URL:** {source_url}\n' if source_url else ''
    entry = (
        f'\n## {ts} — {source_filename}\n\n'
        f'{url_line}'
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
#
# Plan Vault inbox topology (2026-07-15, PP-HERMES-EA-001 #1435/#1436): the
# inbox was split per-actor to stop the class of incident where one actor
# processed another's inbox as their own contract (Tigwa reading Claude's
# inbox as her own Step 1 job). admin-file/pm_intake now discovers ONLY:
#   inbox/            root staging (unassigned intake)
#   inbox/dave/       Dave-originated intake
#   inbox/tigwa/      Tigwa-originated intake
# and never recurses into inbox/claude/ (Claude's own correspondence —
# explicitly not general intake), inbox/queued/, inbox/archive/, or
# inbox/review/ (operational subtrees, not fresh intake). glob('*.md') on
# each of those three directories is non-recursive, so this exclusion is
# structural, not just a filename check.
# ---------------------------------------------------------------------------

_INTAKE_OWNERS: tuple = ('root', 'dave', 'tigwa')
_CONTROL_FILENAMES: frozenset = frozenset({'readme.md', 'untitled.base'})


def _owner_dir(inbox_dir: Path, owner: str) -> Path:
    return inbox_dir if owner == 'root' else inbox_dir / owner


def _relative_name(owner: str, md_file: Path) -> str:
    """Owner-qualified relative name used as queue payload/path identity.

    Root files keep their bare filename (backward compatible with existing
    queued/<filename> layout); dave/tigwa files are qualified as
    '<owner>/<filename>' so same-name notes from different owners never
    collide once staged into queued/, review/, or archive/.
    """
    return md_file.name if owner == 'root' else f'{owner}/{md_file.name}'


def _sha256_file(path: Path) -> str:
    import hashlib
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(65536), b''):
            h.update(chunk)
    return h.hexdigest()


def _iter_source_files(inbox_dir: Path):
    """Yield (owner, md_file) for every intake-eligible (Markdown, staged/
    enqueued) candidate, sorted deterministically by owner then filename."""
    for owner in _INTAKE_OWNERS:
        subdir = _owner_dir(inbox_dir, owner)
        if not subdir.is_dir():
            continue
        for md_file in sorted(subdir.glob('*.md')):
            if md_file.name.lower() in _CONTROL_FILENAMES:
                continue
            yield owner, md_file


def _iter_all_candidate_files(inbox_dir: Path):
    """Yield (owner, path) for every direct-child regular file across
    root/dave/tigwa, including non-Markdown types — manifest reporting only,
    never used to drive a move/enqueue. Excludes control files. Non-
    recursive, same as _iter_source_files."""
    for owner in _INTAKE_OWNERS:
        subdir = _owner_dir(inbox_dir, owner)
        if not subdir.is_dir():
            continue
        for path in sorted(subdir.iterdir()):
            if not path.is_file():
                continue
            if path.name.lower() in _CONTROL_FILENAMES:
                continue
            yield owner, path


_UNSUPPORTED_TYPE_REASON = 'seen but not actionable: unsupported source type pending supervised normalization'


def build_intake_manifest(cfg: Dict[str, Any], bypass_delay: bool = False) -> List[Dict[str, Any]]:
    """Non-mutating inventory of intake candidates across root/dave/tigwa.

    Covers every direct-child regular file, not just Markdown (Tigwa review,
    #1438): non-Markdown files are reported eligible=False with an explicit
    "seen but not actionable" reason instead of being invisible, since
    knowledge-first intake (Dave, 2026-07-15) requires every direct source
    artifact to appear as a preserved/deferred candidate even when staging
    stays Markdown-only. Each entry reports source path, owner queue, file
    type, size, mtime, sha256, eligibility decision, and (Markdown only) the
    queue path a real run would use. Never touches the filesystem beyond
    stat()/read for hashing.
    """
    inbox_dir: Path = cfg['plan_inbox_path']
    delay_hours: float = float(cfg.get('pm_intake_delay_hours', 4.0))
    manifest: List[Dict[str, Any]] = []

    for owner, path in _iter_all_candidate_files(inbox_dir):
        rel_name = _relative_name(owner, path)
        is_markdown = path.suffix.lower() == '.md'
        try:
            stat = path.stat()
        except OSError as exc:
            manifest.append({
                'source_path': rel_name, 'owner': owner, 'eligible': False,
                'reason': f'stat failed: {exc}',
            })
            continue

        try:
            sha256 = _sha256_file(path)
        except OSError as exc:
            manifest.append({
                'source_path': rel_name, 'owner': owner, 'eligible': False,
                'reason': f'read failed: {exc}',
            })
            continue

        age_hours = (time.time() - stat.st_mtime) / 3600
        entry: Dict[str, Any] = {
            'source_path': rel_name,
            'owner': owner,
            'file_type': path.suffix.lstrip('.').lower() or 'unknown',
            'size_bytes': stat.st_size,
            'mtime': datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
            'age_hours': round(age_hours, 1),
            'sha256': sha256,
        }

        if not is_markdown:
            entry['eligible'] = False
            entry['reason'] = _UNSUPPORTED_TYPE_REASON
            manifest.append(entry)
            continue

        aged_enough = bypass_delay or age_hours >= delay_hours
        entry['eligible'] = aged_enough
        entry['reason'] = 'ready' if aged_enough else f'{age_hours:.1f}h old, delay gate is {delay_hours:.0f}h'
        entry['planned_queue_path'] = f'queued/{rel_name}'
        manifest.append(entry)

    return manifest


def scan_and_enqueue(cfg: Dict[str, Any], bypass_delay: bool = False) -> List[str]:
    """Scan root/dave/tigwa inbox queues for eligible .md files, move each to
    queued/<owner-qualified-name>, and enqueue a pm_intake job.

    Files that have not yet aged past pm_intake_delay_hours are skipped unless
    bypass_delay is True. Returns list of owner-qualified names enqueued (or
    already queued) — plain filename for root, '<owner>/<filename>' for
    dave/tigwa sources.
    """
    inbox_dir: Path = cfg['plan_inbox_path']
    queued_dir = inbox_dir / 'queued'
    queued_dir.mkdir(parents=True, exist_ok=True)

    delay_hours: float = float(cfg.get('pm_intake_delay_hours', 4.0))
    enqueued: List[str] = []

    for owner, md_file in _iter_source_files(inbox_dir):
        rel_name = _relative_name(owner, md_file)

        if not bypass_delay:
            try:
                age_hours = (time.time() - md_file.stat().st_mtime) / 3600
            except OSError:
                continue
            if age_hours < delay_hours:
                log.debug(
                    'skipping %s: %.1fh old, delay gate is %.0fh',
                    rel_name, age_hours, delay_hours,
                )
                continue

        try:
            sha256 = _sha256_file(md_file)
        except OSError as exc:
            log.warning('could not hash %s: %s', rel_name, exc)
            continue

        dest = queued_dir / rel_name
        queued_name = rel_name

        # Collision guard (Tigwa review, #1438): Path.rename() replaces an
        # existing destination on this filesystem, so a naive move could
        # silently clobber an already-queued file of the same owner-
        # qualified name. Never overwrite: if the queued destination already
        # exists with identical content, this is an idempotent duplicate
        # (skip the move, don't re-enqueue); if it exists with different
        # content, give the incoming file its own content-addressed
        # destination instead of colliding.
        if dest.exists():
            try:
                existing_sha256 = _sha256_file(dest)
            except OSError as exc:
                log.warning('could not hash existing queued/%s to check collision: %s', rel_name, exc)
                continue
            if existing_sha256 == sha256:
                log.info('%s already queued with identical content — skipping duplicate', rel_name)
                tgw_logging.log_event(
                    'pm_intake_duplicate_skipped', filename=rel_name, owner=owner, sha256=sha256,
                )
                enqueued.append(rel_name)
                continue
            collision_filename = f'{dest.stem}__{sha256[:8]}{dest.suffix}'
            dest = dest.parent / collision_filename
            queued_name = f'{owner}/{collision_filename}' if owner != 'root' else collision_filename
            log.warning(
                'collision at queued/%s (content differs) — routing to queued/%s instead',
                rel_name, queued_name,
            )

        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            md_file.rename(dest)
        except OSError as exc:
            log.warning('could not move %s to queued/: %s', rel_name, exc)
            continue

        # Content+path-qualified dedupe key: same owner+filename with unchanged
        # content is not re-enqueued (idempotent rerun), but an edited note
        # (new sha256) or a same-named note from a different owner (different
        # rel_name) gets its own key — no cross-owner or stale-content collision.
        dedupe_key = f'pm_intake:{queued_name}:{sha256[:16]}'
        try:
            jid = state_machine.enqueue_job(
                queue_name=QUEUE_NAME,
                payload={
                    'filename': queued_name,
                    'owner': owner,
                    'source_path': rel_name,
                    'sha256': sha256,
                    'intake_ts': datetime.now(tz=timezone.utc).isoformat(),
                },
                dedupe_key=dedupe_key,
                max_attempts=3,
            )
            log.info('enqueued pm_intake job for %s (job %s)', queued_name, jid)
            tgw_logging.log_event('pm_intake_enqueued', filename=queued_name, owner=owner, job_id=jid)
            enqueued.append(queued_name)
        except psycopg2.errors.UniqueViolation:
            log.info('pm_intake job already exists for %s — skipping', queued_name)
            enqueued.append(queued_name)
        except Exception:
            log.exception('failed to enqueue pm_intake job for %s', queued_name)
            dest.rename(md_file)

    return enqueued


# ---------------------------------------------------------------------------
# CLI command: tgw admin-file [--now] [--dry-run]
# ---------------------------------------------------------------------------

def cmd_admin_file(
    cfg: Dict[str, Any],
    bypass_delay: bool = False,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Scan inbox and enqueue eligible notes for pm_intake processing.

    Called by `tgw admin-file [--now] [--dry-run]`.
    --dry-run reports a manifest (source path, owner, type, size, mtime,
    sha256, eligibility, planned queue path) without moving or enqueuing
    anything. Returns {'ok', 'enqueued', 'files'} normally, or
    {'ok', 'dry_run': True, 'candidates': [...]} under --dry-run.
    """
    if dry_run:
        manifest = build_intake_manifest(cfg, bypass_delay=bypass_delay)
        if manifest:
            print(f'{len(manifest)} candidate(s) found (dry run — nothing moved or enqueued):')
            for entry in manifest:
                status = 'ELIGIBLE' if entry.get('eligible') else 'skip'
                print(f'  [{status}] {entry["owner"]:5s} {entry["source_path"]} — {entry.get("reason", "")}')
        else:
            print('No candidate inbox files found (dry run).')
        return {'ok': True, 'dry_run': True, 'candidates': manifest}

    state_machine.init(
        cfg.get('postgres_dsn', 'dbname=state_machine user=tgw'),
        configured_ebay_environment(cfg),
    )
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
        processed_dir = inbox_dir / 'archive'
        vault_path: Path = self.config['plan_vault_path']
        # This is an explicit *authoring* repository target, not Plan
        # authority.  Operational readers bind the separate clean standalone
        # materialization through approved_plan_binding(); never fall back to
        # it here or to a mutable vault path.
        master_plan_path = self.config.get('plan_update_master_path')
        if not isinstance(master_plan_path, Path):
            raise HardFailure('pm_intake requires an explicit Plan update target')
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
            _archive(note_path, processed_dir, source_label=filename)
            return

        # PP-DOCFLOW-001 Phase 3: URL submission — fetch and substitute content
        _source_url = ''
        url = is_url_submission(note_text)
        if url:
            log.info('URL submission detected in %s: %s', filename, url)
            tgw_logging.log_event('pm_intake_url_submission', filename=filename, url=url)
            fetch = fetch_url(url)
            if not fetch['ok']:
                error_msg = fetch.get('error', 'unknown fetch error')
                log.warning('URL fetch failed for %s (%s): %s', filename, url, error_msg)
                tgw_logging.log_event(
                    'pm_intake_url_fetch_failed', filename=filename, url=url, error=error_msg
                )
                # Immediate flag_for_review — no LLM call needed
                review_dir = inbox_dir / 'review'
                dest = review_dir / filename
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(note_path), str(dest))
                try:
                    todo_add(
                        'admin',
                        f'[pm_intake] URL fetch failed for {url}: {error_msg} → inbox/review/{filename}',
                        priority=40,
                        source='pm_intake',
                    )
                except Exception:
                    log.exception('failed to create todo for URL fetch failure — review file still moved')
                _archive(note_path, processed_dir, source_label=filename)
                tgw_logging.log_event(
                    'pm_intake_url_flagged', filename=filename, url=url, error=error_msg
                )
                return
            note_text = _build_url_note(fetch)
            _source_url = url
            log.info(
                'URL fetched (%s): %d chars extracted, final_url=%s',
                url, len(note_text), fetch.get('final_url', url),
            )

        # Force file_document for docs that contain code or are truncated — never distil these.
        _HAS_CODE_RE = re.compile(r'```|\n    \S', re.MULTILINE)
        has_code = bool(_HAS_CODE_RE.search(note_text))
        truncated = len(note_text) > _NOTE_MAX_CHARS
        force_file = has_code or truncated

        plan_headings = '\n'.join(
            line for line in plan_text.splitlines() if line.startswith('#')
        )
        note_excerpt = note_text[:_NOTE_MAX_CHARS]
        truncation_note = (
            f' (truncated to {_NOTE_MAX_CHARS} chars of {len(note_text)} total)'
            if truncated else ''
        )
        force_note = (
            ' NOTE: This document contains code or is truncated — you MUST choose file_document.'
            if force_file else ''
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
            truncation_note=truncation_note + force_note,
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

        # Safety net: if doc has code or was truncated, never reduce it to a plan summary.
        # Promote append_to_section/no_change → file_document so the original is preserved.
        if force_file and action in ('append_to_section', 'no_change'):
            log.info(
                'promoting %s → file_document for %s (has_code=%s truncated=%s)',
                action, filename, has_code, truncated,
            )
            tgw_logging.log_event(
                'pm_intake_promoted_to_file', filename=filename,
                original_action=action, has_code=has_code, truncated=truncated,
            )
            action = 'file_document'
            # Carry forward any plan pointer the LLM suggested; pick a sensible destination.
            result.setdefault('destination', 'dev-workflow/research')
            stem = Path(filename).stem
            result.setdefault('destination_filename', f'RESEARCH-{stem}.md')

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
            atomic_write_text(master_plan_path, new_plan,
                              archive_root=self.config.get('archive_root'))
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
            dest_filename = Path((result.get('destination_filename') or '').strip() or filename).name
            dest_dir = vault_path / destination
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest_path = dest_dir / dest_filename
            if _source_url:
                # URL submission: write the synthesised markdown, not the URL stub
                dest_path.write_text(note_text, encoding='utf-8')
            else:
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
                    source_url=_source_url,
                )
            except Exception:
                log.exception('failed to write FILING-LOG.md for %s', filename)

            plan_pointer = (result.get('plan_pointer') or '').strip()
            plan_pointer_section = (result.get('plan_pointer_section') or '').strip()
            if plan_pointer and plan_pointer_section:
                try:
                    refreshed_plan = master_plan_path.read_text(encoding='utf-8')
                    new_plan = _patch_plan_append(refreshed_plan, plan_pointer_section, plan_pointer)
                    atomic_write_text(master_plan_path, new_plan,
                              archive_root=self.config.get('archive_root'))
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
            dest = review_dir / filename
            dest.parent.mkdir(parents=True, exist_ok=True)
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

        _archive(note_path, processed_dir, source_label=filename)
        tgw_logging.log_event('pm_intake_archived', filename=filename)


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(prog='tgw-pm-intake-worker')
    parser.add_argument('--config', default=str(DEFAULT_CONFIG))
    args = parser.parse_args()

    from tgw.config import load_operational_config
    cfg = load_operational_config(Path(args.config))
    worker = PMIntakeWorker(queue_name=QUEUE_NAME, config=cfg)
    worker.run()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
