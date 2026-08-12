import json
import subprocess

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from tgw.review_egress_broker import ReviewEgressPolicy, verify_network_attestation
from tgw.review_egress_namespace import NamespaceError, Topology, collect_kernel_attestation, commands, execute


def nft_fixtures(t):
    def match(left, right, op="=="):
        return {"match": {"op": op, "left": left, "right": right}}

    def meta(key):
        return {"meta": {"key": key}}

    def payload(protocol, field):
        return {"payload": {"protocol": protocol, "field": field}}

    ns_exprs = [
        [match(meta("oifname"), "lo"), match(meta("skuid"), 972), {"accept": None}],
        [match(meta("oifname"), "lo"), match(meta("skuid"), 973), match(payload("tcp", "dport"), 18443), {"accept": None}],
        [match(meta("skuid"), 972), match(payload("udp", "dport"), 53), {"accept": None}],
        [match(meta("skuid"), 972), match(payload("tcp", "dport"), [53, 443], "in"), {"accept": None}],
        [match({"ct": {"key": "state"}}, ["established", "related"], "in"), {"accept": None}],
    ]
    private = ["0.0.0.0/8", "10.0.0.0/8", "100.64.0.0/10", "127.0.0.0/8", "169.254.0.0/16", "172.16.0.0/12", "192.168.0.0/16", "224.0.0.0/4", "240.0.0.0/4"]
    host_exprs = [
        [match(meta("iifname"), t.host_if), match(payload("ip", "daddr"), private, "in"), {"drop": None}],
        [match(meta("iifname"), t.host_if), match(payload("tcp", "dport"), 443), {"accept": None}],
        [match(meta("iifname"), t.host_if), match(payload("udp", "dport"), 53), {"accept": None}],
        [match(meta("iifname"), t.host_if), match(payload("tcp", "dport"), 53), {"accept": None}],
        [match(meta("iifname"), t.host_if), {"drop": None}],
        [match(meta("oifname"), t.host_if, "!="), match(payload("ip", "saddr"), t.peer_address), {"masquerade": None}],
    ]
    ns = {
        "nftables": [
            {"metainfo": {"json_schema_version": 1}},
            {"table": {"family": "inet", "name": "tgw_review"}},
            {"chain": {"family": "inet", "table": "tgw_review", "name": "output", "type": "filter", "hook": "output", "prio": 0, "policy": "drop"}},
        ]
        + [{"rule": {"family": "inet", "table": "tgw_review", "chain": "output", "expr": expr}} for expr in ns_exprs]
    }
    name = f"tgw_review_{t.run_id}"
    host = {
        "nftables": [
            {"table": {"family": "inet", "name": name}},
            {"chain": {"family": "inet", "table": name, "name": "forward", "type": "filter", "hook": "forward", "prio": 0, "policy": "accept"}},
            {"chain": {"family": "inet", "table": name, "name": "postrouting", "type": "nat", "hook": "postrouting", "prio": 100}},
        ]
        + [{"rule": {"family": "inet", "table": name, "chain": "postrouting" if i == 5 else "forward", "expr": expr}} for i, expr in enumerate(host_exprs)]
    }
    return ns, host


