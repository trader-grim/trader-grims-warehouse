import hashlib
import json
import socket
import struct
import time

import pytest

from tgw.review_egress_broker import BrokerError, ReviewEgressPolicy, audit_receipt, parse_connect, resolve_public, tls_client_hello_sni


def _policy(**changes):
    value = {
        "run_id": "review:candidate-1", "allowed_hosts": ["auth.openai.com", "chatgpt.com"],
        "expires_unix": int(time.time()) + 300, "max_connections": 4,
        "max_bytes_each_direction": 10_000_000,
        "runtime_sha256": "sha256:" + "a" * 64, "credential_sha256": "sha256:" + "b" * 64,
    }
    value.update(changes)
    return ReviewEgressPolicy.parse(value)


def _hello(host):
    name = host.encode()
    sni = b"\x00\x00" + struct.pack("!H", 5 + len(name)) + struct.pack("!H", 3 + len(name)) + b"\x00" + struct.pack("!H", len(name)) + name
    body = b"\x03\x03" + b"x" * 32 + b"\x00" + b"\x00\x02\x13\x01" + b"\x01\x00" + struct.pack("!H", len(sni)) + sni
    handshake = b"\x01" + len(body).to_bytes(3, "big") + body
    return b"\x16\x03\x01" + len(handshake).to_bytes(2, "big") + handshake


def test_policy_is_exact_hash_bound_and_rejects_generic_allowlists():
    policy = _policy()
    assert policy.policy_hash.startswith("sha256:")
    with pytest.raises(BrokerError, match="wildcards"):
        _policy(allowed_hosts=["*.openai.com"])
    with pytest.raises(BrokerError, match="positive"):
        _policy(max_connections=0)


def test_connect_is_https_exact_host_only_and_forbids_proxy_identity_headers():
    allowed = _policy().allowed_hosts
    assert parse_connect(b"CONNECT chatgpt.com:443 HTTP/1.1\r\nHost: chatgpt.com:443\r\n\r\n", allowed) == "chatgpt.com"
    for request in (
        b"CONNECT api.openai.com:443 HTTP/1.1\r\n\r\n",
        b"GET https://chatgpt.com/ HTTP/1.1\r\n\r\n",
        b"CONNECT chatgpt.com:80 HTTP/1.1\r\n\r\n",
        b"CONNECT chatgpt.com:443 HTTP/1.1\r\nProxy-Authorization: x\r\n\r\n",
    ):
        with pytest.raises(BrokerError):
            parse_connect(request, allowed)


def test_dns_resolution_rejects_any_private_or_metadata_answer_and_pins_public_ip():
    def public(*args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("203.0.113.9", 443))]
    # Documentation ranges are non-global, so use one IANA-global resolver result.
    def global_answer(*args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))]
    assert resolve_public("chatgpt.com", resolver=global_answer)[2] == "8.8.8.8"
    with pytest.raises(BrokerError, match="non-public"):
        resolve_public("chatgpt.com", resolver=lambda *a, **k: public() + [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", 443))])
    with pytest.raises(BrokerError, match="IP-literal"):
        resolve_public("127.0.0.1", resolver=global_answer)


def test_tls_sni_must_be_present_and_can_be_compared_to_connect_host():
    assert tls_client_hello_sni(_hello("chatgpt.com")) == "chatgpt.com"
    assert tls_client_hello_sni(_hello("auth.openai.com")) != "chatgpt.com"
    with pytest.raises(BrokerError):
        tls_client_hello_sni(b"not tls")


def test_runtime_and_credential_digests_expiry_and_audit_receipt(tmp_path):
    runtime, credential = tmp_path / "codex", tmp_path / "auth.json"
    runtime.write_bytes(b"runtime")
    credential.write_bytes(b"credential")
    policy = _policy(
        runtime_sha256="sha256:" + hashlib.sha256(b"runtime").hexdigest(),
        credential_sha256="sha256:" + hashlib.sha256(b"credential").hexdigest(),
    )
    policy.verify_runtime(runtime, credential)
    receipt = audit_receipt(policy, [{"host": "chatgpt.com", "outcome": "completed"}])
    assert receipt["receipt_hash"].startswith("sha256:")
    assert json.dumps(receipt, sort_keys=True)
    with pytest.raises(BrokerError, match="expired"):
        _policy(expires_unix=1).verify_runtime(runtime, credential)
