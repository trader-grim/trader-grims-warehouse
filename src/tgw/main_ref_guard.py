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
#: coding-lifecycle foreman that fast-forwards ``main``.  ``root`` is deliberately
#: *not* in this list: a root-performed raw advance is still allowed (for
#: receipt-driven recovery/bootstrap -- ``tgw-coding-bootstrap`` is root-owned),
#: but never as a silent publisher advance.  It goes through the ``implicit_root``
#: branch of :func:`evaluate`, which always writes a durable override record with
#: ``"implicit_root": true``.  See :func:`_is_publisher`.
DEFAULT_PUBLISHER_IDENTITIES: tuple[str, ...] = ("db",)

#: Explicit, umask-independent permissions for the guard's durable audit trail.
#: The canonical ``.git`` tree is setgid to group ``tgw-coders`` (the population
#: this guard targets): the state directory is group-writable *and* setgid so a
#: non-publisher emergency override can still append its event, while the
#: recorded high-water mark in ``guard.json`` (see :func:`_record_override_high_water`)
#: makes a later truncation or deletion of the log tamper-evident -- :func:`guard_status`
#: then reports ``modified`` (Doctor FAIL), the same escalation as removing the hook.
GUARD_STATE_DIR_MODE = 0o2775
GUARD_FILE_MODE = 0o664

#: Chain anchor for the first override-events.log line (see :func:`verify_override_log`).
_OVERRIDE_LOG_GENESIS = "tgw-main-ref-guard-override-log/v1-genesis"

#: Non-empty value = an explicit emergency override justification.  Its use is
#: always recorded in the durable override event log; it is never the default.
OVERRIDE_ENV = "TGW_MAIN_REF_GUARD_OVERRIDE"

_ZERO_OID = re.compile(r"\A0{40}(?:0{24})?\Z")
_OID = re.compile(r"\A[0-9a-f]{40}(?:[0-9a-f]{24})?\Z")
#: githooks(5): for a *symbolic* reference update the old/new field of a
#: reference-transaction line is a ref target with a ``ref:`` prefix
#: (e.g. ``ref:refs/heads/main``) rather than an object id.
_REF_TARGET = re.compile(r"\Aref:\S.*\Z")
_TRANSACTION_STATES = ("prepared", "committed", "aborted")


def _is_oid_or_symref(value: str) -> bool:
    """True for a well-formed object id *or* the documented ``ref:`` symref form."""

    return bool(_OID.match(value) or _REF_TARGET.match(value))


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
        if not _is_oid_or_symref(old) or not _is_oid_or_symref(new):
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


