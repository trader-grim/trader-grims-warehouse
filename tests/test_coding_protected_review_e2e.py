import hashlib
import io
import json
import os
import shutil
import stat
import subprocess
import tarfile
from pathlib import Path

import pytest

import tgw.governed_review_adapter as governed_adapter
from tests import test_governed_review_adapter as governed_fixture
from tests.test_candidate_receipt_sink import (
    _candidate_evidence_sink,
    _commit_sink,
    _new_sink,
    _pinned_candidate_evidence_descriptor,
    approved_plan_repository,
    candidate_repository,
    canonical,
    digest,
    w06_plan_materialization,
    write_json,
)
from tests.test_coding_lifecycle import plan_binding
from tgw.candidate_receipt_sink import (
    CANDIDATE_EVIDENCE_CARD_BINDING_SCHEMA,
    PinnedCandidateEvidenceDescriptor,
)
from tgw.development import coding_lifecycle
from tgw.development.coding_lifecycle import (
    LifecycleStore,
    build_binding,
    candidate_job_binding,
    create,
    job_binding,
)
from tgw.development.coding_review import run_local_review
from tgw.development.coding_root_effect import (
    RootEffectPaths,
    ensure_review_preparation_request,
    read_review_preparation_response,
)
from tgw.review_contract import ReviewRunnerError
from tgw.review_snapshot import snapshot_hash_entries, snapshot_preimage


def _sudo(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["sudo", "-n", *arguments],
        check=True,
        text=True,
        capture_output=True,
    )


def _root_python(code: str, *arguments: Path) -> str:
    python_path = str(Path(__file__).resolve().parents[1] / "src") + ":" + str(
        Path(__file__).resolve().parents[1]
        / "agent-services/providers/promptcraft"
    )
    result = subprocess.run(
        [
            "sudo",
            "-n",
            "/usr/bin/env",
            f"PYTHONPATH={python_path}",
            "/opt/TGW/.venvs/controller/bin/python3",
            "-c",
            code,
            *map(str, arguments),
        ],
        check=False,
        text=True,
        capture_output=True,
    )
    if result.returncode:
        raise AssertionError(result.stderr or result.stdout)
    return result.stdout


def _archive_snapshot_hash(repository: Path, commit: str) -> str:
    archive = subprocess.check_output(["git", "archive", commit], cwd=repository)
    entries = {}
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as stream:
        for member in stream.getmembers():
            if member.isfile():
                source = stream.extractfile(member)
                assert source is not None
                entries[member.name] = source.read()
    return snapshot_hash_entries(entries)


def _closure(solution: dict) -> dict:
    return {
        name: solution[name]
        for name in (
            "plan_commit",
            "root",
            "complete",
            "selected_providers",
            "selected_capabilities",
            "selected_alternatives",
            "satisfied_installed",
            "work_units",
            "phase_order",
        )
    }


def _install_root_json(source: Path, destination: Path, mode: str = "400") -> None:
    _sudo(
        "install",
        "-o",
        "root",
        "-g",
        "root",
        "-m",
        mode,
        str(source),
        str(destination),
    )


