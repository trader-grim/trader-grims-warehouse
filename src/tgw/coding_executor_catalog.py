"""One declarative source of truth for coding executors — LEAF-11-9 W1.

Every place that needs to know *what coding executors exist, where their
binaries live, which runtime they need, and which credential each
authenticates with* reads THIS catalogue:

  * ``tgw.development.harness_session`` — the executor chain, the per-session
    model credential, and executor-binary discovery;
  * ``tgw.development.harness_cli`` — resolving an absolute binary path to
    hand the confined coder identity (whose ``sudo`` ``secure_path`` cannot
    find a binary under an operator home);
  * ``tgw.model_selector`` — the set of executor names a policy/availability
    file is allowed to name.

There are no hard-coded executor lists anywhere else. Adding a new executor
(``deepseek`` / ``agy`` / ``gemini`` / ``opencode`` / …) is a catalogue edit —
a new entry in ``config/tgw-coding-executors.json`` — with no code change.

The built-in ``stub`` executor (the offline bootstrap canary, W0) has NO
catalogue entry: it needs no binary, credential, or install. The chain still
accepts ``executor_preference=['stub']``; ``stub`` is a reserved name here.

A missing or unverifiable credential is never fatal: ``credential_ready``
reports it, the chain marks that executor unavailable and moves on (WARN,
not FAIL) — onboarding must reach a working system with no working model.

Catalogue file (first found wins), same convention as ``model-availability``::

    1. $TGW_CODING_EXECUTORS
    2. /opt/TGW/tgw-lib/config/tgw-coding-executors.json   (operator-editable)
    3. <repo>/config/tgw-coding-executors.json             (committed default)

Per-executor contract (extra keys are carried for humans and ignored here)::

    {
      "binary_name":         "claude",
      "install_source":      "npm:@anthropic-ai/claude-code" | null,
      "install_target_path": "/usr/local/bin/claude"        | null,
      "runtime_deps":        ["node"],
      "verify_cmd":          ["claude", "--version"]         | null,
      "credential_env":      ["CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY"],
      "auth_file":           "~/.claude/.credentials.json"   | null,
      "enabled":             true,
      "discovery_paths":     ["/home/claude/.local/bin/claude"]   (optional)
    }
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SCHEMA = "tgw-coding-executors/v1"

_CONFIG_PATH = Path("/opt/TGW/tgw-lib/config/tgw-coding-executors.json")
_REPO_DEFAULT = Path(__file__).resolve().parent.parent.parent / "config" / "tgw-coding-executors.json"

# Names that never come from the catalogue. ``stub`` is the offline bootstrap
# executor built into harness_session; ``manual`` is model_selector's
# always-available supervised-session fallback. A catalogue that names either
# is malformed.
RESERVED_NAMES: frozenset[str] = frozenset({"stub", "manual"})


class CatalogError(RuntimeError):
    """The coding-executor catalogue cannot be used as written."""


@dataclass(frozen=True)
class ExecutorSpec:
    """One executor's install + credential contract."""

    name: str
    binary_name: str
    install_source: str | None
    install_target_path: str | None
    runtime_deps: tuple[str, ...]
    verify_cmd: tuple[str, ...] | None
    credential_env: tuple[str, ...]
    auth_file: str | None
    enabled: bool
    discovery_paths: tuple[str, ...] = field(default=())

    def auth_file_path(self) -> Path | None:
        return Path(self.auth_file).expanduser() if self.auth_file else None


# --------------------------------------------------------------------------- #
# load + parse
# --------------------------------------------------------------------------- #

def catalog_path() -> Path:
    override = os.environ.get("TGW_CODING_EXECUTORS")
    if override:
        return Path(override)
    if _CONFIG_PATH.is_file():
        return _CONFIG_PATH
    return _REPO_DEFAULT


def _str_or_none(entry: dict[str, Any], key: str, name: str) -> str | None:
    value = entry.get(key)
    if value is None or (isinstance(value, str) and value):
        return value
    raise CatalogError(f"executor {name!r}: {key!r} must be a non-empty string or null")


def _str_list(entry: dict[str, Any], key: str, name: str, *, allow_none: bool = False) -> tuple[str, ...] | None:
    value = entry.get(key)
    if value is None and allow_none:
        return None
    if not isinstance(value, list) or not all(isinstance(x, str) and x for x in value):
        raise CatalogError(f"executor {name!r}: {key!r} must be a list of non-empty strings"
                           + (" or null" if allow_none else ""))
    return tuple(value)


def _parse_executor(name: str, entry: Any) -> ExecutorSpec:
    if not isinstance(name, str) or not name or name != name.strip() or " " in name:
        raise CatalogError(f"executor name {name!r} is not a bare token")
    if name in RESERVED_NAMES:
        raise CatalogError(f"executor name {name!r} is reserved (built-in) and cannot appear in the catalogue")
    if not isinstance(entry, dict):
        raise CatalogError(f"executor {name!r}: entry must be an object")
    if not isinstance(entry.get("enabled"), bool):
        raise CatalogError(f"executor {name!r}: 'enabled' must be a boolean")
    binary_name = entry.get("binary_name")
    if not isinstance(binary_name, str) or not binary_name:
        raise CatalogError(f"executor {name!r}: 'binary_name' must be a non-empty string")
    return ExecutorSpec(
        name=name,
        binary_name=binary_name,
        install_source=_str_or_none(entry, "install_source", name),
        install_target_path=_str_or_none(entry, "install_target_path", name),
        runtime_deps=_str_list(entry, "runtime_deps", name) or (),
        verify_cmd=_str_list(entry, "verify_cmd", name, allow_none=True),
        credential_env=_str_list(entry, "credential_env", name) or (),
        auth_file=_str_or_none(entry, "auth_file", name),
        enabled=entry["enabled"],
        discovery_paths=_str_list(entry, "discovery_paths", name, allow_none=True) or (),
    )


