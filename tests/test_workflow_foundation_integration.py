from dataclasses import replace

import pytest

from tgw.workflow import (
    FoundationDispatchBinding,
    GenerationConflict,
    ReceiptDisposition,
    RuntimeWorkGraph,
    TreatmentReceipt,
    classify_receipt,
    integration_receipt,
)


def _graph(generation="generation-7"):
    return RuntimeWorkGraph(
        schema_version="runtime-work-graph/v1", graph_id="graph-7", object_id="object-1",
        object_generation=generation, goal_profile_id="goal", goal_profile_version="1",
        evaluator_version="evaluator/v1", evidence_set_hash="e", condition_hash="c",
        treatment_registry_hash="t", fingerprints=(), satisfied_requirements=(), unmet_requirements=(),
        explicit_requirements=(), eligible_treatments=(), waiting_treatments=(), ownership_conflicts=(),
        reconciliation_gates=(), next_event_classes=(),
    )


def _payload(**changes):
    value = {
        "graph_id": "graph-7", "object_id": "object-1", "object_generation": "generation-7",
        "treatment_id": "build-foundation", "treatment_version": "candidate-1",
    }
    value.update(changes)
    return value


def _receipt(outcome="satisfied", **changes):
    value = {
        "treatment_id": "build-foundation", "treatment_version": "candidate-1", "graph_id": "graph-7",
        "outcome": outcome, "established_conditions": ("queue.durable-claims@1",),
        "artifacts": ("test:foundation",),
    }
    value.update(changes)
    return TreatmentReceipt(**value)


def test_dispatch_binding_reuses_runtime_graph_as_generation_bound_dedupe_and_run_once_identity():
    first = FoundationDispatchBinding.from_payload(_payload(), _graph())
    repeated = FoundationDispatchBinding.from_payload(dict(reversed(list(_payload().items()))), _graph())

    assert first.dedupe_key == "graph-7"
    assert first.run_identity == repeated.run_identity


def test_generation_conflict_fails_before_worker_evidence_can_be_accepted():
    with pytest.raises(GenerationConflict, match="generation is stale"):
        FoundationDispatchBinding.from_payload(_payload(object_generation="generation-6"), _graph())


@pytest.mark.parametrize(
    ("outcome", "expected"),
    [
        ("satisfied", ReceiptDisposition.ACCEPTED),
        ("partial", ReceiptDisposition.RETRY),
        ("retry", ReceiptDisposition.RETRY),
        ("ambiguous", ReceiptDisposition.AMBIGUOUS),
        ("failed", ReceiptDisposition.TERMINAL_FAILURE),
        ("conflict", ReceiptDisposition.CONFLICT),
    ],
)
def test_terminal_receipt_outcomes_preserve_retry_ambiguity_and_conflict(outcome, expected):
    binding = FoundationDispatchBinding.from_payload(_payload(), _graph())
    assert classify_receipt(_receipt(outcome), binding) is expected


def test_stale_receipt_never_establishes_current_generation():
    binding = FoundationDispatchBinding.from_payload(_payload(), _graph())
    stale = replace(_receipt(), graph_id="graph-older")

    assert classify_receipt(stale, binding) is ReceiptDisposition.STALE_GENERATION


def test_integration_receipt_is_immutable_deterministic_and_binds_terminal_evidence():
    first = integration_receipt(graph=_graph(), dispatch_payload=_payload(), receipt=_receipt())
    repeated = integration_receipt(graph=_graph(), dispatch_payload=_payload(), receipt=_receipt())

    assert first == repeated
    assert first.schema == "tgw-foundation-integration-receipt/v1"
    assert first.disposition is ReceiptDisposition.ACCEPTED
    assert first.treatment_receipt_fingerprint == _receipt().fingerprint
    with pytest.raises(Exception):
        first.disposition = ReceiptDisposition.RETRY
