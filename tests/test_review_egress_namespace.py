import subprocess

import pytest

from tgw.review_egress_namespace import Topology, commands, execute


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
        "exact allowlisted TLS succeeds", "direct public 443 denied",
        "metadata/private/link-local denied", "SNI mismatch denied",
        "mixed public/private DNS denied", "expiry limits terminate",
    )
    for requirement in requirements:
        assert requirement in probes
    assert contract["rollback"]
    assert contract["deployment_status"] == "NOT_INSTALLED"
