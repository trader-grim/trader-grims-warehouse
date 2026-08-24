import base64
import grp
import hashlib
import json
import os
import pwd
import shutil
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

from tgw import actor_fleet_provider as fleet_provider_module
from tgw.actor_contract import actor_contract_public_key, sign_actor_contract
from tgw.actor_fleet_provider import (
    _ACTOR_VERIFICATION_BOOTSTRAP,
    _ACTOR_VERIFICATION_MAX_INPUT,
    ActorFleetError,
    ActorFleetProvider,
    _actor_context_process_inventory,
    _actor_verification_payload,
    _context_registration,
    create_actor_fleet_app,
)
from tgw.admission_recovery import compile_release_admission

_CONTRACT_SIGNING_KEY = Ed25519PrivateKey.from_private_bytes(b"k" * 32)
_CONTRACT_PUBLIC_KEY = actor_contract_public_key(_CONTRACT_SIGNING_KEY)
_PREDECESSOR_CONTRACT_SIGNING_KEY = Ed25519PrivateKey.from_private_bytes(
    b"p" * 32
)
_PREDECESSOR_CONTRACT_PUBLIC_KEY = actor_contract_public_key(
    _PREDECESSOR_CONTRACT_SIGNING_KEY
)


def _protected_fixture_root(path):
    return (
        Path("/opt/TGW/tgw-lib/var/context-update/provider-test-fixtures")
        / Path(path).name
    )


def _runtime_entrypoint_content():
    return (
        f"#!{sys.executable}\n"
        "# exact admitted startup entrypoint\n"
    )


@pytest.fixture
def durable_path(monkeypatch):
    path = Path("/opt/TGW/var/tmp") / f"actor-provider-test-{uuid.uuid4().hex}"
    protected = _protected_fixture_root(path)
    path.mkdir(mode=0o700)
    protected.mkdir(mode=0o700, parents=True)
    actor_name = pwd.getpwuid(os.getuid()).pw_name
    real_getpwnam = pwd.getpwnam

    def fixture_account(name):
        observed = real_getpwnam(name)
        if name != actor_name:
            return observed
        return pwd.struct_passwd(
            (
                observed.pw_name,
                observed.pw_passwd,
                observed.pw_uid,
                observed.pw_gid,
                observed.pw_gecos,
                str(protected / "actor-home"),
                observed.pw_shell,
            )
        )

    monkeypatch.setattr(pwd, "getpwnam", fixture_account)
    try:
        yield path
    finally:
        for root in (path, protected):
            for child in sorted(
                root.rglob("*"), key=lambda item: len(item.parts), reverse=True
            ):
                if not child.is_symlink():
                    child.chmod(0o700 if child.is_dir() else 0o600)
            shutil.rmtree(root)
        try:
            protected.parent.rmdir()
        except OSError:
            pass


@pytest.fixture
def strict_runtime(durable_path, monkeypatch):
    launcher = _protected_fixture_root(durable_path) / "runtime-bin/tgw-actor"
    launcher.parent.mkdir(mode=0o700)
    launcher.write_text(_runtime_entrypoint_content())
    launcher.chmod(0o555)
    monkeypatch.setattr(
        fleet_provider_module,
        "_STABLE_CONTEXT_LAUNCHER",
        launcher,
    )
    real_stat = Path.stat

    def root_owned_runtime_stat(path, *args, **kwargs):
        observed = real_stat(path, *args, **kwargs)
        if path in {launcher, launcher.parent}:
            values = list(observed)
            values[4] = 0
            return os.stat_result(values)
        return observed

    monkeypatch.setattr(Path, "stat", root_owned_runtime_stat)
    monkeypatch.setattr(
        fleet_provider_module,
        "validate_context_source",
        lambda source_root, _git, *, expected_commit, expected_tree: (
            Path(source_root).resolve(strict=True),
            expected_commit,
            expected_tree,
        ),
    )
    return launcher


def _hash(value):
    return "sha256:" + hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _file_hash(path):
    return "sha256:" + hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _tree_hash(path):
    root = Path(path)
    digest = hashlib.sha256()
    for item in sorted((item for item in root.rglob("*") if item.is_file()), key=lambda value: value.relative_to(root).as_posix()):
        digest.update(item.relative_to(root).as_posix().encode())
        digest.update(b"\0")
        digest.update(item.read_bytes())
        digest.update(b"\0")
    return "sha256:" + digest.hexdigest()


def _context_proof(actor, value):
    revisions = value["revisions"]
    unsigned = {
        "schema": "tgw-actor-context-mcp-proof/v1",
        "status": "PASS",
        "actor": actor,
        "tools": [
            "tgw_context_bundle",
            "tgw_context_code_graph",
            "tgw_context_confirm_rebind",
            "tgw_context_onboarding",
            "tgw_context_plan_graph",
            "tgw_context_plan_source",
            "tgw_context_runbooks",
            "tgw_context_status",
        ],
        "plan": revisions["plan"],
        "solution": revisions["solution"],
        "evidence_plan": revisions["evidence_plan"],
        "evidence_tree": revisions["evidence_tree"],
        "source": revisions["source"],
        "source_tree": revisions["source_tree"],
        "catalog": revisions["catalog"],
        "onboarding_bundle_sha256": "sha256:" + "8" * 64,
        "task_bundle_sha256": "sha256:" + "9" * 64,
        "current_plan_sources_sha256": "sha256:" + "7" * 64,
        "current_plan_source_identities_sha256": _hash(
            revisions["current_plan_sources"]
        ),
    }
    return {**unsigned, "proof_hash": _hash(unsigned)}


@pytest.mark.parametrize(
    ("suffix", "content"),
    [
        (
            ".json",
            json.dumps(
                {
                    "mcpServers": {
                        "tgw-context": {
                            "command": "/opt/TGW/bin/tgw-actor",
                            "args": ["--context-mcp"],
                            "env": {"TGW_ACTOR": "codex"},
                        }
                    }
                }
            ),
        ),
        (
            ".toml",
            '[mcp_servers.tgw-context]\ncommand = "/opt/TGW/bin/tgw-actor"\nargs = ["--context-mcp"]\n'
            '[mcp_servers.tgw-context.env]\nTGW_ACTOR = "codex"\n',
        ),
        (
            ".yml",
            "- insert:\n"
            "    - id: tgw-context\n"
            "      name: '@deepseek-ai/dsh-mcp-client'\n"
            "      config:\n"
            "        command: /opt/TGW/bin/tgw-actor\n"
            "        args: ['--context-mcp']\n"
            "        env:\n"
            "          TGW_ACTOR: codex\n",
        ),
    ],
)
def test_context_registration_loads_harness_native_formats(durable_path, suffix, content):
    registration = durable_path / f"registration{suffix}"
    registration.write_text(content, encoding="utf-8")

    assert _context_registration(registration) == (
        "/opt/TGW/bin/tgw-actor",
        ["--context-mcp"],
        {"TGW_ACTOR": "codex"},
    )


@pytest.mark.parametrize(
    "content",
    [
        "{}",
        json.dumps({"mcpServers": {"tgw-context": {"command": "tgw-actor", "args": [], "env": {}}}}),
        json.dumps(
            {
                "mcpServers": {
                    "tgw-context": {
                        "command": "/opt/TGW/bin/tgw-actor",
                        "args": ["--context-mcp"],
                        "env": {"TGW_ACTOR": 7},
                    }
                }
            }
        ),
    ],
)
def test_context_registration_rejects_missing_or_ambient_bindings(durable_path, content):
    registration = durable_path / "registration.json"
    registration.write_text(content, encoding="utf-8")

    with pytest.raises(ActorFleetError, match="registration"):
        _context_registration(registration)


def test_context_registration_rejects_destination_swap_after_validation(
    durable_path, monkeypatch,
):
    source = durable_path / "approved.json"
    attacker = durable_path / "attacker.json"
    destination = durable_path / "registration.json"
    content = json.dumps(
        {
            "mcpServers": {
                "tgw-context": {
                    "command": "/opt/TGW/bin/tgw-actor",
                    "args": ["--context-mcp"],
                    "env": {"TGW_ACTOR": "codex"},
                }
            }
        }
    )
    source.write_text(content, encoding="utf-8")
    attacker.write_text(content.replace("codex", "attacker"), encoding="utf-8")
    destination.symlink_to(source)
    real_open = os.open

    def swap_before_open(path, flags, *args, **kwargs):
        if Path(path) == destination:
            destination.unlink()
            destination.symlink_to(attacker)
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr("tgw.actor_fleet_provider.os.open", swap_before_open)
    with pytest.raises(ActorFleetError, match="active-store projection differs"):
        _context_registration(destination, source)


def test_live_process_inventory_distinguishes_stable_and_direct_context_children(durable_path):
    actor = pwd.getpwuid(os.getuid()).pw_name
    proc_root = durable_path / "proc"
    proc_root.mkdir()
    boot_id = proc_root / "sys/kernel/random/boot_id"
    boot_id.parent.mkdir(parents=True)
    boot_id.write_text("11111111-2222-3333-4444-555555555555\n")
    executable = durable_path / "python"
    executable.write_bytes(b"fixture executable\n")

    def process(pid, arguments, environment, *, ppid):
        process_dir = proc_root / str(pid)
        process_dir.mkdir()
        (process_dir / "status").write_text(
            f"Uid:\t{os.getuid()} {os.getuid()} {os.getuid()} {os.getuid()}\n"
            f"PPid:\t{ppid}\n"
        )
        (process_dir / "stat").write_text(
            f"{pid} (context mcp) S " + "0 " * 18 + f"{pid * 10}\n"
        )
        (process_dir / "cmdline").write_bytes(
            b"\0".join(item.encode() for item in arguments) + b"\0"
        )
        (process_dir / "environ").write_bytes(
            b"\0".join(
                f"{key}={value}".encode() for key, value in environment.items()
            ) + b"\0"
        )
        (process_dir / "exe").symlink_to(executable)

    process(
        77,
        [str(executable), "/opt/ordinary-harness/session.py", "--session"],
        {},
        ppid=1,
    )

    process(
        101,
        [str(executable), "/opt/TGW/tgw-lib/bin/tgw-actor", "--context-mcp"],
        {"TGW_CONTEXT_GENERATION": "sha256:" + "1" * 64, "TGW_CONTEXT_STARTUP_BINDING": "/etc/tgw/actors/x"},
        ppid=77,
    )
    process(
        102,
        [str(executable), "-m", "tgw.context_mcp_server"],
        {"TGW_CONTEXT_PLAN_COMMIT": "f" * 40},
        ppid=77,
    )

    inventory = _actor_context_process_inventory([actor], proc_root=proc_root)

    assert [(item["pid"], item["stable_launcher"], item["guarded"]) for item in inventory] == [
        (101, True, True),
        (102, False, False),
    ]
    for item in inventory:
        assert item["executable_path"] == str(executable.resolve())
        assert item["identity_hash"] == _hash(
            {
                name: item[name]
                for name in (
                    "boot_id", "pid", "start_ticks", "uid", "ppid",
                    "executable_path", "executable_device", "executable_inode",
                    "executable_sha256", "cmdline_shape", "cmdline_sha256",
                )
            }
        )
        parent = item["parent"]
        assert parent["pid"] == 77
        assert parent["uid"] == os.getuid()
        assert parent["executable_path"] == str(executable.resolve())
        assert parent["cmdline_shape"] == [executable.name, "--session"]
        assert parent["identity_hash"] == _hash(
            {
                name: parent[name]
                for name in (
                    "boot_id", "pid", "start_ticks", "uid", "ppid",
                    "executable_path", "executable_device", "executable_inode",
                    "executable_sha256", "cmdline_shape", "cmdline_sha256",
                )
            }
        )


class _Materializer:
    def __init__(self, destination_root=None):
        self.calls = []
        self.destination_root = (
            Path(destination_root) if destination_root is not None else None
        )

    def materialize_complete_actor_contracts(
        self,
        bundle,
        *,
        source_root,
        contracts,
        trusted_contract_public_key,
        apply=False,
        replace_existing=False,
        additional_source_roots=(),
        transaction_journal_path=None,
    ):
        self.calls.append((apply, replace_existing))
        bindings = []
        for actor, specification in bundle["actors"].items():
            for raw in specification["bindings"]:
                source = (Path(source_root) / raw["source"]).resolve()
                destination = Path(raw["destination"])
                declared_home = Path("/home") / actor
                if (
                    self.destination_root is not None
                    and declared_home in destination.parents
                ):
                    destination = self.destination_root / destination.relative_to(
                        declared_home
                    )
                if apply:
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    if destination.is_symlink():
                        destination.unlink()
                    destination.symlink_to(source)
                bindings.append({"actor": actor, "source": str(source), "destination": str(destination), "kind": raw["kind"]})
                bindings[-1].update({"name": raw["name"], "sha256": raw["sha256"]})
        return {
            "schema": "tgw-w18-complete-actor-materialization/v1",
            "status": "COMPLETE_MATERIALIZED_NOT_SERVICE_ACTIVATED" if apply else "PREPARED",
            "bindings": bindings,
            "rollback_journal": [],
        }

    def rollback_complete_actor_contracts(self, receipt):
        for binding in receipt["bindings"]:
            path = Path(binding["destination"])
            if path.is_symlink():
                path.unlink()