def load_catalog(path: Path | None = None) -> dict[str, ExecutorSpec]:
    """Return ``{name: ExecutorSpec}`` for the resolved catalogue file.

    Raises ``CatalogError`` for an unreadable, malformed, or reserved-name
    catalogue — a broken source of truth must fail loudly, not silently drop
    executors.
    """
    resolved = path or catalog_path()
    try:
        data = json.loads(resolved.read_text(encoding="utf-8"))
    except OSError as exc:
        raise CatalogError(f"coding-executor catalogue is unreadable: {resolved}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise CatalogError(f"coding-executor catalogue is not valid JSON: {resolved}: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("executors"), dict):
        raise CatalogError(f"coding-executor catalogue needs an 'executors' object: {resolved}")
    return {name: _parse_executor(name, entry) for name, entry in data["executors"].items()}


# --------------------------------------------------------------------------- #
# queries
# --------------------------------------------------------------------------- #

def executor_specs(*, enabled_only: bool = False, path: Path | None = None) -> dict[str, ExecutorSpec]:
    specs = load_catalog(path)
    if enabled_only:
        return {n: s for n, s in specs.items() if s.enabled}
    return specs


def executor_names(*, enabled_only: bool = False, path: Path | None = None) -> tuple[str, ...]:
    """Catalogue executor names in declaration order (a stable default chain order)."""
    return tuple(executor_specs(enabled_only=enabled_only, path=path))


def executor_spec(name: str, *, path: Path | None = None) -> ExecutorSpec | None:
    return load_catalog(path).get(name)


# --------------------------------------------------------------------------- #
# binary discovery
# --------------------------------------------------------------------------- #

def binary_env_var(name: str) -> str:
    """The per-executor binary-path override env var, e.g. ``TGW_CLAUDE_BIN``."""
    return f"TGW_{name.upper()}_BIN"


def _executable(candidate: str | os.PathLike[str]) -> bool:
    p = Path(candidate)
    return p.is_file() and os.access(p, os.X_OK)


def discover_binary(name: str, override: str | None = None, *, spec: ExecutorSpec | None = None) -> str | None:
    """Absolute path to *name*'s executable, or None if it cannot be found.

    Order: explicit *override* → ``$TGW_<NAME>_BIN`` → ``$PATH`` →
    ``install_target_path`` → each ``discovery_paths`` entry. Discovery never
    raises — a missing binary marks the executor unavailable, it is not an
    error here.
    """
    if spec is None:
        try:
            spec = executor_spec(name)
        except CatalogError:
            spec = None
    binary = spec.binary_name if spec else name

    if override:
        return override if _executable(override) else None
    env = os.environ.get(binary_env_var(name))
    if env:
        return env if _executable(env) else None
    found = shutil.which(binary)
    if found:
        return found
    candidates: list[str] = []
    if spec:
        if spec.install_target_path:
            candidates.append(spec.install_target_path)
        candidates.extend(spec.discovery_paths)
    for candidate in candidates:
        if _executable(candidate):
            return str(Path(candidate).resolve())
    return None


# --------------------------------------------------------------------------- #
# credentials — best effort, a miss is a WARN not a FAIL
# --------------------------------------------------------------------------- #

def _source_secrets() -> None:
    """Source ``secrets_root/tgw.env`` into the process (harmless if absent).

    The scoped-credential broker (Todo #1253) replaces this single host-wide
    store later; until then the catalogue only *names* the slots and the
    values come from ``tgw.env``.
    """
    try:
        from tgw.config import DEFAULT_CONFIG, load_config

        load_config(DEFAULT_CONFIG)
    except Exception:
        pass


def session_credential(name: str, *, spec: ExecutorSpec | None = None) -> tuple[str, str] | None:
    """``(env_var, value)`` for *name*'s model credential, or None.

    None does not mean unusable — the executor may still authenticate from
    ``auth_file`` in the coder's home; see ``credential_ready``.
    """
    _source_secrets()
    if spec is None:
        try:
            spec = executor_spec(name)
        except CatalogError:
            spec = None
    names = spec.credential_env if spec and spec.credential_env else (f"{name.upper()}_API_KEY",)
    for env_name in names:
        value = os.environ.get(env_name, "")
        if value:
            return env_name, value
    return None


def credential_ready(name: str, *, spec: ExecutorSpec | None = None) -> bool:
    """True when *name* has either an env credential or an on-disk auth file.

    A False here is a WARN: the executor is marked unavailable and the chain
    continues; onboarding does not fail.
    """
    if spec is None:
        try:
            spec = executor_spec(name)
        except CatalogError:
            spec = None
    if session_credential(name, spec=spec):
        return True
    auth = spec.auth_file_path() if spec else None
    return bool(auth and auth.is_file())