def test_topology_is_derived_only_from_bounded_run_identity():
    topology = Topology.for_run("abcdef123456")
    namespace_nft, host_nft = nft_fixtures(topology)
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
    namespace_nft, host_nft = nft_fixtures(topology)

    def invoke(argv, **kwargs):
        if argv[0] == "systemctl":
            return subprocess.CompletedProcess(argv, 0, "123\n", "")
        positive = str(topology.broker_port) in argv
        if "nc" in argv:
            return subprocess.CompletedProcess(argv, 0 if positive else 1, "", "")
        key = " ".join(argv)
        outputs = {
            "ip netns list-id": topology.namespace,
            f"ip netns exec {topology.namespace} ip -j address show dev {topology.peer_if}": json.dumps(
                [
                    {
                        "ifindex": 2,
                        "ifname": topology.peer_if,
                        "flags": ["BROADCAST", "UP", "LOWER_UP"],
                        "addr_info": [{"family": "inet", "local": topology.peer_address.split("/")[0], "prefixlen": 30, "scope": "global"}],
                    }
                ]
            ),
            f"ip -j link show {topology.host_if}": json.dumps([{"ifindex": 3, "ifname": topology.host_if, "flags": ["UP", "LOWER_UP"], "operstate": "UP"}]),
            f"ip netns exec {topology.namespace} ip -j route show": json.dumps(
                [{"dst": "default", "gateway": topology.host_address.split("/")[0], "dev": topology.peer_if, "protocol": "static", "flags": []}]
            ),
            f"ip netns exec {topology.namespace} nft -j list ruleset": json.dumps(namespace_nft),
            f"nft -j list table inet tgw_review_{topology.run_id}": json.dumps(host_nft),
            "ps --no-headers -o pid=,uid=,cgroup= -p 123": f"123 972 tgw-review-egress@{topology.run_id}.service",
            "awk {print $22} /proc/123/stat": "42",
            "sha256sum /proc/123/exe": "a" * 64 + " /proc/123/exe",
            f"ip netns exec {topology.namespace} tgw-review-socket-readback 123 {topology.broker_port}": (
                f"LISTEN pid=123 uid=972 inode=9 local={topology.host_address.split('/')[0]}:{topology.broker_port}"
            ),
        }
        output = outputs[key]
        return subprocess.CompletedProcess(argv, 0, output, "")

    attestation = collect_kernel_attestation(
        run_id=topology.run_id,
        policy_hash=policy.policy_hash,
        topology=topology,
        private_key=private_bytes,
        expected_runtime_sha256="sha256:" + "a" * 64,
        invoke=invoke,
        now=lambda: 100,
    )
    assert verify_network_attestation(attestation, policy, public_bytes, now=120)["namespace"] == topology.namespace
    tampered = dict(attestation, namespace="other")
    with pytest.raises(Exception, match="signature"):
        verify_network_attestation(tampered, policy, public_bytes, now=120)
    with pytest.raises(Exception, match="expired"):
        verify_network_attestation(attestation, policy, public_bytes, now=161)
    with pytest.raises(NamespaceError):
        collect_kernel_attestation(
            run_id=topology.run_id,
            policy_hash=policy.policy_hash,
            topology=topology,
            private_key=private_bytes,
            expected_runtime_sha256="sha256:" + "a" * 64,
            invoke=lambda argv, **kwargs: subprocess.CompletedProcess(argv, 1, "", "failed"),
        )


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("broker_process", {"pid": 312, "uid": 972, "cgroup": "tgw-review-egress@abcdef123456.service"}),
        ("broker_process", {"pid": 123, "uid": 1972, "cgroup": "tgw-review-egress@abcdef123456.service"}),
        ("broker_socket", [{"pid": 312, "uid": 972, "inode": 9, "local_ip": "169.254.191.1", "local_port": 18443, "state": "LISTEN"}]),
        ("broker_socket", [{"pid": 123, "uid": 1972, "inode": 9, "local_ip": "169.254.191.1", "local_port": 18443, "state": "LISTEN"}]),
    ],
)
def test_typed_identity_parser_rejects_pid_uid_collisions(field, replacement):
    from tgw.review_egress_namespace import parse_live_identity

    topology = Topology.for_run("abcdef123456")
    namespace_nft, host_nft = nft_fixtures(topology)
    evidence = {
        "namespace_readback": topology.namespace,
        "address": json.dumps([{"ifname": topology.peer_if, "flags": ["UP"], "addr_info": [{"family": "inet", "local": topology.peer_address.split("/")[0], "prefixlen": 30}]}]),
        "link": json.dumps([{"ifname": topology.host_if, "flags": ["UP"]}]),
        "route": json.dumps([{"dst": "default", "gateway": topology.host_address.split("/")[0], "dev": topology.peer_if}]),
        "ruleset": json.dumps(namespace_nft),
        "counters": json.dumps(host_nft),
        "broker_process": "123 972 tgw-review-egress@abcdef123456.service",
        "broker_starttime": "42",
        "broker_exe": "a" * 64 + " /proc/123/exe",
        "broker_socket": f"LISTEN pid=123 uid=972 inode=9 local={topology.host_address.split('/')[0]}:18443",
    }
    if field == "broker_process":
        evidence[field] = f"{replacement['pid']} {replacement['uid']} {replacement['cgroup']}"
    else:
        row = replacement[0]
        evidence[field] = f"LISTEN pid={row['pid']} uid={row['uid']} inode={row['inode']} local={row['local_ip']}:{row['local_port']}"
    with pytest.raises(NamespaceError):
        parse_live_identity(evidence, topology, pid=123, runtime_sha256="sha256:" + "a" * 64)


