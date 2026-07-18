"""
TGW Aider MCP Server (PP-MULTIMODEL-001)

Wraps Aider as MCP tools so Claude Code can delegate mechanical code-editing
tasks to Aider without a shell escape.  Each call is atomic: spawn, edit,
return diff.  No persistent aider session.

Tools:
    aider_run_task  — run an Aider edit or architect task on repo files
    aider_get_log   — read recent entries from the per-invocation audit log
    aider_get_diff  — show current git diff (unstaged or staged) in the repo

Run:
    python -m tgw.aider_mcp_server          (stdio transport — Claude Code)
    python -m tgw.aider_mcp_server --sse    (SSE transport — remote)

Register in Claude Code (~/.claude/settings.json mcpServers):
    "tgw-aider": {
        "command": "sudo",
        "args": ["-u", "tgw",
                 "/opt/TGW/.venvironments/tgw/bin/python",
                 "-m", "tgw.aider_mcp_server"],
        "env": {}
    }
"""

from __future__ import annotations

import csv
import json
import os
import subprocess
import tempfile
import time
from pathlib import Path

from mcp.server import FastMCP

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Derived, not hardcoded (CI incident 2026-07-18, todo #1458 follow-up):
# a literal '/opt/TGW/src/trader-grims-warehouse' only exists on tgw-prod.
# _ensure_worktree() passes this as subprocess cwd, and GitHub's hosted
# runner checks the repo out elsewhere -- a hardcoded cwd that doesn't
# exist makes Popen raise FileNotFoundError before git even runs.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_AIDER_BIN = Path('/home/tgw/.local/bin/aider')
_AUDIT_LOG = Path.home() / '.local/share/aider-audit/usage.csv'

_AUDIT_FIELDS = ['timestamp', 'mode', 'files', 'prompt_excerpt', 'exit_code', 'duration_s']

_TASK_TIMEOUT = 300  # seconds; architect mode can be slow
_WORKTREES_ROOT = Path('/opt/TGW/var/worktrees')

# ---------------------------------------------------------------------------
# Secrets + audit helpers
# ---------------------------------------------------------------------------


def _load_api_keys() -> dict[str, str]:
    from tgw.apis.secrets import get_api_key

    keys = {}
    for env_name, provider in [
        ('ANTHROPIC_API_KEY', 'anthropic'),
        ('OPENROUTER_API_KEY', 'openrouter'),
        ('DEEPSEEK_API_KEY', 'deepseek'),
    ]:
        try:
            val = get_api_key(provider)
            if val:
                keys[env_name] = val
        except Exception:
            pass
    return keys


_API_KEYS: dict[str, str] = _load_api_keys()


def _ensure_audit_log() -> None:
    _AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
    if not _AUDIT_LOG.exists():
        with _AUDIT_LOG.open('w', newline='') as f:
            csv.DictWriter(f, fieldnames=_AUDIT_FIELDS).writeheader()


def _append_audit(row: dict) -> None:
    _ensure_audit_log()
    with _AUDIT_LOG.open('a', newline='') as f:
        csv.DictWriter(f, fieldnames=_AUDIT_FIELDS).writerow(row)


# ---------------------------------------------------------------------------
# File validation
# ---------------------------------------------------------------------------


def _resolve_files(files: list[str], base: Path | None = None) -> tuple[list[Path], str | None]:
    """Resolve repo-relative paths against `base` (default: shared checkout);
    reject anything outside `base`.

    `base` matters when running inside a worktree — the paths must resolve
    against the worktree's own copy, not the shared checkout, or a "task
    isolated" run silently edits the wrong tree.

    Returns (resolved_paths, error_message_or_None).
    """
    base = base or _REPO_ROOT
    resolved = []
    base_str = str(base.resolve())
    for f in files:
        candidate = (base / f).resolve()
        if not str(candidate).startswith(base_str + os.sep) and str(candidate) != base_str:
            return [], f'file outside repo boundary: {f!r}'
        resolved.append(candidate)
    return resolved, None


def _slugify_task_slug(task_slug: str) -> tuple[str, str | None]:
    """Validate a caller-supplied task_slug for safe use as a branch/dir name."""
    import re

    if not re.match(r'^[0-9]+-[a-z0-9_-]+$|^aider-[0-9]+$', task_slug):
        return '', (
            f'invalid task_slug {task_slug!r}; expected "<id>-<slug>" '
            '(e.g. "1358-aider-worktree-fix")'
        )
    return task_slug, None


def _build_preflight_context(work_dir: Path) -> str:
    """Surface the same class of Plan Vault awareness Claude's own sessions
    get automatically from `.claude/hooks/session-start-briefing.py`
    (SessionStart hook) — inbox file count/names + `tgw plan check`
    warnings — into an Aider task's initial prompt.

    Best-effort: any failure here degrades to a short note rather than
    blocking the task (this is context, not a gate).
    """
    lines = ['## Plan Vault preflight (auto-injected, PP-HERMES-EA-001)']

    inbox_dir = work_dir / 'docs/TGW-Plan-Vault/inbox/claude'
    try:
        inbox_files = sorted(p.name for p in inbox_dir.glob('*.md'))
    except Exception:
        inbox_files = None
    if inbox_files is None:
        lines.append('- inbox/claude: could not read (skipped)')
    elif inbox_files:
        shown = ', '.join(inbox_files[:10])
        more = f' (+{len(inbox_files) - 10} more)' if len(inbox_files) > 10 else ''
        lines.append(f'- inbox/claude: {len(inbox_files)} file(s): {shown}{more}')
    else:
        lines.append('- inbox/claude: empty')

    try:
        proc = subprocess.run(
            ['tgw', 'plan', 'check'],
            cwd=work_dir,
            capture_output=True,
            text=True,
            timeout=30,
        )
        check_out = (proc.stdout or proc.stderr or '').strip()
        lines.append(f'- `tgw plan check`: {check_out[:800] or "(no output)"}')
    except Exception as exc:
        lines.append(f'- `tgw plan check`: could not run ({exc})')

    lines.append(
        '- If anything above is directly relevant to the files you are about '
        'to edit, take it into account before proceeding.'
    )
    return '\n'.join(lines) + '\n'


def _ensure_worktree(task_slug: str) -> tuple[Path, str | None]:
    """Create (or reattach to) the isolated worktree+branch for one task,
    matching tgw-coder's contract (PP-HERMES-EA-001, mandatory 2026-07-13)
    and bin/tgw-aider's shell-side equivalent. Base branch is verified LIVE,
    never hardcoded.

    Returns (worktree_dir, error_message_or_None).
    """
    worktree_dir = _WORKTREES_ROOT / task_slug
    branch = f'task/{task_slug}'

    if worktree_dir.is_dir():
        return worktree_dir, None

    base_proc = subprocess.run(
        ['git', 'branch', '--show-current'],
        cwd=_REPO_ROOT, capture_output=True, text=True,
    )
    base_branch = base_proc.stdout.strip()
    if not base_branch:
        return worktree_dir, 'could not determine live base branch (detached HEAD?)'

    branch_exists = subprocess.run(
        ['git', 'show-ref', '--quiet', f'refs/heads/{branch}'],
        cwd=_REPO_ROOT,
    ).returncode == 0

    cmd = ['git', 'worktree', 'add']
    cmd += [str(worktree_dir), branch] if branch_exists else ['-b', branch, str(worktree_dir), base_branch]
    proc = subprocess.run(cmd, cwd=_REPO_ROOT, capture_output=True, text=True)
    if proc.returncode != 0:
        return worktree_dir, f'git worktree add failed: {proc.stderr.strip()}'
    return worktree_dir, None


# ---------------------------------------------------------------------------
# MCP server
# ---------------------------------------------------------------------------

mcp = FastMCP(
    name='tgw-aider',
    instructions=(
        'Aider code-editing bridge for TGW, running DeepSeek V4 Flash '
        '(direct API) — the "busywork" execution tier: XS/S mechanical '
        'coding tasks, monitoring/schlepping/merging, not architecture or '
        'eBay-invariant work. Delegate mechanical Python edits to Aider via '
        'aider_run_task, passing task_slug="<todo-id>-<slug>" for anything '
        'with a real todo behind it — that isolates the run to its own '
        'worktree+branch (mandatory contract, PP-HERMES-EA-001), matching '
        'tgw-coder. Use aider_get_diff / aider_get_log to inspect results. '
        'Files must be repo-relative paths (e.g. "src/tgw/items.py"). '
        'Prefer "edit" mode for straightforward changes; "architect" for '
        'multi-file refactors that need a planning pass first.'
    ),
)


@mcp.tool()
def aider_run_task(
    prompt: str,
    files: list[str],
    mode: str = 'edit',
    task_slug: str = '',
) -> str:
    """Run an Aider code-editing task on the specified TGW source files.

    Args:
        prompt: Coding instruction for Aider.  Be specific and self-contained
            (e.g. "Add a guard in items._write_field() that raises ValueError
            when qty < 0, then add a test in tests/test_items.py").
        files: Repo-relative paths to hand to Aider (e.g. ["src/tgw/items.py",
            "tests/test_items.py"]).  All must be inside the task's tree.
        mode: "edit" — deepseek-v4-flash edits directly (default, fast, good
            for focused changes).  "architect" — a planning pass first, then
            edits applied (better for multi-file refactors).
        task_slug: REQUIRED. "<todo-id>-<slug>" (e.g. "1358-aider-worktree-fix").
            Runs in an isolated worktree at /opt/TGW/var/worktrees/<task_slug>
            on branch task/<task_slug> — the same mandatory-isolation
            contract tgw-coder follows (PP-HERMES-EA-001). Reattaches if the
            worktree already exists. There is no shared-checkout fallback:
            an empty/omitted task_slug used to silently run directly against
            the shared checkout with auto-commits enabled (todo #1458) — now
            rejected outright rather than approval-gated, since no legitimate
            caller was found depending on that path.

    Returns JSON: {ok, exit_code, output, diff, duration_s}
    """
    if mode not in ('edit', 'architect'):
        return json.dumps({
            'ok': False,
            'error': f'invalid mode {mode!r}; must be "edit" or "architect"',
        })

    if not task_slug or not task_slug.strip():
        return json.dumps({
            'ok': False,
            'error': (
                'task_slug is required — every aider_run_task dispatch must run '
                'in its own isolated worktree ("<todo-id>-<slug>", e.g. '
                '"1358-aider-worktree-fix"), matching tgw-coder\'s mandatory '
                'worktree-isolation contract (PP-HERMES-EA-001). There is no '
                'shared-checkout fallback (todo #1458).'
            ),
        })

    task_slug, err = _slugify_task_slug(task_slug)
    if err:
        return json.dumps({'ok': False, 'error': err})
    work_dir, err = _ensure_worktree(task_slug)
    if err:
        return json.dumps({'ok': False, 'error': err})

    paths, err = _resolve_files(files, base=work_dir)
    if err:
        return json.dumps({'ok': False, 'error': err})
    if not paths:
        return json.dumps({'ok': False, 'error': 'files list is empty'})

    preflight = _build_preflight_context(work_dir)
    full_prompt = f'{preflight}\n{prompt}'

    msg_file = None
    try:
        with tempfile.NamedTemporaryFile(
            mode='w', suffix='.md', prefix='aider_task_', delete=False
        ) as tf:
            tf.write(full_prompt)
            msg_file = tf.name

        cmd = [str(_AIDER_BIN), '--yes', '--message-file', msg_file]
        if mode == 'architect':
            cmd.append('--architect')
        cmd += [str(p) for p in paths]

        env = {**os.environ, **_API_KEYS}
        # Matches tgw-coder's documented worktree gotchas (todo #1374): the
        # tgw venv's editable install + psycopg2's libz.so.1 both need these
        # or a worktree run silently tests/imports the wrong copy of the code.
        env['PYTHONPATH'] = f"{work_dir / 'src'}:{env.get('PYTHONPATH', '')}"
        env['LD_LIBRARY_PATH'] = (
            f"{env.get('NIX_LD_LIBRARY_PATH', '')}:{env.get('LD_LIBRARY_PATH', '')}"
        )
        t0 = time.monotonic()

        proc = subprocess.run(
            cmd,
            cwd=work_dir,
            env=env,
            capture_output=True,
            text=True,
            timeout=_TASK_TIMEOUT,
        )
        duration = round(time.monotonic() - t0, 1)

        diff_proc = subprocess.run(
            ['git', 'diff'],
            cwd=work_dir,
            capture_output=True,
            text=True,
        )
        diff = diff_proc.stdout or ''

        rel_files = ' '.join(
            str(p.relative_to(work_dir)) for p in paths
        )
        _append_audit({
            'timestamp': time.strftime('%Y-%m-%dT%H:%M:%S'),
            'mode': mode,
            'files': rel_files,
            'prompt_excerpt': prompt[:120].replace('\n', ' '),
            'exit_code': proc.returncode,
            'duration_s': duration,
        })

        combined = (proc.stdout + proc.stderr).strip()
        return json.dumps({
            'ok': proc.returncode == 0,
            'exit_code': proc.returncode,
            'output': combined[-4000:],
            'diff': diff[:8000] if diff else '(no changes)',
            'diff_truncated': len(diff) > 8000,
            'duration_s': duration,
        })

    except subprocess.TimeoutExpired:
        return json.dumps({'ok': False, 'error': f'aider timed out after {_TASK_TIMEOUT}s'})
    except Exception as exc:
        return json.dumps({'ok': False, 'error': str(exc)})
    finally:
        if msg_file:
            try:
                Path(msg_file).unlink()
            except Exception:
                pass


@mcp.tool()
def aider_get_log(n: int = 10) -> str:
    """Return recent entries from the Aider invocation audit log.

    Args:
        n: Number of most-recent entries to return (default 10, capped at 100).

    Returns JSON: {ok, entries: [{timestamp, mode, files, prompt_excerpt,
        exit_code, duration_s}, ...]}
    """
    n = min(max(1, n), 100)
    if not _AUDIT_LOG.exists():
        return json.dumps({'ok': True, 'entries': [], 'note': 'no audit log yet'})
    try:
        with _AUDIT_LOG.open(newline='') as f:
            rows = list(csv.DictReader(f))
        return json.dumps({'ok': True, 'count': len(rows), 'entries': rows[-n:]})
    except Exception as exc:
        return json.dumps({'ok': False, 'error': str(exc)})


@mcp.tool()
def aider_get_diff(staged: bool = False) -> str:
    """Return the current git diff from the TGW repo.

    Call this after aider_run_task to inspect what changed before committing.

    Args:
        staged: False (default) = working-tree changes not yet staged.
                True = only staged (git add'd) changes.

    Returns JSON: {ok, staged, diff, truncated}
    """
    cmd = ['git', 'diff']
    if staged:
        cmd.append('--staged')
    try:
        result = subprocess.run(
            cmd,
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=10,
        )
        diff = result.stdout or ''
        return json.dumps({
            'ok': True,
            'staged': staged,
            'diff': diff[:12000] if diff else '(no changes)',
            'truncated': len(diff) > 12000,
        })
    except Exception as exc:
        return json.dumps({'ok': False, 'error': str(exc)})


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    import sys
    if '--sse' in sys.argv:
        mcp.run(transport='sse')
    else:
        mcp.run(transport='stdio')


if __name__ == '__main__':
    main()
