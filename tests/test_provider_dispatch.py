"""Focused W07 role-to-local-adapter coverage."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import json

from tgw.development.foreman import ForemanConfig, TodoRecord, tick
from tgw.development.provider_dispatch import ProviderDispatchError, resolve_implementation_adapter
from tgw.development.plan_binding import execution_root_hash
from tgw.development.profiles import CODING_READY_FOR_IMPLEMENTATION
from tgw.workflow_kernel.contracts import RuntimeWorkGraph, TreatmentDisposition
from tgw.workers.coding import CodingWorker
from tgw.queue.worker_base import HardFailure


ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "agent-services/catalogs/harness-providers-v1.json"


def _runner(path: Path) -> str:
    path.write_text("#!/bin/sh\nexit 0\n")
    path.chmod(0o755)
    return str(path)


def _adapters() -> dict[str, str]:
    return {
        "tgw-plan": str(ROOT / "agent-services/skills/tgw-plan"),
        "promptcraft": str(ROOT / "agent-services/providers/promptcraft"),
        "promptcraft-card-handoff": str(ROOT / "agent-services/providers/promptcraft/bin/promptcraft-handoff"),
    }


def _graph(worktree: Path) -> RuntimeWorkGraph:
    return RuntimeWorkGraph(
        "runtime-work-graph/v1", "graph-provider", str(worktree), "generation",
        CODING_READY_FOR_IMPLEMENTATION.identity, "1", "test", "evidence", "condition",
        "registry", (), (), (), (),
        (TreatmentDisposition("codex-implement", "1", ("ready",)),), (), (), (), (),
    )


def test_neutral_implementation_selects_codex_local_runner(tmp_path):
    adapter = resolve_implementation_adapter(
        {"commands": {"codex-implement": [_runner(tmp_path / "codex"), "run"]}},
        registry_path=CATALOG, adapters=_adapters(),
    )
    assert (adapter.role, adapter.selected_provider, adapter.treatment_id, adapter.queue_name) == (
        "implementation", "codex-local-runner", "codex-implement", "codex-implement",
    )


def test_foreman_worker_and_receipt_preserve_role_provider_adapter_and_plan_binding(tmp_path, monkeypatch):
    worktree = tmp_path / "worktree"; worktree.mkdir()
    binding = {
        "schema": "tgw-plan-coding-todo/v1", "plan_commit": "a" * 40,
        "solution_hash": "sha256:solution", "closure_hash": "sha256:closure",
        "capability": "code@1", "treatment_id": "establish:base@1",
        "source_commit": "a" * 40, "requested_worktree_identity": "allocated",
        "idempotency_key": "sha256:key", "worktree": str(worktree),
        "worktree_identity": {"worktree": str(worktree)},
        "execution_root": {
            "schema": "tgw-execution-root/v1", "kind": "plan", "plan_id": "P",
            "profile": "default", "plan_commit": "a" * 40,
        },
    }
    binding["execution_root"]["identity_hash"] = execution_root_hash(binding["execution_root"])
    todo = TodoRecord(9, "codex", 1, "implement", str(worktree), binding)
    enqueue = MagicMock(return_value="job-1")
    config = ForemanConfig(
        coding_config={"commands": {"codex-implement": [_runner(tmp_path / "codex"), "run"]}},
        provider_registry_path=str(CATALOG), provider_adapters=_adapters(),
    )
    with patch("tgw.development.foreman.validated_coding_worktree", return_value=worktree), patch("tgw.development.foreman.build_coding_snapshot", return_value=object()), patch("tgw.development.foreman.evaluate", return_value=_graph(worktree)):
        assert tick(config, fetch_todos=lambda: [todo], check_active_fn=lambda _: False, enqueue_fn=enqueue).dispatched == 1
    payload = enqueue.call_args.kwargs["payload"]
    assert {key: payload[key] for key in ("coding_role", "selected_provider", "adapter_treatment_id", "adapter_queue_name")} == {
        "coding_role": "implementation", "selected_provider": "codex-local-runner",
        "adapter_treatment_id": "codex-implement", "adapter_queue_name": "codex-implement",
    }
    assert payload["plan_binding"] == binding
    worker = CodingWorker("codex-implement", {"coding": {}}, launcher=lambda *_: {"outcome": "satisfied", "established_conditions": ["implemented"], "artifacts": []})
    monkeypatch.setattr(worker, "_validated_worktree", lambda _: worktree)
    receipt = worker.handle({"payload_json": payload})
    assert receipt["execution_envelope"]["plan_binding"] == binding
    assert receipt["plan_binding"] == binding
    assert {key: receipt[key] for key in ("coding_role", "selected_provider", "adapter_treatment_id", "adapter_queue_name")} == {
        key: payload[key] for key in ("coding_role", "selected_provider", "adapter_treatment_id", "adapter_queue_name")
    }
    assert receipt["execution_envelope"]["selected_provider"] == "codex-local-runner"


@pytest.mark.parametrize("config, match", [
    ({"commands": {}}, "no eligible"),
    ({"commands": {"codex-implement": ["missing-runner"]}}, "no eligible"),
])
def test_unavailable_or_malformed_selection_refuses_before_enqueue(tmp_path, config, match):
    with pytest.raises(ProviderDispatchError, match=match):
        resolve_implementation_adapter(config, registry_path=CATALOG, adapters=_adapters())


def test_malformed_selected_provider_adapter_refuses_before_enqueue(tmp_path):
    catalog = json.loads(CATALOG.read_text())
    provider = next(item for item in catalog["providers"] if item["id"] == "codex-local-runner")
    provider["runner"] = {"kind": "configured-argv", "key": "wrong-queue"}
    bad_catalog = tmp_path / "catalog.json"; bad_catalog.write_text(json.dumps(catalog))
    with pytest.raises(ProviderDispatchError, match="no eligible|mapping is malformed"):
        resolve_implementation_adapter(
            {"commands": {"codex-implement": [_runner(tmp_path / "codex"), "run"], "wrong-queue": [_runner(tmp_path / "wrong"), "run"]}},
            registry_path=bad_catalog, adapters=_adapters(),
        )


def test_worker_refuses_mismatched_provider_adapter_before_execution(tmp_path):
    worker = CodingWorker("codex-implement", {"coding": {}}, launcher=MagicMock())
    with pytest.raises(HardFailure, match="provider adapter binding"):
        worker.handle({"payload_json": {
            "treatment_id": "codex-implement", "graph_id": "g", "object_generation": "v",
            "coding_role": "implementation", "selected_provider": "wrong",
            "adapter_treatment_id": "codex-implement", "adapter_queue_name": "codex-implement",
        }})
    worker._launcher.assert_not_called()
