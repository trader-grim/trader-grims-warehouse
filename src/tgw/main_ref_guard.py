#!/usr/bin/python3
"""Canonical ``main`` ref guard for the shared local development repository.

Todo 1942 / ``plan/reconciliation/DRIFT-PREVENTION-1942-MAIN-GUARD-20260901.md``.

On 2026-08-31 a coding agent advanced ``refs/heads/main`` of the canonical
repository ``/opt/TGW/tgw-lib/src/trader-grims-warehouse`` with a raw local
``git merge`` (no review, no release path).  The raw merge desynchronized the
task-cursor commit, the runtime selector (release installer), and the Context
MCP snapshot, and recovery required a ``git reset --hard`` plus a receipt-driven
runtime rollback.

The operator's standing directive is that ordinary ``tgw-coders`` agents cannot
advance ``main`` by raw Git: the canonical HEAD and the task-cursor commit must
advance together through the sanctioned source publisher -- the ``db``-owned
coding-lifecycle integration path (``tgw.development.local_workflow`` foreman,
``coding-git:fast-forward/v1``) that fast-forwards ``main`` and then installs
the matching runtime release and cursor.

This module is a Git ``reference-transaction`` hook plus its verifier and
installer.  The hook rejects any transaction that updates ``refs/heads/main``
unless the caller is the sanctioned publisher identity or an explicit, durably
recorded override is present.  Every other ref (worktree branches, tags, notes,
stash, remote-tracking refs, ``HEAD``, ``ORIG_HEAD``) is left untouched.  The
guard is a single local hook file: removing it restores the previous behaviour
exactly.

It is deliberately independent of the coding lifecycle, tgw-prod, and provider
effects.  ``src/tgw/protected_git.py`` (deterministic read-only Git for service
accounts) stays orthogonal -- that is a read guard, this is a ref guard.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pwd
import re
import stat
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable, Mapping, Sequence

SENTINEL = "TGW-MAIN-REF-GUARD"
GUARD_VERSION = "v1"
HOOK_NAME = "reference-transaction"
GUARD_STATE_DIRNAME = "tgw-main-ref-guard"
GUARD_CONFIG_NAME = "guard.json"
OVERRIDE_LOG_NAME = "override-events.log"
GUARD_CONFIG_SCHEMA = "tgw-main-ref-guard-config/v1"
OVERRIDE_EVENT_SCHEMA = "tgw-main-ref-guard-override-event/v1"

#: The refs an ordinary agent must never advance by raw Git.
PROTECTED_REFS: tuple[str, ...] = ("refs/heads/main",)

#: Unix account names that are the sanctioned source publisher.  ``db`` runs the
#: coding-lifecycle foreman that fast-forwards ``main``; ``root`` is retained for
#: receipt-driven recovery/bootstrap (``tgw-coding-bootstrap`` is root-owned).
DEFAULT_PUBLISHER_IDENTITIES: tuple[str, ...] = ("db",)

#: Non-empty value = an explicit emergency override justification.  Its use is
#: always recorded in the durable override event log; it is never the default.
OVERRIDE_ENV = "TGW_MAIN_REF_GUARD_OVERRIDE"

_ZERO_OID = re.compile(r"\A0{40}(?:0{24})?\Z")
_OID = re.compile(r"\A[0-9a-f]{40}(?:[0-9a-f]{24})?\Z")
_TRANSACTION_STATES = ("prepared", "committed", "aborted")


class MainRefGuardError(RuntimeError):
    """The guard cannot be installed, verified, or evaluated safely."""


@dataclass(frozen=True)
class RefUpdate:
    """One ``<old> <new> <ref>`` line from a reference-transaction hook."""

    old: str
    new: str
    name: str

    @property
    def creates(self) -> bool:
        return bool(_ZERO_OID.match(self.old)) and not _ZERO_OID.match(self.new)

    @property
    def deletes(self) -> bool:
        return bool(_ZERO_OID.match(self.new)) and not _ZERO_OID.match(self.old)

    @property
    def changes(self) -> bool:
        return self.old != self.new


@dataclass(frozen=True)
class GuardDecision:
    """The verdict for one transaction."""

    action: str  # "allow" | "reject"
    reason: str
    protected_updates: tuple[RefUpdate, ...] = ()
    override: Mapping[str, object] | None = None

    @property
    def allowed(self) -> bool:
        return self.action == "allow"


# --------------------------------------------------------------------------- #
# Pure evaluation
# --------------------------------------------------------------------------- #
def parse_transaction_input(text: str) -> list[RefUpdate]:
    """Parse the newline-delimited ``<old> <new> <ref>`` records on hook stdin."""

    updates: list[RefUpdate] = []
    for raw in text.splitlines():
        line = raw.rstrip("\n")
        if not line:
            continue
        parts = line.split(" ", 2)
        if len(parts) != 3:
            raise MainRefGuardError(f"malformed reference-transaction line: {raw!r}")
        old, new, name = parts
        if not _OID.match(old) or not _OID.match(new):
            raise MainRefGuardError(f"malformed object id in hook line: {raw!r}")
        updates.append(RefUpdate(old=old, new=new, name=name))
    return updates


def protected_updates(updates: Iterable[RefUpdate]) -> list[RefUpdate]:
    """Return the updates that actually advance/rewrite/delete a protected ref."""

    return [u for u in updates if u.name in PROTECTED_REFS and u.changes]


def caller_identity(uid: int | None = None) -> tuple[int, str]:
    """Return ``(uid, name)`` for the effective caller of the hook."""

    resolved = os.getuid() if uid is None else uid
    try:
        name = pwd.getpwuid(resolved).pw_name
    except KeyError:
        name = f"uid:{resolved}"
    return resolved, name


def _is_publisher(uid: int, name: str, publisher_identities: Sequence[str]) -> bool:
    # root is always allowed: receipt-driven recovery/bootstrap is root-owned and
    # is itself the sanctioned atomic HEAD+cursor path of last resort.
    if uid == 0:
        return True
    return name in tuple(publisher_identities)


def evaluate(
    state: str,
    updates: Sequence[RefUpdate],
    *,
    uid: int,
    caller_name: str,
    publisher_identities: Sequence[str] = DEFAULT_PUBLISHER_IDENTITIES,
    override_value: str | None = None,
) -> GuardDecision:
    """Decide whether a reference transaction touching ``main`` may proceed.

    Only the ``prepared`` state can be vetoed; ``committed``/``aborted`` are
    after-the-fact notifications and are always allowed.
    """

    touched = tuple(protected_updates(updates))
    if not touched:
        return GuardDecision("allow", "transaction does not touch a protected ref")
    if state != "prepared":
        return GuardDecision(
            "allow",
            f"reference-transaction state {state!r} is not vetoable",
            protected_updates=touched,
        )
    if _is_publisher(uid, caller_name, publisher_identities):
        return GuardDecision(
            "allow",
            f"caller {caller_name!r} is the sanctioned source publisher",
            protected_updates=touched,
        )
    justification = (override_value or "").strip()
    if justification:
        override = {
            "schema": OVERRIDE_EVENT_SCHEMA,
            "justification": justification,
            "caller_uid": uid,
            "caller_name": caller_name,
            "protected_updates": [
                {"old": u.old, "new": u.new, "ref": u.name} for u in touched
            ],
        }
        return GuardDecision(
            "allow",
            "explicit recorded override",
            protected_updates=touched,
            override=override,
        )
    return GuardDecision(
        "reject",
        (
            f"caller {caller_name!r} is not the sanctioned source publisher; "
            f"{', '.join(u.name for u in touched)} may only be advanced through "
            "the coding-lifecycle integration path"
        ),
        protected_updates=touched,
    )


# --------------------------------------------------------------------------- #
# Repository layout helpers
# --------------------------------------------------------------------------- #
def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-c", f"safe.directory={Path(repo).resolve()}", "-C", str(repo), *args],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if completed.returncode:
        raise MainRefGuardError(
            completed.stderr.strip() or f"git {' '.join(args)} failed in {repo}"
        )
    return completed.stdout.strip()


def common_git_dir(repo: Path) -> Path:
    raw = Path(_git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir"))
    return raw if raw.is_absolute() else (repo / raw).resolve()


def hooks_dir(repo: Path) -> Path:
    raw = Path(_git(repo, "rev-parse", "--path-format=absolute", "--git-path", "hooks"))
    return raw if raw.is_absolute() else (repo / raw).resolve()


def guard_state_dir(repo: Path) -> Path:
    return common_git_dir(repo) / GUARD_STATE_DIRNAME


def guard_common_git_dir(hook_file: str | Path) -> Path:
    """Resolve the common git dir from an installed hook's own path.

    The hook lives at ``<hooks-dir>/reference-transaction``; the hooks dir is a
    child of the common git dir unless ``core.hooksPath`` relocates it, in which
    case the installer records the common dir in ``guard.json`` beside the hook.
    """

    return Path(hook_file).resolve().parent.parent


# --------------------------------------------------------------------------- #
# Hook script rendering
# --------------------------------------------------------------------------- #
def render_hook_script(*, source_path: str, python_executable: str = "/usr/bin/python3") -> str:
    """Render the self-contained ``reference-transaction`` hook.

    The hook does its own stdlib-only pre-filter and imports :mod:`tgw` only when
    ``refs/heads/main`` is actually being changed.  If the import then fails it
    exits non-zero -- fail closed, protecting ``main``.
    """

    protected = ", ".join(repr(ref) for ref in PROTECTED_REFS)
    return f"""#!{python_executable}