@pytest.mark.skipif(
    subprocess.run(["sudo", "-n", "true"], check=False).returncode != 0,
    reason="passwordless sudo is required for protected review end-to-end coverage",
)
def test_doctor_prepares_and_root_verifies_real_protected_governed_review(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    protected_root = Path(
        _sudo(
            "mktemp", "-d", "/var/lib/tgw/coding-protected-review-test-XXXXXXXX"
        ).stdout.strip()
    )
    onboarding_destination = protected_root.with_name(protected_root.name + ".json")
    receipts = protected_root.with_name(protected_root.name + "-receipts")
    try:
        _sudo("chmod", "0755", str(protected_root))
        plan_repository, plan_commit = approved_plan_repository(tmp_path)
        candidate, candidate_commit, candidate_tree = candidate_repository(
            tmp_path, plan_commit=plan_commit
        )
        materialization_pin, materialization = w06_plan_materialization(
            plan_repository, plan_commit=plan_commit
        )
        candidate_sink, candidate_sink_config, evidence = _candidate_evidence_sink(
            tmp_path,
            candidate_repo=candidate,
            source_commit=candidate_commit,
            source_tree=candidate_tree,
            plan_commit=plan_commit,
            w06_materialization=materialization,
        )
        descriptor_authority, descriptor_config = (
            _pinned_candidate_evidence_descriptor(
                tmp_path,
                candidate,
                candidate_sink_config,
                materialization_pin,
            )
        )
        descriptor_value = json.loads(descriptor_config.read_text())
        descriptor = PinnedCandidateEvidenceDescriptor(
            descriptor_value, candidate_repository=candidate
        )

        monkeypatch.setattr(governed_fixture, "PLAN", plan_commit)
        monkeypatch.setattr(governed_fixture, "COMMIT", candidate_commit)
        monkeypatch.setattr(governed_fixture, "TREE", candidate_tree)
        expected_snapshot = _archive_snapshot_hash(candidate, candidate_commit)
        (
            _unused_source,
            _executable,
            provider_identity,
            environment,
            context_private_key,
        ) = governed_fixture._fixture(
            tmp_path / "provider",
            review_snapshot_hash=expected_snapshot,
        )
        evidence_sink = governed_fixture._sink_descriptor()
        resource_service, resource_catalog = (
            governed_fixture._resource_service_catalog()
        )
        execution_environment_hash = provider_identity["artifacts"][
            "execution_environment"
        ]["content_sha256"]
        profile = {
            "schema": "tgw-local-coding-protected-review-profile/v1",
            "provider_identity": provider_identity,
            "environment": environment,
            "evidence_sink": evidence_sink,
            "resource_service": resource_service,
            "resource_service_catalog": resource_catalog,
            "receiver_profile": {"id": "claude-code", "version": 1},
            "environment_preflight_receipt": {
                "schema": "tgw-environment-preflight-receipt/v1",
                "result": "PASS",
                "catalog_sha256": execution_environment_hash,
                "actor": "claude",
                "profile": "development",
                "attempt_id": "protected-e2e",
                "tools": [],
            },
            "skill_contract_hash": governed_fixture._fixture_skill_contract_hash(
                provider_identity
            ),
            "timeout_seconds": 5,
            "output_limit": 8 * 1024 * 1024,
        }

        # This valid initial pin proves Doctor provisions both bindings.  The
        # protected publisher replaces only the published pin after execution.
        execution_sink = tmp_path / "initial-execution-sink"
        _new_sink(
            execution_sink,
            email="initial@example.invalid",
            name="Initial protected execution sink",
        )
        initial_raw = write_json(execution_sink / "initial.json", {"held": True})
        initial_descriptor = _commit_sink(
            execution_sink,
            sink_id="initial-execution-evidence",
            artifacts=[
                {
                    "ref": "artifact:initial:held",
                    "path": "initial.json",
                    "content_sha256": digest(initial_raw),
                }
            ],
            message="initial protected evidence binding",
        )
        onboarding = {
            "schema": "tgw-local-coding-protected-review-onboarding/v1",
            "request_profile": profile,
            "candidate_evidence_descriptor_config": descriptor_value,
            "execution_evidence_sink_config": initial_descriptor,
            "execution_evidence_pin_source_config": initial_descriptor,
        }
        onboarding_source = tmp_path / "onboarding.json"
        onboarding_source.write_text(json.dumps(onboarding, sort_keys=True))
        _install_root_json(onboarding_source, onboarding_destination)

        live_config = Path("/opt/TGW/tgw-lib/config")
        config_identity_before = live_config.stat(follow_symlinks=False)
        repair_code = """
import json, sys
from pathlib import Path
from tgw.doctor_cli import DoctorPaths, repair_protected_review
p=DoctorPaths(repository=Path(sys.argv[1]), protected_review_root=Path(sys.argv[2]), protected_review_onboarding=Path(sys.argv[3]), receipts=Path(sys.argv[4]))
print(json.dumps(repair_protected_review(p), sort_keys=True))
print(json.dumps(repair_protected_review(p), sort_keys=True))
"""
        repairs = [
            json.loads(line)
            for line in _root_python(
                repair_code,
                candidate,
                protected_root,
                onboarding_destination,
                receipts,
            ).splitlines()
        ]
        assert repairs[0]["changed"] is True
        assert repairs[1]["changed"] is False
        assert repairs[0]["config_root_unchanged"] is True
        config_identity_after = live_config.stat(follow_symlinks=False)
        assert (
            config_identity_after.st_uid,
            config_identity_after.st_gid,
            stat.S_IMODE(config_identity_after.st_mode),
        ) == (
            config_identity_before.st_uid,
            config_identity_before.st_gid,
            stat.S_IMODE(config_identity_before.st_mode),
        )

        plan_todo = plan_binding(
            candidate,
            source=candidate_commit,
            source_tree=candidate_tree,
        )
        plan_todo.update(
            {
                "plan_commit": plan_commit,
                "solution_hash": materialization["solution_hash"],
                "closure_hash": materialization["closure_hash"],
            }
        )
        store = LifecycleStore(tmp_path / "lifecycles", group_gid=os.getegid())
        binding = build_binding(
            target=1915,
            plan_binding=plan_todo,
            source_tree=candidate_tree,
        )
        record = create(store, target=1915, binding=binding)
        candidate_receipt = {
            "schema": "tgw-local-coding-candidate-evidence/v1",
            "root_id": record["root_id"],
            "binding_hash": binding["binding_hash"],
            "worktree": str(candidate),
            "commit": candidate_commit,
            "tree": candidate_tree,
            "classification": "CLOSED_CANDIDATE",
        }
        record["effects"]["candidate"] = {
            "receipt": candidate_receipt,
            "receipt_hash": "sha256:" + hashlib.sha256(
                canonical(candidate_receipt)
            ).hexdigest(),
            "idempotency_key": coding_lifecycle.stage_idempotency_key(
                record, "candidate"
            ),
        }
        store.put(record)

        trigger_root = tmp_path / "root-effects"
        trigger_root.mkdir()
        _sudo("chown", f"0:{os.getegid()}", str(trigger_root))
        _sudo("chmod", "3770", str(trigger_root))
        paths = RootEffectPaths(
            request_root=trigger_root,
            lifecycle_root=store.root,
            repository=candidate,
            runtime_root=tmp_path / "runtime",
            coding_config=tmp_path / "coding.json",
            protected_review_config=protected_root / "config.json",
            group_gid=os.getegid(),
            root_uid=0,
        )
        preparation_request = ensure_review_preparation_request(paths, record)
        assert not any(
            forbidden in json.dumps(preparation_request).lower()
            for forbidden in ("/var/lib", "request_path", "provider", "result")
        )
        consume_code = """
import sys
from pathlib import Path
from tgw.development.coding_root_effect import RootEffectPaths, consume_once
p=RootEffectPaths(
    request_root=Path(sys.argv[1]), lifecycle_root=Path(sys.argv[2]),
    repository=Path(sys.argv[3]), runtime_root=Path(sys.argv[4]),
    coding_config=Path(sys.argv[5]), protected_review_config=Path(sys.argv[6]),
    group_gid=int(sys.argv[7]), root_uid=0,
)
print(consume_once(p))
"""
        assert _root_python(
            consume_code,
            trigger_root,
            store.root,
            candidate,
            tmp_path / "runtime",
            tmp_path / "coding.json",
            protected_root / "config.json",
            Path(str(os.getegid())),
        ).strip() == "1"
        preparation = read_review_preparation_response(paths, preparation_request)
        assert preparation is not None
        assert preparation["candidate_commit"] == candidate_commit
        assert preparation["candidate_tree"] == candidate_tree
        assert preparation["plan_commit"] == plan_commit
        request_path = protected_root / "requests" / f"{candidate_commit}.request.json"
        governed_request = json.loads(request_path.read_text())
        assert governed_request["source_commit"] == candidate_commit
        assert governed_request["source_tree"] == candidate_tree
        assert governed_request["plan_commit"] == plan_commit
        assert governed_request["review_packet"]["plan"] == {
            "commit": plan_commit,
            "solution_hash": materialization["solution_hash"],
            "closure_hash": materialization["closure_hash"],
        }

        class Sink:
            retained = None
            artifacts = {}

            def __init__(self, _descriptor):
                pass

            def publish(self, execution):
                self.retained = execution
                return {
                    "schema": "tgw-governed-review-publication/v1",
                    "sink_ref": evidence_sink["sink_ref"],
                    "execution_hash": execution["execution_hash"],
                    "artifact_ref": "candidate:review-execution",
                    "artifact_hash": governed_fixture._hash(execution),
                }

            def read(self, _publication):
                return self.retained

            def publish_artifact(self, artifact_ref, value):
                self.artifacts[artifact_ref] = value
                return {
                    "ref": artifact_ref,
                    "content_sha256": governed_fixture._hash(value),
                }

            def read_artifact(self, pointer):
                return self.artifacts[pointer["ref"]]

        monkeypatch.setattr(governed_adapter, "HTTPReviewEvidenceSink", Sink)
        handoff = governed_request["handoff"]
        provider_for_request = governed_request["provider_identity"]
        source_snapshot = Path(governed_request["snapshot"])
        solution_unsigned = dict(materialization["solution"])
        solution_unsigned.pop("solution_hash")
        descriptor_content = {
            "schema": CANDIDATE_EVIDENCE_CARD_BINDING_SCHEMA,
            "descriptor": descriptor._value,
        }
        sink_unsigned = dict(evidence_sink)
        sink_unsigned.pop("descriptor_hash")
        resource_contents = {
            "plan_input": canonical({"plan_commit": plan_commit}),
            "plan_commit": canonical({"commit": plan_commit}),
            "plan_graph": canonical(solution_unsigned),
            "codegraph_snapshot": canonical(
                {"commit": candidate_commit, "tree": candidate_tree}
            ),
            "source_tree": snapshot_preimage(source_snapshot),
            "execution_environment": Path(
                provider_for_request["artifacts"]["execution_environment"][
                    "resolved_path"
                ]
            ).read_bytes(),
            "authority_conditions": canonical(_closure(materialization["solution"])),
            "candidate_evidence": canonical(descriptor_content),
            "receipt_sink": canonical(sink_unsigned),
        }
        for name, binding_value in handoff["card"]["bindings"].items():
            assert digest(resource_contents[name]) == binding_value["hash"]

        class ContextClient:
            def __init__(self, _descriptor):
                pass

            def read(self, challenge):
                attestation = governed_fixture._context_attestation(
                    provider_for_request,
                    context_private_key,
                    handoff,
                    "context-run",
                    challenge,
                )
                return governed_fixture._context_service_bundle(
                    attestation,
                    challenge=challenge,
                    skill_contract_hash=profile["skill_contract_hash"],
                    resource_contents=resource_contents,
                )

        monkeypatch.setattr(governed_adapter, "HTTPContextBundleClient", ContextClient)
        lifecycle_binding = job_binding(record)
        candidate_binding = candidate_job_binding(
            lifecycle_binding,
            commit=candidate_commit,
            tree=candidate_tree,
        )
        task = {
            "schema": "coding-task/v1",
            "todo_id": 1915,
            "agent": "codex",
            "body": "Apply the bounded protected-review defensive remediation.",
        }
        payload = {
            "status": "PASS",
            "todo_id": 1915,
            "treatment_id": "claude-review",
            "job_id": "protected-review-job",
            "plan_binding": record["binding"]["plan_todo_binding"],
            "coding_lifecycle": lifecycle_binding,
            "coding_candidate": candidate_binding,
            "task_spec": task,
        }
        projection = run_local_review(
            payload,
            candidate,
            protected_config=protected_root / "config.json",
        )
        worker_receipt = {**payload, **projection}
        assert worker_receipt["outcome"] == "satisfied"
        assert worker_receipt["artifacts"][0]["protected_review"][
            "candidate_commit"
        ] == candidate_commit

        actual_sink = tmp_path / "actual-execution-sink"
        _new_sink(
            actual_sink,
            email="actual@example.invalid",
            name="Actual governed execution evidence",
        )
        manifest_artifacts = []
        for index, (ref, value) in enumerate(sorted(Sink.artifacts.items())):
            relative = f"artifacts/{index:03d}.json"
            raw = write_json(actual_sink / relative, value)
            manifest_artifacts.append(
                {
                    "ref": ref,
                    "path": relative,
                    "content_sha256": digest(raw),
                }
            )
        actual_descriptor = _commit_sink(
            actual_sink,
            sink_id="actual-governed-review-evidence",
            artifacts=manifest_artifacts,
            message="retain actual protected governed review evidence",
        )
        actual_config = tmp_path / "actual-execution-config.json"
        actual_config.write_text(json.dumps(actual_descriptor, sort_keys=True))
        _install_root_json(
            actual_config,
            protected_root / "execution-evidence-published.json",
        )

        controller = {"schema": "controller", "status": "PASS"}
        review_evidence = {
            "schema": "tgw-local-coding-queue-evidence/v1",
            "root_id": record["root_id"],
            "binding_hash": binding["binding_hash"],
            "job_id": "protected-review-job",
            "result": worker_receipt,
        }
        integration = {
            "schema": "tgw-local-coding-integration/v1",
            "candidate_commit": candidate_commit,
            "candidate_tree": candidate_tree,
        }
        for stage, receipt in (
            ("controller", controller),
            ("review", review_evidence),
            ("integration", integration),
        ):
            record["effects"][stage] = {
                "receipt": receipt,
                "receipt_hash": "sha256:" + hashlib.sha256(canonical(receipt)).hexdigest(),
                "idempotency_key": coding_lifecycle.stage_idempotency_key(
                    record, stage
                ),
            }
        record["stage"] = "materialization"
        record["state"] = "WAITING"
        record = store.put(record)
        verify_code = """
import json, sys
from pathlib import Path
from tgw.development.coding_lifecycle import LifecycleStore
from tgw.development.coding_root_effect import RootEffectPaths, build_request, verify_protected_review_evidence
p=RootEffectPaths(
    request_root=Path(sys.argv[1]), lifecycle_root=Path(sys.argv[2]),
    repository=Path(sys.argv[3]), runtime_root=Path(sys.argv[4]),
    coding_config=Path(sys.argv[5]), protected_review_config=Path(sys.argv[6]),
    group_gid=int(sys.argv[7]), root_uid=0,
)
s=LifecycleStore(p.lifecycle_root, group_gid=p.group_gid)
r=s.get(sys.argv[8])
print(json.dumps(verify_protected_review_evidence(p, build_request(r), r), sort_keys=True))
"""

        def verify_as_root() -> dict:
            return json.loads(
                _root_python(
                    verify_code,
                    trigger_root,
                    store.root,
                    candidate,
                    tmp_path / "runtime",
                    tmp_path / "coding.json",
                    protected_root / "config.json",
                    Path(str(os.getegid())),
                    Path(record["root_id"]),
                )
            )

        verified = verify_as_root()
        assert verified["candidate_commit"] == candidate_commit
        assert verified["candidate_tree"] == candidate_tree
        assert verified["plan_commit"] == plan_commit
        assert verified["execution_hash"] == worker_receipt["artifacts"][0][
            "protected_review"
        ]["execution_hash"]

        original_request = tmp_path / "original-request.json"
        shutil.copyfile(request_path, original_request)
        _sudo("chmod", "0664", str(request_path))
        with pytest.raises(ReviewRunnerError, match="governed independent review failed"):
            run_local_review(
                payload,
                candidate,
                protected_config=protected_root / "config.json",
            )
        _install_root_json(original_request, request_path, mode="444")
        _sudo("mv", str(request_path), str(request_path) + ".held")
        with pytest.raises(ReviewRunnerError, match="governed independent review failed"):
            run_local_review(
                payload,
                candidate,
                protected_config=protected_root / "config.json",
            )
        _sudo("mv", str(request_path) + ".held", str(request_path))
        substituted = json.loads(original_request.read_text())
        substituted["source_tree"] = "0" * 40
        substituted_request = tmp_path / "substituted-request.json"
        substituted_request.write_text(json.dumps(substituted, sort_keys=True))
        _install_root_json(substituted_request, request_path, mode="444")
        with pytest.raises(ReviewRunnerError, match="governed independent review failed"):
            run_local_review(
                payload,
                candidate,
                protected_config=protected_root / "config.json",
            )
        _install_root_json(original_request, request_path, mode="444")

        _sudo(
            "chmod",
            "0660",
            str(protected_root / "execution-evidence-published.json"),
        )
        with pytest.raises(AssertionError, match="ProtectedReviewEvidenceError"):
            verify_as_root()
        _install_root_json(
            actual_config,
            protected_root / "execution-evidence-published.json",
        )
        stale_config = tmp_path / "stale-execution-config.json"
        stale_config.write_text(json.dumps(initial_descriptor, sort_keys=True))
        _install_root_json(
            stale_config,
            protected_root / "execution-evidence-published.json",
        )
        with pytest.raises(AssertionError, match="ProtectedReviewEvidenceError"):
            verify_as_root()

        # The prior group-writable reproduction remains rejected at the main
        # protected configuration boundary as well as the evidence boundary.
        _sudo("chmod", "0664", str(protected_root / "config.json"))
        with pytest.raises(ReviewRunnerError, match="configuration is unavailable"):
            run_local_review(
                payload,
                candidate,
                protected_config=protected_root / "config.json",
            )
    finally:
        for target in (protected_root, onboarding_destination, receipts):
            if str(target).startswith("/var/lib/tgw/coding-protected-review-test-"):
                subprocess.run(
                    ["sudo", "-n", "rm", "-rf", "--", str(target)],
                    check=False,
                )
