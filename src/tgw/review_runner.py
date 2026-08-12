"""Isolated-snapshot semantic review adapter for any configured harness."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import unquote, urlparse


class ReviewRunnerError(ValueError):
    pass


_PROMPTCRAFT = Path(__file__).resolve().parents[2] / "agent-services/providers/promptcraft"
if str(_PROMPTCRAFT) not in sys.path:
    sys.path.insert(0, str(_PROMPTCRAFT))
from promptcraft.handoff import HandoffError, verify_for_launcher  # noqa: E402


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


def _sandbox_command(
    provider_argv: list[str],
    snapshot: Path,
    *,
    network_egress: bool,
    credential_file: Path | None,
    tool_root: Path | None,
) -> list[str]:
    bwrap = shutil.which("bwrap")
    if bwrap is None:
        raise ReviewRunnerError("review sandbox is unavailable")
    executable = Path(provider_argv[0]).resolve()
    if not executable.is_file():
        raise ReviewRunnerError("review provider executable is unavailable")
    command = [bwrap, "--unshare-user", "--unshare-pid", "--unshare-ipc", "--unshare-uts", "--unshare-cgroup"]
    if not network_egress:
        command.append("--unshare-net")
    command.extend(["--die-with-parent", "--new-session", "--clearenv"])
    for system_path in ("/usr", "/bin", "/lib", "/lib64"):
        if Path(system_path).exists():
            command.extend(["--ro-bind", system_path, system_path])
    command.extend(["--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp"])
    command.extend(["--ro-bind", str(snapshot), "/workspace", "--chdir", "/workspace"])
    if executable.name.startswith("python"):
        runtime = executable.parent.parent
        command.extend(["--ro-bind", str(runtime), "/runtime"])
        provider = [f"/runtime/bin/{executable.name}", *provider_argv[1:]]
    else:
        command.extend(["--ro-bind", str(executable), "/review-provider"])
        provider = ["/review-provider", *provider_argv[1:]]
    command.extend(["--setenv", "PATH", "/runtime/bin:/usr/bin:/bin"])
    command.extend(["--setenv", "PYTHONPATH", "/workspace/src"])
    command.extend(["--setenv", "HOME", "/tmp/home"])
    command.extend(["--dir", "/tmp/home"])
    if network_egress:
        if credential_file is None or not credential_file.is_file():
            raise ReviewRunnerError("declared network review credential is unavailable")
        if tool_root is None or not tool_root.is_dir():
            raise ReviewRunnerError("declared network review tool root is unavailable")
        command.extend(["--ro-bind", str(credential_file.resolve()), "/credentials/auth.json"])
        command.extend(["--ro-bind", str(tool_root.resolve()), "/tools"])
        command.extend(["--setenv", "TGW_CODEX_REVIEW_AUTH", "/credentials/auth.json"])
        command.extend(["--setenv", "PATH", "/tools/bin:/runtime/bin:/usr/bin:/bin"])
    return [*command, "--", *provider]


def run_review(
    handoff: Mapping[str, Any],
    provider_argv: list[str],
    *,
    timeout_seconds: float = 300,
    now: datetime | None = None,
    network_egress: bool = False,
    credential_file: Path | None = None,
    tool_root: Path | None = None,
) -> dict[str, Any]:
    if handoff.get("schema") != "tgw-launcher-handoff/v1":
        raise ReviewRunnerError("review runner requires a launcher handoff")
    try:
        invocation = verify_for_launcher(handoff, now=now or datetime.now(timezone.utc))
    except HandoffError as exc:
        raise ReviewRunnerError(f"invalid Promptcraft handoff: {exc}") from exc
    card = handoff.get("card")
    if not isinstance(card, Mapping) or card.get("role") != "independent-review":
        raise ReviewRunnerError("review runner received another role")
    _verify_bound(card, "card_hash")
    if invocation.get("role") != "independent-review":
        raise ReviewRunnerError("review launcher invocation role mismatch")
    if invocation.get("selected_provider") != card.get("selected_provider"):
        raise ReviewRunnerError("review launcher provider mismatch")
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
            "snapshot_root": "/workspace",
            "output_contract": "tgw-code-review/v1",
        }
        sandboxed = _sandbox_command(
            provider_argv,
            isolated,
            network_egress=network_egress,
            credential_file=credential_file,
            tool_root=tool_root,
        )
        try:
            completed = subprocess.run(
            sandboxed,
            cwd=isolated,
            input=json.dumps(request),
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            raise ReviewRunnerError("review provider exceeded bounded timeout") from exc
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
    parser.add_argument("--timeout-seconds", type=float, default=300)
    parser.add_argument("--network-egress", action="store_true")
    parser.add_argument("--credential-file", type=Path)
    parser.add_argument("--tool-root", type=Path)
    args = parser.parse_args()
    try:
        provider_argv = json.loads(args.provider_command_json)
        result = run_review(
            json.load(sys.stdin),
            provider_argv,
            timeout_seconds=args.timeout_seconds,
            network_egress=args.network_egress,
            credential_file=args.credential_file,
            tool_root=args.tool_root,
        )
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