def _fixture(tmp_path, *, admission_expires_at="2026-08-24T00:00:00Z"):
    protected = _protected_fixture_root(tmp_path)
    generations, workspaces, caches = (
        tmp_path / name
        for name in ("generations", "workspaces", "caches")
    )
    state, releases, admissions, startup_bindings, actor_caches = (
        protected / name
        for name in (
            "state",
            "releases",
            "admissions",
            "startup-bindings",
            "actor-caches",
        )
    )
    for path in (state, releases, admissions, generations, workspaces, caches, actor_caches, startup_bindings):
        path.mkdir()
    state.chmod(0o750)
    (state / "private").mkdir(mode=0o700)
    actor_caches.chmod(0o750)
    startup_bindings.chmod(0o755)
    workspaces.chmod(0o2770)
    caches.chmod(0o2770)
    actor_group = grp.getgrgid(workspaces.stat().st_gid).gr_name
    actor = pwd.getpwuid(os.getuid()).pw_name
    source_commit, plan_commit = "e" * 40, "f" * 40
    solution = "sha256:" + "1" * 64
    release = releases / "candidate"
    release.mkdir()
    source_tree = "d" * 40
    for name, content in {
        "launcher": "actor launcher\n",
        "bootstrap.json": '{"status":"PASS"}\n',
        "mcp.json": json.dumps(
            {
                "mcpServers": {
                    "tgw-context": {
                        "command": "/opt/TGW/bin/tgw-actor",
                        "args": ["--context-mcp"],
                        "env": {"TGW_ACTOR": actor},
                    }
                }
            }
        ),
        "AGENTS.md": "# TGW agent entry point\n",
        "skill/SKILL.md": "bounded plan\n",
        "scripts/tgw_actor_startup.py": _runtime_entrypoint_content(),
        "src/tgw/actor_startup.py": "# exact admitted startup module\n",
        "src/tgw/context_mcp_server.py": "# exact admitted context module\n",
        "src/tgw/actor_fleet_provider.py": "# exact admitted worker source\n",
        "config/environment/tmpfiles.d/tgw-actor-host.conf": (
            "# exact admitted bounded tmpfiles policy\n"
        ),
    }.items():
        path = release / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    home, project = protected / "actor-home", protected / "project"
    declared_home = home
    destination = home / ".local/bin/tgw-actor"
    generation_hash = "sha256:" + "b" * 64
    generation = generations / generation_hash.removeprefix("sha256:")
    (generation / "contracts").mkdir(parents=True)
    environment = {
        "schema": "tgw-execution-environment-catalog/v3",
        "bootstrap_revision": {"content_sha256": "sha256:" + "3" * 64},
        "broker_policy_revision": {"content_sha256": "sha256:" + "4" * 64},
        "flake_lock": {"path": "flake.lock", "sha256": "1" * 64},
        "actors": {
            actor: {
                "enabled": True,
                "permitted_profiles": ["development"],
                "required_skills": ["tgw-plan"],
                "required_hooks": [],
                "required_mcp_endpoints": ["tgw-context"],
            }
        },
        "profiles": {
            "development": {
                "state": "ready-for-preflight",
                "broker_capabilities": ["plan-read", "source-read"],
                "tools": [
                    {
                        "name": "git",
                        "executable_path": str(Path(shutil.which("git") or "/usr/bin/git")),
                        "executable_sha256": _file_hash(
                            Path(shutil.which("git") or "/usr/bin/git")
                        ),
                    }
                ],
            }
        },
    }
    environment_path = generation / "environment-catalog.json"
    environment_path.write_text(json.dumps(environment))
    catalog = _hash(environment)
    launcher_hash = _file_hash(release / "launcher")
    skill_hash = _tree_hash(release / "skill")
    mcp_hash = _file_hash(release / "mcp.json")
    instruction_hash = _file_hash(release / "AGENTS.md")
    instruction_destination = declared_home / (
        ".claude/CLAUDE.md"
        if actor == "claude"
        else ".dsh/AGENTS.md" if actor == "deepseek" else ".codex/AGENTS.md"
    )
    mcp_destination = home / ".mcp/tgw-context.json"
    launcher_local = {"path": str(destination), "sha256": launcher_hash}
    mcp_local = {
        "endpoints": ["tgw-context"],
        "binding_hash": _hash(
            [
                {
                    "endpoint": "tgw-context",
                    "source_sha256": mcp_hash,
                    "destination": str(mcp_destination),
                }
            ]
        ),
    }
    bootstrap_body = {
        "schema": "tgw-actor-bootstrap-receipt/v1",
        "status": "READY",
        "actor": actor,
        "profile": "development",
        "generation": generation_hash,
        "catalog_hash": catalog,
        "plan": {"commit": plan_commit, "solution_hash": solution},
        "code_graph": {"commit": source_commit, "tree": source_tree, "freshness_hash": "sha256:" + "3" * 64},
        "declared_policy_hash": "sha256:" + "4" * 64,
        "launcher": launcher_local,
        "skills": {"tgw-plan": skill_hash},
        "hooks": {},
        "mcp": mcp_local,
        "instructions": {
            "agent-entry-point": {
                "path": str(instruction_destination),
                "sha256": instruction_hash,
            }
        },
    }
    (release / "bootstrap.json").write_text(json.dumps({**bootstrap_body, "receipt_hash": _hash(bootstrap_body)}))
    files = {
        path.relative_to(release).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(release.rglob("*"))
        if path.is_file()
    }
    content_hash = hashlib.sha256(
        (json.dumps(dict(sorted(files.items())), sort_keys=True, separators=(",", ":")) + "\n").encode()
    ).hexdigest()
    (release / ".release-manifest.json").write_text(
        json.dumps(
            {
                "schema": "tgw-release-manifest-v1",
                "generation": "candidate",
                "commit": source_commit,
                "tree": f"exact-git-archive:{source_commit}",
                "git_tree": source_tree,
                "src_root": "src",
                "archive_sha256": "a" * 64,
                "content_manifest_sha256": content_hash,
                "file_count": len(files),
                "files": files,
            }
        )
    )
    for path in sorted(release.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        path.chmod(0o555 if path.is_dir() or path.stat().st_mode & 0o111 else 0o444)
    release.chmod(0o555)
    bootstrap_hash = _file_hash(release / "bootstrap.json")
    contract_body = {
        "schema": "tgw-actor-contract-receipt/v1",
        "status": "READY",
        "actor": actor,
        "catalog_hash": catalog,
        "plan": {"commit": plan_commit, "solution_hash": solution},
        "code_graph": {
            "commit": source_commit,
            "tree": source_tree,
            "freshness_hash": "sha256:" + "3" * 64,
        },
        "profile": "development",
        "local": {
            "bootstrap_receipt_hash": bootstrap_hash,
            "launcher": launcher_local,
            "skills": {"tgw-plan": skill_hash},
            "hooks": {},
            "mcp": mcp_local,
        },
        "diagnostics": [],
        "activation": "declarative-only",
    }
    contract = sign_actor_contract(
        {**contract_body, "receipt_hash": _hash(contract_body)},
        signing_private_key=_CONTRACT_SIGNING_KEY,
    )
    bindings = [
        {"kind": "skill", "name": "tgw-plan", "source": "skill", "destination": str(declared_home / ".skills/tgw-plan"), "sha256": skill_hash},
        {"kind": "mcp", "name": "tgw-context", "source": "mcp.json", "destination": str(declared_home / ".mcp/tgw-context.json"), "sha256": mcp_hash},
        {"kind": "instruction", "name": "agent-entry-point", "source": "AGENTS.md", "destination": str(instruction_destination), "sha256": instruction_hash},
        {"kind": "launcher", "name": "launcher", "source": "launcher", "destination": str(declared_home / ".local/bin/tgw-actor"), "sha256": launcher_hash},
        {"kind": "bootstrap", "name": "bootstrap-receipt", "source": "bootstrap.json", "destination": str(declared_home / ".tgw/bootstrap.json"), "sha256": bootstrap_hash},
        {
            "kind": "environment",
            "name": "environment-catalog",
            "source": str(environment_path),
            "destination": str(declared_home / ".tgw/execution-environment-catalog.json"),
            "sha256": _file_hash(environment_path),
        },
    ]
    bundle = {
        "schema": "tgw-complete-actor-contract-bundle/v1",
        "generation": generation_hash,
        "actors": {actor: {"home": str(declared_home), "project": str(project), "bindings": bindings}},
    }
    (generation / "bundle.json").write_text(json.dumps(bundle))
    (generation / "contracts" / f"{actor}.json").write_text(json.dumps(contract))
    receipt_unsigned = {
        "schema": "tgw-actor-generation-receipt/v1",
        "status": "PREPARED",
        "generation": generation_hash,
        "actors": [actor],
        "bundle_hash": _hash(bundle),
        "contract_receipt_hashes": {actor: contract["receipt_hash"]},
        "signer_public_key": _CONTRACT_PUBLIC_KEY,
        "generation_identity": {
            "catalog_hash": catalog,
            "plan_commit": plan_commit,
            "solution_hash": solution,
            "source_commit": source_commit,
            "source_tree": source_tree,
            "context_source_root": str(release),
        },
    }
    (generation / "generation-receipt.json").write_text(json.dumps({**receipt_unsigned, "receipt_hash": _hash(receipt_unsigned)}))
    admission_key = Ed25519PrivateKey.generate()
    admission = compile_release_admission(
        request={
            "schema": "tgw-w16-release-admission-request/v1",
            "request_id": "actor-fleet-fixture",
            "candidate": {"commit": source_commit, "tree": source_tree},
            "plan": {"commit": plan_commit, "solution_hash": solution},
            "environment": {"catalog_hash": catalog, "receipt_hash": "sha256:" + "5" * 64},
            "review": {
                "status": "PASS",
                "candidate_commit": source_commit,
                "solution_hash": solution,
                "receipt_hash": "sha256:" + "6" * 64,
            },
            "admission": {
                "status": "PASS",
                "candidate_commit": source_commit,
                "solution_hash": solution,
                "receipt_hash": "sha256:" + "7" * 64,
            },
        },
        signing_private_key=admission_key,
        signer_key_id="actor-fixture",
        issued_at="2026-08-21T00:00:00Z",
        expires_at=admission_expires_at,
    )
    (admissions / (admission["receipt_hash"].removeprefix("sha256:") + ".json")).write_text(json.dumps(admission))
    admission_public_key = tmp_path / "release-admission.pub"
    admission_public_key.write_bytes(admission_key.public_key().public_bytes_raw())
    admission_public_key.chmod(0o444)
    systemctl = tmp_path / "systemctl"
    systemctl.write_text("fixture\n")
    config = {
        "schema": "tgw-actor-fleet-provider/v1",
        "token_sha256": "sha256:" + hashlib.sha256(b"secret").hexdigest(),
        "state_root": str(state),
        "release_root": str(releases),
        "admission_root": str(admissions),
        "actor_generation_root": str(generations),
        "admission_public_key": str(admission_public_key),
        "contract_public_key": _CONTRACT_PUBLIC_KEY,
        "startup_binding_root": str(startup_bindings),
        "actor_group": actor_group,
        "attempt_workspace_root": str(workspaces),
        "attempt_cache_root": str(caches),
        "actor_cache_root": str(actor_caches),
        "systemctl_path": str(systemctl),
        "managed_services": ["tgw-coding-provision-pull.timer"],
        "quiescence_units": ["tgw-coding-provision-pull.service"],
    }
    request = {
        "schema": "tgw-w18-fleet-refresh-request/v1",
        "transaction_id": "refresh-one",
        "idempotency_key": "refresh-one",
        "predecessor_generation": "sha256:" + "a" * 64,
        "successor_generation": generation_hash,
        "revisions": {
            "plan": plan_commit,
            "solution": solution,
            "evidence_plan": "9" * 40,
            "evidence_tree": "8" * 40,
            "source": source_commit,
            "source_tree": source_tree,
            "current_plan_sources": {
                path: "sha256:" + str(index) * 64
                for index, path in enumerate(
                    (
                        "plan/execution/AMENDMENT-20260823-MCP-LIVE-CLIENT-CONVERGENCE.yaml",
                        "pp/PP-ACTOR-MCP-BOUNDARY-001.md",
                        "plan/execution/targets/W19-W21-MCP-ONLY-ACTOR-HARDENING-v1.yaml",
                        "plan/execution/ACTIVE-PLAN-AMENDMENT-PROCESS-v1.yaml",
                    ),
                    start=5,
                )
            },
            "catalog": catalog,
            "bootstrap": "sha256:" + "3" * 64,
            "broker_policy": "sha256:" + "4" * 64,
            "review": "sha256:" + "6" * 64,
            "admission": admission["receipt_hash"],
        },
        "actors": [actor],
    }
    return config, request, destination


@pytest.mark.parametrize("instruction_failure", ["missing", "swapped", "drifted"])
def test_actor_verification_rejects_invalid_instruction_binding(
    durable_path, instruction_failure,
):
    config, request, _destination = _fixture(durable_path)
    actor = request["actors"][0]
    generation = (
        Path(config["actor_generation_root"])
        / request["successor_generation"].removeprefix("sha256:")
    )
    bundle = json.loads((generation / "bundle.json").read_text())
    contract = json.loads(
        (generation / "contracts" / f"{actor}.json").read_text()
    )
    materialization = _Materializer().materialize_complete_actor_contracts(
        bundle,
        source_root=Path(config["release_root"]) / "candidate",
        contracts={actor: contract},
        trusted_contract_public_key=_CONTRACT_PUBLIC_KEY,
        apply=True,
    )
    bindings = [
        item for item in materialization["bindings"] if item["actor"] == actor
    ]
    instruction = next(item for item in bindings if item["kind"] == "instruction")
    skill = next(item for item in bindings if item["kind"] == "skill")

    if instruction_failure == "missing":
        bindings.remove(instruction)
    elif instruction_failure == "swapped":
        instruction.update(
            {
                "source": skill["source"],
                "destination": skill["destination"],
                "sha256": skill["sha256"],
            }
        )
    else:
        instruction_path = Path(instruction["destination"])
        instruction_path.unlink()
        instruction_path.symlink_to(Path(skill["source"]))

    proof = _actor_verification_payload(
        actor,
        request,
        bindings,
        bundle,
        contract,
        lambda proof_actor, _path, _source, value: _context_proof(
            proof_actor, value
        ),
    )

    assert proof["status"] == "FAIL"
    assert "instruction" in proof["reason"] or "binding" in proof["reason"]


def test_instruction_proof_is_bound_into_ledger_and_fleet_projection(
    durable_path, monkeypatch,
):
    config, request, _destination = _fixture(durable_path)
    provider = _new_provider(config, durable_path)
    actor = request["actors"][0]
    generation = (
        Path(config["actor_generation_root"])
        / request["successor_generation"].removeprefix("sha256:")
    )
    bundle = json.loads((generation / "bundle.json").read_text())
    instruction = next(
        item
        for item in bundle["actors"][actor]["bindings"]
        if item["kind"] == "instruction"
    )
    proof = {
        "status": "PASS",
        "primary_real_store_semantic_sha256": "sha256:" + "a" * 64,
        "instruction_entry_point_path": instruction["destination"],
        "instruction_entry_point_sha256": instruction["sha256"],
    }
    verification = {
        "proof": proof,
        "actor_proof_hash": _hash(proof),
        "context_mcp_proof_hash": "sha256:" + "b" * 64,
        "primary_real_store_semantic_sha256": proof[
            "primary_real_store_semantic_sha256"
        ],
        "instruction_entry_point_path": proof[
            "instruction_entry_point_path"
        ],
        "instruction_entry_point_sha256": proof[
            "instruction_entry_point_sha256"
        ],
        "live_context_state": "CURRENT",
        "verified_at": "2026-08-23T12:00:00Z",
    }
    journal = {
        "schema": "tgw-actor-fleet-journal/v1",
        "transaction_id": request["transaction_id"],
        "status": "VERIFYING",
        "request": request,
        "created_at": "2026-08-23T11:59:00Z",
        "updated_at": "2026-08-23T12:00:00Z",
        "journal_payload_sha256": "sha256:" + "c" * 64,
        "ledger_sequence": 1,
        "ledger_record_sha256": "sha256:" + "d" * 64,
        "coordinator_binding": {
            "binding_sha256": "sha256:" + "e" * 64,
            "coordinator_opening": {
                "review_receipt": {"receipt_hash": request["revisions"]["review"]},
                "admission_receipt": {
                    "receipt_hash": request["revisions"]["admission"]
                },
            },
        },
        "actor_verifications": {actor: verification},
        "context_rebind": {
            "direction": "successor",
            "obligations": [],
            "latest": {"pending": [], "dispositions": []},
            "confirmations": {},
            "parent_transitions": {},
            "parent_transition_history": [],
        },
    }

    evidence = provider._ledger_evidence_receipts(journal)
    receipt = evidence["actor_verification_receipts"][0]
    assert receipt["instruction_entry_point_path"] == instruction["destination"]
    assert receipt["instruction_entry_point_sha256"] == instruction["sha256"]

    ledger_link = {
        "schema": "tgw-provider-ledger-evidence-link/v1",
        "sequence": 1,
        "record_sha256": journal["ledger_record_sha256"],
        "evidence_sha256": evidence["evidence_sha256"],
        "review_receipt_sha256": request["revisions"]["review"],
        "admission_receipt_sha256": request["revisions"]["admission"],
        "actor_verification_receipt_hashes": {
            actor: receipt["receipt_sha256"]
        },
        "client_confirmation_hashes": [],
        "parent_transition_hashes": [],
        "cold_handoff_receipt_sha256": None,
        "managed_service_action_receipt_sha256": None,
        "terminal_convergence_receipt_sha256": None,
        "link_sha256": "sha256:" + "f" * 64,
    }
    monkeypatch.setattr(
        provider,
        "_validated_journal_ledger_link",
        lambda _journal: ledger_link,
    )
    projection = provider._fleet_convergence_projection(journal)
    projected = projection["actor_verifications"][0]
    assert projected["instruction_entry_point_path"] == instruction["destination"]
    assert projected["instruction_entry_point_sha256"] == instruction["sha256"]
    assert projection["real_store_evidence_sha256"] == _hash(
        [
            {
                "actor": actor,
                "semantic_sha256": proof[
                    "primary_real_store_semantic_sha256"
                ],
                "instruction_path": instruction["destination"],
                "instruction_sha256": instruction["sha256"],
                "proof_sha256": verification["actor_proof_hash"],
            }
        ]
    )


def _live_process(request, fixture_root, *, pid, stable=True, current=False):
    revisions = request["revisions"]
    actor = request["actors"][0]
    source_root = _protected_fixture_root(fixture_root) / "releases/candidate"
    executable_argument = sys.executable
    executable = Path(executable_argument).resolve(strict=True)
    executable_state = executable.stat(follow_symlinks=False)
    entrypoint = source_root / "scripts/tgw_actor_startup.py"
    startup_module = source_root / "src/tgw/actor_startup.py"
    context_module = source_root / "src/tgw/context_mcp_server.py"
    home = Path(pwd.getpwnam(actor).pw_dir)
    stable_launcher = fleet_provider_module._STABLE_CONTEXT_LAUNCHER
    arguments = [
        executable_argument,
        "-I",
        "-s",
        "-P",
        str(entrypoint),
        "--context-mcp-runtime",
        "--context-mcp",
        "--context-mcp-stable-launcher",
        str(stable_launcher),
    ]
    git_path = Path(shutil.which("git") or "/usr/bin/git")
    environment = {
        "TGW_CONTEXT_PLAN_COMMIT": revisions["plan"],
        "TGW_CONTEXT_PLAN_SOLUTION": revisions["solution"],
        "TGW_CONTEXT_PLAN_REPOSITORY": "/opt/TGW/library/plans",
        "TGW_CONTEXT_PLAN_ROOT": f"/opt/TGW/library/approved/{revisions['plan']}",
        "TGW_CONTEXT_SOURCE_ROOT": str(source_root),
        "TGW_CONTEXT_RUNTIME_ROOT": "/opt/TGW/tgw-lib/var/context",
        "TGW_CONTEXT_ENVIRONMENT_CATALOG": "/etc/tgw/execution-environment-catalog.json",
        "TGW_CONTEXT_ENVIRONMENT_CATALOG_HASH": revisions["catalog"],
        "TGW_CONTEXT_ACTOR": actor,
        "TGW_CONTEXT_ENDPOINT": "tgw-context",
        "TGW_CONTEXT_PROFILE": "development",
        "TGW_CONTEXT_GENERATION": request["successor_generation"],
        "TGW_CONTEXT_SOURCE_COMMIT": revisions["source"],
        "TGW_CONTEXT_SOURCE_TREE": revisions["source_tree"],
        "TGW_CONTEXT_STARTUP_BINDING": str(
            _protected_fixture_root(fixture_root)
            / "startup-bindings"
            / f"{actor}-startup.json"
        ),
        "TGW_CONTEXT_FLEET_CONVERGENCE": str(
            _protected_fixture_root(fixture_root) / "state/fleet-convergence.json"
        ),
        "HOME": str(home),
        "LANG": "C.UTF-8",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
        "PYTHONSAFEPATH": "1",
        "TMPDIR": str(
            _protected_fixture_root(fixture_root)
            / "actor-caches"
            / actor
            / request["successor_generation"].removeprefix("sha256:")
            / "context-mcp"
        ),
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "safe.directory",
        "GIT_CONFIG_VALUE_0": str(source_root),
        "PATH": f"{git_path.parent}:/usr/bin:/bin",
        "TGW_CONTEXT_RUNTIME_ENTRYPOINT": str(entrypoint),
        "TGW_CONTEXT_RUNTIME_ENTRYPOINT_SHA256": _file_hash(entrypoint),
        "TGW_CONTEXT_RUNTIME_MODULE": str(startup_module),
        "TGW_CONTEXT_RUNTIME_MODULE_SHA256": _file_hash(startup_module),
        "TGW_CONTEXT_RUNTIME_CONTEXT_MODULE": str(context_module),
        "TGW_CONTEXT_RUNTIME_CONTEXT_MODULE_SHA256": _file_hash(context_module),
        "TGW_CONTEXT_STABLE_LAUNCHER": str(stable_launcher),
        "TGW_CONTEXT_STABLE_LAUNCHER_SHA256": _file_hash(entrypoint),
        "TGW_CONTEXT_RUNTIME_EXECUTABLE": executable_argument,
        "TGW_CONTEXT_RUNTIME_EXECUTABLE_SHA256": _file_hash(executable),
        "TGW_CONTEXT_RUNTIME_EXECUTABLE_DEVICE": str(executable_state.st_dev),
        "TGW_CONTEXT_RUNTIME_EXECUTABLE_INODE": str(executable_state.st_ino),
    }
    value = {
        "actor": actor,
        "boot_id": "11111111-2222-3333-4444-555555555555",
        "pid": pid,
        "ppid": 7,
        "start_ticks": pid * 10,
        "uid": os.getuid(),
        "stable_launcher": stable,
        "guarded": current,
        "startup_binding": (
            str(
                _protected_fixture_root(fixture_root)
                / "startup-bindings"
                / f"{request['actors'][0]}-startup.json"
            )
            if current else ""
        ),
        "generation": request["successor_generation"] if current else request["predecessor_generation"],
        "plan": revisions["plan"] if current else "0" * 40,
        "solution": revisions["solution"] if current else "sha256:" + "0" * 64,
        "source_commit": revisions["source"] if current else "1" * 40,
        "source_tree": revisions["source_tree"] if current else "2" * 40,
        "source_root": str(source_root) if current else "/predecessor/source",
        "catalog": revisions["catalog"] if current else "sha256:" + "1" * 64,
        "arguments": arguments,
        "cmdline_shape": [
            Path(executable_argument).name,
            "--context-mcp-runtime",
            "--context-mcp",
            "--context-mcp-stable-launcher",
        ],
        "cmdline_sha256": _hash(arguments),
        "executable_path": str(executable),
        "executable_device": executable_state.st_dev,
        "executable_inode": executable_state.st_ino,
        "executable_sha256": _file_hash(executable),
        "runtime_entrypoint": str(entrypoint),
        "runtime_entrypoint_sha256": _file_hash(entrypoint),
        "runtime_module": str(startup_module),
        "runtime_module_sha256": _file_hash(startup_module),
        "runtime_context_module": str(context_module),
        "runtime_context_module_sha256": _file_hash(context_module),
        "stable_launcher_path": str(stable_launcher),
        "stable_launcher_sha256": _file_hash(entrypoint),
        "runtime_executable": executable_argument,
        "runtime_executable_sha256": _file_hash(executable),
        "runtime_executable_device": str(executable_state.st_dev),
        "runtime_executable_inode": str(executable_state.st_ino),
        "environment_keys": sorted(environment),
        "environment_sha256": _hash(environment),
    }
    return value


def _new_provider(config, durable_path, **kwargs):
    coordinator_root = (
        _protected_fixture_root(durable_path) / "coordinator-transactions"
    )
    coordinator_root.mkdir(mode=0o700, exist_ok=True)
    coordinator_root.chmod(0o700)
    kwargs.setdefault("materializer_loader", lambda _release: _Materializer())
    return ActorFleetProvider(
        config,
        coordinator_transaction_root=coordinator_root,
        **kwargs,
    )


def _ordinary_harness_parent(*, label="ordinary-harness"):
    value = {
        "boot_id": "11111111-2222-3333-4444-555555555555",
        "pid": 7001,
        "start_ticks": 123456,
        "uid": os.getuid(),
        "ppid": 1,
        "executable_path": "/opt/tgw-test/bin/ordinary-harness",
        "executable_device": 71,
        "executable_inode": 7001,
        "executable_sha256": _hash({"executable": label}),
        "cmdline_shape": ["ordinary-harness", "--session"],
        "cmdline_sha256": _hash(["ordinary-harness", "--session", label]),
    }
    return {**value, "identity_hash": _hash(value)}


def _rebind_process(request, fixture_root, *, pid, parent, current, label):
    process = _live_process(
        request,
        fixture_root,
        pid=pid,
        stable=True,
        current=current,
    )
    process.update(
        {
            "endpoint": "tgw-context",
            "profile": "development",
            "parent": parent,
            "ppid": parent["pid"],
        }
    )
    identity_fields = (
        "boot_id", "pid", "start_ticks", "uid", "ppid", "executable_path",
        "executable_device", "executable_inode", "executable_sha256",
        "cmdline_shape", "cmdline_sha256",
    )
    process["identity_hash"] = _hash(
        {name: process[name] for name in identity_fields}
    )
    return process


def _reconciliation_provider(durable_path):
    config, request, _destination = _fixture(durable_path)
    provider = _new_provider(
        config,
        durable_path,
        service_runner=lambda arguments: subprocess.CompletedProcess(
            arguments, 0, "active\n", "",
        ),
    )
    provider._startup_binding = lambda _actor, _request: {
        "schema": "tgw-actor-startup-binding/v3",
        "actor": request["actors"][0],
        "trusted_public_key": config["contract_public_key"],
        "expected_generation": request["successor_generation"],
        "expected_plan_commit": request["revisions"]["plan"],
        "expected_solution_hash": request["revisions"]["solution"],
        "expected_source_commit": request["revisions"]["source"],
        "expected_source_tree": request["revisions"]["source_tree"],
        "expected_catalog_hash": request["revisions"]["catalog"],
        "context_source_root": str(
            _protected_fixture_root(durable_path) / "releases/candidate"
        ),
        "fleet_convergence_path": str(
            _protected_fixture_root(durable_path) / "state/fleet-convergence.json"
        ),
        "stable_launcher_path": str(
            fleet_provider_module._STABLE_CONTEXT_LAUNCHER
        ),
    }
    return provider, request


def _handoff_confirmation(
    request,
    obligation,
    *,
    process_identity_hash,
    parent_identity_hash,
    transaction_id=None,
    direction="successor",
):
    value = {
        "schema": "tgw-context-client-confirmation-receipt/v1",
        "transaction_id": transaction_id or request["transaction_id"],
        "direction": direction,
        "obligation_id": obligation["obligation_id"],
        "actor": obligation["actor"],
        "process_identity_hash": process_identity_hash,
        "parent_identity_hash": parent_identity_hash,
    }
    return {**value, "confirmation_hash": _hash(value)}


@pytest.mark.parametrize("forgery", ("arguments", "environment"))
def test_current_context_process_rejects_forged_exec_or_sanitized_environment(
    durable_path,
    strict_runtime,
    forgery,
):
    provider, request = _reconciliation_provider(durable_path)
    process = _rebind_process(
        request,
        durable_path,
        pid=42000,
        parent=_ordinary_harness_parent(),
        current=True,
        label=f"forged-{forgery}",
    )
    assert provider._is_current_context_process(process, request) is True

    forged = dict(process)
    if forgery == "arguments":
        forged["arguments"] = [*process["arguments"][:-1], "/forged/launcher"]
        forged["cmdline_sha256"] = _hash(forged["arguments"])
    else:
        forged["environment_keys"] = ["TGW_CONTEXT_GENERATION"]
        forged["environment_sha256"] = _hash(
            {"TGW_CONTEXT_GENERATION": request["successor_generation"]}
        )

    assert provider._is_current_context_process(forged, request) is False


def test_rollback_reconciliation_rejects_forged_target_process(
    durable_path,
    strict_runtime,
):
    provider, request = _reconciliation_provider(durable_path)
    actor = request["actors"][0]
    target = provider._startup_binding(actor, request)
    process = _rebind_process(
        request,
        durable_path,
        pid=42009,
        parent=_ordinary_harness_parent(label="rollback-target"),
        current=True,
        label="rollback-target",
    )
    assert provider._is_context_process_for_startup_binding(
        process, actor, target
    ) is True
    forged = dict(process)
    forged["environment_sha256"] = "sha256:" + "0" * 64
    obligations = provider._freeze_context_obligations(
        request["actors"], [], direction="rollback",
    )

    pending, dispositions = provider._reconcile_rollback_obligations(
        obligations,
        [forged],
        {actor: target},
        None,
    )

    assert dispositions == []
    assert {item["reason"] for item in pending} == {
        "ROLLBACK_IDLE_TARGET_NOT_UNIQUE",
        "UNEXPECTED_ROLLBACK_CONTEXT_PATH",
    }


def test_v1_predecessor_process_requires_bound_key_receipt_and_protected_source(
    durable_path,
    strict_runtime,
    monkeypatch,
):
    provider, request = _reconciliation_provider(durable_path)
    actor = request["actors"][0]
    predecessor_generation = request["predecessor_generation"]
    predecessor_root = provider.actor_generation_root / (
        predecessor_generation.removeprefix("sha256:")
    )
    shutil.copytree(provider._generation_root(request), predecessor_root)

    contract_path = predecessor_root / "contracts" / f"{actor}.json"
    successor_contract = json.loads(contract_path.read_text())
    predecessor_receipt = {
        name: value
        for name, value in successor_contract.items()
        if name not in {"issuer_public_key", "signature"}
    }
    predecessor_contract = sign_actor_contract(
        predecessor_receipt,
        signing_private_key=_PREDECESSOR_CONTRACT_SIGNING_KEY,
    )
    contract_path.write_text(json.dumps(predecessor_contract))

    receipt_path = predecessor_root / "generation-receipt.json"
    generation_receipt = json.loads(receipt_path.read_text())
    generation_receipt.update(
        {
            "generation": predecessor_generation,
            "contract_receipt_hashes": {
                actor: predecessor_contract["receipt_hash"]
            },
            "signer_public_key": _PREDECESSOR_CONTRACT_PUBLIC_KEY,
        }
    )
    generation_receipt.pop("receipt_hash")
    generation_receipt["receipt_hash"] = _hash(generation_receipt)
    receipt_path.write_text(json.dumps(generation_receipt))

    source_root = Path(
        generation_receipt["generation_identity"]["context_source_root"]
    )
    entrypoint = source_root / "scripts/tgw_actor_startup.py"
    legacy_home = durable_path / "legacy-home"
    legacy_launcher = legacy_home / ".local/bin/tgw-actor"
    legacy_launcher.parent.mkdir(parents=True)
    legacy_launcher.symlink_to(entrypoint)
    account = pwd.getpwnam(actor)
    monkeypatch.setattr(
        fleet_provider_module.pwd,
        "getpwnam",
        lambda name: type(
            "LegacyAccount",
            (),
            {"pw_dir": str(legacy_home), "pw_uid": account.pw_uid},
        )(),
    )
    inherited_stat = Path.stat

    def root_owned_legacy_target(path, *args, **kwargs):
        observed = inherited_stat(path, *args, **kwargs)
        if Path(path) == entrypoint:
            values = list(observed)
            values[4] = 0
            return os.stat_result(values)
        return observed

    monkeypatch.setattr(Path, "stat", root_owned_legacy_target)

    revisions = request["revisions"]
    binding = {
        "schema": "tgw-actor-startup-binding/v1",
        "actor": actor,
        "trusted_public_key": _PREDECESSOR_CONTRACT_PUBLIC_KEY,
        "expected_generation": predecessor_generation,
        "expected_plan_commit": revisions["plan"],
        "expected_solution_hash": revisions["solution"],
        "expected_source_commit": revisions["source"],
        "expected_catalog_hash": revisions["catalog"],
    }
    executable = Path(sys.executable).resolve(strict=True)
    executable_state = executable.stat(follow_symlinks=False)
    git_path = Path(shutil.which("git") or "/usr/bin/git")
    arguments = [str(executable), str(legacy_launcher), "--context-mcp"]
    environment = {
        "TGW_CONTEXT_PLAN_COMMIT": revisions["plan"],
        "TGW_CONTEXT_PLAN_SOLUTION": revisions["solution"],
        "TGW_CONTEXT_PLAN_REPOSITORY": "/opt/TGW/library/plans",
        "TGW_CONTEXT_PLAN_ROOT": f"/opt/TGW/library/approved/{revisions['plan']}",
        "TGW_CONTEXT_SOURCE_ROOT": str(source_root),
        "TGW_CONTEXT_RUNTIME_ROOT": "/opt/TGW/tgw-lib/var/context",
        "TGW_CONTEXT_ENVIRONMENT_CATALOG": (
            "/etc/tgw/execution-environment-catalog.json"
        ),
        "TGW_CONTEXT_ENVIRONMENT_CATALOG_HASH": revisions["catalog"],
        "HOME": str(legacy_home),
        "LANG": "C.UTF-8",
        "PYTHONDONTWRITEBYTECODE": "1",
        "TMPDIR": str(
            provider.actor_cache_root
            / actor
            / predecessor_generation.removeprefix("sha256:")
            / "context-mcp"
        ),
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "safe.directory",
        "GIT_CONFIG_VALUE_0": str(source_root),
        "PATH": f"{git_path.parent}:/usr/bin:/bin",
    }
    process = {
        "actor": actor,
        "guarded": False,
        "startup_binding": "",
        "generation": "",
        "plan": revisions["plan"],
        "solution": revisions["solution"],
        "source_commit": "",
        "source_tree": "",
        "source_root": str(source_root),
        "catalog": revisions["catalog"],
        "arguments": arguments,
        "cmdline_shape": [executable.name, "--context-mcp"],
        "cmdline_sha256": _hash(arguments),
        "executable_path": str(executable),
        "executable_device": executable_state.st_dev,
        "executable_inode": executable_state.st_ino,
        "executable_sha256": _file_hash(executable),
        "environment_keys": sorted(environment),
        "environment_sha256": _hash(environment),
        "stable_launcher": False,
        **{
            name: ""
            for name in (
                "runtime_entrypoint",
                "runtime_entrypoint_sha256",
                "runtime_module",
                "runtime_module_sha256",
                "runtime_context_module",
                "runtime_context_module_sha256",
                "stable_launcher_path",
                "stable_launcher_sha256",
                "runtime_executable",
                "runtime_executable_sha256",
                "runtime_executable_device",
                "runtime_executable_inode",
            )
        },
    }

    assert provider._is_context_process_for_startup_binding(
        process, actor, binding
    ) is True
    assert provider._is_context_process_for_startup_binding(
        process,
        actor,
        {**binding, "trusted_public_key": _CONTRACT_PUBLIC_KEY},
    ) is False

    def reject_mutable_source(*_args, **_kwargs):
        raise fleet_provider_module.ContextSourceGuardError(
            "legacy source is actor-writable"
        )

    monkeypatch.setattr(
        fleet_provider_module,
        "validate_context_source",
        reject_mutable_source,
    )
    assert provider._is_context_process_for_startup_binding(
        process, actor, binding
    ) is False


def test_current_context_process_rejects_writable_launcher_parent(
    durable_path,
    strict_runtime,
):
    provider, request = _reconciliation_provider(durable_path)
    process = _rebind_process(
        request,
        durable_path,
        pid=42010,
        parent=_ordinary_harness_parent(label="writable-launcher-parent"),
        current=True,
        label="writable-launcher-parent",
    )
    assert provider._is_current_context_process(process, request) is True

    strict_runtime.parent.chmod(0o777)

    assert provider._is_current_context_process(process, request) is False


def _transaction_request(request, transaction_id):
    return {
        **request,
        "transaction_id": transaction_id,
        "idempotency_key": transaction_id,
    }


def _bind_coordinator(
    provider,
    request,
    *,
    private_mutator=None,
    before_bind=None,
):
    transaction_id = request["transaction_id"]
    transaction_root = provider.coordinator_transaction_root / transaction_id
    root_request_sha256 = _hash(
        {"schema": "fixture-root-request/v1", "transaction_id": transaction_id}
    )
    release_manifest = json.loads(
        (
            provider.release_root / "candidate/.release-manifest.json"
        ).read_text()
    )
    encoded_file = {
        "encoding": "base64",
        "content": base64.b64encode(b"exact coordinator preimage\n").decode(),
    }
    expected_paths = {
        "actor-public-trust": fleet_provider_module._ACTOR_PUBLIC_TRUST,
        "environment-public-trust": (
            fleet_provider_module._ENVIRONMENT_PUBLIC_TRUST
        ),
        "admission-public-trust": (
            fleet_provider_module._ADMISSION_PUBLIC_TRUST
        ),
        "provider-config": fleet_provider_module._ACTOR_PROVIDER_CONFIG,
        "release-admission": provider.admission_root
        / f"{request['revisions']['admission'].removeprefix('sha256:')}.json",
        "environment-catalog": Path(
            "/etc/tgw/execution-environment-catalog.json"
        ),
        "release-selector": provider.release_root / "current",
        "provider-unit": Path(
            "/etc/systemd/system/tgw-actor-fleet-provider.service"
        ),
        "provider-tmpfiles": Path("/etc/tmpfiles.d/tgw-actor-host.conf"),
        "relay-unit": Path(
            "/etc/systemd/system/tgw-context-confirmation-relay.service"
        ),
        "stable-launcher": fleet_provider_module._STABLE_CONTEXT_LAUNCHER,
        "stable-bin-parent": fleet_provider_module._STABLE_CONTEXT_LAUNCHER.parent,
        "status-executable": Path(
            "/opt/TGW/tgw-lib/bin/tgw-context-generation-status"
        ),
        "status-sudoers": Path(
            "/etc/sudoers.d/tgw-context-generation-status"
        ),
        "provider-state-journal": provider._journal_path(transaction_id),
        "provider-state-materializer": provider.private_state_root
        / f"{transaction_id}.actor-materializer.json",
        "provider-state-projection": provider._fleet_convergence_path,
        "provider-state-pointer": provider.state_root
        / "active-fleet-transaction.json",
        "cold-continuity-workspace": (
            fleet_provider_module._DEFAULT_CONTEXT_UPDATE_SCRATCH_ROOT
            / transaction_id
            / "claude-cold-continuity"
        ),
        "transaction-scratch-root": (
            fleet_provider_module._DEFAULT_CONTEXT_UPDATE_SCRATCH_ROOT
            / transaction_id
        ),
        "cold-continuity-transcript": transaction_root
        / "cold-continuity-transcript.jsonl",
        "cold-continuity-receipt": transaction_root
        / "cold-continuity-receipt.json",
        "deepseek-service-action-receipt": transaction_root
        / "deepseek-service-action.json",
        "deepseek-service-progress": transaction_root
        / "deepseek-service-progress.json",
        "deepseek-linger-token": transaction_root / "deepseek-linger-token",
        "deepseek-linger": fleet_provider_module._DEEPSEEK_LINGER,
        "provider-attestation-receipt": transaction_root
        / "provider-attestation.json",
        "coordinator-terminal-receipt": transaction_root
        / "terminal-receipt.json",
    }
    bundle = json.loads(
        (
            provider._generation_root(request) / "bundle.json"
        ).read_text()
    )
    tmpfiles_source = (
        provider.release_root
        / "candidate/config/environment/tmpfiles.d/tgw-actor-host.conf"
    )
    for row in tmpfiles_source.read_text().splitlines():
        stripped = row.strip()
        if not stripped or stripped.startswith("#"):
            continue
        fields = stripped.split()
        path = Path(fields[1])
        identity = hashlib.sha256(str(path).encode()).hexdigest()[:16]
        expected_paths[f"tmpfiles-dir-{identity}"] = path
    observed_paths = set(expected_paths.values())
    parent_paths = set()
    for actor in request["actors"]:
        startup = provider.startup_binding_root / f"{actor}-startup.json"
        expected_paths[f"startup-{actor}"] = startup
        expected_paths[f"actor-cache-{actor}"] = (
            provider.actor_cache_root
            / actor
            / request["successor_generation"].removeprefix("sha256:")
        )
        observed_paths.add(startup)
        for index, actor_binding in enumerate(
            bundle["actors"][actor]["bindings"]
        ):
            destination = Path(actor_binding["destination"])
            home = Path(bundle["actors"][actor]["home"])
            assert destination == home or home in destination.parents
            expected_paths[f"actor-{actor}-{index:03d}"] = destination
            observed_paths.add(destination)
            parent = destination.parent
            while parent != home:
                if parent not in observed_paths:
                    parent_paths.add(parent)
                parent = parent.parent
    for parent in sorted(parent_paths, key=str):
        identity = hashlib.sha256(str(parent).encode()).hexdigest()[:16]
        expected_paths[f"parent-{identity}"] = parent
        observed_paths.add(parent)

    def absent(target_id, path):
        return {
            "target_id": target_id,
            "path": str(path),
            "kind": "absent",
            "mode": None,
            "uid": None,
            "gid": None,
            "nlink": None,
            "payload": {},
        }

    preimages = [
        absent(target_id, path)
        for target_id, path in sorted(expected_paths.items())
    ]

    def replace_preimage(target_id, value):
        index = next(
            index
            for index, item in enumerate(preimages)
            if item["target_id"] == target_id
        )
        preimages[index] = {"target_id": target_id, **value}

    def encoded_json(value):
        return {
            "encoding": "base64",
            "content": base64.b64encode(
                json.dumps(
                    value, sort_keys=True, separators=(",", ":")
                ).encode()
            ).decode(),
        }

    replace_preimage(
        "provider-config",
        {
            "path": str(expected_paths["provider-config"]),
            "kind": "file",
            "mode": 0o440,
            "uid": 0,
            "gid": 0,
            "nlink": 1,
            "payload": encoded_json(
                {
                    "actor_fleet_provider": {
                        "contract_public_key": (
                            _PREDECESSOR_CONTRACT_PUBLIC_KEY
                        )
                    }
                }
            ),
        },
    )
    for actor in request["actors"]:
        predecessor_startup = {
            "schema": "tgw-actor-startup-binding/v1",
            "actor": actor,
            "trusted_public_key": _PREDECESSOR_CONTRACT_PUBLIC_KEY,
            "expected_generation": request["predecessor_generation"],
            "expected_plan_commit": "a" * 40,
            "expected_solution_hash": "sha256:" + "2" * 64,
            "expected_source_commit": "b" * 40,
            "expected_catalog_hash": "sha256:" + "5" * 64,
        }
        startup_path = expected_paths[f"startup-{actor}"]
        startup_bytes = (
            startup_path.read_bytes()
            if startup_path.is_file() and not startup_path.is_symlink()
            else json.dumps(
                predecessor_startup,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        )
        replace_preimage(
            f"startup-{actor}",
            {
                "path": str(startup_path),
                "kind": "file",
                "mode": 0o444,
                "uid": 0,
                "gid": 0,
                "nlink": 1,
                "payload": {
                    "encoding": "base64",
                    "content": base64.b64encode(startup_bytes).decode(),
                },
            },
        )

    replace_preimage(
        "environment-catalog",
        {
            "path": str(expected_paths["environment-catalog"]),
            "kind": "file",
            "mode": 0o444,
            "uid": 0,
            "gid": 0,
            "nlink": 1,
            "payload": encoded_file,
        },
    )
    replace_preimage(
        "release-selector",
        {
            "path": str(expected_paths["release-selector"]),
            "kind": "symlink",
            "mode": 0o777,
            "uid": 0,
            "gid": 0,
            "nlink": 1,
            "payload": {"target": "candidate"},
        },
    )
    replace_preimage(
        "provider-tmpfiles",
        {
            "path": str(expected_paths["provider-tmpfiles"]),
            "kind": "directory",
            "mode": 0o755,
            "uid": 0,
            "gid": 0,
            "nlink": 2,
            "payload": {
                "coverage": "recursive",
                "entries": [
                    {
                        "relative_path": "10-provider.conf",
                        "kind": "file",
                        "mode": 0o444,
                        "uid": 0,
                        "gid": 0,
                        "nlink": 1,
                        "payload": encoded_file,
                    }
                ],
            },
        },
    )
    replace_preimage(
        "stable-bin-parent",
        {
            "path": str(expected_paths["stable-bin-parent"]),
            "kind": "directory",
            "mode": 0o755,
            "uid": 0,
            "gid": 0,
            "nlink": 2,
            "payload": {"coverage": "metadata-only", "entries": []},
        },
    )
    service_properties = {
        "LoadState": "loaded",
        "ActiveState": "active",
        "SubState": "running",
        "UnitFileState": "enabled",
        "MainPID": "1701",
        "FragmentPath": "/etc/systemd/system/fixture.service",
        "ExecMainStartTimestampMonotonic": "123456789",
    }
    service_preimages = [
        {
            "target_id": "provider-service",
            "service": "tgw-actor-fleet-provider.service",
            "properties": service_properties,
        },
        {
            "target_id": "relay-service",
            "service": "tgw-context-confirmation-relay.service",
            "properties": service_properties,
        },
    ]
    for unit in sorted(set(provider.services) | set(provider.quiescence_units)):
        service_preimages.append(
            {
                "target_id": "managed-service-"
                + hashlib.sha256(unit.encode()).hexdigest()[:16],
                "service": unit,
                "properties": service_properties,
            }
        )
    service_preimages.append(
        {
            "target_id": "deepseek-user-service",
            "service": fleet_provider_module._DEEPSEEK_USER_SERVICE,
            "actor": "deepseek",
            "uid": fleet_provider_module._DEEPSEEK_UID,
            "unit_path": str(fleet_provider_module._DEEPSEEK_USER_UNIT),
            "unit_sha256": "sha256:" + "d" * 64,
            "unit_mode": 0o444,
            "unit_uid": fleet_provider_module._DEEPSEEK_UID,
            "unit_gid": fleet_provider_module._DEEPSEEK_UID,
            "unit_nlink": 1,
            "unit_file_state": "enabled",
            "runtime_directory": "/run/user/1005",
            "bus_path": "/run/user/1005/bus",
            "runtime_present": False,
            "manager_available": False,
            "linger_path": str(fleet_provider_module._DEEPSEEK_LINGER),
            "linger_present": False,
            "linger_sha256": None,
            "login": {"Linger": "no", "State": "offline", "Sessions": ""},
            "properties": None,
            "parent_identity": None,
        }
    )
    preimage_kinds = {
        item["target_id"]: item["kind"] for item in preimages
    }
    actor_ids = sorted(
        target_id
        for target_id in preimage_kinds
        if target_id.startswith(("actor-", "startup-", "parent-"))
    )
    actor_cache_ids = sorted(
        target_id
        for target_id in preimage_kinds
        if target_id.startswith("actor-cache-")
    )
    provider_state_ids = sorted(
        target_id
        for target_id in preimage_kinds
        if target_id.startswith("provider-state-")
    )
    tmpfiles_ids = sorted(
        target_id
        for target_id in preimage_kinds
        if target_id.startswith("tmpfiles-dir-")
    )
    managed_service_ids = sorted(
        item["target_id"]
        for item in service_preimages
        if item["target_id"].startswith("managed-service-")
    )
    targets_by_action = {
        "INSTALL_PLATFORM_TRUST": [
            ("FILESYSTEM", "actor-public-trust"),
            ("FILESYSTEM", "environment-public-trust"),
            ("FILESYSTEM", "admission-public-trust"),
            ("FILESYSTEM", "provider-config"),
        ],
        "PUBLISH_ADMISSION": [("FILESYSTEM", "release-admission")],
        "INSTALL_CATALOG": [("FILESYSTEM", "environment-catalog")],
        "SELECT_RELEASE": [("FILESYSTEM", "release-selector")],
        "INSTALL_ACTOR_HOST": [
            ("FILESYSTEM", "provider-unit"),
            ("FILESYSTEM", "provider-tmpfiles"),
            *[("FILESYSTEM", target_id) for target_id in tmpfiles_ids],
        ],
        "INSTALL_STABLE_LAUNCHER": [
            ("FILESYSTEM", "stable-launcher"),
            ("FILESYSTEM", "stable-bin-parent"),
        ],
        "INSTALL_DIRECT_STATUS": [
            ("FILESYSTEM", "status-executable"),
            ("FILESYSTEM", "status-sudoers"),
            ("FILESYSTEM", "stable-bin-parent"),
        ],
        "INSTALL_CONFIRMATION_RELAY": [("FILESYSTEM", "relay-unit")],
        "RESTART_PROVIDER": [
            ("SERVICE", "provider-service"),
            ("SERVICE", "relay-service"),
            ("FILESYSTEM", "provider-attestation-receipt"),
        ],
        "BIND_COORDINATOR": [
            ("PROVIDER", "actor-fleet-provider-api"),
            *[("FILESYSTEM", target_id) for target_id in provider_state_ids],
        ],
        "QUIESCE_ACTORS": [
            ("PROVIDER", "actor-fleet-provider-api"),
            *[("SERVICE", target_id) for target_id in managed_service_ids],
        ],
        "REBUILD_ACTORS": [
            ("PROVIDER", "actor-fleet-provider-api"),
            *[("FILESYSTEM", target_id) for target_id in actor_cache_ids],
        ],
        "ACTIVATE_ACTORS": [
            ("PROVIDER", "actor-fleet-provider-api"),
            *[("FILESYSTEM", target_id) for target_id in actor_ids],
        ],
        "VERIFY_COLD_CONTINUITY": [
            ("FILESYSTEM", "transaction-scratch-root"),
            ("FILESYSTEM", "cold-continuity-workspace"),
            ("FILESYSTEM", "cold-continuity-transcript"),
            ("FILESYSTEM", "cold-continuity-receipt"),
        ],
        "TRANSITION_DEEPSEEK_SERVICE": [
            ("SERVICE", "deepseek-user-service"),
            ("FILESYSTEM", "deepseek-service-action-receipt"),
            ("FILESYSTEM", "deepseek-service-progress"),
            ("FILESYSTEM", "deepseek-linger-token"),
            ("FILESYSTEM", "deepseek-linger"),
            ("PROVIDER", "actor-fleet-provider-api"),
        ],
        "RESTART_ACTORS": [
            ("PROVIDER", "actor-fleet-provider-api"),
            *[("SERVICE", target_id) for target_id in managed_service_ids],
        ],
        "HEALTH_ACTORS": [("PROVIDER", "actor-fleet-provider-api")],
        "VERIFY_ACTORS": [("PROVIDER", "actor-fleet-provider-api")],
        "FINALIZE_TRANSACTION": [
            ("FILESYSTEM", "coordinator-terminal-receipt"),
            ("COORDINATOR", "coordinator-progress"),
        ],
    }
    effects = []
    for sequence, (action, effect_targets) in enumerate(
        targets_by_action.items(), 1
    ):
        targets = []
        for target_class, target_id in effect_targets:
            expected_kind = (
                preimage_kinds[target_id]
                if target_class == "FILESYSTEM"
                else {
                    "SERVICE": "service",
                    "PROVIDER": "provider-request",
                    "COORDINATOR": "private-progress",
                }[target_class]
            )
            targets.append(
                {
                    "target_class": target_class,
                    "target_id": target_id,
                    "expected_preimage_kind": expected_kind,
                }
            )
        effects.append(
            {"sequence": sequence, "action": action, "targets": targets}
        )
    unsigned_effect_plan = {
        "schema": "tgw-context-update-effect-plan/v1",
        "transaction_id": transaction_id,
        "effects": effects,
    }
    effect_plan = {
        **unsigned_effect_plan,
        "effect_plan_sha256": _hash(unsigned_effect_plan),
    }
    private = {
        "schema": "tgw-context-update-private-journal/v1",
        "transaction_id": transaction_id,
        "created_at": "2026-08-23T00:00:00Z",
        "nonce": hashlib.sha256(transaction_id.encode()).hexdigest(),
        "request_sha256": root_request_sha256,
        "candidate": {
            "commit": request["revisions"]["source"],
            "tree": request["revisions"]["source_tree"],
            "release_generation": "candidate",
            "release_manifest_sha256": _hash(release_manifest),
            "actor_generation": request["successor_generation"],
            "catalog_sha256": request["revisions"]["catalog"],
            "admission_receipt_sha256": request["revisions"]["admission"],
            "review_receipt_sha256": request["revisions"]["review"],
            "prepared_evidence_sha256": _hash(
                {"prepared": transaction_id}
            ),
        },
        "managed_services": provider.services,
        "quiescence_units": provider.quiescence_units,
        "preimages": preimages,
        "service_preimages": service_preimages,
        "effect_plan": effect_plan,
        "rollback_order": list(range(len(effects), 0, -1)),
    }
    if private_mutator is not None:
        private_mutator(private)
    transaction_root.mkdir(mode=0o700, parents=True)
    transaction_root.chmod(0o700)
    private_path = transaction_root / "private-journal.json"
    private_path.write_text(json.dumps(private, sort_keys=True))
    private_path.chmod(0o600)
    assert len(private_path.read_bytes()) < 8 * 1024 * 1024

    ledger_root = Path(provider.state_root) / "generation-ledger"
    ledger_root.mkdir(mode=0o750, exist_ok=True)
    ledger_root.chmod(0o750)
    os.chown(ledger_root, -1, provider.actor_group_gid)
    existing = sorted(ledger_root.glob("*.json"))
    previous = json.loads(existing[-1].read_text()) if existing else None
    sequence = len(existing) + 1
    admission_receipt = json.loads(
        (
            provider.admission_root
            / (
                request["revisions"]["admission"].removeprefix("sha256:")
                + ".json"
            )
        ).read_text()
    )
    review_receipt = {
        "status": "PASS",
        "candidate_commit": request["revisions"]["source"],
        "solution_hash": request["revisions"]["solution"],
        "receipt_hash": request["revisions"]["review"],
    }
    opening_body = {
        "schema": "tgw-generation-ledger-entry/v1",
        "record_role": "COORDINATOR_OPENING",
        "sequence": sequence,
        "previous_record_sha256": (
            previous["record_sha256"] if previous is not None else None
        ),
        "recorded_at": "2026-08-23T00:00:00Z",
        "transaction_id": transaction_id,
        "provider_status": "PREPARED",
        "request_sha256": root_request_sha256,
        "actor_request_sha256": _hash(request),
        "candidate_commit": request["revisions"]["source"],
        "candidate_tree": request["revisions"]["source_tree"],
        "admission_receipt_hash": request["revisions"]["admission"],
        "review_receipt_hash": request["revisions"]["review"],
        "admission_receipt": admission_receipt,
        "review_receipt": review_receipt,
        "predecessor_actor_public_sha256": "sha256:"
        + hashlib.sha256(
            base64.b64decode(_PREDECESSOR_CONTRACT_PUBLIC_KEY, validate=True)
        ).hexdigest(),
        "successor_actor_public_sha256": "sha256:"
        + hashlib.sha256(
            base64.b64decode(_CONTRACT_PUBLIC_KEY, validate=True)
        ).hexdigest(),
        "trust_projection_sha256": _hash(
            {"trust_projection": transaction_id}
        ),
        "coordinator_journal_sha256": _hash(private),
        "effect_plan_sha256": effect_plan["effect_plan_sha256"],
    }
    opening = {**opening_body, "record_sha256": _hash(opening_body)}
    opening_path = ledger_root / (
        f"{sequence:012d}-{opening['record_sha256'].removeprefix('sha256:')}.json"
    )
    opening_path.write_text(json.dumps(opening, sort_keys=True))
    opening_path.chmod(0o640)
    os.chown(opening_path, -1, provider.actor_group_gid)

    unsigned = {
        "schema": "tgw-context-update-coordinator-binding/v1",
        "outer_transaction_id": transaction_id,
        "actor_request_sha256": _hash(request),
        "coordinator_journal_sha256": _hash(private),
        "coordinator_ledger_opening_sha256": opening["record_sha256"],
        "effect_plan_sha256": effect_plan["effect_plan_sha256"],
    }
    binding = {**unsigned, "binding_sha256": _hash(unsigned)}
    if before_bind is not None:
        before_bind(private_path, opening_path)
    assert provider.bind_coordinator(request, binding) == {
        "status": "COORDINATOR_BOUND",
        "transaction_id": transaction_id,
        "binding_sha256": binding["binding_sha256"],
    }
    stored_journal = json.loads(
        provider._journal_path(transaction_id).read_text()
    )
    return stored_journal["coordinator_binding"]


def _pending_journal(provider, request, *, status="RESTART_REQUIRED"):
    coordinator_binding = _bind_coordinator(provider, request)
    persisted = json.loads(
        provider._journal_path(request["transaction_id"]).read_text()
    )
    obligations = provider._freeze_context_obligations(
        request["actors"], [], direction="successor",
    )
    return {
        "schema": "tgw-actor-fleet-journal/v1",
        "transaction_id": request["transaction_id"],
        "status": status,
        "request": request,
        "candidate_release": None,
        "materialization": None,
        "coordinator_binding": coordinator_binding,
        "private_nonce": persisted["private_nonce"],
        "context_rebind": {
            "schema": "tgw-actor-context-rebind/v2",
            "direction": "successor",
            "baseline": [],
            "obligations": obligations,
            "managed_service_restart_intent": True,
            "managed_service_restart_completed": True,
            "attempts": [],
            "latest": {
                "observed": [],
                "inventory_state": "IDLE_NO_LIVE",
                "pending": [
                    {
                        "obligation_id": obligations[0]["obligation_id"],
                        "reason": "ORDINARY_HARNESS_HANDOFF_REQUIRED",
                    }
                ],
                "dispositions": [],
            },
        },
    }


def test_coordinator_binding_covers_exact_preimages_and_typed_effect_classes(
    durable_path,
):
    provider, request = _reconciliation_provider(durable_path)
    observed_before_effect = []

    def assert_private_state_precedes_provider_effect(private_path, opening_path):
        assert private_path.is_file()
        assert opening_path.is_file()
        assert not provider._journal_path(request["transaction_id"]).exists()
        observed_before_effect.append(True)

    binding = _bind_coordinator(
        provider,
        request,
        before_bind=assert_private_state_precedes_provider_effect,
    )

    private_path = (
        provider.coordinator_transaction_root
        / request["transaction_id"]
        / "private-journal.json"
    )
    private = json.loads(private_path.read_text())
    absent = [item for item in private["preimages"] if item["kind"] == "absent"]
    assert absent
    assert all(
        [item[name] for name in ("mode", "uid", "gid", "nlink")]
        == [None, None, None, None]
        and item["payload"] == {}
        for item in absent
    )
    directories = [
        item["payload"]
        for item in private["preimages"]
        if item["kind"] == "directory"
    ]
    assert {item["coverage"] for item in directories} == {
        "metadata-only",
        "recursive",
    }
    recursive = next(
        item for item in directories if item["coverage"] == "recursive"
    )
    assert recursive["entries"][0]["relative_path"] == "10-provider.conf"
    assert {
        target["target_class"]
        for effect in private["effect_plan"]["effects"]
        for target in effect["targets"]
    } == {
        "COORDINATOR",
        "FILESYSTEM",
        "PROVIDER",
        "SERVICE",
    }
    assert private["rollback_order"] == list(
        range(len(private["effect_plan"]["effects"]), 0, -1)
    )
    assert request["revisions"]["review"] == "sha256:" + "6" * 64
    assert binding["coordinator_journal_sha256"] == _hash(private)
    trust = provider._journal(request["transaction_id"])[
        "coordinator_binding"
    ]["contract_trust"]
    assert trust["predecessor_contract_public_key"] == (
        _PREDECESSOR_CONTRACT_PUBLIC_KEY
    )
    assert trust["successor_contract_public_key"] == _CONTRACT_PUBLIC_KEY
    assert set(trust["startup_preimage_sha256"]) == set(request["actors"])
    assert trust["trust_sha256"] == _hash(
        {name: value for name, value in trust.items() if name != "trust_sha256"}
    )
    assert observed_before_effect == [True]


@pytest.mark.parametrize(
    "wrong_direction",
    ("provider-config-uses-successor", "startup-uses-successor"),
)
def test_coordinator_binding_rejects_wrong_direction_contract_trust(
    durable_path,
    wrong_direction,
):
    provider, request = _reconciliation_provider(durable_path)

    def mutate(private):
        target_id = (
            "provider-config"
            if wrong_direction == "provider-config-uses-successor"
            else f"startup-{request['actors'][0]}"
        )
        preimage = next(
            item
            for item in private["preimages"]
            if item["target_id"] == target_id
        )
        value = json.loads(
            base64.b64decode(preimage["payload"]["content"], validate=True)
        )
        if target_id == "provider-config":
            value["actor_fleet_provider"]["contract_public_key"] = (
                _CONTRACT_PUBLIC_KEY
            )
        else:
            value["trusted_public_key"] = _CONTRACT_PUBLIC_KEY
        preimage["payload"]["content"] = base64.b64encode(
            json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        ).decode()

    with pytest.raises(
        ActorFleetError,
        match="predecessor actor startup trust binding differs",
    ):
        _bind_coordinator(provider, request, private_mutator=mutate)


@pytest.mark.parametrize("drift", ("omitted", "extra"))
def test_coordinator_binding_refuses_destination_set_drift(
    durable_path,
    drift,
):
    provider, request = _reconciliation_provider(durable_path)

    def mutate(private):
        if drift == "omitted":
            private["preimages"] = [
                item
                for item in private["preimages"]
                if item["target_id"] != "stable-launcher"
            ]
        else:
            private["preimages"].append(
                {
                    "target_id": "unexpected-extra",
                    "path": "/opt/TGW/tgw-lib/var/context-update/unexpected-extra",
                    "kind": "absent",
                    "mode": None,
                    "uid": None,
                    "gid": None,
                    "nlink": None,
                    "payload": {},
                }
            )

    with pytest.raises(
        ActorFleetError,
        match="coordinator preimage destination set differs",
    ):
        _bind_coordinator(
            provider,
            request,
            private_mutator=mutate,
        )


def _restart_only_provider(durable_path, scenario):
    config, request, _destination = _fixture(durable_path)
    baseline, inventories = scenario(request)
    service_calls = []

    def service(arguments):
        service_calls.append(arguments)
        return subprocess.CompletedProcess(arguments, 0, "active\n", "")

    sequence = iter(inventories)
    provider = _new_provider(
        config,
        durable_path,
        service_runner=service,
        actor_context_process_inventory=lambda _actors: next(sequence),
    )
    provider._startup_binding = lambda _actor, _request: {
        "schema": "tgw-actor-startup-binding/v3",
        "actor": request["actors"][0],
        "trusted_public_key": config["contract_public_key"],
        "expected_generation": request["successor_generation"],
        "expected_plan_commit": request["revisions"]["plan"],
        "expected_solution_hash": request["revisions"]["solution"],
        "expected_source_commit": request["revisions"]["source"],
        "expected_source_tree": request["revisions"]["source_tree"],
        "expected_catalog_hash": request["revisions"]["catalog"],
        "context_source_root": str(
            _protected_fixture_root(durable_path) / "releases/candidate"
        ),
        "fleet_convergence_path": str(
            _protected_fixture_root(durable_path) / "state/fleet-convergence.json"
        ),
        "stable_launcher_path": str(
            fleet_provider_module._STABLE_CONTEXT_LAUNCHER
        ),
    }
    obligations = provider._freeze_context_obligations(
        request["actors"], baseline, direction="successor",
    )
    coordinator_binding = _bind_coordinator(provider, request)
    persisted = json.loads(
        provider._journal_path(request["transaction_id"]).read_text()
    )
    provider._save(
        {
            "schema": "tgw-actor-fleet-journal/v1",
            "transaction_id": request["transaction_id"],
            "status": "ACTIVATED",
            "request": request,
            "candidate_release": None,
            "materialization": None,
            "coordinator_binding": coordinator_binding,
            "private_nonce": persisted["private_nonce"],
            "context_rebind": {
                "schema": "tgw-actor-context-rebind/v2",
                "direction": "successor",
                "baseline": baseline,
                "obligations": obligations,
                "managed_service_restart_intent": False,
                "attempts": [],
            },
        }
    )
    activated = {
        "status": "ACTIVATED",
        "transaction_id": request["transaction_id"],
        "generation": request["successor_generation"],
    }
    return provider, request, activated, service_calls


def test_idle_context_path_stays_pending_without_transaction_bound_handoff(
    durable_path,
):
    provider, request = _reconciliation_provider(durable_path)
    obligations = provider._freeze_context_obligations(
        request["actors"], [], direction="successor",
    )
    assert len(obligations) == 1
    obligation = obligations[0]
    assert obligation["baseline_state"] == "IDLE"

    forged = _handoff_confirmation(
        request,
        obligation,
        process_identity_hash=_hash({"exited-process": 1}),
        parent_identity_hash=_hash({"ordinary-harness": 1}),
        transaction_id="another-fleet-transaction",
    )
    for confirmations in (None, {obligation["obligation_id"]: forged}):
        pending, dispositions = provider._reconcile_context_obligations(
            obligations, [], request, confirmations,
        )
        assert dispositions == []
        assert pending == [
            {
                "obligation_id": obligation["obligation_id"],
                "reason": "ORDINARY_HARNESS_HANDOFF_REQUIRED",
            }
        ]


def test_confirmed_idle_context_handoff_accepts_unique_current_or_confirmed_exit(
    durable_path,
    strict_runtime,
):
    provider, request = _reconciliation_provider(durable_path)
    obligation = provider._freeze_context_obligations(
        request["actors"], [], direction="successor",
    )[0]
    parent = _ordinary_harness_parent()
    successor = _rebind_process(
        request,
        durable_path,
        pid=42001,
        parent=parent,
        current=True,
        label="idle-successor",
    )
    confirmation = _handoff_confirmation(
        request,
        obligation,
        process_identity_hash=successor["identity_hash"],
        parent_identity_hash=parent["identity_hash"],
    )
    confirmations = {obligation["obligation_id"]: confirmation}

    pending, dispositions = provider._reconcile_context_obligations(
        [obligation], [successor], request, confirmations,
    )
    assert pending == []
    assert dispositions == [
        {
            "obligation_id": obligation["obligation_id"],
            "disposition": "IDLE_TO_CURRENT_CONFIRMED",
            "successor_identity_hash": successor["identity_hash"],
            "client_confirmation_hash": confirmation["confirmation_hash"],
        }
    ]

    pending, dispositions = provider._reconcile_context_obligations(
        [obligation], [], request, confirmations,
    )
    assert pending == []
    assert dispositions == [
        {
            "obligation_id": obligation["obligation_id"],
            "disposition": "IDLE_CONFIRMED_HANDOFF_COMPLETE",
            "client_confirmation_hash": confirmation["confirmation_hash"],
        }
    ]


def test_idle_to_current_same_session_confirmation_is_persisted_and_reconciled(
    durable_path,
    strict_runtime,
):
    provider, request = _reconciliation_provider(durable_path)
    journal = _pending_journal(provider, request, status="RESTART_REQUIRED")
    obligation = journal["context_rebind"]["obligations"][0]
    assert obligation["baseline_state"] == "IDLE"
    parent = _ordinary_harness_parent(label="idle-confirmation-parent")
    successor = _rebind_process(
        request,
        durable_path,
        pid=42002,
        parent=parent,
        current=True,
        label="idle-confirmation-successor",
    )
    provider._actor_context_process_inventory = lambda _actors: [successor]
    provider._save(journal)
    projection = json.loads(
        (Path(provider.state_root) / "fleet-convergence.json").read_text()
    )
    process_identity_fields = (
        "boot_id", "pid", "start_ticks", "uid", "ppid", "executable_path",
        "executable_device", "executable_inode", "executable_sha256",
        "cmdline_shape", "cmdline_sha256", "identity_hash",
    )
    status = {
        "runtime": {
            "process": {
                name: successor[name] for name in process_identity_fields
            }
        },
        "startup": {
            "actor": request["actors"][0],
            "generation": request["successor_generation"],
        },
        "fleet_convergence": projection,
        "plan": {
            "approved_commit": request["revisions"]["plan"],
            "approved_solution_hash": request["revisions"]["solution"],
        },
        "source": {"commit": request["revisions"]["source"]},
        "environment": {"catalog_hash": request["revisions"]["catalog"]},
    }
    status["context_sha256"] = _hash(status)

    first = provider.confirm_context_rebind(
        {
            "schema": "tgw-context-client-confirmation/v1",
            "transaction_id": request["transaction_id"],
            "direction": "successor",
            "obligation_id": obligation["obligation_id"],
            "status": status,
        }
    )
    assert first == {
        "status": "RETRY_REQUIRED",
        "transaction_id": request["transaction_id"],
        "obligation_id": first["obligation_id"],
        "previous_obligation_id": obligation["obligation_id"],
    }
    assert first["obligation_id"] != obligation["obligation_id"]

    status["fleet_convergence"] = json.loads(
        (Path(provider.state_root) / "fleet-convergence.json").read_text()
    )
    status.pop("context_sha256")
    status["context_sha256"] = _hash(status)
    result = provider.confirm_context_rebind(
        {
            "schema": "tgw-context-client-confirmation/v1",
            "transaction_id": request["transaction_id"],
            "direction": "successor",
            "obligation_id": first["obligation_id"],
            "status": status,
        }
    )
    assert result["status"] == "CONFIRMED"
    latest = provider._journal(request["transaction_id"])["context_rebind"]
    confirmation = latest["confirmations"][first["obligation_id"]]
    assert confirmation["process_identity_hash"] == successor["identity_hash"]
    assert confirmation["parent_identity_hash"] == parent["identity_hash"]

    pending, dispositions = provider._reconcile_context_obligations(
        latest["obligations"],
        [successor],
        request,
        latest["confirmations"],
    )
    assert pending == []
    assert dispositions[-1]["disposition"] == "LATE_CURRENT_CONFIRMED"


def test_multiple_old_children_from_one_parent_require_one_confirmed_successor(
    durable_path,
    strict_runtime,
):
    provider, request = _reconciliation_provider(durable_path)
    parent = _ordinary_harness_parent()
    old_children = [
        _rebind_process(
            request,
            durable_path,
            pid=pid,
            parent=parent,
            current=False,
            label=label,
        )
        for pid, label in ((42011, "old-one"), (42012, "old-two"))
    ]
    obligations = provider._freeze_context_obligations(
        request["actors"], old_children, direction="successor",
    )
    assert len(obligations) == 1
    obligation = obligations[0]
    assert obligation["baseline_state"] == "LIVE"
    assert obligation["baseline"]["child_identity_hashes"] == sorted(
        child["identity_hash"] for child in old_children
    )

    successor = _rebind_process(
        request,
        durable_path,
        pid=42013,
        parent=parent,
        current=True,
        label="one-successor",
    )
    confirmation = _handoff_confirmation(
        request,
        obligation,
        process_identity_hash=successor["identity_hash"],
        parent_identity_hash=parent["identity_hash"],
    )
    pending, dispositions = provider._reconcile_context_obligations(
        obligations,
        [successor],
        request,
        {obligation["obligation_id"]: confirmation},
    )
    assert pending == []
    assert dispositions == [
        {
            "obligation_id": obligation["obligation_id"],
            "disposition": "CURRENT_SUCCESSOR_OBSERVED",
            "successor_identity_hash": successor["identity_hash"],
            "retired_child_identity_hashes": sorted(
                child["identity_hash"] for child in old_children
            ),
            "client_confirmation_hash": confirmation["confirmation_hash"],
        }
    ]


@pytest.mark.parametrize("late_is_current", (False, True), ids=("stale", "current"))
def test_same_parent_late_child_extends_one_path_and_uses_one_successor(
    durable_path,
    strict_runtime,
    late_is_current,
):
    provider, request = _reconciliation_provider(durable_path)
    parent = _ordinary_harness_parent(label=f"late-same-parent-{late_is_current}")
    old = _rebind_process(
        request,
        durable_path,
        pid=42014,
        parent=parent,
        current=False,
        label="checkpoint-old",
    )
    obligations = provider._freeze_context_obligations(
        request["actors"], [old], direction="successor",
    )
    journal = _pending_journal(provider, request, status="ACTIVATED")
    journal["context_rebind"]["baseline"] = [old]
    journal["context_rebind"]["obligations"] = obligations
    late = _rebind_process(
        request,
        durable_path,
        pid=42015,
        parent=parent,
        current=late_is_current,
        label="late-child",
    )
    successor = late
    observed = [old, late]
    if not late_is_current:
        successor = _rebind_process(
            request,
            durable_path,
            pid=42016,
            parent=parent,
            current=True,
            label="single-successor",
        )
        observed.append(successor)

    rebind = provider._capture_late_context_paths(journal, observed, request)

    assert len(rebind["obligations"]) == 1
    obligation = rebind["obligations"][0]
    assert obligation["obligation_id"] == obligations[0]["obligation_id"]
    baseline = obligation["baseline"]
    assert baseline["target_child_identity_hashes"] == [
        successor["identity_hash"]
    ]
    assert baseline["child_identity_hashes"] == sorted(
        [old["identity_hash"]]
        + ([] if late_is_current else [late["identity_hash"]])
    )
    addition = rebind["late_arrivals"][-1]["path_additions"]
    assert {item["disposition"] for item in addition} == (
        {"LATE_ARRIVAL_CURRENT"}
        if late_is_current
        else {"LATE_ARRIVAL_CURRENT", "LATE_ARRIVAL_STALE"}
    )
    confirmation = _handoff_confirmation(
        request,
        obligation,
        process_identity_hash=successor["identity_hash"],
        parent_identity_hash=parent["identity_hash"],
    )
    pending, dispositions = provider._reconcile_context_obligations(
        [obligation],
        [successor],
        request,
        {obligation["obligation_id"]: confirmation},
    )
    assert pending == []
    assert dispositions[0]["successor_identity_hash"] == successor["identity_hash"]
    assert dispositions[0]["retired_child_identity_hashes"] == sorted(
        baseline["child_identity_hashes"]
    )


@pytest.mark.parametrize(
    ("drift_field", "drift_value"),
    [
        ("executable_path", "/opt/tgw-test/bin/parent-mimic"),
        ("cmdline_sha256", "sha256:" + "c" * 64),
    ],
    ids=("executable-mismatch", "cmdline-mismatch"),
)
def test_same_uid_parent_mimic_cannot_satisfy_live_context_obligation(
    durable_path, strict_runtime, drift_field, drift_value,
):
    provider, request = _reconciliation_provider(durable_path)
    parent = _ordinary_harness_parent()
    old = _rebind_process(
        request,
        durable_path,
        pid=42021,
        parent=parent,
        current=False,
        label="old-child",
    )
    obligation = provider._freeze_context_obligations(
        request["actors"], [old], direction="successor",
    )[0]

    mimic_body = {key: value for key, value in parent.items() if key != "identity_hash"}
    mimic_body[drift_field] = drift_value
    mimic = {**mimic_body, "identity_hash": _hash(mimic_body)}
    assert mimic["uid"] == parent["uid"]
    successor = _rebind_process(
        request,
        durable_path,
        pid=42022,
        parent=mimic,
        current=True,
        label=f"mimic-{drift_field}",
    )
    confirmation = _handoff_confirmation(
        request,
        obligation,
        process_identity_hash=successor["identity_hash"],
        parent_identity_hash=mimic["identity_hash"],
    )

    pending, dispositions = provider._reconcile_context_obligations(
        [obligation],
        [successor],
        request,
        {obligation["obligation_id"]: confirmation},
    )
    assert dispositions == []
    assert pending == [
        {
            "obligation_id": obligation["obligation_id"],
            "reason": "CURRENT_SUCCESSOR_PATH_NOT_UNIQUE",
        },
        {
            "reason": "UNEXPECTED_CURRENT_PATH",
            "identities": [successor["identity_hash"]],
        },
    ]


def test_late_current_path_is_checkpointed_and_requires_bound_confirmation(
    durable_path,
    strict_runtime,
):
    provider, request = _reconciliation_provider(durable_path)
    journal = _pending_journal(provider, request, status="ACTIVATED")
    parent = _ordinary_harness_parent(label="late-current-parent")
    late_current = _rebind_process(
        request,
        durable_path,
        pid=42031,
        parent=parent,
        current=True,
        label="late-current",
    )

    rebind = provider._capture_late_context_paths(
        journal, [late_current], request,
    )
    assert len(rebind["obligations"]) == 2
    late_obligation = rebind["obligations"][1]
    assert late_obligation["baseline_state"] == "LATE_CURRENT"
    assert late_obligation["checkpoint_disposition"] == "LATE_ARRIVAL"
    assert late_obligation["baseline"]["child_identity_hashes"] == []
    assert late_obligation["baseline"]["target_child_identity_hashes"] == [
        late_current["identity_hash"],
    ]
    projection = json.loads(
        (Path(provider.state_root) / "fleet-convergence.json").read_text()
    )
    projected_late = projection["transaction"]["obligations"][1]
    assert projected_late["baseline_state"] == "LATE_CURRENT"
    assert projected_late["checkpoint_disposition"] == "LATE_ARRIVAL"

    pending, dispositions = provider._reconcile_context_obligations(
        rebind["obligations"], [late_current], request,
    )
    assert dispositions == [
        {
            "obligation_id": rebind["obligations"][0]["obligation_id"],
            "disposition": "IDLE_BASELINE_LATE_PATH_TRACKED",
        }
    ]
    assert pending == [
        {
            "obligation_id": late_obligation["obligation_id"],
            "reason": "SAME_CLIENT_SESSION_CONFIRMATION_REQUIRED",
            "process_identity_hash": late_current["identity_hash"],
        },
        {
            "reason": "UNEXPECTED_CURRENT_PATH",
            "identities": [late_current["identity_hash"]],
        },
    ]

    confirmation = _handoff_confirmation(
        request,
        late_obligation,
        process_identity_hash=late_current["identity_hash"],
        parent_identity_hash=parent["identity_hash"],
    )
    confirmations = {late_obligation["obligation_id"]: confirmation}
    pending, dispositions = provider._reconcile_context_obligations(
        rebind["obligations"], [late_current], request, confirmations,
    )
    assert pending == []
    assert dispositions[-1] == {
        "obligation_id": late_obligation["obligation_id"],
        "disposition": "LATE_CURRENT_CONFIRMED",
        "successor_identity_hash": late_current["identity_hash"],
        "client_confirmation_hash": confirmation["confirmation_hash"],
    }

    pending, dispositions = provider._reconcile_context_obligations(
        rebind["obligations"], [], request, confirmations,
    )
    assert pending == []
    assert dispositions[-1] == {
        "obligation_id": late_obligation["obligation_id"],
        "disposition": "LATE_CURRENT_HANDOFF_COMPLETE",
        "client_confirmation_hash": confirmation["confirmation_hash"],
    }


def test_late_stale_path_must_retire_before_one_confirmed_current_successor(
    durable_path,
    strict_runtime,
):
    provider, request = _reconciliation_provider(durable_path)
    journal = _pending_journal(provider, request, status="ACTIVATED")
    parent = _ordinary_harness_parent(label="late-stale-parent")
    late_stale = _rebind_process(
        request,
        durable_path,
        pid=42041,
        parent=parent,
        current=False,
        label="late-stale",
    )
    rebind = provider._capture_late_context_paths(
        journal, [late_stale], request,
    )
    late_obligation = rebind["obligations"][1]
    assert late_obligation["baseline_state"] == "LATE_STALE"
    assert late_obligation["checkpoint_disposition"] == "LATE_ARRIVAL"
    assert late_obligation["baseline"]["child_identity_hashes"] == [
        late_stale["identity_hash"],
    ]

    pending, _dispositions = provider._reconcile_context_obligations(
        rebind["obligations"], [late_stale], request,
    )
    assert pending == [
        {
            "obligation_id": late_obligation["obligation_id"],
            "reason": "OLD_IDENTITIES_STILL_LIVE",
            "identities": [late_stale["identity_hash"]],
        }
    ]

    successor = _rebind_process(
        request,
        durable_path,
        pid=42042,
        parent=parent,
        current=True,
        label="late-stale-successor",
    )
    confirmation = _handoff_confirmation(
        request,
        late_obligation,
        process_identity_hash=successor["identity_hash"],
        parent_identity_hash=parent["identity_hash"],
    )
    pending, dispositions = provider._reconcile_context_obligations(
        rebind["obligations"],
        [successor],
        request,
        {late_obligation["obligation_id"]: confirmation},
    )
    assert pending == []
    assert dispositions[-1] == {
        "obligation_id": late_obligation["obligation_id"],
        "disposition": "CURRENT_SUCCESSOR_OBSERVED",
        "successor_identity_hash": successor["identity_hash"],
        "retired_child_identity_hashes": [late_stale["identity_hash"]],
        "client_confirmation_hash": confirmation["confirmation_hash"],
    }


def test_explicit_supersession_binds_exact_old_journal_and_selects_successor(
    durable_path,
):
    provider, base_request = _reconciliation_provider(durable_path)
    old_request = _transaction_request(base_request, "historical-refresh")
    successor_request = _transaction_request(base_request, "successor-refresh")
    old_journal = _pending_journal(provider, old_request)
    provider._save(old_journal)
    successor_journal = _pending_journal(provider, successor_request)

    with pytest.raises(
        ActorFleetError,
        match="unsuperseded nonterminal transactions",
    ):
        provider._save(successor_journal)
    ambiguous = json.loads(
        (Path(provider.state_root) / "fleet-convergence.json").read_text()
    )
    assert ambiguous["state"] == "AMBIGUOUS"
    assert ambiguous["generation_status"] == "HOLD"
    assert provider.generation_status()["status"] == "HOLD"

    inventory = provider.nonterminal_transactions()
    by_id = {
        item["transaction_id"]: item for item in inventory["transactions"]
    }
    assert set(by_id) == {"historical-refresh", "successor-refresh"}
    assert by_id["historical-refresh"]["journal_sha256"] == _hash(old_journal)
    pointer_before = provider._active_fleet_pointer()
    assert pointer_before["transaction_id"] == "historical-refresh"

    request = {
        "schema": "tgw-fleet-supersession-request/v1",
        "successor_transaction_id": "successor-refresh",
        "records": [
            {
                "transaction_id": "historical-refresh",
                "journal_sha256": "sha256:" + "0" * 64,
                "disposition": "ABANDONED_NONTERMINAL",
            }
        ],
    }
    with pytest.raises(ActorFleetError, match="supersession binding differs"):
        provider.supersede_transactions(request)
    assert provider._active_fleet_pointer() == pointer_before

    request["records"][0]["journal_sha256"] = by_id["historical-refresh"][
        "journal_sha256"
    ]
    supersession_root = provider._fleet_supersession_root
    supersession_root.mkdir(mode=0o750)
    supersession_root.chmod(0o750)
    os.chown(supersession_root, -1, provider.actor_group_gid)
    result = provider.supersede_transactions(request)
    assert result["status"] == "SUPERSEDED"
    assert result["superseded_transaction_ids"] == ["historical-refresh"]
    pointer = provider._active_fleet_pointer()
    assert pointer["transaction_id"] == "successor-refresh"
    projection = json.loads(
        (Path(provider.state_root) / "fleet-convergence.json").read_text()
    )
    assert projection["state"] == "ACTIVE"
    assert projection["active_transaction_ids"] == ["successor-refresh"]
    assert projection["active_pointer_sha256"] == pointer["pointer_sha256"]
    assert projection["transaction"]["transaction_id"] == "successor-refresh"
    supersession = json.loads(
        (
            Path(provider.state_root)
            / "fleet-supersessions/historical-refresh.json"
        ).read_text()
    )
    assert supersession["superseded_journal_sha256"] == _hash(old_journal)
    assert supersession["successor_transaction_id"] == "successor-refresh"
    assert projection["supersessions_sha256"] == _hash(
        {"historical-refresh": supersession}
    )


def test_private_provider_payload_precedes_sanitized_phase_ledger_and_repairs(
    durable_path, monkeypatch,
):
    provider, request = _reconciliation_provider(durable_path)
    journal = _pending_journal(provider, request, status="ACTIVATED")
    journal_path = provider._journal_path(request["transaction_id"])
    ledger_root = Path(provider.state_root) / "generation-ledger"
    opening_segments = sorted(ledger_root.glob("*.json"))
    assert len(opening_segments) == 1
    opening = json.loads(opening_segments[0].read_text())
    assert opening["record_role"] == "COORDINATOR_OPENING"
    real_append = provider._append_generation_ledger
    observed_pending = {}

    def crash_before_provider_ledger(value):
        on_disk = json.loads(journal_path.read_text())
        observed_pending.update(on_disk)
        assert on_disk["ledger_pending"] is True
        assert on_disk["journal_payload_sha256"] == _hash(
            {
                key: item
                for key, item in on_disk.items()
                if key not in {
                    "journal_payload_sha256", "ledger_pending",
                    "ledger_sequence", "ledger_record_sha256",
                    "ledger_evidence",
                }
            }
        )
        raise RuntimeError("simulated pre-ledger crash")

    monkeypatch.setattr(provider, "_append_generation_ledger", crash_before_provider_ledger)
    with pytest.raises(RuntimeError, match="simulated pre-ledger crash"):
        provider._save(journal)
    assert observed_pending["status"] == "ACTIVATED"
    assert len(list(ledger_root.glob("*.json"))) == 1

    monkeypatch.setattr(provider, "_append_generation_ledger", real_append)
    repaired = provider._journal(request["transaction_id"])
    assert repaired["ledger_pending"] is False
    assert repaired["journal_payload_sha256"] == observed_pending[
        "journal_payload_sha256"
    ]
    segments = sorted(ledger_root.glob("*.json"))
    assert len(segments) == 2
    phase = json.loads(segments[-1].read_text())
    assert phase["record_role"] == "PROVIDER_PHASE"
    assert phase["previous_record_sha256"] == opening["record_sha256"]
    assert phase["journal_payload_sha256"] == repaired["journal_payload_sha256"]
    assert phase["coordinator_journal_sha256"] == repaired[
        "coordinator_binding"
    ]["coordinator_journal_sha256"]


@pytest.mark.parametrize("tamper", ["content", "hard-link"])
def test_generation_ledger_refuses_chain_or_link_count_tamper(
    durable_path, tamper,
):
    provider, request = _reconciliation_provider(durable_path)
    journal = _pending_journal(provider, request, status="ACTIVATED")
    provider._save(journal)
    ledger_root = Path(provider.state_root) / "generation-ledger"
    segments = sorted(ledger_root.glob("*.json"))
    assert [json.loads(path.read_text())["record_role"] for path in segments] == [
        "COORDINATOR_OPENING",
        "PROVIDER_PHASE",
    ]
    segment = segments[-1]
    if tamper == "content":
        record = json.loads(segment.read_text())
        record["provider_status"] = "FORGED"
        segment.chmod(0o644)
        segment.write_text(json.dumps(record))
        segment.chmod(0o640)
        message = "ledger chain differs"
    else:
        os.link(segment, durable_path / "ledger-segment-hard-link")
        message = "ledger segment is not protected"

    journal["status"] = "RESTART_REQUIRED"
    with pytest.raises(ActorFleetError, match=message):
        provider._save(journal)
    assert len(list(ledger_root.glob("*.json"))) == len(segments)


def test_actor_restart_waits_for_harness_and_never_kills_direct_context_child(
    durable_path, monkeypatch,
):
    def scenario(request):
        process = _rebind_process(
            request,
            durable_path,
            pid=41001,
            parent=_ordinary_harness_parent(),
            current=False,
            label="direct-child",
        )
        process["stable_launcher"] = False
        return [process], [[process]]

    provider, _request_value, activated, service_calls = _restart_only_provider(
        durable_path, scenario,
    )
    kills = []
    monkeypatch.setattr(os, "kill", lambda *arguments: kills.append(arguments))

    result = provider.restart(activated)

    assert result["status"] == "RESTART_REQUIRED"
    assert result["lifecycle"] == "WAIT_EXTERNAL_RESTART"
    assert {item["reason"] for item in result["pending"]} == {
        "OLD_IDENTITIES_STILL_LIVE",
    }
    assert service_calls == [
        ["restart", "tgw-coding-provision-pull.timer"],
        ["is-active", "tgw-coding-provision-pull.timer"],
    ]
    assert kills == []


def test_actor_restart_keeps_vacuous_empty_observation_pending(durable_path):
    def scenario(request):
        old = _rebind_process(
            request,
            durable_path,
            pid=41002,
            parent=_ordinary_harness_parent(),
            current=False,
            label="departed-without-successor",
        )
        return [old], [[]]

    provider, _request_value, activated, _service_calls = _restart_only_provider(
        durable_path, scenario,
    )

    result = provider.restart(activated)

    assert result["status"] == "RESTART_REQUIRED"
    assert result["pending"] == [
        {
            "obligation_id": provider._journal(
                activated["transaction_id"]
            )["context_rebind"]["obligations"][0]["obligation_id"],
            "reason": "CURRENT_SUCCESSOR_PATH_NOT_UNIQUE",
        }
    ]


def test_actor_restart_requires_successor_and_is_retryable_when_already_current(
    durable_path,
    strict_runtime,
):
    scenario_state = {}

    def scenario(request):
        parent = _ordinary_harness_parent()
        old = _rebind_process(
            request,
            durable_path,
            pid=41003,
            parent=parent,
            current=False,
            label="retry-old",
        )
        successor = _rebind_process(
            request,
            durable_path,
            pid=41004,
            parent=parent,
            current=True,
            label="retry-successor",
        )
        scenario_state.update({"parent": parent, "successor": successor})
        return [old], [[successor], [successor], [successor]]

    provider, request, activated, service_calls = _restart_only_provider(
        durable_path, scenario,
    )
    successor = scenario_state["successor"]
    journal = provider._journal(request["transaction_id"])
    obligation = journal["context_rebind"]["obligations"][0]
    confirmation = _handoff_confirmation(
        request,
        obligation,
        process_identity_hash=successor["identity_hash"],
        parent_identity_hash=scenario_state["parent"]["identity_hash"],
    )
    journal["context_rebind"]["confirmations"] = {
        obligation["obligation_id"]: confirmation,
    }
    provider._save(journal)

    result = provider.restart(activated)
    assert result["status"] == "RESTARTED"
    assert provider.health(result)["status"] == "HEALTHY"

    # A replay after convergence neither restarts services again nor disconnects it.
    journal = provider._journal(request["transaction_id"])
    journal["status"] = "RESTART_REQUIRED"
    provider._save(journal)
    retry = provider.restart(activated)
    assert retry["status"] == "RESTARTED"
    assert sum(call[0] == "restart" for call in service_calls) == 1


def test_actor_provider_holds_on_drifted_attempt_root(durable_path):
    config, _request, _destination = _fixture(durable_path)
    Path(config["attempt_cache_root"]).chmod(0o750)
    with pytest.raises(ValueError, match="mode 2770"):
        ActorFleetProvider(config)


def test_actor_provider_materializes_verifies_repairs_and_rolls_back(durable_path, monkeypatch):
    config, request, destination = _fixture(durable_path)
    monkeypatch.setattr(
        "tgw.actor_fleet_provider.validate_context_source",
        lambda source_root, _git, *, expected_commit, expected_tree: (
            Path(source_root).resolve(), expected_commit, expected_tree,
        ),
    )
    actor = request["actors"][0]
    predecessor_binding = {
        "schema": "tgw-actor-startup-binding/v1",
        "actor": actor,
        "trusted_public_key": _PREDECESSOR_CONTRACT_PUBLIC_KEY,
        "expected_generation": request["predecessor_generation"],
        "expected_plan_commit": "a" * 40,
        "expected_solution_hash": "sha256:" + "2" * 64,
        "expected_source_commit": "b" * 40,
        "expected_catalog_hash": "sha256:" + "5" * 64,
    }
    startup = Path(config["startup_binding_root"]) / f"{actor}-startup.json"
    startup.write_text(json.dumps(predecessor_binding))
    startup.chmod(0o444)
    service_state = {"value": "active"}

    def service(arguments):
        if arguments[0] == "stop":
            service_state["value"] = "inactive"
        if arguments[0] == "restart":
            service_state["value"] = "active"
        return subprocess.CompletedProcess(arguments, 0, service_state["value"] + "\n", "")

    materializer = _Materializer(destination_root=destination.parents[2])
    provider = _new_provider(
        config,
        durable_path,
        service_runner=service,
        materializer_loader=lambda _: materializer,
        current_time=lambda: datetime(2026, 8, 21, 12, tzinfo=timezone.utc),
        actor_context_probe=lambda actor, _path, _source, value: _context_proof(actor, value),
        actor_context_process_inventory=lambda _actors: [],
    )
    _bind_coordinator(provider, request)
    assert provider.quiesce(request)["status"] == "QUIESCED"
    rebuilt = provider.rebuild(request)
    assert materializer.calls[-1] == (False, True)
    activated = provider.activate(request, rebuilt)
    assert materializer.calls[-1] == (True, True)

    def redirect_actor_inputs(target_provider):
        exact_inputs = target_provider._actor_inputs

        def local_inputs(release, value):
            loaded, bundle, contracts = exact_inputs(release, value)
            local_bundle = json.loads(json.dumps(bundle))
            for name, specification in local_bundle["actors"].items():
                declared_home = Path(specification["home"])
                for binding in specification["bindings"]:
                    bound_destination = Path(binding["destination"])
                    if (
                        bound_destination == declared_home
                        or declared_home in bound_destination.parents
                    ):
                        binding["destination"] = str(
                            destination.parents[2]
                            / bound_destination.relative_to(declared_home)
                        )
            return loaded, local_bundle, contracts

        monkeypatch.setattr(target_provider, "_actor_inputs", local_inputs)

    redirect_actor_inputs(provider)
    journal = provider._journal(request["transaction_id"])
    obligation = journal["context_rebind"]["obligations"][0]
    confirmation = _handoff_confirmation(
        request,
        obligation,
        process_identity_hash=_hash({"exited-forward-context": actor}),
        parent_identity_hash=_hash({"forward-ordinary-harness": actor}),
    )
    journal["context_rebind"]["confirmations"] = {
        obligation["obligation_id"]: confirmation,
    }
    provider._save(journal)
    restarted = provider.restart(activated)
    assert provider.health(restarted)["status"] == "HEALTHY"
    verified = provider.verify_actor(request["actors"][0], request)
    assert verified["status"] == "VERIFIED"
    assert verified["context_mcp_proof_hash"].startswith("sha256:")

    production_provider = _new_provider(
        config,
        durable_path,
        service_runner=service,
        materializer_loader=lambda _: materializer,
        current_time=lambda: datetime(2026, 8, 21, 12, tzinfo=timezone.utc),
        actor_context_process_inventory=lambda _actors: [],
    )
    redirect_actor_inputs(production_provider)
    subprocess_calls = []

    def run_worker(command, **kwargs):
        subprocess_calls.append((command, kwargs))
        source_root = Path(config["release_root"]) / "candidate/src"
        assert command == [
            sys.executable,
            "-I",
            "-s",
            "-P",
            "-c",
            _ACTOR_VERIFICATION_BOOTSTRAP,
            str(source_root),
            "--verify-actor-worker",
        ]
        assert kwargs["cwd"] == source_root
        assert kwargs["env"].get("PYTHONPATH") is None
        assert kwargs["env"]["PYTHONNOUSERSITE"] == "1"
        assert kwargs["env"]["PYTHONSAFEPATH"] == "1"
        payload = json.loads(kwargs["input"])
        proof = _actor_verification_payload(
            payload["actor"],
            payload["request"],
            payload["bindings"],
            payload["bundle"],
            payload["contract"],
            lambda actor, _path, _source, value: _context_proof(actor, value),
        )
        return subprocess.CompletedProcess(
            command,
            0,
            json.dumps(proof, sort_keys=True, separators=(",", ":")).encode(),
            b"",
        )

    monkeypatch.setattr("tgw.actor_fleet_provider.subprocess.run", run_worker)
    production_verified = production_provider.verify_actor(request["actors"][0], request)
    assert production_verified["status"] == "VERIFIED"
    assert subprocess_calls

    def forged_worker(command, **kwargs):
        completed = run_worker(command, **kwargs)
        forged = json.loads(completed.stdout)
        forged["source"] = "0" * 40
        return subprocess.CompletedProcess(
            command,
            0,
            json.dumps(forged, sort_keys=True, separators=(",", ":")).encode(),
            b"",
        )

    monkeypatch.setattr("tgw.actor_fleet_provider.subprocess.run", forged_worker)
    with pytest.raises(ActorFleetError, match="proof differs"):
        production_provider.verify_actor(request["actors"][0], request)

    assert provider.repair(request)["status"] == "REPAIRED"
    assert destination.is_symlink()
    successor_binding = json.loads(startup.read_text())
    assert successor_binding["schema"] == "tgw-actor-startup-binding/v3"
    assert successor_binding["trusted_public_key"] == _CONTRACT_PUBLIC_KEY
    assert successor_binding["expected_generation"] == request[
        "successor_generation"
    ]
    assert startup.stat().st_mode & 0o022 == 0
    rollback_wait = provider.rollback(request)
    assert rollback_wait["status"] == "RESTART_REQUIRED"
    journal = provider._journal(request["transaction_id"])
    rollback_obligation = journal["context_rebind"]["obligations"][0]
    rollback_confirmation = _handoff_confirmation(
        request,
        rollback_obligation,
        process_identity_hash=_hash({"exited-rollback-context": actor}),
        parent_identity_hash=_hash({"rollback-ordinary-harness": actor}),
        direction="rollback",
    )
    journal["context_rebind"]["confirmations"] = {
        rollback_obligation["obligation_id"]: rollback_confirmation,
    }
    provider._save(journal)
    assert provider.rollback(request)["status"] == "ROLLED_BACK"
    assert not destination.exists()
    restored_predecessor = json.loads(startup.read_text())
    assert restored_predecessor == predecessor_binding
    assert restored_predecessor["schema"] == "tgw-actor-startup-binding/v1"
    assert restored_predecessor["trusted_public_key"] == (
        _PREDECESSOR_CONTRACT_PUBLIC_KEY
    )


@pytest.mark.parametrize(
    ("worker_input", "reason"),
    [
        (b"{}", "input is invalid"),
        (b"x" * (_ACTOR_VERIFICATION_MAX_INPUT + 1), "input is too large"),
    ],
)
def test_actor_verification_worker_rejects_malformed_or_oversized_input(
    durable_path, worker_input, reason,
):
    source_root = Path(__file__).resolve().parents[1] / "src"
    completed = subprocess.run(
        [
            sys.executable,
            "-I",
            "-s",
            "-P",
            "-c",
            _ACTOR_VERIFICATION_BOOTSTRAP,
            str(source_root),
            "--verify-actor-worker",
        ],
        input=worker_input,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        cwd=source_root,
        env={
            "HOME": str(durable_path),
            "LANG": "C.UTF-8",
            "PATH": "/usr/bin:/bin",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONNOUSERSITE": "1",
            "PYTHONSAFEPATH": "1",
            "TMPDIR": str(durable_path),
        },
        timeout=20,
    )
    assert completed.returncode == 1
    assert reason in json.loads(completed.stdout)["reason"]


def test_actor_provider_rejects_forged_signed_admission(durable_path):
    config, request, _destination = _fixture(durable_path)
    admission_path = Path(config["admission_root"]) / (
        request["revisions"]["admission"].removeprefix("sha256:") + ".json"
    )
    admission = json.loads(admission_path.read_text())
    admission["signature"] = "A" + admission["signature"][1:]
    admission_path.write_text(json.dumps(admission))
    state = {"value": "active"}

    def service(arguments):
        if arguments[0] == "stop":
            state["value"] = "inactive"
        return subprocess.CompletedProcess(arguments, 0, state["value"] + "\n", "")

    provider = _new_provider(
        config,
        durable_path,
        service_runner=service,
        materializer_loader=lambda _: _Materializer(),
        current_time=lambda: datetime(2026, 8, 21, 12, tzinfo=timezone.utc),
    )
    with pytest.raises(ValueError, match="admission receipt is not exact"):
        _bind_coordinator(provider, request)
    assert state["value"] == "active"


def test_actor_provider_rejects_expired_signed_admission(durable_path):
    config, request, _destination = _fixture(durable_path, admission_expires_at="2026-08-21T11:00:00Z")
    provider = _new_provider(
        config,
        durable_path,
        service_runner=lambda arguments: subprocess.CompletedProcess(arguments, 0, "inactive\n", ""),
        materializer_loader=lambda _: _Materializer(),
        current_time=lambda: datetime(2026, 8, 21, 12, tzinfo=timezone.utc),
    )
    with pytest.raises(ValueError, match="admission receipt is not exact"):
        _bind_coordinator(provider, request)


def test_actor_provider_rejects_cross_tree_signed_admission(durable_path):
    config, request, _destination = _fixture(durable_path)
    manifest_path = Path(config["release_root"]) / "candidate/.release-manifest.json"
    manifest_path.chmod(0o644)
    manifest = json.loads(manifest_path.read_text())
    manifest["git_tree"] = "c" * 40
    manifest_path.write_text(json.dumps(manifest))
    manifest_path.chmod(0o444)
    provider = _new_provider(
        config,
        durable_path,
        service_runner=lambda arguments: subprocess.CompletedProcess(arguments, 0, "inactive\n", ""),
        materializer_loader=lambda _: _Materializer(),
        current_time=lambda: datetime(2026, 8, 21, 12, tzinfo=timezone.utc),
    )
    with pytest.raises(ValueError, match="admission receipt is not exact"):
        _bind_coordinator(provider, request)


def test_actor_provider_rejects_manifest_content_drift(durable_path):
    config, request, _destination = _fixture(durable_path)
    launcher = Path(config["release_root"]) / "candidate/launcher"
    launcher.chmod(0o644)
    launcher.write_text("drifted actor launcher\n")
    launcher.chmod(0o444)
    provider = _new_provider(
        config,
        durable_path,
        service_runner=lambda arguments: subprocess.CompletedProcess(arguments, 0, "inactive\n", ""),
        materializer_loader=lambda _: _Materializer(),
        current_time=lambda: datetime(2026, 8, 21, 12, tzinfo=timezone.utc),
    )
    with pytest.raises(ValueError, match="content does not match its manifest"):
        _bind_coordinator(provider, request)


def test_actor_provider_reverifies_release_before_activation(durable_path):
    config, request, _destination = _fixture(durable_path)
    state = {"value": "active"}

    def service(arguments):
        if arguments[0] == "stop":
            state["value"] = "inactive"
        return subprocess.CompletedProcess(arguments, 0, state["value"] + "\n", "")

    provider = _new_provider(
        config,
        durable_path,
        service_runner=service,
        materializer_loader=lambda _: _Materializer(),
        current_time=lambda: datetime(2026, 8, 21, 12, tzinfo=timezone.utc),
    )
    _bind_coordinator(provider, request)
    provider.quiesce(request)
    rebuilt = provider.rebuild(request)
    launcher = Path(config["release_root"]) / "candidate/launcher"
    launcher.chmod(0o644)
    launcher.write_text("post-rebuild drift\n")
    launcher.chmod(0o444)
    with pytest.raises(ValueError, match="content does not match its manifest"):
        provider.activate(request, rebuilt)


def test_actor_provider_rejects_writable_state_root(durable_path):
    config, _request, _destination = _fixture(durable_path)
    Path(config["state_root"]).chmod(0o777)
    with pytest.raises(ValueError, match="state root is not root protected"):
        ActorFleetProvider(config)


@pytest.mark.parametrize("writable_ancestor", (False, True))
def test_actor_provider_requires_exact_protected_state_ancestry(
    durable_path,
    monkeypatch,
    writable_ancestor,
):
    config, _request, _destination = _fixture(durable_path)
    state_root = Path(config["state_root"])
    private_root = state_root / "private"
    actor_gid = grp.getgrnam(config["actor_group"]).gr_gid
    synthetic_ancestor = Path("/var/lib/tgw")
    root_owned = {
        Path(config["admission_public_key"]): (0o444, 0, 0),
        Path(config["startup_binding_root"]): (0o755, 0, 0),
        Path(config["actor_cache_root"]): (0o750, 0, 0),
        Path(config["attempt_workspace_root"]): (0o2770, 0, actor_gid),
        Path(config["attempt_cache_root"]): (0o2770, 0, actor_gid),
        state_root: (0o750, 0, actor_gid),
        private_root: (0o700, 0, 0),
        synthetic_ancestor: (
            0o775 if writable_ancestor else 0o755,
            0,
            0,
        ),
    }
    real_stat = Path.stat
    real_resolve = Path.resolve

    def protected_stat(path, *args, **kwargs):
        path = Path(path)
        mode_owner = root_owned.get(path)
        observed = (
            real_stat(Path("/var/lib"), *args, **kwargs)
            if path == synthetic_ancestor
            else real_stat(path, *args, **kwargs)
        )
        if mode_owner is None:
            return observed
        mode, uid, gid = mode_owner
        values = list(observed)
        values[0] = (values[0] & ~0o7777) | mode
        values[4] = uid
        values[5] = gid
        return os.stat_result(values)

    def protected_resolve(path, *args, **kwargs):
        if Path(path) == synthetic_ancestor:
            return synthetic_ancestor
        return real_resolve(path, *args, **kwargs)

    monkeypatch.setattr(os, "geteuid", lambda: 0)
    monkeypatch.setattr(
        fleet_provider_module,
        "_DEFAULT_ACTOR_FLEET_STATE_ROOT",
        state_root,
    )
    monkeypatch.setattr(Path, "stat", protected_stat)
    monkeypatch.setattr(Path, "resolve", protected_resolve)

    if writable_ancestor:
        with pytest.raises(
            ActorFleetError,
            match="actor fleet state ancestry is not protected",
        ):
            ActorFleetProvider(config)
    else:
        provider = ActorFleetProvider(config)
        assert provider.state_root == state_root
        assert provider.private_state_root == private_root


def test_actor_provider_loads_real_dataclass_materializer():
    release = Path(__file__).resolve().parents[1]
    materializer = ActorFleetProvider._materializer(release)
    assert callable(materializer.materialize_complete_actor_contracts)
    assert callable(materializer.rollback_complete_actor_contracts)


def test_actor_provider_http_rejects_auth_and_unbound_invocation(durable_path):
    config, request, _destination = _fixture(durable_path)
    app = create_actor_fleet_app(
        {"actor_fleet_provider": config},
        materializer_loader=lambda _: _Materializer(),
        service_runner=lambda args: subprocess.CompletedProcess(args, 0, "inactive\n", ""),
        current_time=lambda: datetime(2026, 8, 21, 12, tzinfo=timezone.utc),
    )
    client = TestClient(app)
    invocation = {"schema": "tgw-actor-fleet-provider-invocation/v1", "step": "quiesce", "arguments": [request]}
    body = {**invocation, "invocation_hash": _hash(invocation)}
    assert client.post("/v1/actor-fleet/quiesce", json=body).status_code == 401
    assert (
        client.post(
            "/v1/actor-fleet/quiesce",
            json={**body, "invocation_hash": "sha256:" + "0" * 64},
            headers={"Authorization": "Bearer secret"},
        ).status_code
        == 409
    )
