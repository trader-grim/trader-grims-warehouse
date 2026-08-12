import subprocess

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from tgw.review_egress_broker import ReviewEgressPolicy, verify_network_attestation
from tgw.review_egress_namespace import NamespaceError, Topology, collect_kernel_attestation, commands, execute


def test_topology_is_derived_only_from_bounded_run_identity():
    topology = Topology.for_run("abcdef123456")
    assert topology.namespace == "tgw-review-abcdef123456"
    assert topology.broker_port == 18443
    for invalid in ("ABCDEF123456", "short", "../../escape", "a" * 13):
        with pytest.raises(ValueError):
            Topology.for_run(invalid)


def test_prepare_has_fixed_namespace_uid_firewall_and_host_private_denials():
    value = commands(Topology.for_run("abcdef123456"), "prepare", broker_uid=972, worker_uid=973)
    rendered = "\n".join(" ".join(command) for command in value)
    assert "meta skuid 973 tcp dport 18443 accept" in rendered
    assert "meta skuid 972 tcp dport { 53, 443 } accept" in rendered
    assert "169.254.0.0/16" in rendered and "10.0.0.0/8" in rendered
    assert "masquerade" in rendered
    assert not any("sh" in command[:1] or "bash" in command[:1] for command in value)


def test_distinct_nonroot_service_identities_are_mandatory():
    topology = Topology.for_run("abcdef123456")
    for broker, worker in ((0, 973), (972, 0), (972, 972)):
        with pytest.raises(ValueError):
            commands(topology, "prepare", broker_uid=broker, worker_uid=worker)


def test_executor_uses_argv_without_shell_and_hashes_outputs():
    seen = []

    def invoke(argv, **kwargs):
        seen.append((list(argv), kwargs))
        return subprocess.CompletedProcess(argv, 0, "ok", "")

    receipts = execute("verify", Topology.for_run("abcdef123456"), broker_uid=972, worker_uid=973, invoke=invoke)
    assert len(receipts) == 3
    assert all("shell" not in kwargs for _, kwargs in seen)
    assert all(row["stdout_sha256"].startswith("sha256:") for row in receipts)


def test_install_contract_lists_positive_negative_rollback_and_no_installed_claim():
    import json
    from pathlib import Path

    contract = json.loads(Path("agent-services/catalogs/review-egress-install-v1.json").read_text())
    probes = " ".join(contract["verify"])
    requirements = (
        "exact allowlisted TLS succeeds",
        "direct public 443 denied",
        "metadata/private/link-local denied",
        "SNI mismatch denied",
        "mixed public/private DNS denied",
        "expiry limits terminate",
    )
    for requirement in requirements:
        assert requirement in probes
    assert contract["rollback"]
    assert contract["deployment_status"] == "NOT_INSTALLED"
    nix = Path("nix/review-egress.nix").read_text()
    broker_start = next(line for line in nix.splitlines() if "tgw-review-egress-broker" in line)
    assert "auth.json" not in broker_start and "--credential" not in broker_start
    assert "attestation.pub" in broker_start and "attestation.key" not in broker_start


def test_privileged_kernel_attestation_derives_live_evidence_and_is_asymmetrically_bound():
    topology = Topology.for_run("abcdef123456")
    policy = ReviewEgressPolicy.parse(
        {
            "run_id": topology.run_id,
            "allowed_hosts": ["chatgpt.com"],
            "expires_unix": 200,
            "max_connections": 2,
            "max_bytes_each_direction": 1000,
            "runtime_sha256": "sha256:" + "a" * 64,
            "credential_sha256": "sha256:" + "b" * 64,
        }
    )
    private = Ed25519PrivateKey.generate()
    private_bytes = private.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())
    public_bytes = private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)

    def invoke(argv, **kwargs):
        positive = str(topology.broker_port) in argv
        return subprocess.CompletedProcess(argv, 0 if positive or "nc" not in argv else 1, "live-kernel-readback", "")

    attestation = collect_kernel_attestation(
        run_id=topology.run_id,
        policy_hash=policy.policy_hash,
        topology=topology,
        broker_pid=123,
        issued_unix=100,
        expires_unix=150,
        nonce="run-unique-nonce",
        private_key=private_bytes,
        invoke=invoke,
    )
    assert verify_network_attestation(attestation, policy, public_bytes, now=120)["namespace"] == topology.namespace
    tampered = dict(attestation, namespace="other")
    with pytest.raises(Exception, match="signature"):
        verify_network_attestation(tampered, policy, public_bytes, now=120)
    with pytest.raises(Exception, match="expired"):
        verify_network_attestation(attestation, policy, public_bytes, now=151)
    with pytest.raises(NamespaceError):
        collect_kernel_attestation(
            run_id=topology.run_id,
            policy_hash=policy.policy_hash,
            topology=topology,
            broker_pid=123,
            issued_unix=100,
            expires_unix=150,
            nonce="n",
            private_key=private_bytes,
            invoke=lambda argv, **kwargs: subprocess.CompletedProcess(argv, 1, "", "failed"),
        )
