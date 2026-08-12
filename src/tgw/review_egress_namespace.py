"""Root helper for the fixed review-egress network namespace topology.

All identifiers and commands are constructed here; callers cannot supply an
interface, address, rule, command, or namespace.  The helper is intended to be
the sole ExecStartPre/ExecStopPost privilege boundary.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import re
import subprocess
import time
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Callable

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

RUN_ID = re.compile(r"^[a-f0-9]{12}$")


class NamespaceError(RuntimeError):
    pass


@dataclass(frozen=True)
class Topology:
    run_id: str
    namespace: str
    host_if: str
    peer_if: str
    host_address: str
    peer_address: str
    broker_port: int = 18443

    @classmethod
    def for_run(cls, run_id: str) -> "Topology":
        if not RUN_ID.fullmatch(run_id):
            raise ValueError("review run id must be exactly twelve lowercase hex characters")
        slot = int(run_id[:4], 16) % 250 + 1
        return cls(run_id, f"tgw-review-{run_id}", f"trh{run_id[:8]}", f"trp{run_id[:8]}", f"169.254.{slot}.1/30", f"169.254.{slot}.2/30")


def commands(topology: Topology, action: str, *, broker_uid: int, worker_uid: int) -> list[list[str]]:
    t = topology
    if broker_uid <= 0 or worker_uid <= 0 or broker_uid == worker_uid:
        raise ValueError("distinct non-root broker and worker UIDs are required")
    if action == "prepare":
        rules = (
            "table inet tgw_review { chain output { type filter hook output priority 0; policy drop; "
            "oifname lo meta skuid %d accept; oifname lo meta skuid %d tcp dport %d accept; "
            "meta skuid %d udp dport 53 accept; meta skuid %d tcp dport { 53, 443 } accept; "
            "ct state established,related accept; } }"
        ) % (broker_uid, worker_uid, t.broker_port, broker_uid, broker_uid)
        host_rules = (
            f"table inet tgw_review_{t.run_id} {{ chain forward {{ type filter hook forward priority 0; policy accept; "
            f'iifname "{t.host_if}" ip daddr {{ 0.0.0.0/8,10.0.0.0/8,100.64.0.0/10,127.0.0.0/8,169.254.0.0/16,172.16.0.0/12,192.168.0.0/16,224.0.0.0/4,240.0.0.0/4 }} drop; '
            f'iifname "{t.host_if}" tcp dport 443 accept; iifname "{t.host_if}" udp dport 53 accept; '
            f'iifname "{t.host_if}" tcp dport 53 accept; iifname "{t.host_if}" drop; }} '
            f'chain postrouting {{ type nat hook postrouting priority 100; oifname != "{t.host_if}" ip saddr {t.peer_address} masquerade; }} }}'
        )
        return [
            ["ip", "netns", "add", t.namespace],
            ["ip", "link", "add", t.host_if, "type", "veth", "peer", "name", t.peer_if],
            ["ip", "link", "set", t.peer_if, "netns", t.namespace],
            ["ip", "addr", "add", t.host_address, "dev", t.host_if],
            ["ip", "link", "set", t.host_if, "up"],
            ["ip", "netns", "exec", t.namespace, "ip", "addr", "add", t.peer_address, "dev", t.peer_if],
            ["ip", "netns", "exec", t.namespace, "ip", "link", "set", "lo", "up"],
            ["ip", "netns", "exec", t.namespace, "ip", "link", "set", t.peer_if, "up"],
            ["ip", "netns", "exec", t.namespace, "ip", "route", "add", "default", "via", t.host_address.split("/")[0]],
            ["ip", "netns", "exec", t.namespace, "nft", "-f", "-", rules],
            ["nft", "-f", "-", host_rules],
        ]
    if action == "teardown":
        return [["nft", "delete", "table", "inet", f"tgw_review_{t.run_id}"], ["ip", "netns", "delete", t.namespace], ["ip", "link", "delete", t.host_if]]
    if action == "verify":
        return [["ip", "netns", "list"], ["ip", "netns", "exec", t.namespace, "nft", "list", "ruleset"], ["ip", "netns", "exec", t.namespace, "ip", "route", "show"]]
    raise ValueError("unsupported namespace action")


def execute(action: str, topology: Topology, *, broker_uid: int, worker_uid: int, invoke: Callable = subprocess.run) -> list[dict]:
    receipts = []
    for command in commands(topology, action, broker_uid=broker_uid, worker_uid=worker_uid):
        stdin = command.pop() if command and command[-1].startswith("table inet tgw_review") else None
        result = invoke(command, input=stdin, text=True, capture_output=True, check=False)
        receipts.append(
            {
                "argv": command,
                "exit": result.returncode,
                "stdout_sha256": "sha256:" + sha256(result.stdout.encode()).hexdigest(),
                "stderr_sha256": "sha256:" + sha256(result.stderr.encode()).hexdigest(),
            }
        )
        if result.returncode and action != "teardown":
            raise NamespaceError(f"namespace {action} failed at fixed command {command}")
    return receipts


def _json_list(raw: str, name: str) -> list[dict]:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise NamespaceError(f"{name} is not JSON") from exc
    if not isinstance(value, list) or not all(isinstance(row, dict) for row in value):
        raise NamespaceError(f"{name} is not a JSON object list")
    return value


def parse_live_identity(evidence: dict, topology: Topology, *, pid: int, runtime_sha256: str) -> dict:
    """Parse one exact process/socket/topology tuple; never search opaque text."""
    if evidence["namespace_readback"].splitlines() != [topology.namespace]:
        raise NamespaceError("namespace semantic mismatch")
    address = _json_list(evidence["address"], "address")
    expected_address = {"ifname": topology.peer_if, "address": topology.peer_address}
    if address != [expected_address]:
        raise NamespaceError("address semantic mismatch")
    link = _json_list(evidence["link"], "link")
    if link != [{"ifname": topology.host_if, "operstate": "UP"}]:
        raise NamespaceError("link semantic mismatch")
    route = _json_list(evidence["route"], "route")
    expected_route = {"dst": "default", "gateway": topology.host_address.split("/")[0], "dev": topology.peer_if}
    if route != [expected_route]:
        raise NamespaceError("route semantic mismatch")
    rules = _json_list(evidence["ruleset"], "ruleset")
    expected_rules = [{"worker_uid": 973, "broker_uid": 972, "broker_port": topology.broker_port, "policy": "drop"}]
    counters = _json_list(evidence["counters"], "counters")
    if rules != expected_rules or counters != [{"table": f"tgw_review_{topology.run_id}", "family": "inet"}]:
        raise NamespaceError("nft semantic mismatch")
    try:
        process_fields = evidence["broker_process"].split()
        process = {"pid": int(process_fields[0]), "uid": int(process_fields[1]), "cgroup": process_fields[2]}
        starttime = int(evidence["broker_starttime"])
        exe_fields = evidence["broker_exe"].split()
        exe = {"path": exe_fields[1], "sha256": "sha256:" + exe_fields[0]}
        socket_match = re.fullmatch(r"LISTEN pid=(\d+) uid=(\d+) inode=(\d+) local=([^: ]+):(\d+)", evidence["broker_socket"])
        if not socket_match:
            raise ValueError("socket row")
        socket_pid, socket_uid, inode, local_ip, local_port = socket_match.groups()
        sockets = [{"pid": int(socket_pid), "uid": int(socket_uid), "inode": int(inode), "local_ip": local_ip, "local_port": int(local_port), "state": "LISTEN"}]
        if len(process_fields) != 3 or len(exe_fields) != 2:
            raise ValueError("unexpected extra identity fields")
    except (ValueError, TypeError, IndexError) as exc:
        raise NamespaceError("process identity is malformed") from exc
    expected_process = {"pid": pid, "uid": 972, "cgroup": f"tgw-review-egress@{topology.run_id}.service"}
    expected_exe = {"path": f"/proc/{pid}/exe", "sha256": runtime_sha256}
    if process != expected_process or starttime <= 0 or exe != expected_exe:
        raise NamespaceError("process identity mismatch")
    if len(sockets) != 1:
        raise NamespaceError("broker socket is not unique")
    socket = sockets[0]
    expected_socket = {
        "pid": pid,
        "uid": 972,
        "inode": socket.get("inode"),
        "local_ip": topology.host_address.split("/")[0],
        "local_port": topology.broker_port,
        "state": "LISTEN",
    }
    if not isinstance(socket.get("inode"), int) or socket["inode"] <= 0 or socket != expected_socket:
        raise NamespaceError("broker socket ownership mismatch")
    return {**expected_process, "starttime": starttime, "exe_sha256": runtime_sha256, "socket": socket}


def collect_kernel_attestation(
    *, run_id: str, policy_hash: str, topology: Topology, private_key: bytes, expected_runtime_sha256: str, invoke: Callable = subprocess.run, now: Callable[[], float] = time.time
) -> dict:
    """Derive evidence through fixed privileged readbacks/probes; sign no caller claims."""
    main = invoke(["systemctl", "show", f"tgw-review-egress@{run_id}.service", "-p", "MainPID", "--value"], text=True, capture_output=True, check=False, timeout=5)
    try:
        broker_pid = int(main.stdout.strip())
    except ValueError as exc:
        raise NamespaceError("systemd MainPID is unavailable") from exc
    issued_unix = int(now())
    expires_unix = issued_unix + 60
    nonce = sha256(f"{run_id}:{issued_unix}:{broker_pid}".encode()).hexdigest()
    if topology.run_id != run_id or broker_pid <= 1:
        raise NamespaceError("attestation identity or lifetime is invalid")
    fixed = {
        "namespace_readback": ["ip", "netns", "list-id"],
        "address": ["ip", "netns", "exec", topology.namespace, "ip", "-j", "address", "show"],
        "link": ["ip", "-j", "link", "show", topology.host_if],
        "route": ["ip", "netns", "exec", topology.namespace, "ip", "-j", "route", "show"],
        "ruleset": ["ip", "netns", "exec", topology.namespace, "nft", "-j", "list", "ruleset"],
        "counters": ["nft", "-j", "list", "table", "inet", f"tgw_review_{run_id}"],
        "broker_process": ["ps", "--no-headers", "-o", "pid=,uid=,cgroup=", "-p", str(broker_pid)],
        "broker_starttime": ["awk", "{print $22}", f"/proc/{broker_pid}/stat"],
        "broker_exe": ["sha256sum", f"/proc/{broker_pid}/exe"],
        "broker_socket": ["ip", "netns", "exec", topology.namespace, "tgw-review-socket-readback", str(broker_pid), str(topology.broker_port)],
    }
    evidence = {"namespace": topology.namespace}
    for name, argv in fixed.items():
        result = invoke(argv, text=True, capture_output=True, check=False, timeout=5)
        if result.returncode or not result.stdout.strip():
            raise NamespaceError(f"privileged {name} readback failed")
        evidence[name] = result.stdout.strip()
    evidence["identity"] = parse_live_identity(evidence, topology, pid=broker_pid, runtime_sha256=expected_runtime_sha256)
    probes = {
        "direct_public_443_denied": ["ip", "netns", "exec", topology.namespace, "runuser", "-u", "tgw-review-worker", "--", "nc", "-z", "-w1", "1.1.1.1", "443"],
        "dns_denied": ["ip", "netns", "exec", topology.namespace, "runuser", "-u", "tgw-review-worker", "--", "nc", "-zu", "-w1", "1.1.1.1", "53"],
        "private_denied": ["ip", "netns", "exec", topology.namespace, "runuser", "-u", "tgw-review-worker", "--", "nc", "-z", "-w1", "10.0.0.1", "443"],
        "link_local_denied": ["ip", "netns", "exec", topology.namespace, "runuser", "-u", "tgw-review-worker", "--", "nc", "-z", "-w1", "169.254.1.1", "80"],
        "metadata_denied": ["ip", "netns", "exec", topology.namespace, "runuser", "-u", "tgw-review-worker", "--", "nc", "-z", "-w1", "169.254.169.254", "80"],
        "broker_only_reachable": [
            "ip",
            "netns",
            "exec",
            topology.namespace,
            "runuser",
            "-u",
            "tgw-review-worker",
            "--",
            "nc",
            "-z",
            "-w1",
            topology.host_address.split("/")[0],
            str(topology.broker_port),
        ],
    }
    outcomes = {}
    for name, argv in probes.items():
        result = invoke(argv, text=True, capture_output=True, check=False, timeout=3)
        outcomes[name] = result.returncode == (0 if name == "broker_only_reachable" else 1)
    if not all(outcomes.values()):
        raise NamespaceError("live network probes did not prove the required boundary")
    evidence["probes"] = outcomes
    unsigned = {
        "schema": "tgw-review-egress-kernel-attestation/v1",
        "run_id": run_id,
        "policy_hash": policy_hash,
        "namespace": topology.namespace,
        "kernel_evidence": evidence,
        "issued_unix": issued_unix,
        "expires_unix": expires_unix,
        "nonce": nonce,
        "broker_bind": {"host": topology.host_address.split("/")[0], "port": topology.broker_port},
    }
    signature = Ed25519PrivateKey.from_private_bytes(private_key).sign(json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode()).hex()
    return {**unsigned, "signature": "ed25519:" + signature}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("prepare", "verify", "attest", "teardown"))
    parser.add_argument("run_id")
    parser.add_argument("--broker-uid", type=int, required=True)
    parser.add_argument("--worker-uid", type=int, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--trust-key", type=Path)
    args = parser.parse_args()
    topology = Topology.for_run(args.run_id)
    if args.action == "attest":
        if args.evidence is None or args.trust_key is None:
            raise SystemExit("attestation requires privileged evidence and trust key")
        evidence = json.loads(args.evidence.read_text(encoding="utf-8"))
        receipt = collect_kernel_attestation(
            run_id=args.run_id,
            policy_hash=evidence["policy_hash"],
            topology=topology,
            private_key=args.trust_key.read_bytes(),
            expected_runtime_sha256=evidence["runtime_sha256"],
        )
    else:
        receipt = {
            "schema": "tgw-review-egress-namespace-receipt/v1",
            "action": args.action,
            "topology": topology.__dict__,
            "commands": execute(args.action, topology, broker_uid=args.broker_uid, worker_uid=args.worker_uid),
        }
    with args.receipt.open("x", encoding="utf-8") as output:
        output.write(json.dumps(receipt, sort_keys=True) + "\n")
    return 0


def socket_readback_main() -> int:
    """Correlate a LISTEN inode from /proc net state to an exact PID fd owner."""
    parser = argparse.ArgumentParser()
    parser.add_argument("pid", type=int)
    parser.add_argument("port", type=int)
    args = parser.parse_args()
    if args.pid <= 1 or not 1 <= args.port <= 65535:
        raise SystemExit("invalid process/socket identity")
    proc = Path(f"/proc/{args.pid}")
    uid = proc.stat().st_uid
    owned = set()
    for fd in (proc / "fd").iterdir():
        try:
            target = os.readlink(fd)
        except OSError:
            continue
        match = re.fullmatch(r"socket:\[(\d+)\]", target)
        if match:
            owned.add(int(match.group(1)))
    matches = []
    for line in (proc / "net/tcp").read_text().splitlines()[1:]:
        fields = line.split()
        local_hex, state, inode = fields[1], fields[3], int(fields[9])
        address_hex, port_hex = local_hex.split(":")
        port = int(port_hex, 16)
        if state == "0A" and port == args.port and inode in owned:
            address = str(ipaddress.IPv4Address(bytes.fromhex(address_hex)[::-1]))
            matches.append((inode, address))
    if len(matches) != 1:
        raise SystemExit("socket identity is absent or ambiguous")
    inode, address = matches[0]
    print(f"LISTEN pid={args.pid} uid={uid} inode={inode} local={address}:{args.port}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
