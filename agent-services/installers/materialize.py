"""Materialize shared tgw-plan and Promptcraft adapters without copying policy."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCHEMA = "tgw-agent-service-installation/v1"
TARGETS = ("codex", "claude", "hermes", "isolated-worker")


class InstallError(ValueError):
    pass


@dataclass(frozen=True)
class Adapter:
    capability: str
    source: Path
    destination: Path
    hold_legacy: bool = False


def tree_digest(path: Path) -> str:
    digest = hashlib.sha256()
    files = [item for item in path.rglob("*") if item.is_file() and "__pycache__" not in item.parts]
    for item in sorted(files, key=lambda value: value.relative_to(path).as_posix()):
        relative = item.relative_to(path).as_posix()
        digest.update(relative.encode())
        digest.update(b"\0")
        digest.update(item.read_bytes())
        digest.update(b"\0")
    return "sha256:" + digest.hexdigest()


def _matrix(target: str, *, home: Path, project: Path, source_root: Path) -> list[Adapter]:
    skill = source_root / "agent-services/skills/tgw-plan"
    provider = source_root / "agent-services/providers/promptcraft"
    if target == "codex":
        return [
            Adapter("tgw-plan", skill, home / ".codex/skills/tgw-plan"),
            Adapter("promptcraft", provider, home / ".codex/providers/promptcraft"),
        ]
    if target == "claude":
        return [
            Adapter("tgw-plan", skill, project / ".claude/skills/tgw-plan", hold_legacy=True),
            Adapter("promptcraft", provider, project / ".claude/providers/promptcraft"),
        ]
    if target == "hermes":
        return [
            Adapter("tgw-plan", skill, home / ".hermes/skills/tgw-plan"),
            Adapter("promptcraft", provider, home / ".hermes/providers/promptcraft"),
        ]
    if target == "isolated-worker":
        return [
            Adapter(
                "promptcraft-card-handoff",
                provider / "bin/promptcraft-handoff",
                project / ".tgw-worker/bin/promptcraft-handoff",
            )
        ]
    raise InstallError(f"unknown target: {target}")


def _same_link(destination: Path, source: Path) -> bool:
    return destination.is_symlink() and destination.resolve(strict=False) == source


def materialize(
    target: str,
    *,
    home: str | Path,
    project: str | Path,
    source_root: str | Path,
    apply: bool = False,
) -> dict[str, Any]:
    """Inspect or create the exact adapter matrix for one harness."""

    home_path = Path(home).resolve()
    project_path = Path(project).resolve()
    root = Path(source_root).resolve()
    adapters = _matrix(target, home=home_path, project=project_path, source_root=root)
    initial: list[tuple[Adapter, str]] = []
    for adapter in adapters:
        if not adapter.source.exists():
            raise InstallError(f"canonical source is missing: {adapter.source}")
        destination = adapter.destination
        if _same_link(destination, adapter.source):
            status = "CURRENT"
        elif destination.exists() or destination.is_symlink():
            status = "HELD_LEGACY" if adapter.hold_legacy else "CONFLICT"
        else:
            status = "MISSING"
        initial.append((adapter, status))
    ok = not any(status == "CONFLICT" for _, status in initial)

    actions: list[dict[str, Any]] = []
    for adapter, initial_status in initial:
        destination = adapter.destination
        if initial_status != "MISSING":
            status = initial_status
        elif apply and ok:
            destination.parent.mkdir(parents=True, exist_ok=True)
            os.symlink(adapter.source, destination, target_is_directory=adapter.source.is_dir())
            status = "INSTALLED"
        elif apply:
            status = "HELD_CONFLICT"
        else:
            status = "WOULD_INSTALL"
        actions.append(
            {
                "capability": adapter.capability,
                "source": str(adapter.source),
                "source_digest": tree_digest(adapter.source) if adapter.source.is_dir() else "sha256:" + hashlib.sha256(adapter.source.read_bytes()).hexdigest(),
                "destination": str(destination),
                "status": status,
            }
        )
    return {
        "schema": SCHEMA,
        "target": target,
        "mode": "apply" if apply else "dry-run",
        "canonical_source_root": str(root),
        "actions": actions,
        "ok": ok,
        "legacy_held": any(item["status"] == "HELD_LEGACY" for item in actions),
    }


def main() -> int:
    parser = argparse.ArgumentParser(prog="materialize-agent-services")
    parser.add_argument("target", choices=TARGETS)
    parser.add_argument("--home", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--source-root", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        result = materialize(
            args.target,
            home=args.home,
            project=args.project,
            source_root=args.source_root,
            apply=args.apply,
        )
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 0 if result["ok"] else 2
    except (InstallError, OSError) as exc:
        print(json.dumps({"error": str(exc), "error_type": type(exc).__name__}), file=os.sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
