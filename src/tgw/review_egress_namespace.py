"""Root helper for the fixed review-egress network namespace topology.

All identifiers and commands are constructed here; callers cannot supply an
interface, address, rule, command, or namespace.  The helper is intended to be
the sole ExecStartPre/ExecStopPost privilege boundary.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Callable

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
        receipts.append({
            "argv": command,
            "exit": result.returncode,
            "stdout_sha256": "sha256:" + sha256(result.stdout.encode()).hexdigest(),
            "stderr_sha256": "sha256:" + sha256(result.stderr.encode()).hexdigest(),
        })
        if result.returncode and action != "teardown":
            raise NamespaceError(f"namespace {action} failed at fixed command {command}")
    return receipts


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("prepare", "verify", "teardown"))
    parser.add_argument("run_id")
    parser.add_argument("--broker-uid", type=int, required=True)
    parser.add_argument("--worker-uid", type=int, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    topology = Topology.for_run(args.run_id)
    receipt = {
        "schema": "tgw-review-egress-namespace-receipt/v1",
        "action": args.action,
        "topology": topology.__dict__,
        "commands": execute(args.action, topology, broker_uid=args.broker_uid, worker_uid=args.worker_uid),
    }
    with args.receipt.open("x", encoding="utf-8") as output:
        output.write(json.dumps(receipt, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
