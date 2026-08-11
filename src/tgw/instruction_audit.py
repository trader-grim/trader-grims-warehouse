"""Read-only inventory and classification of TGW instruction surfaces."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Iterable, Mapping

from tgw.environment_registry import validate_registry


class InstructionAuditError(ValueError):
    """An instruction inventory cannot be produced safely."""


_MEMORY_AUTHORITY = re.compile(
    r"(?:memory|hindsight|histor(?:y|ical)).{0,48}(?:authorit|permission|instruction)",
    re.IGNORECASE,
)
_DEPLOY_COMMAND = re.compile(r"\b(?:nixos-rebuild\s+switch|tgw-release-install\s+install)\b")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _relative_file(root: Path, raw: str) -> Path:
    if not isinstance(raw, str) or not raw or raw != raw.strip():
        raise InstructionAuditError("instruction path is invalid")
    candidate = root / raw
    try:
        resolved = candidate.resolve(strict=True)
        relative = resolved.relative_to(root.resolve(strict=True))
    except (OSError, ValueError) as exc:
        raise InstructionAuditError(f"instruction path escapes or is unavailable: {raw}") from exc
    if candidate.is_symlink() or not resolved.is_file():
        raise InstructionAuditError(f"instruction source is not a regular file: {raw}")
    return root / relative


def discover_instruction_sources(root: Path, registry: Mapping[str, Any]) -> dict[str, set[str]]:
    """Return exact repository-relative sources and the scopes that consume them."""
    validated = validate_registry(registry)
    sources: dict[str, set[str]] = {}

    def add(raw: str, scope: str) -> None:
        path = _relative_file(root, raw)
        relative = path.relative_to(root).as_posix()
        sources.setdefault(relative, set()).add(scope)

    for actor, contract in validated["content"]["agents"].items():
        for raw in contract["authority_files"]:
            add(raw, f"authority:{actor}")
        for raw in contract["excluded_authority_files"]:
            add(raw, f"excluded:{actor}")
    claude_root = root / ".claude"
    if claude_root.exists():
        for path in sorted(claude_root.rglob("*")):
            if path.is_file() and not path.is_symlink() and path.suffix in {".md", ".json"}:
                add(path.relative_to(root).as_posix(), "claude-runtime")
    runbooks = root / "docs/TGW-Plan-Vault/reference/runbooks"
    if runbooks.exists():
        for path in sorted(runbooks.glob("*.md")):
            add(path.relative_to(root).as_posix(), "current-runbook")
    return sources


def _line_findings(
    relative: str,
    text: str,
    scopes: set[str],
    retired_names: Iterable[str],
) -> list[dict[str, Any]]:
    findings = []
    for line_number, line in enumerate(text.splitlines(), 1):
        for name in retired_names:
            if re.search(rf"(?<![A-Za-z0-9_-]){re.escape(name)}(?![A-Za-z0-9_-])", line):
                findings.append({
                    "code": "retired-host-reference",
                    "path": relative,
                    "line": line_number,
                    "identity": name,
                    "scopes": sorted(scopes),
                    "classification": "migration-required" if any(
                        scope.startswith("authority:") or scope == "current-runbook" for scope in scopes
                    ) else "historical-or-runtime-review",
                    "line_sha256": "sha256:" + hashlib.sha256(line.encode()).hexdigest(),
                })
        if _MEMORY_AUTHORITY.search(line):
            findings.append({
                "code": "memory-history-authority-language",
                "path": relative,
                "line": line_number,
                "scopes": sorted(scopes),
                "classification": "review-context-and-negation",
                "line_sha256": "sha256:" + hashlib.sha256(line.encode()).hexdigest(),
            })
        if "current-runbook" in scopes and _DEPLOY_COMMAND.search(line):
            findings.append({
                "code": "direct-mutable-deploy-command",
                "path": relative,
                "line": line_number,
                "scopes": sorted(scopes),
                "classification": "replace-with-registered-procedure-id",
                "line_sha256": "sha256:" + hashlib.sha256(line.encode()).hexdigest(),
            })
    return findings


def audit_instructions(
    root: Path,
    registry: Mapping[str, Any],
    *,
    observed_at: str,
) -> dict[str, Any]:
    validated = validate_registry(registry)
    sources = discover_instruction_sources(root, validated)
    retired_names = sorted(validated["content"]["retired_hosts"])
    inventory = []
    findings = []
    for relative, scopes in sorted(sources.items()):
        path = _relative_file(root, relative)
        text = path.read_text(encoding="utf-8")
        inventory.append({
            "path": relative,
            "sha256": _sha256(path),
            "scopes": sorted(scopes),
        })
        findings.extend(_line_findings(relative, text, scopes, retired_names))
    obsolete_profile = ".claude/agents/nix-flake-maintainer.md"
    if obsolete_profile in sources:
        profile_text = (root / obsolete_profile).read_text(encoding="utf-8")
        if not all(marker in profile_text for marker in (
            "tgw-instruction-tombstone/v1", "tools: \"\"", "RETIRED_PROFILE",
        )):
            findings.append({
                "code": "obsolete-maintainer-profile-present",
                "path": obsolete_profile,
                "line": 1,
                "scopes": sorted(sources[obsolete_profile]),
                "classification": "claude-runtime-remediation-required",
                "line_sha256": inventory[
                    next(index for index, value in enumerate(inventory) if value["path"] == obsolete_profile)
                ]["sha256"],
            })
    return {
        "schema": "tgw-instruction-audit/v1",
        "observed_at": observed_at,
        "registry_revision": validated["revision"],
        "source_count": len(inventory),
        "sources": inventory,
        "finding_count": len(findings),
        "findings": sorted(findings, key=lambda item: (item["path"], item["line"], item["code"])),
        "commands_executed_from_sources": False,
        "source_files_modified": False,
    }


def persist_audit(path: Path, audit: Mapping[str, Any]) -> Path:
    """Create one immutable audit artifact without following links or overwriting."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(
        path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o444,
    )
    try:
        body = json.dumps(audit, sort_keys=True, indent=2, allow_nan=False).encode() + b"\n"
        with os.fdopen(descriptor, "wb", closefd=False) as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        path.unlink(missing_ok=True)
        raise
    finally:
        os.close(descriptor)
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    return path


def main() -> int:
    parser = argparse.ArgumentParser(prog="tgw-instruction-audit")
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--observed-at", required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        import yaml

        registry = yaml.safe_load(args.registry.read_text(encoding="utf-8"))
        result = audit_instructions(args.root, registry, observed_at=args.observed_at)
        if args.output is not None:
            persist_audit(args.output, result)
    except (OSError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
