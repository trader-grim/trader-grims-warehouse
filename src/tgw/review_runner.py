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

from tgw.review_broker_supervisor import run_with_broker
from tgw.review_contract import ReviewRunnerError
from tgw.review_contract import validate_review_report as _validate_report
from tgw.review_egress_broker import ReviewEgressPolicy

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


def _sandbox_command(
    provider_argv: list[str],
    snapshot: Path,
    *,
    network_egress: bool,
    credential_file: Path | None,
    tool_root: Path | None,
    proxy_url: str | None,
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
    command.extend(["--ro-bind", str(snapshot), "/workspace"])
    if executable.name.startswith("python"):
        runtime = executable.parent.parent
        command.extend(["--ro-bind", str(runtime), "/runtime"])
        provider = [f"/runtime/bin/{executable.name}", *provider_argv[1:]]
    else:
        command.extend(["--ro-bind", str(executable), "/review-provider"])
        provider = ["/review-provider", *provider_argv[1:]]
    command.extend(["--setenv", "PATH", "/runtime/bin:/usr/bin:/bin"])
    command.extend(["--setenv", "HOME", "/tmp/home"])
    # The candidate is data for the reviewer, never its import root or cwd.
    # Python places cwd on sys.path even without PYTHONPATH, so execute from an
    # empty sandbox-owned directory and pass /workspace only in the request.
    command.extend(["--dir", "/tmp/home", "--chdir", "/tmp/home"])
    if network_egress:
        if credential_file is None or not credential_file.is_file():
            raise ReviewRunnerError("declared network review credential is unavailable")
        if tool_root is None or not tool_root.is_dir():
            raise ReviewRunnerError("declared network review tool root is unavailable")
        command.extend(["--ro-bind", str(credential_file.resolve()), "/credentials/auth.json"])
        command.extend(["--ro-bind", str(tool_root.resolve()), "/tools"])
        command.extend(["--setenv", "TGW_CODEX_REVIEW_AUTH", "/credentials/auth.json"])
        command.extend(["--setenv", "PATH", "/tools/bin:/runtime/bin:/usr/bin:/bin"])
        if not proxy_url:
            raise ReviewRunnerError("attested review broker address is unavailable")
        command.extend(["--setenv", "HTTPS_PROXY", proxy_url])
        command.extend(["--setenv", "https_proxy", proxy_url])
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
    egress_policy: Mapping[str, Any] | None = None,
    network_attestation: Mapping[str, Any] | None = None,
    egress_receipt: Mapping[str, Any] | None = None,
    broker_argv: list[str] | None = None,
    egress_receipt_path: Path | None = None,
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
        proxy_url = None
        policy = None
        if network_egress:
            try:
                policy = ReviewEgressPolicy.parse(egress_policy or {})
                policy.verify_runtime(Path(provider_argv[0]).resolve(), credential_file or Path(""))
            except (ValueError, OSError) as exc:
                raise ReviewRunnerError(f"review egress policy is invalid: {exc}") from exc
            attestation = network_attestation or {}
            if set(attestation) != {"schema", "policy_hash", "direct_egress_denied", "broker_bind", "attestation_hash"}:
                raise ReviewRunnerError("review network attestation fields are invalid")
            unsigned_attestation = dict(attestation)
            claimed = unsigned_attestation.pop("attestation_hash")
            if claimed != _hash(unsigned_attestation):
                raise ReviewRunnerError("review network attestation hash mismatch")
            if attestation["schema"] != "tgw-review-egress-network-attestation/v1" or attestation["policy_hash"] != policy.policy_hash or attestation["direct_egress_denied"] is not True:
                raise ReviewRunnerError("review network isolation is not attested")
            bind = attestation["broker_bind"]
            if not isinstance(bind, Mapping) or set(bind) != {"host", "port"} or not isinstance(bind["host"], str) or not isinstance(bind["port"], int):
                raise ReviewRunnerError("attested review broker bind is invalid")
            proxy_url = f"http://{bind['host']}:{bind['port']}"
        sandboxed = _sandbox_command(
            provider_argv,
            isolated,
            network_egress=network_egress,
            credential_file=credential_file,
            tool_root=tool_root,
            proxy_url=proxy_url,
        )
        def invoke_provider():
            try:
                return subprocess.run(
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

        if network_egress and broker_argv is not None:
            if egress_receipt is not None or egress_receipt_path is None:
                raise ReviewRunnerError("live broker requires one absent final receipt path, not a preloaded receipt")
            completed, egress_receipt = run_with_broker(broker_argv, invoke_provider, egress_receipt_path)
        else:
            completed = invoke_provider()
        if completed.returncode:
            raise ReviewRunnerError(f"review provider exited {completed.returncode}: {completed.stderr[-500:]}")
        if snapshot_hash(isolated) != expected_hash:
            raise ReviewRunnerError("review provider mutated the isolated snapshot")
        try:
            report = _validate_report(json.loads(completed.stdout), expected_hash, isolated)
        except json.JSONDecodeError as exc:
            raise ReviewRunnerError("review provider returned invalid JSON") from exc
        if network_egress:
            receipt = egress_receipt or {}
            if set(receipt) != {"schema", "run_id", "policy_hash", "sessions", "receipt_hash"}:
                raise ReviewRunnerError("review egress receipt fields are invalid")
            unsigned_receipt = dict(receipt)
            claimed = unsigned_receipt.pop("receipt_hash")
            if claimed != _hash(unsigned_receipt) or receipt["schema"] != "tgw-review-egress-receipt/v1":
                raise ReviewRunnerError("review egress receipt hash/schema is invalid")
            if receipt["run_id"] != policy.run_id or receipt["policy_hash"] != policy.policy_hash:
                raise ReviewRunnerError("review egress receipt policy binding mismatch")
            if not isinstance(receipt["sessions"], list) or any(not isinstance(item, Mapping) or item.get("outcome") != "completed" for item in receipt["sessions"]):
                raise ReviewRunnerError("review egress receipt contains denied or invalid sessions")
    passed = report["verdict"] == "PASS"
    return {
        "outcome": "satisfied" if passed else "failed",
        "established_conditions": ["reviewed"] if passed else [],
        "artifacts": [{"kind": "semantic_review", "report": report}],
        # The runner receives the descriptor-bearing handoff, never copied
        # card resources.  Echoing this checked receipt is required by the
        # governed-role protocol before a review can be admitted.
        "resource_receipt_hash": handoff["resource_receipt"]["receipt_hash"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(prog="tgw-review-runner")
    parser.add_argument("--provider-command-json", required=True)
    parser.add_argument("--timeout-seconds", type=float, default=300)
    parser.add_argument("--network-egress", action="store_true")
    parser.add_argument("--credential-file", type=Path)
    parser.add_argument("--tool-root", type=Path)
    parser.add_argument("--egress-policy", type=Path)
    parser.add_argument("--network-attestation", type=Path)
    parser.add_argument("--egress-receipt", type=Path)
    parser.add_argument("--broker-command-json")
    parser.add_argument("--egress-receipt-path", type=Path)
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
            egress_policy=json.loads(args.egress_policy.read_text()) if args.egress_policy else None,
            network_attestation=json.loads(args.network_attestation.read_text()) if args.network_attestation else None,
            egress_receipt=json.loads(args.egress_receipt.read_text()) if args.egress_receipt else None,
            broker_argv=json.loads(args.broker_command_json) if args.broker_command_json else None,
            egress_receipt_path=args.egress_receipt_path,
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
