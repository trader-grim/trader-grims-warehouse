import json
import subprocess
from pathlib import Path

from tgw.candidate_review import generate_review_packet
from tgw.codex_review_backend import health, run
from tgw.harness_registry import load_registry, observe_health
from tgw.review_configuration import configured_review_command
from tgw.review_runner import snapshot_hash

ROOT = Path(__file__).resolve().parents[1]


def executable(path):
    path.write_text("#!/bin/sh\nexit 0\n")
    path.chmod(0o755)
    return path


def test_health_requires_dedicated_executable_and_auth_without_external_call(tmp_path):
    codex = executable(tmp_path / "codex")
    auth = tmp_path / "auth.json"
    auth.write_text("{}")

    observed = health(codex_bin=codex, auth_file=auth)

    assert observed["available"] is True
    assert observed["executable"] == str(codex)
    assert observed["auth_file"] == str(auth)
    assert observed["reasons"] == []


def test_configuration_materializes_wrapper_only_after_health_pass(tmp_path):
    evidence = {
        "schema": "tgw-codex-review-backend-health/v1",
        "available": True,
        "executable": str(tmp_path / "codex"),
        "auth_file": str(tmp_path / "auth.json"),
        "reasons": [],
    }
    calls = []

    def probe(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, json.dumps(evidence), "")

    configured = configured_review_command(python=tmp_path / "python", probe=probe)

    assert configured["status"] == "AVAILABLE"
    assert configured["command"][1:3] == ["-m", "tgw.review_runner"]
    assert json.loads(configured["command"][-1])[-1] == "tgw.codex_review_backend"
    assert calls[0][0][-1] == "--health"


def test_failed_health_produces_hold_and_no_runner_command(tmp_path):
    evidence = {
        "schema": "tgw-codex-review-backend-health/v1",
        "available": False,
        "executable": None,
        "auth_file": None,
        "reasons": ["unavailable"],
    }

    configured = configured_review_command(
        python=tmp_path / "python",
        probe=lambda command, **kwargs: subprocess.CompletedProcess(
            command, 2, json.dumps(evidence), ""
        ),
    )

    assert configured == {
        "schema": "tgw-review-runner-configuration/v1",
        "status": "HOLD",
        "command": None,
        "health": evidence,
    }


def test_backend_uses_ephemeral_read_only_codex_protocol_and_validates_hash(tmp_path):
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    (snapshot / "app.py").write_text("answer = 42\n")
    codex = executable(tmp_path / "codex")
    auth = tmp_path / "auth.json"
    auth.write_text("{}")
    request = {
        "schema": "tgw-code-review-request/v1",
        "handoff_hash": "sha256:handoff",
        "card_hash": "sha256:card",
        "snapshot_hash": snapshot_hash(snapshot),
        "snapshot_root": str(snapshot),
        "output_contract": "tgw-code-review/v1",
    }
    observed = {}

    def invoke(command, **kwargs):
        observed["command"] = command
        observed["env"] = kwargs["env"]
        output = Path(command[command.index("-o") + 1])
        output.write_text(
            json.dumps(
                {
                    "schema": "tgw-code-review/v1",
                    "verdict": "PASS",
                    "snapshot_hash": request["snapshot_hash"],
                    "summary": "no findings",
                    "findings": [],
                }
            )
        )
        return subprocess.CompletedProcess(command, 0, "", "")

    report = run(
        request,
        snapshot,
        codex_bin=codex,
        auth_file=auth,
        invoke=invoke,
    )

    assert report["verdict"] == "PASS"
    assert "--ephemeral" in observed["command"]
    assert observed["command"][observed["command"].index("--sandbox") + 1] == "read-only"
    assert observed["command"][observed["command"].index("-C") + 1] == str(snapshot)
    assert Path(observed["env"]["CODEX_HOME"]) != Path.home() / ".codex"


def test_verified_configuration_makes_candidate_packet_executable_without_invocation(tmp_path):
    review_command = [str(executable(tmp_path / "review-wrapper"))]
    registry = load_registry(ROOT / "agent-services/catalogs/harness-providers-v1.json")
    adapters = {
        "tgw-plan": ROOT / "agent-services/skills/tgw-plan",
        "promptcraft": ROOT / "agent-services/providers/promptcraft",
        "promptcraft-card-handoff": ROOT / "agent-services/providers/promptcraft/bin/promptcraft-handoff",
    }
    health_state = observe_health(
        registry,
        coding_config={"commands": {"harness-review": review_command}},
        adapters=adapters,
    )
    source = tmp_path / "candidate"
    source.mkdir()
    (source / "app.py").write_text("answer = 42\n")
    manifest = {
        "schema": "tgw-integrated-candidate-manifest/v1",
        "source": {
            "commit": "a" * 40,
            "tree": "b" * 40,
            "archive_sha256": "sha256:" + "c" * 64,
        },
        "plan": {
            "commit": "plan",
            "solution_hash": "sha256:" + "d" * 64,
            "closure_hash": "sha256:" + "e" * 64,
        },
        "candidate_closed": True,
        "installed": False,
    }

    packet = generate_review_packet(
        manifest,
        registry,
        health_state,
        adapters=adapters,
        snapshot_ref=source.resolve().as_uri(),
        snapshot_hash=snapshot_hash(source),
    )

    assert packet["status"] == "EXECUTABLE"
    assert packet["selected_provider"] == "codex-isolated-review-runner"
    assert packet["runner_argv"] == review_command
    assert packet["hold"] is None
