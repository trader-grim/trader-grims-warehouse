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

_REPO_ROOT = Path('/opt/TGW/src/trader-grims-warehouse')
_AIDER_BIN = Path('/home/tgw/.local/bin/aider')
_AUDIT_LOG = Path.home() / '.local/share/aider-audit/usage.csv'

_AUDIT_FIELDS = ['timestamp', 'mode', 'files', 'prompt_excerpt', 'exit_code', 'duration_s']

_TASK_TIMEOUT = 300  # seconds; architect mode can be slow

# ---------------------------------------------------------------------------
# Secrets + audit helpers
# ---------------------------------------------------------------------------


def _load_api_keys() -> dict[str, str]:
    from tgw.apis.secrets import get_api_key

    keys = {}
    for env_name, provider in [
        ('ANTHROPIC_API_KEY', 'anthropic'),
        ('OPENROUTER_API_KEY', 'openrouter'),
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


def _resolve_files(files: list[str]) -> tuple[list[Path], str | None]:
    """Resolve repo-relative paths; reject anything outside the repo.

    Returns (resolved_paths, error_message_or_None).
    """
    resolved = []
    repo_str = str(_REPO_ROOT.resolve())
    for f in files:
        candidate = (_REPO_ROOT / f).resolve()
        if not str(candidate).startswith(repo_str + os.sep) and str(candidate) != repo_str:
            return [], f'file outside repo boundary: {f!r}'
        resolved.append(candidate)
    return resolved, None


# ---------------------------------------------------------------------------
# MCP server
# ---------------------------------------------------------------------------

mcp = FastMCP(
    name='tgw-aider',
    instructions=(
        'Aider code-editing bridge for TGW. '
        'Delegate mechanical Python edits to Aider (Claude-backed) via '
        'aider_run_task. Use aider_get_diff / aider_get_log to inspect results. '
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
) -> str:
    """Run an Aider code-editing task on the specified TGW source files.

    Args:
        prompt: Coding instruction for Aider.  Be specific and self-contained
            (e.g. "Add a guard in items._write_field() that raises ValueError
            when qty < 0, then add a test in tests/test_items.py").
        files: Repo-relative paths to hand to Aider (e.g. ["src/tgw/items.py",
            "tests/test_items.py"]).  All must be inside the TGW repo.
        mode: "edit" — Sonnet edits directly (default, fast, good for focused
            changes).  "architect" — Sonnet plans the diff, Haiku applies it
            (better for multi-file refactors).

    Returns JSON: {ok, exit_code, output, diff, duration_s}
    """
    if mode not in ('edit', 'architect'):
        return json.dumps({
            'ok': False,
            'error': f'invalid mode {mode!r}; must be "edit" or "architect"',
        })

    paths, err = _resolve_files(files)
    if err:
        return json.dumps({'ok': False, 'error': err})
    if not paths:
        return json.dumps({'ok': False, 'error': 'files list is empty'})

    msg_file = None
    try:
        with tempfile.NamedTemporaryFile(
            mode='w', suffix='.md', prefix='aider_task_', delete=False
        ) as tf:
            tf.write(prompt)
            msg_file = tf.name

        cmd = [str(_AIDER_BIN), '--yes', '--message-file', msg_file]
        if mode == 'architect':
            cmd.append('--architect')
        cmd += [str(p) for p in paths]

        env = {**os.environ, **_API_KEYS}
        t0 = time.monotonic()

        proc = subprocess.run(
            cmd,
            cwd=_REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=_TASK_TIMEOUT,
        )
        duration = round(time.monotonic() - t0, 1)

        diff_proc = subprocess.run(
            ['git', 'diff'],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
        )
        diff = diff_proc.stdout or ''

        rel_files = ' '.join(
            str(p.relative_to(_REPO_ROOT)) for p in paths
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
