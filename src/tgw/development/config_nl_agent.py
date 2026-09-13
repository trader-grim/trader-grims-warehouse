"""NL-driven dotfile changes over a real, multi-file allowlist (todo-2017, round 1).

Experimental, narrowly-allowlisted capability — not a general config-management
tool. Precursor: ``todo-2016`` (``config_agent.py``, landed ``9f6dd040``);
serves the (unratified) ``PP-CATIO-RELEASE-001`` restore-point direction
without completing any Todo, LEAF-11 leaf, or ratter-graph capability.

Why this shape (per the operator's direct design call):

- Input stays generic and unparsed: no structured command syntax, no
  regex-based request parser. A free-text request goes to a real reasoning
  step (``propose_new_content``) that proposes the complete new file content
  directly.
- The deterministic ``config_agent`` backup/verify/rollback wrapper is the
  entire safety story: every change is request → reproducible backup →
  apply → verify → (revertible), never a unilateral edit. Cf.
  ``catio-ratter-ichabod-genesis-conversation-20260912.md``: *"An agent
  should be able to request a change, produce a reproducible plan,
  generate a patch, and open a review record — not unilaterally remodel
  the house."* This module is a layer on top of the unmodified
  ``config_agent.py`` primitives; it does not change their safety
  semantics (single-root containment, symlink-escape resistance,
  corrupted-write detection).
- A real, broader-than-one-file allowlist, but still narrow and explicit:
  ``.bashrc``, ``.profile``, and anything under ``.config/`` — and only
  inside the invoking user's home directory.

Scope notes, stated plainly:

- New-file creation is NOT supported by this task: ``handle_request``
  requires the target to already exist as a regular file, because
  ``config_agent.backup_paths`` (correctly) requires the file to exist
  before it snapshots. Creating a new allowed file under ``.config/**``
  is a separate, later, explicit decision.
- Only the demo script (``scripts/config_nl_agent_demo.py``) defaults
  ``home``/``allowlist_root`` to the real ``$HOME``. Everywhere else both
  are parameters, so automated tests run against a temp directory standing
  in for "home" and never touch real host files.
- Automated tests must never make a live model/API call: ``handle_request``
  takes an injectable ``propose`` callable, and the suite exercises it with
  a deterministic fake. The real CLI-shelling ``propose_new_content`` is
  exercised manually via the demo script only.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Callable

from tgw.development import config_agent

# Explicit, small, easy-to-find allowlist of relative-path patterns under a
# home directory. Deliberately NOT extended beyond .bashrc / .profile /
# .config/** without a separate, explicit, later decision (same posture
# todo-2016 took toward touching real files at all).
ALLOWED_RELATIVE_PATTERNS: tuple[str, ...] = (".bashrc", ".profile", ".config/**")

OPENCODE_DEFAULT_MODEL = "opencode/muse-spark-1.3-contributor-free"
OPENCODE_MODEL_ENV_VAR = "TGW_CONFIG_NL_AGENT_OPENCODE_MODEL"
OPENCODE_BIN_ENV_VAR = "TGW_OPENCODE_BIN"
CLAUDE_BIN_ENV_VAR = "TGW_CLAUDE_BIN"
TIMEOUT_ENV_VAR = "TGW_CONFIG_NL_AGENT_TIMEOUT"

ProposeFn = Callable[..., bytes]


class NLProposeError(RuntimeError):
    """The content-proposal step failed (no CLI available, bad output, ...)."""


def _resolve_home(home: Path) -> Path:
    return Path(home).resolve()


def is_allowed_target(path: Path, home: Path) -> bool:
    """True iff `path` is an allowed dotfile target under `home`.

    Discipline mirrors ``config_agent._check_under_root`` exactly: both
    sides are fully resolved first, so a symlink under ``.config/`` that
    escapes outside ``home`` resolves outside and is refused. The resolved
    path must be (a) under ``home`` and (b) match one of
    ``ALLOWED_RELATIVE_PATTERNS`` (``.bashrc``, ``.profile``, or anything
    strictly under ``.config/``). Pure predicate: never raises, never
    touches anything.
    """
    try:
        home_resolved = _resolve_home(home)
        resolved = Path(path).resolve()
    except Exception:
        return False
    if resolved != home_resolved and home_resolved not in resolved.parents:
        return False
    try:
        rel_posix = resolved.relative_to(home_resolved).as_posix()
    except ValueError:
        return False
    if rel_posix in (".bashrc", ".profile"):
        return True
    # ".config/**": anything strictly under .config/ (the bare .config dir
    # itself is not an allowed target).
    return rel_posix.startswith(".config/")


def _strip_fences(text: str) -> str:
    """Remove one layer of markdown code fences, if present."""
    stripped = text.strip()
    if not stripped.startswith("```"):
        return text.strip() + ("\n" if text.endswith("\n") else "")
    lines = stripped.splitlines()
    # Drop opening fence (``` or ```lang).
    lines = lines[1:]
    # Drop closing fence when present.
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    body = "\n".join(lines).strip()
    return body + "\n" if body else ""


def _opencode_binary() -> str:
    override = os.environ.get(OPENCODE_BIN_ENV_VAR)
    if override:
        return override
    found = shutil.which("opencode")
    if found:
        return found
    return "/usr/local/bin/opencode"


def _claude_binary() -> str:
    override = os.environ.get(CLAUDE_BIN_ENV_VAR)
    if override:
        return override
    found = shutil.which("claude")
    if found:
        return found
    return "/usr/local/bin/claude"


def _timeout_s() -> int:
    try:
        return int(os.environ.get(TIMEOUT_ENV_VAR, "300"))
    except ValueError:
        return 300


def _build_prompt(*, current_content: bytes, request: str, path: Path) -> str:
    try:
        current_text = current_content.decode("utf-8")
    except UnicodeDecodeError:
        current_text = current_content.decode("utf-8", errors="replace")
    return (
        "You are editing a user's dotfile. Apply the request below to the "
        "current file content and output ONLY the complete new file content.\n"
        "Constraints (must follow exactly):\n"
        "- Return only the complete new file content: no markdown fences, "
        "no commentary, no explanation, no surrounding quotes.\n"
        "- Preserve everything unrelated to the request byte-for-byte.\n"
        f"Target file: {path}\n"
        f"Request: {request}\n"
        "Current file content follows between the markers (markers are not "
        "part of the file):\n"
        "----- BEGIN CURRENT CONTENT -----\n"
        f"{current_text}"
        + ("" if current_text.endswith("\n") or not current_text else "\n")
        + "----- END CURRENT CONTENT -----\n"
        "Now output only the complete new file content."
    )


def _extract_opencode_text(stdout: str) -> str:
    """Pull assistant text out of ``opencode run --format json`` output.

    Each line is normally a JSON event carrying the text in a ``text`` /
    ``content`` / ``part`` payload; fall back to the raw stream when it is
    not JSON at all.
    """
    texts: list[str] = []
    for line in stdout.splitlines():
        s = line.strip()
        if not s:
            continue
        try:
            event = json.loads(s)
        except json.JSONDecodeError:
            texts.append(line)
            continue
        if not isinstance(event, dict):
            continue
        for key in ("text", "content"):
            payload = event.get(key)
            if isinstance(payload, str) and payload:
                texts.append(payload)
        part = event.get("part") or event.get("message")
        if isinstance(part, dict):
            payload = part.get("text") or part.get("content")
            if isinstance(payload, str) and payload:
                texts.append(payload)
    if texts:
        return "\n".join(texts)
    return stdout


def _extract_claude_text(stdout: str) -> str:
    """Pull assistant text out of ``claude -p --output-format json`` output."""
    texts: list[str] = []
    last_plain = ""
    for line in stdout.splitlines():
        s = line.strip()
        if not s:
            continue
        try:
            value = json.loads(s)
        except json.JSONDecodeError:
            last_plain = line
            continue
        if not isinstance(value, dict):
            continue
        payload = value.get("result") or value.get("text") or value.get("content")
        if isinstance(payload, str) and payload:
            texts.append(payload)
    if texts:
        return "\n".join(texts)
    if last_plain:
        return last_plain
    return stdout


def _propose_via_opencode(prompt: str) -> bytes:
    model = os.environ.get(OPENCODE_MODEL_ENV_VAR) or OPENCODE_DEFAULT_MODEL
    argv = [_opencode_binary(), "run", "--format", "json", "-m", model, prompt]
    completed = subprocess.run(argv, text=True, capture_output=True, check=False, timeout=_timeout_s())
    if completed.returncode:
        detail = ((completed.stderr or "") + (completed.stdout or "")).strip()[-600:]
        raise NLProposeError(f"opencode proposal failed (exit {completed.returncode}): {detail}")
    text = _strip_fences(_extract_opencode_text(completed.stdout or ""))
    if not text:
        raise NLProposeError("opencode proposal returned empty content")
    return text.encode("utf-8")


def _propose_via_claude(prompt: str) -> bytes:
    argv = [_claude_binary(), "-p", "--output-format", "json", "--permission-mode", "bypassPermissions", prompt]
    completed = subprocess.run(argv, text=True, capture_output=True, check=False, timeout=_timeout_s())
    if completed.returncode:
        detail = ((completed.stderr or "") + (completed.stdout or "")).strip()[-600:]
        raise NLProposeError(f"claude proposal failed (exit {completed.returncode}): {detail}")
    text = _strip_fences(_extract_claude_text(completed.stdout or ""))
    if not text:
        raise NLProposeError("claude proposal returned empty content")
    return text.encode("utf-8")


def propose_new_content(current_content: bytes, request: str, *, path: Path) -> bytes:
    """Propose complete new file content for `request` via a real model call.

    Preference order mirrors the ``implementation`` role in
    ``config/model-availability.json``: ``opencode`` first, ``claude`` as
    the fallback — never a single hardcoded provider with no fallback.
    The prompt requires the model to return only the complete new file
    content (no markdown fences, no commentary); the response is
    fence-stripped and validated before it is treated as file content.

    Swappable: ``handle_request`` takes this (or any fake) as its
    ``propose`` parameter, so automated tests never make a live call.
    """
    if not isinstance(current_content, (bytes, bytearray)):
        raise TypeError("current_content must be bytes")
    prompt = _build_prompt(current_content=bytes(current_content), request=request, path=Path(path))
    try:
        return _propose_via_opencode(prompt)
    except (NLProposeError, OSError):
        pass
    # Fallback per the implementation role's prefer order (opencode, claude).
    return _propose_via_claude(prompt)


def handle_request(
    request: str,
    target_path: Path,
    *,
    home: Path,
    allowlist_root: Path,
    propose: Callable[..., bytes] = propose_new_content,
    snapshot_root: Path | None = None,
) -> str:
    """Apply free-text `request` to `target_path`; return the snapshot id.

    Steps, in order: (1) ``is_allowed_target`` gate — refuse (raise,
    touch nothing) on failure; (2) read current content — the target must
    already exist as a regular file (new-file creation is NOT supported;
    ``config_agent.backup_paths`` requires the file to exist, and this
    layer keeps that requirement rather than silently limiting scope);
    (3) ``propose(current_content, request, path=...)`` — injectable, so
    tests use a deterministic fake; (4) apply via the unmodified
    ``config_agent.apply_change`` (backup-before-write, post-write
    verification, automatic rollback on mismatch) and return its snapshot
    id. On any refusal or failure nothing is written — validate everything
    before touching anything.
    """
    target = Path(target_path)
    if not is_allowed_target(target, Path(home)):
        raise config_agent.AllowlistRefusal(f"refused: {target} is not an allowed dotfile target under {home}")
    resolved = target.resolve()
    if not resolved.is_file():
        raise FileNotFoundError(
            f"nothing to change (not an existing regular file; new-file creation is not supported): {resolved}"
        )
    current = resolved.read_bytes()
    new_content = propose(current, request, path=target)
    if not isinstance(new_content, (bytes, bytearray)):
        raise TypeError("propose must return bytes")
    kwargs: dict = {}
    if snapshot_root is not None:
        kwargs["snapshot_root"] = snapshot_root
    return config_agent.apply_change(target, bytes(new_content), allowlist_root=allowlist_root, **kwargs)
