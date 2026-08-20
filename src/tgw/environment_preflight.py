"""W15 pinned execution-environment preflight.

The preflight has no launcher, broker, service, account-management, or source
write capability.  It turns one catalog-declared actor/profile observation into
a deterministic receipt that later boundaries can bind and revalidate.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Mapping

SCHEMA = "tgw-execution-environment-catalog/v1"
RECEIPT_SCHEMA = "tgw-environment-preflight-receipt/v1"
_HASH = re.compile(r"sha256:[0-9a-f]{64}\Z")
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")


class EnvironmentPreflightError(ValueError):
    """The declared environment cannot safely be used."""


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _hash(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _identifier(value: str, label: str) -> str:
    if not isinstance(value, str) or _ID.fullmatch(value) is None:
        raise EnvironmentPreflightError(f"{label} is invalid")
    return value


def preflight(*, catalog: Mapping[str, Any], actor: str, profile: str, attempt_id: str) -> dict[str, Any]:
    """Verify one catalog-defined environment without executing role work."""
    if not isinstance(catalog, Mapping) or catalog.get("schema") != SCHEMA:
        raise EnvironmentPreflightError("environment catalog schema is invalid")
    actors, profiles = catalog.get("actors"), catalog.get("profiles")
    if not isinstance(actors, Mapping) or not isinstance(profiles, Mapping):
        raise EnvironmentPreflightError("environment catalog registry is invalid")
    actor, profile, attempt_id = (_identifier(actor, "actor"), _identifier(profile, "profile"), _identifier(attempt_id, "attempt id"))
    declared_actor, declared_profile = actors.get(actor), profiles.get(profile)
    if not isinstance(declared_actor, Mapping) or not isinstance(declared_profile, Mapping):
        raise EnvironmentPreflightError("catalog actor or profile is unknown")
    if declared_actor.get("enabled") is not True or profile not in declared_actor.get("permitted_profiles", []):
        raise EnvironmentPreflightError("actor/profile binding is refused")
    if declared_profile.get("state") != "ready-for-preflight":
        raise EnvironmentPreflightError("profile is not ready for preflight")
    tools = declared_profile.get("tools")
    if not isinstance(tools, list) or not tools:
        raise EnvironmentPreflightError("profile tools are invalid")
    observed: list[dict[str, str]] = []
    names: set[str] = set()
    for tool in tools:
        if not isinstance(tool, Mapping) or set(tool) != {"name", "store_path", "store_path_hash", "executable_path"}:
            raise EnvironmentPreflightError("catalog tool declaration is invalid")
        name, executable = tool["name"], tool["executable_path"]
        if not isinstance(name, str) or name in names or not isinstance(executable, str) or not executable.startswith("/"):
            raise EnvironmentPreflightError("catalog tool identity is invalid")
        names.add(name)
        path = Path(executable)
        if path.is_symlink() or not path.is_file() or not os.access(path, os.X_OK):
            raise EnvironmentPreflightError(f"declared executable unavailable: {executable}")
        observed.append({"name": name, "executable_path": executable, "observed_sha256": _file_hash(path)})
    unsigned = {
        "schema": RECEIPT_SCHEMA,
        "result": "PASS",
        "catalog_sha256": _hash(catalog),
        "actor": actor,
        "profile": profile,
        "attempt_id": attempt_id,
        "tools": sorted(observed, key=lambda item: item["name"]),
    }
    return unsigned


def main() -> int:
    parser = argparse.ArgumentParser(prog="tgw-environment-preflight")
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--actor", required=True)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--attempt-id", required=True)
    args = parser.parse_args()
    try:
        raw = json.loads(args.catalog.read_text(encoding="utf-8"))
        print(json.dumps(preflight(catalog=raw, actor=args.actor, profile=args.profile, attempt_id=args.attempt_id), sort_keys=True))
        return 0
    except (OSError, json.JSONDecodeError, EnvironmentPreflightError) as exc:
        print(json.dumps({"result": "HOLD", "error": str(exc)}, sort_keys=True), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
