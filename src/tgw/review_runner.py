"""Isolated-snapshot semantic review adapter for any configured harness."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import unquote, urlparse


class ReviewRunnerError(ValueError):
    pass


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _hash(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def snapshot_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        if path.is_symlink():
            raise ReviewRunnerError("review snapshot cannot contain symlinks")
        if not path.is_file() or ".git" in path.relative_to(root).parts:
            continue
        relative = path.relative_to(root).as_posix()
        digest.update(relative.encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return "sha256:" + digest.hexdigest()


def _verify_bound(value: Mapping[str, Any], field: str) -> None:
    unsigned = dict(value)
    claimed = unsigned.pop(field, None)
    if claimed != _hash(unsigned):
        raise ReviewRunnerError(f"{field} mismatch")


def _snapshot_path(card: Mapping[str, Any]) -> tuple[Path, str]:
    binding = card.get("bindings", {}).get("source_tree", {})
    parsed = urlparse(str(binding.get("ref", "")))
    if parsed.scheme != "file" or parsed.netloc not in {"", "localhost"}:
        raise ReviewRunnerError("review source_tree must be a local file URI snapshot")
    path = Path(unquote(parsed.path)).resolve()
    if not path.is_dir():
        raise ReviewRunnerError("review snapshot is unavailable")
    expected = str(binding.get("hash", ""))
    if snapshot_hash(path) != expected:
        raise ReviewRunnerError("review snapshot hash mismatch")
    return path, expected


def _validate_report(
    report: Any, expected_snapshot: str, snapshot_root: Path
) -> dict[str, Any]:
    if not isinstance(report, dict) or set(report) != {
        "schema",
        "verdict",
        "snapshot_hash",
        "summary",
        "findings",
    }:
        raise ReviewRunnerError("review report fields are invalid")
    if report["schema"] != "tgw-code-review/v1" or report["verdict"] not in {"PASS", "FAIL"}:
        raise ReviewRunnerError("review report contract is invalid")
    if report["snapshot_hash"] != expected_snapshot:
        raise ReviewRunnerError("review report snapshot binding mismatch")
    if not isinstance(report["summary"], str) or not report["summary"].strip():
        raise ReviewRunnerError("review report summary is required")
    findings = report["findings"]
    if not isinstance(findings, list):
        raise ReviewRunnerError("review findings must be a list")
    for finding in findings:
        if not isinstance(finding, dict) or set(finding) != {"severity", "path", "line", "message"}:
            raise ReviewRunnerError("review finding fields are invalid")
        if finding["severity"] not in {"critical", "high", "medium", "low"}:
            raise ReviewRunnerError("review finding severity is invalid")
        relative = Path(str(finding["path"]))
        if (
            not isinstance(finding["path"], str)
            or not finding["path"]
            or relative.is_absolute()
            or ".." in relative.parts
        ):
            raise ReviewRunnerError("review finding path must be snapshot-relative")
        if not isinstance(finding["line"], int) or finding["line"] < 1:
            raise ReviewRunnerError("review finding line is invalid")
        if not isinstance(finding["message"], str) or not finding["message"].strip():
            raise ReviewRunnerError("review finding message is required")
        source = snapshot_root / relative
        if not source.is_file():
            raise ReviewRunnerError("review finding path is absent from the snapshot")
        if finding["line"] > len(source.read_text(encoding="utf-8", errors="replace").splitlines()):
            raise ReviewRunnerError("review finding line is outside the snapshot source")
    if report["verdict"] == "PASS" and findings:
        raise ReviewRunnerError("passing review cannot contain unresolved findings")
    if report["verdict"] == "FAIL" and not findings:
        raise ReviewRunnerError("failed review must identify at least one finding")
    return report


def run_review(handoff: Mapping[str, Any], provider_argv: list[str]) -> dict[str, Any]:
    if handoff.get("schema") != "tgw-launcher-handoff/v1":
        raise ReviewRunnerError("review runner requires a launcher handoff")
    _verify_bound(handoff, "handoff_hash")
    card = handoff.get("card")
    if not isinstance(card, Mapping) or card.get("role") != "independent-review":
        raise ReviewRunnerError("review runner received another role")
    _verify_bound(card, "card_hash")
    source, expected_hash = _snapshot_path(card)
    if not provider_argv or not all(isinstance(item, str) and item for item in provider_argv):
        raise ReviewRunnerError("review provider argv is invalid")
    with tempfile.TemporaryDirectory(prefix="tgw-isolated-review-") as temporary:
        isolated = Path(temporary) / "snapshot"
        shutil.copytree(source, isolated)
        if snapshot_hash(isolated) != expected_hash:
            raise ReviewRunnerError("isolated review snapshot copy mismatch")
        request = {
            "schema": "tgw-code-review-request/v1",
            "handoff_hash": handoff["handoff_hash"],
            "card_hash": card["card_hash"],
            "snapshot_hash": expected_hash,
            "snapshot_root": str(isolated),
            "output_contract": "tgw-code-review/v1",
        }
        completed = subprocess.run(
            provider_argv,
            cwd=isolated,
            input=json.dumps(request),
            text=True,
            capture_output=True,
            check=False,
        )
        if completed.returncode:
            raise ReviewRunnerError(f"review provider exited {completed.returncode}: {completed.stderr[-500:]}")
        if snapshot_hash(isolated) != expected_hash:
            raise ReviewRunnerError("review provider mutated the isolated snapshot")
        try:
            report = _validate_report(json.loads(completed.stdout), expected_hash, isolated)
        except json.JSONDecodeError as exc:
            raise ReviewRunnerError("review provider returned invalid JSON") from exc
    passed = report["verdict"] == "PASS"
    return {
        "outcome": "satisfied" if passed else "failed",
        "established_conditions": ["reviewed"] if passed else [],
        "artifacts": [{"kind": "semantic_review", "report": report}],
    }


def main() -> int:
    parser = argparse.ArgumentParser(prog="tgw-review-runner")
    parser.add_argument("--provider-command-json", required=True)
    args = parser.parse_args()
    try:
        provider_argv = json.loads(args.provider_command_json)
        result = run_review(json.load(sys.stdin), provider_argv)
    except (ReviewRunnerError, json.JSONDecodeError, OSError) as exc:
        result = {
            "outcome": "failed",
            "established_conditions": [],
            "artifacts": [{"kind": "review_runner_failure", "detail": str(exc)}],
        }
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