@pytest.mark.parametrize("mutation", ["wrong_left", "wrong_op", "missing_private_drop", "extra_host_rule", "wrong_nat_interface"])
def test_nft_ast_rejects_plausible_wrong_rules(mutation):
    import copy

    from tgw.review_egress_namespace import validate_review_nft

    topology = Topology.for_run("abcdef123456")
    namespace, host = nft_fixtures(topology)
    if mutation == "wrong_left":
        namespace["nftables"][3]["rule"]["expr"][0]["match"]["left"] = {"meta": {"key": "mark"}}
    elif mutation == "wrong_op":
        namespace["nftables"][3]["rule"]["expr"][0]["match"]["op"] = "!="
    elif mutation == "missing_private_drop":
        del host["nftables"][3]
    elif mutation == "extra_host_rule":
        host["nftables"].append(copy.deepcopy(host["nftables"][4]))
    else:
        host["nftables"][-1]["rule"]["expr"][0]["match"]["right"] = "unrelated0"
    with pytest.raises(NamespaceError):
        validate_review_nft(namespace, host, topology)


def test_nft_capture_receipt_retains_only_canonical_hash_and_counts():
    from tgw.review_egress_namespace import canonical_nft_capture_receipt

    topology = Topology.for_run("abcdef123456")
    namespace, host = nft_fixtures(topology)
    receipt = canonical_nft_capture_receipt(namespace, host, topology)
    assert receipt["canonical_sha256"].startswith("sha256:")
    assert receipt["namespace_rule_count"] == 5 and receipt["host_rule_count"] == 6
    assert receipt["raw_retained"] is False


def test_prepared_nix_evaluation_request_is_non_deploying_and_content_bound():
    from pathlib import Path

    request = json.loads(Path("agent-services/catalogs/nixos-reviewed-evaluation-request-v1.json").read_text())
    assert request["status"] == "PREPARED_NOT_EXECUTED"
    assert request["target_host"] == "tgw-prod" and request["system"] == "x86_64-linux"
    assert request["source"]["module_path"] == "nix/review-egress.nix"
    assert set(request["forbidden_effects"]) >= {"switch", "profile-write", "home-db-write"}
    assert len(request["evaluation"]["expected_units"]) == 3


def test_systemd_dependency_graph_is_acyclic_and_rendered_units_verify(tmp_path):
    import re
    import shutil
    from pathlib import Path

    nix = Path("nix/review-egress.nix").read_text()
    for token in ("requires", "after", "partOf", "tgw-review-egress@%i.service", "tgw-review-egress-attest@%i.service"):
        assert token in nix
    blocks = {}
    for name in ("tgw-review-egress@", "tgw-review-egress-namespace@", "tgw-review-egress-attest@"):
        start = nix.index(f'systemd.services."{name}"')
        end = nix.find('systemd.services."', start + 20)
        blocks[name] = nix[start : len(nix) if end < 0 else end]
    graph = {name: set() for name in blocks}
    unit_to_name = {f"{name}%i.service": name for name in blocks}
    for name, block in blocks.items():
        for relation in ("requires", "after"):
            match = re.search(rf"{relation} = \[ ([^]]*) \]", block)
            if match:
                for unit in re.findall(r'"([^"]+)"', match.group(1)):
                    if unit in unit_to_name and relation == "after":
                        graph[unit_to_name[unit]].add(name)
    visiting, done = set(), set()

    def visit(node):
        assert node not in visiting
        if node not in done:
            visiting.add(node)
            for child in graph[node]:
                visit(child)
            visiting.remove(node)
            done.add(node)

    for node in graph:
        visit(node)
    if shutil.which("systemd-analyze"):
        units = []
        for index, (name, block) in enumerate(blocks.items()):
            unit = tmp_path / f"review-{index}.service"
            after = " ".join(f"review-{list(blocks).index(dep)}.service" for dep in graph if name in graph[dep])
            unit.write_text(f"[Unit]\nAfter={after}\n[Service]\nType=oneshot\nExecStart=/bin/true\n")
            units.append(str(unit))
        result = subprocess.run(["systemd-analyze", "verify", *units], text=True, capture_output=True, check=False)
        assert result.returncode == 0, result.stderr
