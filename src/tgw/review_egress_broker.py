"""Review-only HTTPS CONNECT broker with an exact, immutable run policy.

This broker is intentionally not a general proxy: it accepts CONNECT only,
requires TLS SNI to equal the requested allowlisted host, resolves once and
connects to that exact public address, and emits a bounded audit receipt.
Kernel routing remains a separately attested deployment boundary; see the
network contract in agent-services/catalogs.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import ipaddress
import json
import os
import socket
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


class BrokerError(ValueError):
    pass


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def file_sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _public(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    return ip.is_global and not any((ip.is_private, ip.is_loopback, ip.is_link_local, ip.is_multicast, ip.is_reserved, ip.is_unspecified))


@dataclass(frozen=True)
class ReviewEgressPolicy:
    run_id: str
    allowed_hosts: frozenset[str]
    expires_unix: int
    max_connections: int
    max_bytes_each_direction: int
    runtime_sha256: str
    credential_sha256: str

    @classmethod
    def parse(cls, value: Mapping[str, Any]) -> "ReviewEgressPolicy":
        required = {"run_id", "allowed_hosts", "expires_unix", "max_connections", "max_bytes_each_direction", "runtime_sha256", "credential_sha256"}
        if set(value) != required:
            raise BrokerError(f"policy fields must be exactly {sorted(required)}")
        hosts = value["allowed_hosts"]
        if not isinstance(hosts, list) or not hosts or any(not isinstance(host, str) or host != host.lower() or "." not in host for host in hosts):
            raise BrokerError("allowlist must contain exact lowercase DNS hosts")
        if any("*" in host or ":" in host or host.endswith(".") for host in hosts):
            raise BrokerError("wildcards, ports, and noncanonical hosts are forbidden")
        if not isinstance(value["run_id"], str) or not value["run_id"]:
            raise BrokerError("run identity is required")
        for key in ("expires_unix", "max_connections", "max_bytes_each_direction"):
            if not isinstance(value[key], int) or value[key] <= 0:
                raise BrokerError(f"{key} must be a positive integer")
        for key in ("runtime_sha256", "credential_sha256"):
            digest = value[key]
            if not isinstance(digest, str) or len(digest) != 71 or not digest.startswith("sha256:"):
                raise BrokerError(f"{key} is invalid")
        return cls(value["run_id"], frozenset(hosts), value["expires_unix"], value["max_connections"], value["max_bytes_each_direction"], value["runtime_sha256"], value["credential_sha256"])

    @property
    def policy_hash(self) -> str:
        value = {**self.__dict__, "allowed_hosts": sorted(self.allowed_hosts)}
        return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()

    def verify_runtime(self, runtime: Path, now: float | None = None) -> None:
        if (now or time.time()) >= self.expires_unix:
            raise BrokerError("review egress policy is expired")
        if file_sha256(runtime) != self.runtime_sha256:
            raise BrokerError("review runtime digest mismatch")
        # Credential digest is established by the privileged provisioner and
        # checked by the provider launcher. The network-capable broker must
        # never receive or read bearer credentials.


def verify_network_attestation(value: Mapping[str, Any], policy: ReviewEgressPolicy, public_key: bytes, *, now: int | None = None) -> Mapping[str, Any]:
    required = {"schema", "run_id", "policy_hash", "namespace", "kernel_evidence", "issued_unix", "expires_unix", "nonce", "broker_bind", "signature"}
    if set(value) != required:
        raise BrokerError("network attestation fields are invalid")
    unsigned = dict(value)
    claimed = unsigned.pop("signature")
    try:
        Ed25519PublicKey.from_public_bytes(public_key).verify(bytes.fromhex(str(claimed).removeprefix("ed25519:")), _canonical(unsigned))
    except (ValueError, InvalidSignature) as exc:
        raise BrokerError("network attestation signature is invalid") from exc
    current = int(time.time()) if now is None else now
    if value["schema"] != "tgw-review-egress-kernel-attestation/v1" or value["run_id"] != policy.run_id or value["policy_hash"] != policy.policy_hash:
        raise BrokerError("network attestation binding mismatch")
    if not isinstance(value["issued_unix"], int) or not isinstance(value["expires_unix"], int) or not (value["issued_unix"] <= current < value["expires_unix"] <= policy.expires_unix):
        raise BrokerError("network attestation is expired or future-issued")
    evidence = value["kernel_evidence"]
    if not isinstance(evidence, Mapping) or set(evidence) != {
        "namespace",
        "namespace_readback",
        "address",
        "link",
        "route",
        "ruleset",
        "counters",
        "broker_process",
        "broker_starttime",
        "broker_exe",
        "broker_socket",
        "identity",
        "probes",
    }:
        raise BrokerError("kernel evidence is incomplete")
    probes = evidence["probes"]
    required_probes = {"direct_public_443_denied", "dns_denied", "private_denied", "link_local_denied", "metadata_denied", "broker_only_reachable"}
    if not isinstance(probes, Mapping) or set(probes) != required_probes or any(result is not True for result in probes.values()):
        raise BrokerError("network negative probes are incomplete")
    if (
        evidence["namespace"] != value["namespace"]
        or not all(
            isinstance(evidence[key], str) and evidence[key]
            for key in ("namespace_readback", "address", "link", "route", "ruleset", "counters", "broker_process", "broker_starttime", "broker_exe", "broker_socket")
        )
        or not isinstance(evidence["identity"], Mapping)
        or not value["nonce"]
    ):
        raise BrokerError("network kernel identity is incomplete")
    return value


def resolve_public(host: str, *, resolver=socket.getaddrinfo) -> tuple[int, tuple[Any, ...], str]:
    """Resolve once and return one exact public socket address."""
    if not host or host[0].isdigit() or ":" in host:
        raise BrokerError("IP-literal CONNECT targets are forbidden")
    answers = resolver(host, 443, type=socket.SOCK_STREAM)
    candidates = []
    for family, socktype, proto, _, sockaddr in answers:
        address = sockaddr[0]
        if not _public(address):
            raise BrokerError("DNS answer includes a non-public address")
        candidates.append((family, socktype, proto, sockaddr, address))
    if not candidates:
        raise BrokerError("allowlisted host has no public address")
    family, _, _, sockaddr, address = sorted(candidates, key=lambda item: (item[4], item[0]))[0]
    return family, sockaddr, address


def parse_connect(header: bytes, allowed_hosts: frozenset[str]) -> str:
    if len(header) > 8192 or not header.endswith(b"\r\n\r\n"):
        raise BrokerError("invalid or oversized proxy header")
    try:
        lines = header.decode("ascii").split("\r\n")
        method, authority, version = lines[0].split(" ")
        host, port = authority.rsplit(":", 1)
    except (UnicodeDecodeError, ValueError) as exc:
        raise BrokerError("invalid CONNECT request") from exc
    if method != "CONNECT" or version != "HTTP/1.1" or port != "443":
        raise BrokerError("only HTTPS CONNECT to port 443 is permitted")
    if host not in allowed_hosts:
        raise BrokerError("CONNECT host is not exactly allowlisted")
    if any(line.lower().startswith(("proxy-authorization:", "forwarded:", "x-forwarded-")) for line in lines[1:] if line):
        raise BrokerError("proxy identity headers are forbidden")
    return host


def tls_client_hello_sni(data: bytes) -> str:
    """Extract one SNI DNS name from a bounded TLS ClientHello."""
    if len(data) < 9 or len(data) > 65536 or data[0] != 22:
        raise BrokerError("first tunnel payload is not a bounded TLS handshake")
    record_len = int.from_bytes(data[3:5], "big")
    if len(data) < 5 + record_len or data[5] != 1:
        raise BrokerError("TLS ClientHello is incomplete")
    body = memoryview(data)[9 : 5 + record_len]
    pos = 2 + 32
    if pos >= len(body):
        raise BrokerError("invalid ClientHello")
    pos += 1 + body[pos]
    if pos + 2 > len(body):
        raise BrokerError("invalid ClientHello")
    pos += 2 + int.from_bytes(body[pos : pos + 2], "big")
    if pos >= len(body):
        raise BrokerError("invalid ClientHello")
    pos += 1 + body[pos]
    if pos + 2 > len(body):
        raise BrokerError("ClientHello has no extensions")
    end = pos + 2 + int.from_bytes(body[pos : pos + 2], "big")
    pos += 2
    while pos + 4 <= end and end <= len(body):
        kind = int.from_bytes(body[pos : pos + 2], "big")
        size = int.from_bytes(body[pos + 2 : pos + 4], "big")
        pos += 4
        extension = body[pos : pos + size]
        pos += size
        if kind == 0 and len(extension) >= 5:
            name_len = int.from_bytes(extension[3:5], "big")
            try:
                return bytes(extension[5 : 5 + name_len]).decode("ascii").lower()
            except UnicodeDecodeError as exc:
                raise BrokerError("SNI is not ASCII") from exc
    raise BrokerError("TLS ClientHello has no SNI")


def audit_receipt(policy: ReviewEgressPolicy, sessions: list[Mapping[str, Any]]) -> dict[str, Any]:
    value = {"schema": "tgw-review-egress-receipt/v1", "run_id": policy.run_id, "policy_hash": policy.policy_hash, "sessions": sessions}
    return {**value, "receipt_hash": "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()}


async def _relay(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, limit: int) -> int:
    total = 0
    while True:
        chunk = await reader.read(min(65536, limit - total + 1))
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            raise BrokerError("review egress byte limit exceeded")
        writer.write(chunk)
        await writer.drain()
    try:
        writer.write_eof()
    except (AttributeError, OSError):
        writer.close()
    return total


async def handle_tunnel(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, policy: ReviewEgressPolicy, sessions: list[dict[str, Any]]) -> None:
    session: dict[str, Any] = {"outcome": "denied"}
    started = time.monotonic()
    try:
        if time.time() >= policy.expires_unix or len(sessions) >= policy.max_connections:
            raise BrokerError("review egress run bound is exhausted")
        header = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=5)
        host = parse_connect(header, policy.allowed_hosts)
        family, sockaddr, address = resolve_public(host)
        writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        await writer.drain()
        hello = await asyncio.wait_for(reader.read(65536), timeout=5)
        if tls_client_hello_sni(hello) != host:
            raise BrokerError("TLS SNI does not equal CONNECT host")
        upstream_reader, upstream_writer = await asyncio.open_connection(address, 443, family=family)
        upstream_writer.write(hello)
        await upstream_writer.drain()
        client_to_remote, remote_to_client = await asyncio.wait_for(
            asyncio.gather(
                _relay(reader, upstream_writer, policy.max_bytes_each_direction - len(hello)),
                _relay(upstream_reader, writer, policy.max_bytes_each_direction),
            ),
            timeout=max(1, policy.expires_unix - int(time.time())),
        )
        session.update({"outcome": "completed", "host": host, "resolved_ip": address, "bytes_out": client_to_remote + len(hello), "bytes_in": remote_to_client})
    except Exception as exc:
        session["reason"] = type(exc).__name__ + ":" + str(exc)
    finally:
        session["duration_ms"] = int((time.monotonic() - started) * 1000)
        sessions.append(session)
        writer.close()
        await writer.wait_closed()


async def serve(policy: ReviewEgressPolicy, bind_host: str, bind_port: int, receipt_path: Path, attestation_path: Path, public_key: bytes, ready_path: Path) -> None:
    sessions: list[dict[str, Any]] = []
    gated = True

    async def handler(reader, writer):
        if gated:
            writer.close()
            await writer.wait_closed()
            return
        await handle_tunnel(reader, writer, policy, sessions)

    server = await asyncio.start_server(handler, bind_host, bind_port)
    try:
        async with server:
            deadline = time.monotonic() + 60
            while not attestation_path.is_file() and time.monotonic() < deadline:
                await asyncio.sleep(0.05)
            if not attestation_path.is_file():
                raise BrokerError("privileged attestation did not arrive")
            attestation = json.loads(attestation_path.read_text())
            verify_network_attestation(attestation, policy, public_key)
            ready = {
                "schema": "tgw-review-egress-ready/v1",
                "run_id": policy.run_id,
                "policy_hash": policy.policy_hash,
                "attestation_signature": attestation["signature"],
                "broker_identity": attestation["kernel_evidence"]["identity"],
                "broker_bind": {"host": bind_host, "port": bind_port},
            }
            with ready_path.open("x") as output:
                json.dump(ready, output, sort_keys=True)
            gated = False
            await server.serve_forever()
    finally:
        with receipt_path.open("xb") as receipt:
            receipt.write(_canonical(audit_receipt(policy, sessions)) + b"\n")


def load_policy(path: Path) -> ReviewEgressPolicy:
    return ReviewEgressPolicy.parse(json.loads(path.read_text(encoding="utf-8")))


def main() -> int:
    parser = argparse.ArgumentParser(prog="tgw-review-egress-broker")
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--verify-runtime", type=Path, required=True)
    parser.add_argument("--network-attestation", type=Path, required=True)
    parser.add_argument("--attestation-public-key", type=Path, required=True)
    parser.add_argument("--ready", type=Path, required=True)
    parser.add_argument("--receipt", type=Path)
    args = parser.parse_args()
    policy = load_policy(args.policy)
    policy.verify_runtime(args.verify_runtime)
    if args.receipt is None:
        raise SystemExit("receipt required")
    host = os.environ.get("TGW_REVIEW_BROKER_BIND", "169.254.1.1")
    port = int(os.environ.get("TGW_REVIEW_BROKER_PORT", "18443"))
    asyncio.run(serve(policy, host, port, args.receipt, args.network_attestation, args.attestation_public_key.read_bytes(), args.ready))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
