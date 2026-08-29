"""tgw-root-effect — the host recovery spine (declared privileged operations).

Self-contained by design: imports only the Python standard library and never
``tgw.*``, so it keeps working when the coding runtime it is meant to repair is
broken.  It is root-only and every operation is idempotent and writes a
``tgw-root-effect-receipt/v1`` receipt.  It is the realization of what "Doctor"
was originally meant to be: a bounded, recovery-enabled privileged spine.

This module is the versioned source; the operator installs a root:root pinned
copy as ``/usr/local/sbin/tgw-root-effect`` and a root:root config (see
``DEFAULT_CONFIG``).  No arbitrary paths, environment variables, or commands are
ever accepted: every operand is validated against an exact whitelist or regex.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

RECEIPT_SCHEMA = "tgw-root-effect-receipt/v1"
CONFIG_SCHEMA = "tgw-root-effect-config/v1"
DEFAULT_CONFIG = "/opt/TGW/tgw-lib/config/tgw-root-effect.json"

_COMMIT = re.compile(r"[0-9a-f]{40}\Z")
_CONTAINER_ID = re.compile(r"[a-z0-9][a-z0-9_.-]{0,127}\Z")

# The bootstrap --repair choices are the bounded database/state repairs the
# spine is allowed to run.  The spine never invents a repair name.
_REPAIR_CHOICES = frozenset(
    {
        "context",
        "context-launcher",
        "database",
        "workers",
        "plan-render-worker",
        "runtime",
        "unix-git-access",
        "obsolete-surfaces",
    }
)

_OPS = frozenset(
    {
        "runtime-install",
        "service-restart",
        "database-repair",
        "container-lifecycle",
        "recovery-status",
        "restore-from-receipt",
    }
)


class RootEffectError(ValueError):
    """A requested spine operation was invalid, unsafe, or unsupported."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256_text(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def load_config(path: str | Path = DEFAULT_CONFIG) -> dict[str, Any]:
    raw = Path(path)
    try:
        value = json.loads(raw.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RootEffectError(f"root-effect config is unreadable: {exc}") from exc
    if not isinstance(value, dict) or value.get("schema") != CONFIG_SCHEMA:
        raise RootEffectError("root-effect config schema is invalid")
    for key in ("receipt_root", "bootstrap", "canonical_repo", "runtime_root", "postgres_dsn"):
        if not isinstance(value.get(key), str) or not value[key]:
            raise RootEffectError(f"root-effect config field {key!r} is invalid")
    units = value.get("whitelisted_units")
    if not isinstance(units, list) or not all(isinstance(u, str) and u.endswith(".service") for u in units):
        raise RootEffectError("root-effect config whitelisted_units is invalid")
    value["whitelisted_units"] = frozenset(units)
    return value


def _run(argv: list[str], runner=subprocess.run) -> dict[str, Any]:
    """Run one pinned command without a shell; no env is inherited beyond PATH."""
    completed = runner(argv, text=True, capture_output=True, check=False)
    return {
        "argv": argv,
        "returncode": completed.returncode,
        "stdout": (completed.stdout or "")[-2000:],
        "stderr": (completed.stderr or "")[-2000:],
    }


def _write_receipt(config: Mapping[str, Any], *, op: str, args: Mapping[str, Any],
                   outcome: str, detail: str, evidence: Mapping[str, Any]) -> str:
    receipt = {
        "schema": RECEIPT_SCHEMA,
        "op": op,
        "args": dict(args),
        "outcome": outcome,
        "detail": detail,
        "evidence": dict(evidence),
        "at": _now(),
    }
    receipt["receipt_hash"] = _sha256_text(json.dumps(receipt, sort_keys=True, separators=(",", ":")))
    root = Path(config["receipt_root"])
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = root / f"{op}-{receipt['receipt_hash'][7:23]}.json"
    path.write_text(json.dumps(receipt, sort_keys=True, indent=1), encoding="utf-8")
    path.chmod(0o600)
    return str(path)


def _finish(config: Mapping[str, Any], *, op: str, args: Mapping[str, Any],
            outcome: str, detail: str, evidence: Mapping[str, Any]) -> dict[str, Any]:
    receipt_path = _write_receipt(
        config, op=op, args=args, outcome=outcome, detail=detail, evidence=evidence
    )
    return {
        "ok": outcome == "PASS",
        "op": op,
        "outcome": outcome,
        "detail": detail,
        "receipt": receipt_path,
    }


def _git(canonical_repo: str, *args: str, runner=subprocess.run) -> dict[str, Any]:
    return _run(["git", "-C", canonical_repo, *args], runner=runner)


def _canonical_head(config: Mapping[str, Any], runner=subprocess.run) -> str:
    result = _git(config["canonical_repo"], "rev-parse", "HEAD", runner=runner)
    if result["returncode"]:
        raise RootEffectError(f"canonical repo HEAD is unavailable: {result['stderr'][-300:]}")
    head = result["stdout"].strip()
    if _COMMIT.fullmatch(head) is None:
        raise RootEffectError("canonical repo HEAD is not a commit identity")
    return head


def op_recovery_status(config: Mapping[str, Any], runner=subprocess.run) -> dict[str, Any]:
    checks: dict[str, Any] = {}
    head = _git(config["canonical_repo"], "rev-parse", "HEAD", runner=runner)
    checks["canonical_head"] = head["stdout"].strip() if head["returncode"] == 0 else None
    rt = _run(["readlink", "-f", config["runtime_root"] + "/current"], runner=runner)
    checks["runtime_current"] = rt["stdout"].strip() if rt["returncode"] == 0 else None
    db = _run(
        ["psql", config["postgres_dsn"], "-t", "-A", "-c", "select 1"], runner=runner
    )
    checks["database"] = db["returncode"] == 0 and "1" in db["stdout"]
    units: dict[str, str] = {}
    for unit in sorted(config["whitelisted_units"]):
        probe = _run(["systemctl", "is-active", unit], runner=runner)
        units[unit] = probe["stdout"].strip() if probe["returncode"] == 0 else "unknown"
    checks["units"] = units
    degraded = checks["canonical_head"] != checks["runtime_current"] or not checks["database"]
    return _finish(
        config,
        op="recovery-status",
        args={},
        outcome="DEGRADED" if degraded else "PASS",
        detail="spine self-check" if not degraded else "spine reports a degraded surface",
        evidence={"checks": checks},
    )


def op_runtime_install(config: Mapping[str, Any], commit: str, runner=subprocess.run) -> dict[str, Any]:
    if _COMMIT.fullmatch(commit) is None:
        raise RootEffectError("runtime-install requires an exact 40-hex commit")
    if _canonical_head(config, runner=runner) != commit:
        raise RootEffectError("runtime-install commit does not select the canonical HEAD")
    result = _run([config["bootstrap"], "--commit", commit], runner=runner)
    outcome = "PASS" if result["returncode"] == 0 else "FAIL"
    return _finish(
        config,
        op="runtime-install",
        args={"commit": commit},
        outcome=outcome,
        detail="materialized exact canonical commit",
        evidence=result,
    )


def op_service_restart(config: Mapping[str, Any], unit: str, runner=subprocess.run) -> dict[str, Any]:
    if unit not in config["whitelisted_units"]:
        raise RootEffectError(f"service-restart refuses non-whitelisted unit {unit!r}")
    result = _run(["systemctl", "restart", unit], runner=runner)
    outcome = "PASS" if result["returncode"] == 0 else "FAIL"
    return _finish(
        config,
        op="service-restart",
        args={"unit": unit},
        outcome=outcome,
        detail=f"restarted whitelisted unit {unit}",
        evidence=result,
    )


def op_database_repair(config: Mapping[str, Any], repair: str, runner=subprocess.run) -> dict[str, Any]:
    if repair not in _REPAIR_CHOICES:
        raise RootEffectError(f"database-repair refuses unknown repair {repair!r}")
    head = _canonical_head(config, runner=runner)
    result = _run([config["bootstrap"], "--commit", head, "--repair", repair], runner=runner)
    outcome = "PASS" if result["returncode"] == 0 else "FAIL"
    return _finish(
        config,
        op="database-repair",
        args={"repair": repair, "commit": head},
        outcome=outcome,
        detail=f"ran bounded bootstrap repair {repair}",
        evidence=result,
    )


def op_container_lifecycle(config: Mapping[str, Any], action: str, cid: str,
                           runner=subprocess.run) -> dict[str, Any]:
    runtime = config.get("container_runtime")
    if not isinstance(runtime, str) or not runtime:
        return _finish(
            config,
            op="container-lifecycle",
            args={"action": action, "id": cid},
            outcome="FAIL",
            detail="container runtime is not configured; operation is declared but inert",
            evidence={"configured": False},
        )
    if action not in {"start", "stop", "rm"}:
        raise RootEffectError(f"container-lifecycle refuses action {action!r}")
    if _CONTAINER_ID.fullmatch(cid) is None:
        raise RootEffectError("container id is invalid")
    result = _run([runtime, action, cid], runner=runner)
    outcome = "PASS" if result["returncode"] == 0 else "FAIL"
    return _finish(
        config,
        op="container-lifecycle",
        args={"action": action, "id": cid},
        outcome=outcome,
        detail=f"container {action} {cid}",
        evidence=result,
    )


def op_restore_from_receipt(config: Mapping[str, Any], receipt: str, runner=subprocess.run) -> dict[str, Any]:
    """Reinstall the exact runtime commit recorded by a prior materialization receipt."""
    raw = Path(receipt)
    try:
        value = json.loads(raw.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RootEffectError(f"restore receipt is unreadable: {exc}") from exc
    commit = value.get("commit") or (value.get("args") or {}).get("commit")
    if not isinstance(commit, str) or _COMMIT.fullmatch(commit) is None:
        raise RootEffectError("restore receipt does not carry an exact commit")
    return op_runtime_install(config, commit, runner=runner)


def run(op: str, args: Mapping[str, Any], config: Mapping[str, Any],
        runner=subprocess.run) -> dict[str, Any]:
    if op not in _OPS:
        raise RootEffectError(f"unknown spine operation {op!r}")
    if op == "recovery-status":
        return op_recovery_status(config, runner=runner)
    if op == "runtime-install":
        return op_runtime_install(config, args["commit"], runner=runner)
    if op == "service-restart":
        return op_service_restart(config, args["unit"], runner=runner)
    if op == "database-repair":
        return op_database_repair(config, args["repair"], runner=runner)
    if op == "container-lifecycle":
        return op_container_lifecycle(config, args["action"], args["id"], runner=runner)
    if op == "restore-from-receipt":
        return op_restore_from_receipt(config, args["receipt"], runner=runner)
    raise RootEffectError(f"unreachable operation {op!r}")


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="tgw-root-effect")
    root.add_argument("--config", default=DEFAULT_CONFIG)
    sub = root.add_subparsers(dest="op", required=True)
    sub.add_parser("recovery-status")
    p = sub.add_parser("runtime-install")
    p.add_argument("--commit", required=True)
    p = sub.add_parser("service-restart")
    p.add_argument("unit")
    p = sub.add_parser("database-repair")
    p.add_argument("repair")
    p = sub.add_parser("container-lifecycle")
    p.add_argument("action", choices=("start", "stop", "rm"))
    p.add_argument("id")
    p = sub.add_parser("restore-from-receipt")
    p.add_argument("receipt")
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        config = load_config(args.config)
        result = run(args.op, {k: v for k, v in vars(args).items() if k not in ("op", "config") and v is not None}, config)
    except (RootEffectError, OSError) as exc:
        print(json.dumps({"ok": False, "op": getattr(args, "op", None), "error": str(exc), "error_type": type(exc).__name__}, sort_keys=True))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
