"""Tests for Phase 2 scheduler — dispatch_treatment and build_and_dispatch."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from tgw.workflow.contracts import (
    EffectClass,
    EvidenceAssertion,
    EvidenceReference,
    FingerprintResult,
    ObjectSnapshot,
    Requirement,
    RuntimeWorkGraph,
    TreatmentContract,
    TreatmentDisposition,
)
from tgw.workflow.scheduler import (
    _is_duplicate_key,
    _lookup_treatment,
    dispatch_treatment,
)

# ── Mock state_machine injection ───────────────────────────────────────────
# The controller venv has no psycopg2, so `from tgw.queue import state_machine`
# fails.  We inject a fake `state_machine` module into sys.modules so the
# lazy import inside dispatch_treatment() resolves to our mock.

def _make_mock_sm(return_value="mock-job-id"):
    sm = MagicMock()
    sm.enqueue_job = MagicMock(return_value=return_value)
    return sm


def _mock_enqueue_context(return_value="mock-job-id"):
    """Return a context manager that injects a mock state_machine with the
    given return_value for enqueue_job.  Use as:

        with _mock_enqueue_context("job-001") as mock_sm:
            dispatch_treatment(...)
        mock_sm.enqueue_job.assert_called_once()
    """
    sm = _make_mock_sm(return_value)
    return patch.dict("sys.modules", {"tgw.queue.state_machine": sm}), sm


# ── Synthetic helpers ──────────────────────────────────────────────────────


def _ref(identity: str = "obs-1") -> EvidenceReference:
    return EvidenceReference(
        identity=identity,
        source_class="test",
        source_generation="1",
    )


def _assertion(
    condition_id: str,
    result: FingerprintResult,
    *reasons: str,
) -> EvidenceAssertion:
    return EvidenceAssertion(
        condition_id=condition_id,
        result=result,
        reasons=reasons,
        evidence=(_ref(),),
    )


def _snapshot(
    object_id: str = "obj-1",
    generation: str = "gen-1",
    assertions: tuple[EvidenceAssertion, ...] = (),
) -> ObjectSnapshot:
    return ObjectSnapshot(
        object_id=object_id,
        generation=generation,
        assertions=assertions,
    )


def _treatment(
    identity: str,
    condition_id: str = "ready",
    ownership: str = "default",
    effect_class: EffectClass = EffectClass.LOCAL,
    version: str = "1",
) -> TreatmentContract:
    return TreatmentContract(
        identity=identity,
        version=version,
        requires=(Requirement(condition_id, (FingerprintResult.TRUE,)),),
        may_establish=(condition_id,),
        must_preserve=("data",),
        ownership=(ownership,),
        effect_class=effect_class,
        receipt_schema_id="receipt/test/v1",
    )


def _graph(
    graph_id: str = "graph-abc123",
    *,
    eligible: tuple[TreatmentDisposition, ...] = (),
    conflicts: tuple[tuple[str, str, tuple[str, ...]], ...] = (),
    gates: tuple[str, ...] = (),
    object_id: str = "obj-1",
    object_generation: str = "gen-1",
    goal_profile_id: str = "goal-1",
    goal_profile_version: str = "1",
) -> RuntimeWorkGraph:
    return RuntimeWorkGraph(
        schema_version="runtime-work-graph/v1",
        graph_id=graph_id,
        object_id=object_id,
        object_generation=object_generation,
        goal_profile_id=goal_profile_id,
        goal_profile_version=goal_profile_version,
        evaluator_version="test-evaluator/v1",
        evidence_set_hash="evidence-hash",
        condition_hash="condition-hash",
        treatment_registry_hash="treatment-hash",
        fingerprints=(),
        satisfied_requirements=(),
        unmet_requirements=(),
        explicit_requirements=(),
        eligible_treatments=eligible,
        waiting_treatments=(),
        ownership_conflicts=conflicts,
        reconciliation_gates=gates,
        next_event_classes=(),
    )


def _disposition(
    treatment_id: str,
    version: str = "1",
    reasons: tuple[str, ...] = ("eligible",),
) -> TreatmentDisposition:
    return TreatmentDisposition(
        treatment_id=treatment_id,
        treatment_version=version,
        reasons=reasons,
    )


# ── _is_duplicate_key ─────────────────────────────────────────────────────


def test_is_duplicate_key_via_pgcode():
    """A psycopg2 UniqueViolation with pgcode 23505 is recognized."""
    exc = Exception("duplicate key")
    exc.pgcode = "23505"  # type: ignore[attr-defined]
    assert _is_duplicate_key(exc) is True


def test_is_duplicate_key_via_class_name():
    """Even without pgcode, a class whose __qualname__ ends with
    'UniqueViolation' is recognized.  (Defined at module level so
    __qualname__ is 'UniqueViolation' rather than
    'test_xxx.<locals>.UniqueViolation'.)"""

    class UniqueViolation(Exception):
        pass

    assert _is_duplicate_key(UniqueViolation("dup")) is True


def test_is_duplicate_key_ordinary_exception():
    """A plain Exception is not a duplicate key."""
    assert _is_duplicate_key(Exception("boom")) is False


# ── _lookup_treatment ──────────────────────────────────────────────────────


def test_lookup_treatment_found():
    t = _treatment("codex-implement")
    result = _lookup_treatment("codex-implement", "1", (t,))
    assert result is t


def test_lookup_treatment_not_found():
    t = _treatment("codex-implement")
    result = _lookup_treatment("claude-review", "1", (t,))
    assert result is None


def test_lookup_treatment_version_mismatch():
    t = _treatment("codex-implement", version="1")
    result = _lookup_treatment("codex-implement", "2", (t,))
    assert result is None


# ── dispatch_treatment: eligible → one job ─────────────────────────────────


def test_one_eligible_treatment_enqueues_one_job():
    """Exactly one eligible treatment → enqueues job with graph_id as dedupe_key."""
    snapshot = _snapshot()
    t = _treatment("codex-implement")
    graph = _graph(
        graph_id="graph-abc",
        eligible=(_disposition("codex-implement"),),
    )

    ctx, mock_sm = _mock_enqueue_context("job-uuid-001")
    with ctx:
        job_id = dispatch_treatment(snapshot, graph, (t,))

    assert job_id == "job-uuid-001"
    mock_sm.enqueue_job.assert_called_once()
    call_kwargs = mock_sm.enqueue_job.call_args.kwargs
    assert call_kwargs["queue_name"] == "codex-implement"
    assert call_kwargs["handler_family"] == "codex-implement"
    assert call_kwargs["dedupe_key"] == "graph-abc"
    assert call_kwargs["entity_type"] == "item"
    payload = call_kwargs["payload"]
    assert payload["graph_id"] == "graph-abc"
    assert payload["object_id"] == "obj-1"
    assert payload["treatment_id"] == "codex-implement"


# ── dispatch_treatment: no eligible → none ─────────────────────────────────


def test_no_eligible_treatments_returns_none():
    """Zero eligible treatments → returns None, zero enqueue calls."""
    snapshot = _snapshot()
    t = _treatment("codex-implement")
    graph = _graph(graph_id="graph-abc", eligible=())

    ctx, mock_sm = _mock_enqueue_context()
    with ctx:
        job_id = dispatch_treatment(snapshot, graph, (t,))

    assert job_id is None
    mock_sm.enqueue_job.assert_not_called()


# ── dispatch_treatment: conflict → none ────────────────────────────────────


def test_ownership_conflict_returns_none():
    """When ownership_conflicts is non-empty → returns None."""
    snapshot = _snapshot()
    t = _treatment("worker-a", ownership="shared")
    t2 = _treatment("worker-b", ownership="shared")
    graph = _graph(
        graph_id="graph-abc",
        eligible=(_disposition("worker-a"), _disposition("worker-b")),
        conflicts=(("worker-a", "worker-b", ("shared",)),),
    )

    ctx, mock_sm = _mock_enqueue_context()
    with ctx:
        job_id = dispatch_treatment(snapshot, graph, (t, t2))

    assert job_id is None
    mock_sm.enqueue_job.assert_not_called()


# ── dispatch_treatment: reconciliation gates → none ────────────────────────


def test_reconciliation_gates_returns_none():
    """When reconciliation_gates is non-empty → returns None."""
    snapshot = _snapshot()
    t = _treatment("ebay-upload", ownership="listing.photos",
                   effect_class=EffectClass.EXTERNAL)
    graph = _graph(
        graph_id="graph-abc",
        eligible=(_disposition("ebay-upload"),),
        gates=("provider.account",),
    )

    ctx, mock_sm = _mock_enqueue_context()
    with ctx:
        job_id = dispatch_treatment(snapshot, graph, (t,))

    assert job_id is None
    mock_sm.enqueue_job.assert_not_called()


# ── dispatch_treatment: re-enqueue → idempotent ────────────────────────────


def test_re_enqueue_idempotent_returns_none():
    """Re-enqueue with same graph_id → UniqueViolation caught, returns None."""

    class UniqueViolation(Exception):
        pass

    snapshot = _snapshot()
    t = _treatment("codex-implement")
    graph = _graph(
        graph_id="graph-abc",
        eligible=(_disposition("codex-implement"),),
    )

    sm = _make_mock_sm()
    sm.enqueue_job = MagicMock(side_effect=UniqueViolation("duplicate key"))
    with patch.dict("sys.modules", {"tgw.queue.state_machine": sm}):
        job_id = dispatch_treatment(snapshot, graph, (t,))

    assert job_id is None
    sm.enqueue_job.assert_called_once()


def test_re_enqueue_non_duplicate_error_raises():
    """Non-duplicate errors propagate, not swallowed."""
    import pytest

    snapshot = _snapshot()
    t = _treatment("codex-implement")
    graph = _graph(
        graph_id="graph-abc",
        eligible=(_disposition("codex-implement"),),
    )

    sm = _make_mock_sm()
    sm.enqueue_job = MagicMock(side_effect=RuntimeError("db connection lost"))
    with patch.dict("sys.modules", {"tgw.queue.state_machine": sm}):
        with pytest.raises(RuntimeError, match="db connection lost"):
            dispatch_treatment(snapshot, graph, (t,))


# ── dispatch_treatment: deterministic dispatch ─────────────────────────────


def test_deterministic_first_eligible_dispatched():
    """With two eligible treatments, the first (sorted) is dispatched."""
    snapshot = _snapshot()
    t_a = _treatment("codex-implement", ownership="code.impl")
    t_b = _treatment("controller-verify", ownership="code.verify")
    graph = _graph(
        graph_id="graph-abc",
        eligible=(
            _disposition("codex-implement"),
            _disposition("controller-verify"),
        ),
    )

    ctx, mock_sm = _mock_enqueue_context("job-001")
    with ctx:
        job_id = dispatch_treatment(snapshot, graph, (t_a, t_b))

    assert job_id == "job-001"
    mock_sm.enqueue_job.assert_called_once()
    call_kwargs = mock_sm.enqueue_job.call_args.kwargs
    assert call_kwargs["queue_name"] == "codex-implement"
    assert call_kwargs["handler_family"] == "codex-implement"


# ── dispatch_treatment: treatment_identity as queue/handler ────────────────


def test_treatment_identity_used_as_queue_name_and_handler_family():
    """The treatment's identity field is used for both queue_name and
    handler_family."""
    snapshot = _snapshot()
    t = _treatment("claude-review")
    graph = _graph(
        graph_id="graph-def",
        eligible=(_disposition("claude-review"),),
    )

    ctx, mock_sm = _mock_enqueue_context("job-002")
    with ctx:
        job_id = dispatch_treatment(snapshot, graph, (t,))

    assert job_id == "job-002"
    call_kwargs = mock_sm.enqueue_job.call_args.kwargs
    assert call_kwargs["queue_name"] == "claude-review"
    assert call_kwargs["handler_family"] == "claude-review"


# ── dispatch_treatment: entity_type and entity_id passthrough ──────────────


def test_entity_type_and_entity_id_forwarded():
    """entity_type and entity_id are passed through to enqueue_job."""
    snapshot = _snapshot()
    t = _treatment("codex-implement")
    graph = _graph(
        graph_id="graph-abc",
        eligible=(_disposition("codex-implement"),),
    )

    ctx, mock_sm = _mock_enqueue_context("job-003")
    with ctx:
        job_id = dispatch_treatment(
            snapshot, graph, (t,),
            entity_type="item",
            entity_id="SKU-12345",
        )

    assert job_id == "job-003"
    call_kwargs = mock_sm.enqueue_job.call_args.kwargs
    assert call_kwargs["entity_type"] == "item"
    assert call_kwargs["entity_id"] == "SKU-12345"


# ── dispatch_treatment: payload completeness ───────────────────────────────


def test_payload_contains_all_expected_fields():
    """The enqueued payload carries graph, object, and treatment metadata."""
    snapshot = _snapshot(object_id="my-object", generation="gen-7")
    t = _treatment("codex-implement")
    graph = _graph(
        graph_id="graph-hash-xyz",
        eligible=(_disposition("codex-implement", version="1",
                               reasons=("implemented=false",)),),
        object_id="my-object",
        object_generation="gen-7",
        goal_profile_id="coding.ready_for_implementation",
        goal_profile_version="1",
    )

    ctx, mock_sm = _mock_enqueue_context("job-004")
    with ctx:
        dispatch_treatment(snapshot, graph, (t,))

    payload = mock_sm.enqueue_job.call_args.kwargs["payload"]
    assert payload["graph_id"] == "graph-hash-xyz"
    assert payload["object_id"] == "my-object"
    assert payload["object_generation"] == "gen-7"
    assert payload["goal_profile_id"] == "coding.ready_for_implementation"
    assert payload["goal_profile_version"] == "1"
    assert payload["condition_hash"] == "condition-hash"
    assert payload["treatment_id"] == "codex-implement"
    assert payload["treatment_version"] == "1"
    assert payload["evaluator_version"] == "test-evaluator/v1"


def test_payload_extra_cannot_forge_running_observation_checkpoint():
    from tgw.workflow.scheduler import _dispatch_treatment_v4

    disposition = _disposition("codex-implement", version="1", reasons=("ready",))
    enqueued = MagicMock()
    with pytest.raises(ValueError, match="reserved"):
        _dispatch_treatment_v4(
            disposition=disposition,
            entity_id="SKU-1",
            payload_extra={"observation_checkpoint": None},
            enqueue_fn=enqueued,
        )
    enqueued.assert_not_called()


def test_item_dispatch_binds_legacy_sku_to_entity_id():
    from tgw.workflow.scheduler import _dispatch_treatment_v4

    enqueue = MagicMock(return_value="job-1")
    result = _dispatch_treatment_v4(
        disposition=_disposition("ai-identify", version="1", reasons=("ready",)),
        entity_id="SKU-1",
        entity_type="item",
        enqueue_fn=enqueue,
    )

    assert result.enqueued is True
    assert enqueue.call_args.kwargs["payload"]["sku"] == "SKU-1"


def test_item_dispatch_dedupes_same_treatment_and_generation_not_graph_hash():
    """One click/evaluation cannot launch duplicate work for unchanged data."""
    from tgw.workflow.scheduler import _dispatch_treatment_v4

    disposition = _disposition("ai-identify", version="1", reasons=("ready",))
    first = _graph(
        graph_id="first-evaluation", object_id="SKU-1", object_generation="gen-7",
        eligible=(disposition,),
    )
    second = _graph(
        graph_id="second-evaluation", object_id="SKU-1", object_generation="gen-7",
        eligible=(disposition,),
    )
    enqueue = MagicMock(return_value="job-1")

    _dispatch_treatment_v4(
        disposition=disposition, entity_id="SKU-1", graph=first, enqueue_fn=enqueue,
    )
    _dispatch_treatment_v4(
        disposition=disposition, entity_id="SKU-1", graph=second, enqueue_fn=enqueue,
    )

    assert enqueue.call_args_list[0].kwargs["dedupe_key"] == (
        "treatment:ai_identify:item:SKU-1:gen-7:ai-identify:1"
    )
    assert enqueue.call_args_list[1].kwargs["dedupe_key"] == (
        "treatment:ai_identify:item:SKU-1:gen-7:ai-identify:1"
    )


def test_item_dispatch_rejects_spoofed_sku():
    from tgw.workflow.scheduler import _dispatch_treatment_v4

    with pytest.raises(ValueError, match="sku must match entity_id"):
        _dispatch_treatment_v4(
            disposition=_disposition("ai-identify", version="1", reasons=("ready",)),
            entity_id="SKU-1",
            entity_type="item",
            payload_extra={"sku": "SKU-2"},
            enqueue_fn=MagicMock(),
        )


# ── dispatch_treatment: missing treatment contract → None ──────────────────


def test_missing_treatment_contract_returns_none():
    """When the eligible treatment isn't in the supplied treatments tuple,
    return None (defensive guard)."""
    snapshot = _snapshot()
    # Supply a different treatment than what's in the graph.
    t = _treatment("some-other-treatment")
    graph = _graph(
        graph_id="graph-abc",
        eligible=(_disposition("codex-implement"),),
    )

    ctx, mock_sm = _mock_enqueue_context()
    with ctx:
        job_id = dispatch_treatment(snapshot, graph, (t,))

    assert job_id is None
    mock_sm.enqueue_job.assert_not_called()