def _is_publisher(name: str, publisher_identities: Sequence[str]) -> bool:
    # Identity match only.  ``root`` is *not* an implicit publisher: a
    # root-performed raw advance is still recorded (see :func:`evaluate`), so the
    # receipt-driven recovery path leaves the same durable trail as any override.
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
    if _is_publisher(caller_name, publisher_identities):
        return GuardDecision(
            "allow",
            f"caller {caller_name!r} is the sanctioned source publisher",
            protected_updates=touched,
        )
    justification = (override_value or "").strip()
    implicit_root = uid == 0 and not justification
    if implicit_root:
        # root may still advance main (receipt-driven recovery/bootstrap), but it
        # is never silent: it produces the same durable override record an agent
        # override would, so Finding-5's "zero audit trail" case cannot happen.
        justification = (
            "uid 0 performed a raw refs/heads/main advancement "
            "(implicit recovery identity; no explicit override reason given)"
        )
    if justification:
        override = {
            "schema": OVERRIDE_EVENT_SCHEMA,
            "justification": justification,
            "caller_uid": uid,
            "caller_name": caller_name,
            "implicit_root": implicit_root,
            "protected_updates": [
                {"old": u.old, "new": u.new, "ref": u.name} for u in touched
            ],
        }
        return GuardDecision(
            "allow",
            "root advancement (recorded)" if implicit_root else "explicit recorded override",
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
    """Best-effort common-git-dir guess from an installed hook's own path.

    Only a fallback: the rendered hook carries the absolute common git dir that
    :func:`install_guard` computed, so it never has to infer it.  This helper is
    retained for the manual ``python3 -m tgw.main_ref_guard reference-transaction``
    entry point, where it assumes the default layout (hooks dir is a direct child
    of the common git dir).  ``core.hooksPath`` relocation is handled by the
    embedded path, not here.
    """

    return Path(hook_file).resolve().parent.parent


# --------------------------------------------------------------------------- #
# Hook script rendering
# --------------------------------------------------------------------------- #
def render_hook_script(
    *,
    source_path: str,
    common_git_dir: str | Path,
    publisher_identities: Sequence[str] = DEFAULT_PUBLISHER_IDENTITIES,
    python_executable: str = "/usr/bin/python3",
) -> str:
    """Render the self-contained ``reference-transaction`` hook.

    The hook does its own stdlib-only pre-filter and imports :mod:`tgw` only when
    ``refs/heads/main`` is actually being changed.  If the import then fails it
    exits non-zero -- fail closed, protecting ``main``.

    ``common_git_dir`` is the absolute common git dir computed by
    :func:`install_guard`; it is embedded literally so the hook resolves the
    durable override log correctly even when ``core.hooksPath`` relocates the
    hooks directory outside the git dir.

    ``publisher_identities`` is embedded literally into the hook body -- the
    authorization list travels *inside* the tamper-anchored artifact, never in
    the group-writable state directory.  :func:`guard_status` re-renders the
    expected body from trusted inputs only (the installed package's own source
    path, the freshly resolved common git dir, and the package constant
    :data:`DEFAULT_PUBLISHER_IDENTITIES`) and compares byte-for-byte, so neither
    a body edit nor a rewrite of the embedded allow-list can read as ``ok``.
    """

    protected = ", ".join(repr(ref) for ref in PROTECTED_REFS)
    common = str(Path(common_git_dir))
    publishers = ", ".join(repr(name) for name in publisher_identities)
    return f"""#!{python_executable}
# {SENTINEL} {GUARD_VERSION} -- managed by tgw.main_ref_guard; DO NOT EDIT.
# Rejects non-publisher updates to refs/heads/main (Todo 1942).
# Remove this file to disable the guard (fully reversible); once the guard has
# been installed, removal is tamper-evident -- `tgw doctor` turns FAIL.
import os
import sys

_PROTECTED = ({protected},)
_COMMON_GIT_DIR = {common!r}
_PUBLISHER_IDENTITIES = ({publishers},)


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
        from tgw.main_ref_guard import run_hook
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
        common_git_dir=_COMMON_GIT_DIR,
        publisher_identities=_PUBLISHER_IDENTITIES,
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


#: The ``src`` directory of the *installed* ``tgw`` package running this code.
#: The hook must load ``tgw`` from here; :func:`guard_status` re-renders the
#: expected body against this path, never against a path read out of the
#: (group-writable) installed hook.
PACKAGE_SOURCE_PATH = Path(__file__).resolve().parents[1]


def _expected_hook_body(
    repo: Path, *, python_executable: str = "/usr/bin/python3"
) -> str:
    """Render the only hook body this package would accept as genuine on ``repo``.

    Every input is trusted: the installed package's own source directory, the
    common git dir resolved fresh from ``git`` at status time, the package
    constant :data:`DEFAULT_PUBLISHER_IDENTITIES`, and the standard interpreter.
    A hook that does not match this byte-for-byte -- a redirected ``source_path``,
    a rewritten embedded allow-list, any body edit -- cannot read as ``ok``.
    """

    return render_hook_script(
        source_path=str(PACKAGE_SOURCE_PATH),
        common_git_dir=common_git_dir(repo),
        publisher_identities=DEFAULT_PUBLISHER_IDENTITIES,
        python_executable=python_executable,
    )


# --------------------------------------------------------------------------- #
# Override event log
# --------------------------------------------------------------------------- #
def _relax_mode(path: Path, mode: int) -> None:
    """Best-effort ``chmod`` so the audit trail's posture does not depend on the
    ambient umask.  Silently ignored when we do not own ``path`` -- e.g. a
    non-publisher appending to a log that the publisher already created with the
    right mode."""

    try:
        os.chmod(path, mode)
    except OSError:
        pass


def _ensure_state_dir(common_dir: Path) -> Path:
    """Create ``<common-dir>/tgw-main-ref-guard/`` with an explicit, pinned mode."""

    state_dir = Path(common_dir) / GUARD_STATE_DIRNAME
    state_dir.mkdir(parents=True, exist_ok=True)
    _relax_mode(state_dir, GUARD_STATE_DIR_MODE)
    return state_dir


def _log_lines(log_path: Path) -> list[str]:
    if not log_path.is_file():
        return []
    return [line.strip() for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _record_override_high_water(state_dir: Path, count: int, log_sha256: str) -> None:
    """Best-effort: record the log's length/hash in ``guard.json``.

    ``guard.json`` is group-writable, so this is not an authenticated anchor --
    but it turns the *low-effort* evasion (``: > override-events.log``) into a
    tamper-evident one: :func:`guard_status` compares this high-water mark against
    the log actually present and reports ``modified`` (Doctor FAIL) if the log has
    shrunk.  A determined agent must now also rewrite ``guard.json`` in the same
    step, which is no longer strictly easier or less detectable than removing the
    hook outright."""

    config_path = state_dir / GUARD_CONFIG_NAME
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if not isinstance(config, dict):
        return
    config["override_event_count"] = count
    config["override_log_sha256"] = log_sha256
    tmp = config_path.with_name(f".{GUARD_CONFIG_NAME}.tgw-tmp")
    try:
        tmp.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        _relax_mode(tmp, GUARD_FILE_MODE)
        os.replace(tmp, config_path)
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass


def record_override_event(
    common_dir: Path,
    override: Mapping[str, object],
    *,
    now: datetime | None = None,
) -> Path:
    """Append one durable JSON line for an override use and return the log path.

    Each line carries a 1-based ``seq`` and ``prev_sha256`` (the SHA-256 of the
    preceding line, or :data:`_OVERRIDE_LOG_GENESIS` for the first) so the log is
    a hash chain -- an in-place rewrite of any earlier line is detectable by
    :func:`verify_override_log`.

    Raises :class:`MainRefGuardError` if the event cannot be written durably: the
    guard never allows an *unrecorded* ``refs/heads/main`` advance, so
    :func:`run_hook` turns this into a clean refusal rather than letting an
    unhandled ``OSError`` abort the transaction with a traceback."""

    state_dir = _ensure_state_dir(Path(common_dir))
    log_path = state_dir / OVERRIDE_LOG_NAME
    try:
        existing = _log_lines(log_path)
        if not log_path.exists():
            log_path.touch()
            _relax_mode(log_path, GUARD_FILE_MODE)
    except OSError as exc:
        raise MainRefGuardError(
            f"cannot prepare override log {log_path}: {exc}"
        ) from exc

    event = dict(override)
    event.setdefault("schema", OVERRIDE_EVENT_SCHEMA)
    event["recorded_at"] = (now or datetime.now(UTC)).isoformat()
    event["seq"] = len(existing) + 1
    event["prev_sha256"] = _sha256_text(existing[-1]) if existing else _OVERRIDE_LOG_GENESIS
    serialized = json.dumps(event, sort_keys=True)
    try:
        with log_path.open("a", encoding="utf-8") as stream:
            stream.write(serialized + "\n")
            stream.flush()
            os.fsync(stream.fileno())
    except OSError as exc:
        raise MainRefGuardError(
            f"cannot append to override log {log_path}: {exc}"
        ) from exc

    _record_override_high_water(state_dir, event["seq"], _sha256_file(log_path))
    return log_path


def override_event_count(common_dir: Path) -> int:
    log_path = Path(common_dir) / GUARD_STATE_DIRNAME / OVERRIDE_LOG_NAME
    if not log_path.is_file():
        return 0
    with log_path.open("r", encoding="utf-8") as stream:
        return sum(1 for line in stream if line.strip())


def verify_override_log(common_dir: Path) -> tuple[str, int]:
    """Return ``(state, count)`` for the durable override log.

    ``state`` is ``"ok"`` when every line is JSON with a contiguous 1-based
    ``seq`` and a ``prev_sha256`` that chains to the previous line, ``"broken"``
    when the chain does not hold (an in-place edit or a mid-file deletion), and
    ``"unreadable"`` when the file cannot be read at all."""

    log_path = Path(common_dir) / GUARD_STATE_DIRNAME / OVERRIDE_LOG_NAME
    if not log_path.is_file():
        return ("ok", 0)
    try:
        raw_lines = log_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ("unreadable", 0)
    prev = _OVERRIDE_LOG_GENESIS
    count = 0
    for raw in raw_lines:
        line = raw.strip()
        if not line:
            continue
        count += 1
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            return ("broken", count)
        if (
            not isinstance(event, dict)
            or event.get("seq") != count
            or event.get("prev_sha256") != prev
        ):
            return ("broken", count)
        prev = _sha256_text(line)
    return ("ok", count)


# --------------------------------------------------------------------------- #
# Hook entry point
# --------------------------------------------------------------------------- #
def run_hook(
    argv: Sequence[str],
    stdin_text: str,
    *,
    environ: Mapping[str, str],
    common_git_dir: Path,
    publisher_identities: Sequence[str] = DEFAULT_PUBLISHER_IDENTITIES,
    now: datetime | None = None,
) -> int:
    """Evaluate one reference-transaction invocation; return the hook exit code.

    ``publisher_identities`` is supplied by the caller -- the installed hook
    passes its own embedded ``_PUBLISHER_IDENTITIES`` literal.  The list is
    never read from the group-writable state directory, so it cannot be widened
    by the population the guard targets without editing the tamper-anchored hook
    body (which :func:`guard_status` then flags as ``modified``).
    """

    state = argv[1] if len(argv) > 1 else ""
    try:
        updates = parse_transaction_input(stdin_text)
    except MainRefGuardError as exc:
        # A record we cannot parse must not silently pass a protected update.
        sys.stderr.write(f"tgw main-ref guard: {exc}\n")
        return 1 if state == "prepared" else 0

    if not protected_updates(updates):
        return 0

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
        try:
            log_path = record_override_event(
                Path(common_git_dir), decision.override, now=now
            )
        except MainRefGuardError as exc:
            # Fail closed on the audit guarantee: acceptance condition 3 requires
            # every override to leave a durable record, so an override that
            # cannot be recorded is refused -- but cleanly, not as a traceback.
            sys.stderr.write(
                "tgw main-ref guard: refused.\n"
                f"  the emergency override was requested but could not be "
                f"recorded durably ({exc});\n"
                "  the guard does not permit an unrecorded refs/heads/main "
                "advance.\n"
                "  fix write access to the guard state directory under\n"
                f"  {common_git_dir}, or land the change through the sanctioned\n"
                "  `tgw coding` publisher path.\n"
            )
            return 1
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

    ``publisher_identities`` is not exposed on the CLI: the canonical install
    always embeds :data:`DEFAULT_PUBLISHER_IDENTITIES`, and :func:`guard_status`
    only rates a hook ``ok`` when the embedded list is exactly that constant.
    The argument exists for tests that need to exercise the git-level abort
    against a synthetic identity list.
    """

    repo = Path(repo).resolve()
    resolved_source = (
        Path(source_path).resolve() if source_path else PACKAGE_SOURCE_PATH
    )
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
        source_path=str(resolved_source),
        common_git_dir=common_dir,
        publisher_identities=publisher_identities,
        python_executable=python_executable,
    )
    tmp_path = hook_path.with_name(f".{HOOK_NAME}.tgw-tmp")
    tmp_path.write_text(script, encoding="utf-8")
    tmp_path.chmod(0o755)
    os.replace(tmp_path, hook_path)

    state_dir = _ensure_state_dir(common_dir)
    override_log = state_dir / OVERRIDE_LOG_NAME
    override_log.touch(exist_ok=True)
    _relax_mode(override_log, GUARD_FILE_MODE)
    # Carry the existing durable log forward as the tamper-evidence high-water
    # mark, so reinstalling the guard on a repo that already has recorded
    # overrides does not silently reset the baseline a later truncation is
    # checked against.
    _, existing_overrides = verify_override_log(common_dir)
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
        "override_event_count": existing_overrides,
        "override_log_sha256": _sha256_file(override_log),
    }
    config_path = state_dir / GUARD_CONFIG_NAME
    config_tmp = config_path.with_name(f".{GUARD_CONFIG_NAME}.tgw-tmp")
    config_tmp.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    _relax_mode(config_tmp, GUARD_FILE_MODE)
    os.replace(config_tmp, config_path)

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
    """Read-only report of guard presence and integrity on ``repo``.

    Integrity values:

    * ``ok`` -- hook present, tgw-managed, executable, byte-for-byte the body
      :func:`_expected_hook_body` renders from *trusted* inputs (the installed
      package source path, the freshly resolved common git dir, and the package
      constant :data:`DEFAULT_PUBLISHER_IDENTITIES`), and its recorded
      ``guard.json`` hash agrees;
    * ``config-missing`` -- hook body verified against the package but
      ``guard.json`` is gone;
    * ``removed`` -- no hook, but the guard state directory (created by
      :func:`install_guard` and kept across :func:`uninstall_guard`) is still
      there: the guard was installed and then removed out of band;
    * ``absent`` -- no hook and no state directory: the guard was never
      installed on this repo;
    * ``modified`` / ``foreign`` / ``not-executable`` -- a hook occupies the
      slot but is not the guard this package would install.  ``modified`` also
      covers a durable override log that has been truncated, deleted, or rewritten
      out of hash chain below the high-water mark recorded in ``guard.json``
      (``override_log_tampered``).

    The expected body is re-derived only from trusted inputs, never from values
    read out of the (group-writable) installed hook or ``guard.json``: a
    redirected ``source_path``, a rewritten embedded publisher allow-list, or any
    body edit therefore all read as ``modified`` -- even if ``guard.json`` is
    deleted or rewritten in the same step to try to hide it.
    """

    repo = Path(repo).resolve()
    hooks = hooks_dir(repo)
    common_dir = common_git_dir(repo)
    hook_path = hooks / HOOK_NAME
    state_dir = common_dir / GUARD_STATE_DIRNAME
    config_path = state_dir / GUARD_CONFIG_NAME
    override_log = state_dir / OVERRIDE_LOG_NAME

    status: dict[str, object] = {
        "repository": str(repo),
        "hooks_dir": str(hooks),
        "hook_path": str(hook_path),
        "hook_present": hook_path.is_file(),
        "state_dir": str(state_dir),
        "state_dir_present": state_dir.is_dir(),
        "managed": False,
        "executable": False,
        "config_present": config_path.is_file(),
        "hook_matches_config": False,
        "hook_matches_package": False,
        "publisher_identities": list(DEFAULT_PUBLISHER_IDENTITIES),
        "protected_refs": list(PROTECTED_REFS),
        "override_event_count": override_event_count(common_dir),
        "override_log": str(override_log),
        "override_log_integrity": "ok",
        "override_log_tampered": False,
        "active": False,
        "integrity": "absent",
    }

    config: Mapping[str, object] | None = None
    if config_path.is_file():
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            config = None
    # The enforced allow-list is the package constant, embedded in the hook body
    # -- never whatever ``guard.json`` happens to say.
    status["publisher_identities"] = list(DEFAULT_PUBLISHER_IDENTITIES)

    # Tamper-evidence for the durable override log: the hash chain must hold, and
    # the log must not have fewer events than the high-water mark last recorded
    # in guard.json.  Truncating or deleting the log (the strictly-easier evasion
    # that the disclosed `rm .git/hooks/reference-transaction` bypass had over it)
    # therefore now reads as ``modified`` -- Doctor FAIL, the same escalation.
    log_state, log_count = verify_override_log(common_dir)
    recorded_high_water: int | None = None
    recorded_log_sha: str | None = None
    if isinstance(config, dict):
        rc = config.get("override_event_count")
        if isinstance(rc, int) and not isinstance(rc, bool):
            recorded_high_water = rc
        rs = config.get("override_log_sha256")
        if isinstance(rs, str):
            recorded_log_sha = rs
    shrunk = recorded_high_water is not None and log_count < recorded_high_water
    edited_in_place = (
        recorded_log_sha is not None
        and recorded_high_water == log_count
        and override_log.is_file()
        and recorded_log_sha != _sha256_file(override_log)
    )
    status["override_log_integrity"] = log_state
    status["override_log_tampered"] = bool(
        log_state != "ok" or shrunk or edited_in_place
    )

    if not status["hook_present"]:
        # Distinguish a pristine repo from one whose guard was torn out: the
        # state directory and its durable records outlive an uninstall, so their
        # presence with no hook means "removed", which the Doctor escalates.
        if state_dir.is_dir() and (config is not None or override_log.exists()):
            status["integrity"] = "removed"
        else:
            status["integrity"] = "absent"
        return status

    text = hook_path.read_text(encoding="utf-8", errors="replace")
    status["managed"] = _is_managed_hook(text)
    status["executable"] = bool(hook_path.stat().st_mode & stat.S_IXUSR)
    observed_sha = _sha256_file(hook_path)
    status["hook_sha256"] = observed_sha

    # Compare the installed hook against the one and only body this package would
    # accept, re-derived from trusted inputs alone (see _expected_hook_body).
    # Nothing here is read back out of the installed hook, so an attacker-chosen
    # source_path or a rewritten embedded publisher list cannot round-trip.
    try:
        expected_body = _expected_hook_body(repo)
    except MainRefGuardError:
        expected_body = None
    status["hook_matches_package"] = expected_body is not None and text == expected_body
    if config:
        status["hook_matches_config"] = config.get("hook_sha256") == observed_sha

    if not status["managed"]:
        status["integrity"] = "foreign"
    elif not status["executable"]:
        status["integrity"] = "not-executable"
    elif not status["hook_matches_package"]:
        status["integrity"] = "modified"
    elif status["override_log_tampered"]:
        # Hook body is byte-correct, but the durable override audit trail has
        # been truncated, deleted, or rewritten out of chain.
        status["integrity"] = "modified"
    elif config is None:
        status["integrity"] = "config-missing"
    elif not status["hook_matches_config"]:
        # Body is byte-correct but guard.json's recorded hash disagrees: the
        # record has been tampered with.  Do not rate this "ok".
        status["integrity"] = "modified"
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