# {SENTINEL} {GUARD_VERSION} -- managed by tgw.main_ref_guard; DO NOT EDIT.
# Rejects non-publisher updates to refs/heads/main (Todo 1942).
# Remove this file to disable the guard (fully reversible).
import os
import sys

_PROTECTED = ({protected},)


def _touches_protected(text):
    for line in text.splitlines():
        parts = line.split(" ", 2)
        if len(parts) == 3 and parts[2] in _PROTECTED and parts[0] != parts[1]:
            return True
    return False


def _main():
    data = sys.stdin.read()
    if not _touches_protected(data):
        return 0
    sys.path.insert(0, {source_path!r})
    try:
        from tgw.main_ref_guard import guard_common_git_dir, run_hook
    except Exception as exc:  # fail closed: protect main if the guard cannot load
        sys.stderr.write(
            "tgw main-ref guard: refusing a refs/heads/main update because the "
            "guard module could not be loaded (%s). Use the sanctioned publisher "
            "path or remove the hook to disable the guard.\\n" % exc
        )
        return 1
    return run_hook(
        sys.argv,
        data,
        environ=os.environ,
        common_git_dir=guard_common_git_dir(__file__),
    )


if __name__ == "__main__":
    sys.exit(_main())
"""


def _sha256_text(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


# --------------------------------------------------------------------------- #
# Override event log
# --------------------------------------------------------------------------- #
def record_override_event(
    common_dir: Path,
    override: Mapping[str, object],
    *,
    now: datetime | None = None,
) -> Path:
    """Append one durable JSON line for an override use and return the log path."""

    state_dir = common_dir / GUARD_STATE_DIRNAME
    state_dir.mkdir(parents=True, exist_ok=True)
    log_path = state_dir / OVERRIDE_LOG_NAME
    event = dict(override)
    event.setdefault("schema", OVERRIDE_EVENT_SCHEMA)
    event["recorded_at"] = (now or datetime.now(UTC)).isoformat()
    with log_path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(event, sort_keys=True) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    return log_path


def override_event_count(common_dir: Path) -> int:
    log_path = common_dir / GUARD_STATE_DIRNAME / OVERRIDE_LOG_NAME
    if not log_path.is_file():
        return 0
    with log_path.open("r", encoding="utf-8") as stream:
        return sum(1 for line in stream if line.strip())


# --------------------------------------------------------------------------- #
# Hook entry point
# --------------------------------------------------------------------------- #
def _load_publisher_identities(common_dir: Path) -> list[str]:
    config = common_dir / GUARD_STATE_DIRNAME / GUARD_CONFIG_NAME
    if not config.is_file():
        return list(DEFAULT_PUBLISHER_IDENTITIES)
    try:
        value = json.loads(config.read_text(encoding="utf-8"))
        identities = value["publisher_identities"]
        if not isinstance(identities, list) or not all(
            isinstance(item, str) and item for item in identities
        ):
            raise ValueError
    except (OSError, ValueError, KeyError, json.JSONDecodeError):
        return list(DEFAULT_PUBLISHER_IDENTITIES)
    return identities


def run_hook(
    argv: Sequence[str],
    stdin_text: str,
    *,
    environ: Mapping[str, str],
    common_git_dir: Path,
    now: datetime | None = None,
) -> int:
    """Evaluate one reference-transaction invocation; return the hook exit code."""

    state = argv[1] if len(argv) > 1 else ""
    try:
        updates = parse_transaction_input(stdin_text)
    except MainRefGuardError as exc:
        # A record we cannot parse must not silently pass a protected update.
        sys.stderr.write(f"tgw main-ref guard: {exc}\n")
        return 1 if state == "prepared" else 0

    if not protected_updates(updates):
        return 0

    publisher_identities = _load_publisher_identities(Path(common_git_dir))
    uid, name = caller_identity()
    decision = evaluate(
        state,
        updates,
        uid=uid,
        caller_name=name,
        publisher_identities=publisher_identities,
        override_value=environ.get(OVERRIDE_ENV),
    )

    if not decision.allowed:
        refs = ", ".join(u.name for u in decision.protected_updates)
        sys.stderr.write(
            "tgw main-ref guard: refused.\n"
            f"  {decision.reason}\n"
            f"  refs: {refs}\n"
            "  The canonical HEAD and the task cursor advance together only\n"
            "  through the sanctioned source publisher (the db-owned coding\n"
            "  lifecycle integration path). To land work, take it through\n"
            "  `tgw coding` review + integration.\n"
            f"  Emergency override: set {OVERRIDE_ENV}='<reason>' (the use is\n"
            "  recorded durably); or remove the hook to disable the guard.\n"
        )
        return 1

    if decision.override is not None:
        log_path = record_override_event(
            Path(common_git_dir), decision.override, now=now
        )
        refs = ", ".join(u.name for u in decision.protected_updates)
        sys.stderr.write(
            "tgw main-ref guard: EXPLICIT OVERRIDE in use.\n"
            f"  justification: {decision.override['justification']}\n"
            f"  refs: {refs}\n"
            f"  recorded: {log_path}\n"
        )
    return 0


# --------------------------------------------------------------------------- #
# Install / uninstall / status
# --------------------------------------------------------------------------- #
def _is_managed_hook(text: str) -> bool:
    return f"{SENTINEL} {GUARD_VERSION}" in text or SENTINEL in text


def install_guard(
    repo: Path,
    *,
    publisher_identities: Sequence[str] = DEFAULT_PUBLISHER_IDENTITIES,
    source_path: str | Path | None = None,
    python_executable: str = "/usr/bin/python3",
    force: bool = False,
    now: datetime | None = None,
) -> dict[str, object]:
    """Install the reference-transaction guard hook on ``repo``.

    Reversible: :func:`uninstall_guard` (or ``rm`` of the hook file) fully
    restores the previous behaviour.
    """

    repo = Path(repo).resolve()
    resolved_source = Path(source_path).resolve() if source_path else (repo / "src")
    if not (resolved_source / "tgw" / "main_ref_guard.py").is_file():
        raise MainRefGuardError(
            f"tgw.main_ref_guard is not importable from {resolved_source}"
        )

    hooks = hooks_dir(repo)
    common_dir = common_git_dir(repo)
    hooks.mkdir(parents=True, exist_ok=True)
    hook_path = hooks / HOOK_NAME

    if hook_path.exists() and not force:
        existing = hook_path.read_text(encoding="utf-8", errors="replace")
        if not _is_managed_hook(existing):
            raise MainRefGuardError(
                f"a foreign {HOOK_NAME} hook already exists at {hook_path}; "
                "refusing to overwrite without force=True"
            )

    script = render_hook_script(
        source_path=str(resolved_source), python_executable=python_executable
    )
    tmp_path = hook_path.with_name(f".{HOOK_NAME}.tgw-tmp")
    tmp_path.write_text(script, encoding="utf-8")
    tmp_path.chmod(0o755)
    os.replace(tmp_path, hook_path)

    state_dir = common_dir / GUARD_STATE_DIRNAME
    state_dir.mkdir(parents=True, exist_ok=True)
    config = {
        "schema": GUARD_CONFIG_SCHEMA,
        "version": GUARD_VERSION,
        "sentinel": SENTINEL,
        "protected_refs": list(PROTECTED_REFS),
        "publisher_identities": list(publisher_identities),
        "source_path": str(resolved_source),
        "python_executable": python_executable,
        "hooks_dir": str(hooks),
        "hook_path": str(hook_path),
        "hook_sha256": _sha256_text(script),
        "installed_at": (now or datetime.now(UTC)).isoformat(),
    }
    config_path = state_dir / GUARD_CONFIG_NAME
    config_tmp = config_path.with_name(f".{GUARD_CONFIG_NAME}.tgw-tmp")
    config_tmp.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(config_tmp, config_path)
    (state_dir / OVERRIDE_LOG_NAME).touch(exist_ok=True)

    return {"ok": True, "installed": True, "hook_path": str(hook_path), "config": config}


def uninstall_guard(repo: Path, *, force: bool = False) -> dict[str, object]:
    """Remove the guard hook (keeps the durable override log as a record)."""

    repo = Path(repo).resolve()
    hook_path = hooks_dir(repo) / HOOK_NAME
    removed = False
    if hook_path.exists():
        text = hook_path.read_text(encoding="utf-8", errors="replace")
        if not _is_managed_hook(text) and not force:
            raise MainRefGuardError(
                f"{hook_path} is not a tgw-managed guard hook; refusing to remove "
                "without force=True"
            )
        hook_path.unlink()
        removed = True

    config_path = guard_state_dir(repo) / GUARD_CONFIG_NAME
    if config_path.exists():
        config_path.unlink()

    return {"ok": True, "removed": removed, "hook_path": str(hook_path)}


def guard_status(repo: Path) -> dict[str, object]:
    """Read-only report of guard presence and integrity on ``repo``."""

    repo = Path(repo).resolve()
    hooks = hooks_dir(repo)
    common_dir = common_git_dir(repo)
    hook_path = hooks / HOOK_NAME
    state_dir = common_dir / GUARD_STATE_DIRNAME
    config_path = state_dir / GUARD_CONFIG_NAME

    status: dict[str, object] = {
        "repository": str(repo),
        "hooks_dir": str(hooks),
        "hook_path": str(hook_path),
        "hook_present": hook_path.is_file(),
        "managed": False,
        "executable": False,
        "config_present": config_path.is_file(),
        "hook_matches_config": False,
        "publisher_identities": list(DEFAULT_PUBLISHER_IDENTITIES),
        "protected_refs": list(PROTECTED_REFS),
        "override_event_count": override_event_count(common_dir),
        "override_log": str(state_dir / OVERRIDE_LOG_NAME),
        "active": False,
        "integrity": "absent",
    }

    if not status["hook_present"]:
        return status

    text = hook_path.read_text(encoding="utf-8", errors="replace")
    status["managed"] = _is_managed_hook(text)
    status["executable"] = bool(hook_path.stat().st_mode & stat.S_IXUSR)
    observed_sha = _sha256_file(hook_path)
    status["hook_sha256"] = observed_sha

    config: Mapping[str, object] | None = None
    if config_path.is_file():
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            config = None
    if config:
        status["publisher_identities"] = config.get(
            "publisher_identities", list(DEFAULT_PUBLISHER_IDENTITIES)
        )
        status["hook_matches_config"] = config.get("hook_sha256") == observed_sha

    if not status["managed"]:
        status["integrity"] = "foreign"
    elif not status["executable"]:
        status["integrity"] = "not-executable"
    elif config and not status["hook_matches_config"]:
        status["integrity"] = "modified"
    elif not config:
        status["integrity"] = "unverifiable"
    else:
        status["integrity"] = "ok"

    status["active"] = status["integrity"] == "ok"
    return status


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _emit(value: Mapping[str, object]) -> int:
    print(json.dumps(value, indent=2, sort_keys=True))
    return 0 if value.get("ok", True) else 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tgw.main_ref_guard", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_install = sub.add_parser("install", help="install the guard hook")
    p_install.add_argument("--repo", type=Path, required=True)
    p_install.add_argument("--source-path", type=Path, default=None)
    p_install.add_argument("--python-executable", default="/usr/bin/python3")
    p_install.add_argument(
        "--publisher",
        action="append",
        dest="publishers",
        default=None,
        help="publisher identity (repeatable); default: db",
    )
    p_install.add_argument("--force", action="store_true")

    p_uninstall = sub.add_parser("uninstall", help="remove the guard hook")
    p_uninstall.add_argument("--repo", type=Path, required=True)
    p_uninstall.add_argument("--force", action="store_true")

    p_status = sub.add_parser("status", help="report guard presence/integrity")
    p_status.add_argument("--repo", type=Path, required=True)

    p_hook = sub.add_parser(
        HOOK_NAME, help="reference-transaction hook entry (state arg + stdin)"
    )
    p_hook.add_argument("state", nargs="?", default="")

    args = parser.parse_args(argv)

    try:
        if args.command == "install":
            return _emit(
                install_guard(
                    args.repo,
                    publisher_identities=tuple(args.publishers)
                    if args.publishers
                    else DEFAULT_PUBLISHER_IDENTITIES,
                    source_path=args.source_path,
                    python_executable=args.python_executable,
                    force=args.force,
                )
            )
        if args.command == "uninstall":
            return _emit(uninstall_guard(args.repo, force=args.force))
        if args.command == "status":
            return _emit(guard_status(args.repo))
        if args.command == HOOK_NAME:
            git_dir = os.environ.get("GIT_DIR")
            common_dir = (
                common_git_dir(Path(git_dir)) if git_dir else common_git_dir(Path.cwd())
            )
            return run_hook(
                ["reference-transaction", args.state],
                sys.stdin.read(),
                environ=os.environ,
                common_git_dir=common_dir,
            )
    except MainRefGuardError as exc:
        return _emit({"ok": False, "error": str(exc)})
    parser.error("unknown command")
    return 2


if __name__ == "__main__":
    sys.exit(main())
