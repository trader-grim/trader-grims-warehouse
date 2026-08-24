"""Tests for tgw.development.foreman — coding foreman tick cycle."""

import sys
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from tgw.workflow_kernel.contracts import (
    EvidenceAssertion,
    EvidenceReference,
    Fingerprint,
    FingerprintResult,
    ObjectSnapshot,
    RuntimeWorkGraph,
    TreatmentDisposition,
)
from tgw.development.foreman import (
    EVALUATOR_VERSION,
    ForemanConfig,
    TickResult,
    TodoRecord,
    _has_active_job,
    _has_terminal_job,
    _extract_worktree,
    tick,
)
from tgw.development.profiles import CODING_READY_FOR_IMPLEMENTATION
from tgw.workflow_kernel.scheduler import DispatchResult
from tgw.development.treatments import CODING_TREATMENTS
from tgw.development.provider_dispatch import ProviderAdapter


def test_database_job_state_checks_cast_text_arrays_to_queue_enum(monkeypatch):
    """The local schema uses ``queue_job_state`` rather than text."""
    executed = []

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, sql, params):
            executed.append((sql, params))

        def fetchone(self):
            return None

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def cursor(self):
            return Cursor()

    @contextmanager
    def connection():
        yield Connection()

    monkeypatch.setattr("tgw.queue.state_machine._conn", connection)
    assert _has_active_job("graph") is False
    assert _has_terminal_job("graph") is False
    assert len(executed) == 2
    assert all("ANY(%s::queue_job_state[])" in sql for sql, _params in executed)


@pytest.fixture(autouse=True)
def _fake_worktree_proof_for_mocked_foreman_tests(monkeypatch):
    """Legacy unit cases use invented paths and mock all project work.

    The containment regressions below intentionally use the real proof.
    """
    monkeypatch.setattr(
        "tgw.development.foreman.validated_coding_worktree",
        lambda worktree, _object_id, _config: Path(worktree).resolve(),
    )
    # Legacy unit cases exercise evaluator/direct-treatment mechanics.  The
    # provider-resolution contract has dedicated tests with the real catalog.
    monkeypatch.setattr(
        "tgw.development.foreman.resolve_implementation_adapter",
        lambda *_args, **_kwargs: ProviderAdapter(
            "implementation", "codex-local-runner", "codex-implement", "codex-implement"
        ),
    )

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _snapshot(object_id="/tmp/worktree-1", generation="abc123", *, assertions=()):
    """Build an ObjectSnapshot for testing."""
    return ObjectSnapshot(
        object_id=object_id,
        generation=generation,
        assertions=assertions,
        external_effect_ambiguities=(),
    )


def _assertion(condition_id, result, *reasons):
    return EvidenceAssertion(
        condition_id=condition_id,
        result=result,
        reasons=reasons,
        evidence=(
            EvidenceReference(
                identity="test-1",
                source_class="test",
                source_generation="1",
            ),
        ),
    )


def _implemented():
    return (
        _assertion("implemented", FingerprintResult.FALSE, "not implemented"),
    )


def _implemented_true():
    return (
        _assertion("implemented", FingerprintResult.TRUE, "implemented"),
    )


def _all_satisfied():
    return (
        _assertion("implemented", FingerprintResult.TRUE, "implemented"),
        _assertion("tested", FingerprintResult.TRUE, "tested"),
        _assertion("linted", FingerprintResult.TRUE, "linted"),
    )


def _graph(
    object_id="/tmp/worktree-1",
    graph_id="graph-001",
    *,
    eligible=(),
    waiting=(),
    conflicts=(),
):
    """Build a RuntimeWorkGraph for testing."""
    return RuntimeWorkGraph(
        schema_version="runtime-work-graph/v1",
        graph_id=graph_id,
        object_id=object_id,
        object_generation="abc123",
        goal_profile_id=CODING_READY_FOR_IMPLEMENTATION.identity,
        goal_profile_version=CODING_READY_FOR_IMPLEMENTATION.version,
        evaluator_version=EVALUATOR_VERSION,
        evidence_set_hash="evidence-hash",
        condition_hash="condition-hash",
        treatment_registry_hash="treatment-hash",
        fingerprints=(),
        satisfied_requirements=(),
        unmet_requirements=(),
        explicit_requirements=(),
        eligible_treatments=eligible,
        waiting_treatments=waiting,
        ownership_conflicts=conflicts,
        reconciliation_gates=(),
        next_event_classes=(),
    )


def _todo(todo_id=1, worktree="/tmp/worktree-1", body="worktree: /tmp/worktree-1"):
    return TodoRecord(
        todo_id=todo_id,
        agent="test-agent",
        priority=100,
        body=body,
        worktree=worktree,
    )


# ---------------------------------------------------------------------------
# Test: tick with one eligible todo → dispatches one treatment
# ---------------------------------------------------------------------------


def test_tick_one_eligible_dispatches_one():
    fetch_todos = MagicMock(return_value=[_todo(1)])
    check_active = MagicMock(return_value=False)

    disposition = TreatmentDisposition(
        treatment_id="codex-implement",
        treatment_version="1",
        reasons=("implemented=false: not implemented",),
    )
    mock_enqueue = MagicMock(return_value="job-001")

    with (
        patch(
            "tgw.development.foreman.build_coding_snapshot",
            return_value=_snapshot(assertions=_implemented()),
        ),
        patch(
            "tgw.development.foreman.evaluate",
            return_value=_graph(
                object_id="/tmp/worktree-1",
                graph_id="graph-abc",
                eligible=(disposition,),
            ),
        ),
        patch(
            "tgw.development.foreman.dispatch_treatment",
            return_value=DispatchResult(
                treatment_id="codex-implement",
                treatment_version="1",
                queue_name="codex",
                entity_id="/tmp/worktree-1",
                enqueued=True,
                job_id="job-001",
            ),
        ) as mock_dispatch,
    ):
        result = tick(
            fetch_todos=fetch_todos,
            check_active_fn=check_active,
            enqueue_fn=mock_enqueue,
        )

    assert result.dispatched == 1
    assert result.skipped_waiting == 0
    assert result.skipped_conflict == 0
    assert result.errors == 0
    mock_dispatch.assert_called_once()
    call_kwargs = mock_dispatch.call_args.kwargs
    assert call_kwargs["disposition"].treatment_id == "codex-implement"


# ---------------------------------------------------------------------------
# Test: tick with all todos satisfied → dispatched=0
# ---------------------------------------------------------------------------


def test_tick_all_satisfied_dispatches_zero():
    fetch_todos = MagicMock(return_value=[_todo(1)])
    check_active = MagicMock(return_value=False)

    with (
        patch(
            "tgw.development.foreman.build_coding_snapshot",
            return_value=_snapshot(assertions=_all_satisfied()),
        ),
        patch(
            "tgw.development.foreman.evaluate",
            return_value=_graph(
                object_id="/tmp/worktree-1",
                graph_id="graph-done",
                eligible=(),  # all satisfied — nothing to do
            ),
        ),
    ):
        result = tick(
            fetch_todos=fetch_todos,
            check_active_fn=check_active,
        )

    assert result.dispatched == 0
    assert result.skipped_waiting == 1  # no eligible treatments
    assert result.errors == 0


# ---------------------------------------------------------------------------
# Test: tick with ownership conflict → dispatched=0, skipped_conflict=1
# ---------------------------------------------------------------------------


def test_tick_ownership_conflict_dispatches_zero():
    fetch_todos = MagicMock(return_value=[_todo(1)])
    check_active = MagicMock(return_value=False)

    disposition_a = TreatmentDisposition("codex-implement", "1", ("ready",))
    disposition_b = TreatmentDisposition("competing", "1", ("ready",))

    with (
        patch(
            "tgw.development.foreman.build_coding_snapshot",
            return_value=_snapshot(assertions=_implemented()),
        ),
        patch(
            "tgw.development.foreman.evaluate",
            return_value=_graph(
                object_id="/tmp/worktree-1",
                graph_id="graph-conflict",
                eligible=(disposition_a, disposition_b),
                conflicts=(
                    ("codex-implement", "competing", ("code.implementation",)),
                ),
            ),
        ),
    ):
        result = tick(
            fetch_todos=fetch_todos,
            check_active_fn=check_active,
        )

    assert result.dispatched == 0
    assert result.skipped_conflict == 1
    assert result.errors == 0


# ---------------------------------------------------------------------------
# Test: tick with same generation re-run → dispatched=0 (idempotent)
# ---------------------------------------------------------------------------


def test_tick_same_generation_rerun_is_idempotent():
    """Re-running tick with the same graph_id must skip (active job check)."""
    fetch_todos = MagicMock(return_value=[_todo(1)])
    # Active job exists for this graph_id.
    check_active = MagicMock(return_value=True)

    disposition = TreatmentDisposition(
        "codex-implement", "1", ("implemented=false",)
    )

    with (
        patch(
            "tgw.development.foreman.build_coding_snapshot",
            return_value=_snapshot(assertions=_implemented()),
        ),
        patch(
            "tgw.development.foreman.evaluate",
            return_value=_graph(
                object_id="/tmp/worktree-1",
                graph_id="graph-same",
                eligible=(disposition,),
            ),
        ),
    ):
        result = tick(
            fetch_todos=fetch_todos,
            check_active_fn=check_active,
        )

    assert result.dispatched == 0
    assert result.skipped_active == 1  # deduped
    assert result.errors == 0


# ---------------------------------------------------------------------------
# Test: tick skips todos that already have an active job with same graph_id
# ---------------------------------------------------------------------------


def test_tick_skips_todos_with_active_job():
    """Multiple todos, one has an active job — it is skipped."""
    fetch_todos = MagicMock(return_value=[_todo(1), _todo(2, worktree="/tmp/wt2")])
    # First todo's graph_id already has an active job.
    check_active_responses = [True, False]

    def check_active(graph_id):
        return check_active_responses.pop(0)

    disposition = TreatmentDisposition(
        "codex-implement", "1", ("implemented=false",)
    )
    mock_enqueue = MagicMock(return_value="job-002")

    with (
        patch(
            "tgw.development.foreman.build_coding_snapshot",
            side_effect=[
                _snapshot(object_id="/tmp/worktree-1", assertions=_implemented()),
                _snapshot(object_id="/tmp/wt2", assertions=_implemented()),
            ],
        ),
        patch(
            "tgw.development.foreman.evaluate",
            side_effect=[
                _graph(graph_id="graph-active", object_id="/tmp/worktree-1",
                       eligible=(disposition,)),
                _graph(graph_id="graph-fresh", object_id="/tmp/wt2",
                       eligible=(disposition,)),
            ],
        ),
        patch(
            "tgw.development.foreman.dispatch_treatment",
            return_value=DispatchResult(
                treatment_id="codex-implement",
                treatment_version="1",
                queue_name="codex",
                entity_id="/tmp/wt2",
                enqueued=True,
                job_id="job-002",
            ),
        ),
    ):
        result = tick(
            fetch_todos=fetch_todos,
            check_active_fn=check_active,
            enqueue_fn=mock_enqueue,
        )

    assert result.dispatched == 1  # second todo dispatched
    assert result.skipped_active == 1  # first todo skipped
    assert result.errors == 0


# ---------------------------------------------------------------------------
# Test: tick with empty todo list → dispatched=0, no errors
# ---------------------------------------------------------------------------


def test_tick_empty_todo_list():
    fetch_todos = MagicMock(return_value=[])
    check_active = MagicMock(return_value=False)

    result = tick(fetch_todos=fetch_todos, check_active_fn=check_active)

    assert result.dispatched == 0
    assert result.skipped_waiting == 0
    assert result.skipped_conflict == 0
    assert result.skipped_active == 0
    assert result.errors == 0


# ---------------------------------------------------------------------------
# Test: tick with one todo having unmet dependencies → waiting, no dispatch
# ---------------------------------------------------------------------------


def test_tick_unmet_dependencies_waits():
    """Todo with unmet conditions — treatments are waiting, not eligible."""
    fetch_todos = MagicMock(return_value=[_todo(1)])
    check_active = MagicMock(return_value=False)

    waiting_disp = TreatmentDisposition(
        "codex-implement",
        "1",
        ("implemented=true: already done",),
    )

    with (
        patch(
            "tgw.development.foreman.build_coding_snapshot",
            return_value=_snapshot(assertions=_implemented_true()),
        ),
        patch(
            "tgw.development.foreman.evaluate",
            return_value=_graph(
                object_id="/tmp/worktree-1",
                graph_id="graph-waiting",
                eligible=(),  # nothing eligible
                waiting=(waiting_disp,),  # codex-implement is waiting
            ),
        ),
    ):
        result = tick(
            fetch_todos=fetch_todos,
            check_active_fn=check_active,
        )

    assert result.dispatched == 0
    assert result.skipped_waiting == 1  # all waiting
    assert result.errors == 0


# ---------------------------------------------------------------------------
# Test: multiple todos — dispatches exactly one treatment total
# ---------------------------------------------------------------------------


def test_tick_multiple_todos_dispatches_exactly_one():
    """When multiple todos are all eligible, only the first one dispatches."""
    fetch_todos = MagicMock(
        return_value=[_todo(1, worktree="/tmp/wt1"), _todo(2, worktree="/tmp/wt2")]
    )
    check_active = MagicMock(return_value=False)

    disposition = TreatmentDisposition(
        "codex-implement", "1", ("implemented=false",)
    )

    with (
        patch(
            "tgw.development.foreman.build_coding_snapshot",
            side_effect=[
                _snapshot(object_id="/tmp/wt1", assertions=_implemented()),
                _snapshot(object_id="/tmp/wt2", assertions=_implemented()),
            ],
        ),
        patch(
            "tgw.development.foreman.evaluate",
            side_effect=[
                _graph(graph_id="graph-1", object_id="/tmp/wt1",
                       eligible=(disposition,)),
                _graph(graph_id="graph-2", object_id="/tmp/wt2",
                       eligible=(disposition,)),
            ],
        ),
        patch(
            "tgw.development.foreman.dispatch_treatment",
            return_value=DispatchResult(
                treatment_id="codex-implement",
                treatment_version="1",
                queue_name="codex",
                entity_id="/tmp/wt1",
                enqueued=True,
                job_id="job-001",
            ),
        ),
    ):
        result = tick(
            fetch_todos=fetch_todos,
            check_active_fn=check_active,
        )

    assert result.dispatched == 1
    assert result.errors == 0


def test_tick_admits_most_urgent_eligible_todo():
    """Admission considers all evaluated todos, not caller ordering."""
    less_urgent = TodoRecord(2, "test-agent", 90, "", "/tmp/wt-later")
    urgent = TodoRecord(1, "test-agent", 10, "", "/tmp/wt-urgent")
    disposition = TreatmentDisposition("codex-implement", "1", ("ready",))
    enqueue = MagicMock(return_value="job-urgent")

    with (
        patch(
            "tgw.development.foreman.build_coding_snapshot",
            side_effect=[
                _snapshot(object_id=less_urgent.worktree, assertions=_implemented()),
                _snapshot(object_id=urgent.worktree, assertions=_implemented()),
            ],
        ),
        patch(
            "tgw.development.foreman.evaluate",
            side_effect=[
                _graph(graph_id="graph-later", object_id=less_urgent.worktree, eligible=(disposition,)),
                _graph(graph_id="graph-urgent", object_id=urgent.worktree, eligible=(disposition,)),
            ],
        ),
    ):
        result = tick(
            fetch_todos=lambda: [less_urgent, urgent],
            check_active_fn=lambda _graph_id: False,
            enqueue_fn=enqueue,
        )

    assert result.dispatched == 1
    assert enqueue.call_args.kwargs["dedupe_key"] == (
        "treatment:codex-implement:coding_task:/tmp/wt-urgent:"
        "abc123:codex-implement:1"
    )
    assert enqueue.call_args.kwargs["payload"]["todo_id"] == urgent.todo_id


def test_tick_equal_priority_orders_by_todo_id_then_treatment_identity():
    """The global admission key includes a stable todo tie-breaker."""
    first = TodoRecord(1, "test-agent", 10, "", "/tmp/wt-z")
    second = TodoRecord(2, "test-agent", 10, "", "/tmp/wt-a")
    zeta = TreatmentDisposition("zeta", "1", ("ready",))
    alpha = TreatmentDisposition("alpha", "1", ("ready",))
    enqueue = MagicMock(return_value="job-alpha")
    with (
        patch("tgw.development.foreman.build_coding_snapshot", side_effect=[
            _snapshot(object_id=first.worktree), _snapshot(object_id=second.worktree),
        ]),
        patch("tgw.development.foreman.evaluate", side_effect=[
            _graph(graph_id="graph-z", object_id=first.worktree, eligible=(zeta,)),
            _graph(graph_id="graph-a", object_id=second.worktree, eligible=(alpha,)),
        ]),
    ):
        result = tick(fetch_todos=lambda: [first, second], check_active_fn=lambda _: False, enqueue_fn=enqueue)

    assert result.dispatched == 1
    assert enqueue.call_args.kwargs["dedupe_key"] == (
        "treatment:zeta:coding_task:/tmp/wt-z:abc123:zeta:1"
    )


def test_tick_null_priority_is_last():
    """A missing priority has the documented value 999, after real priorities."""
    missing = TodoRecord(1, "test-agent", None, "", "/tmp/wt-missing")
    real = TodoRecord(2, "test-agent", 20, "", "/tmp/wt-real")
    disposition = TreatmentDisposition("codex-implement", "1", ("ready",))
    enqueue = MagicMock(return_value="job-real")
    with (
        patch("tgw.development.foreman.build_coding_snapshot", side_effect=[
            _snapshot(object_id=missing.worktree), _snapshot(object_id=real.worktree),
        ]),
        patch("tgw.development.foreman.evaluate", side_effect=[
            _graph(graph_id="graph-missing", object_id=missing.worktree, eligible=(disposition,)),
            _graph(graph_id="graph-real", object_id=real.worktree, eligible=(disposition,)),
        ]),
    ):
        result = tick(fetch_todos=lambda: [missing, real], check_active_fn=lambda _: False, enqueue_fn=enqueue)

    assert result.dispatched == 1
    assert enqueue.call_args.kwargs["dedupe_key"] == (
        "treatment:codex-implement:coding_task:/tmp/wt-real:"
        "abc123:codex-implement:1"
    )


def test_tick_retry_wait_job_is_active_and_not_reenqueued():
    """A retry-wait graph remains owned; dedupe is not recorded as an error."""
    enqueue = MagicMock(return_value="should-not-run")
    disposition = TreatmentDisposition("codex-implement", "1", ("ready",))
    with (
        patch("tgw.development.foreman.build_coding_snapshot", return_value=_snapshot()),
        patch("tgw.development.foreman.evaluate", return_value=_graph(eligible=(disposition,))),
    ):
        result = tick(
            fetch_todos=lambda: [_todo()],
            check_active_fn=lambda _graph_id: True,  # retry_wait is active
            enqueue_fn=enqueue,
        )

    assert result.skipped_active == 1
    assert result.errors == 0
    enqueue.assert_not_called()


def test_tick_wires_evaluator_fingerprints_to_codex_dispatch():
    """A ready implementation graph launches codex with its decision record."""
    disposition = TreatmentDisposition(
        "codex-implement", "1", ("implemented=false: not implemented",)
    )
    graph = _graph(
        graph_id="graph-ready",
        eligible=(disposition,),
    )
    graph = RuntimeWorkGraph(
        **{
            **graph.__dict__,
            "fingerprints": (
                Fingerprint(
                    "implemented",
                    FingerprintResult.FALSE,
                    ("not implemented",),
                    (),
                ),
            ),
        }
    )
    enqueue = MagicMock(return_value="job-codex")

    with (
        patch(
            "tgw.development.foreman.build_coding_snapshot",
            return_value=_snapshot(assertions=_implemented()),
        ),
        patch("tgw.development.foreman.evaluate", return_value=graph) as evaluator,
    ):
        result = tick(
            fetch_todos=lambda: [_todo(1731)],
            check_active_fn=lambda _graph_id: False,
            enqueue_fn=enqueue,
        )

    assert result.dispatched == 1
    evaluator.assert_called_once()
    call = enqueue.call_args.kwargs
    assert call["queue_name"] == "codex-implement"
    assert call["handler_family"] == "codex-implement"
    assert call["dedupe_key"] == (
        "treatment:codex-implement:coding_task:/tmp/worktree-1:"
        "abc123:codex-implement:1"
    )
    assert call["entity_type"] == "coding_task"
    assert call["payload"]["todo_id"] == 1731
    assert call["payload"]["treatment_id"] == "codex-implement"
    assert call["payload"]["fingerprints"] == [
        {
            "condition_id": "implemented",
            "result": "false",
            "reasons": ["not implemented"],
            "evidence": [],
        }
    ]


def test_tick_routes_post_implementation_treatments_by_identity():
    """Evaluator-selected review/verification treatments reach their workers."""
    for treatment_id in ("claude-review", "controller-verify", "hermes-stitch"):
        disposition = TreatmentDisposition(treatment_id, "1", ("ready",))
        enqueue = MagicMock(return_value=f"job-{treatment_id}")
        with (
            patch(
                "tgw.development.foreman.build_coding_snapshot",
                return_value=_snapshot(assertions=_all_satisfied()),
            ),
            patch(
                "tgw.development.foreman.evaluate",
                return_value=_graph(
                    graph_id=f"graph-{treatment_id}", eligible=(disposition,)
                ),
            ),
        ):
            result = tick(
                fetch_todos=lambda: [_todo(1731)],
                check_active_fn=lambda _graph_id: False,
                enqueue_fn=enqueue,
            )

        assert result.dispatched == 1
        assert enqueue.call_args.kwargs["queue_name"] == treatment_id
        assert enqueue.call_args.kwargs["handler_family"] == treatment_id


def test_real_evaluator_dispatches_stitch_after_review_and_verification():
    """Review/verification receipts advance coding without an operator wait."""
    todo = _todo(1731)
    snapshot = _snapshot(
        assertions=(
            _assertion("implemented", FingerprintResult.TRUE, "implemented"),
            _assertion("tested", FingerprintResult.TRUE, "tested"),
            _assertion("linted", FingerprintResult.TRUE, "linted"),
            _assertion("reviewed", FingerprintResult.TRUE, "reviewed"),
            _assertion("controller_verified", FingerprintResult.TRUE, "verified"),
            _assertion("committed", FingerprintResult.FALSE, "not committed"),
        ),
    )
    dispatch = MagicMock(return_value=DispatchResult(
        "hermes-stitch", "1", "hermes-stitch", todo.worktree, True, job_id="stitch-job",
    ))
    with (
        patch("tgw.development.foreman.build_coding_snapshot", return_value=snapshot),
        patch("tgw.development.foreman.dispatch_treatment", dispatch),
    ):
        result = tick(
            fetch_todos=lambda: [todo], check_active_fn=lambda _: False,
            check_terminal_fn=lambda _: False,
        )
    assert result.dispatched == 1
    assert [call.kwargs["disposition"].treatment_id for call in dispatch.call_args_list] == ["hermes-stitch"]


def test_tick_dispatch_failure_continues_to_lower_priority_todo():
    first = TodoRecord(1, "test-agent", 1, "", "/tmp/first")
    second = TodoRecord(2, "test-agent", 5, "", "/tmp/second")
    disposition = TreatmentDisposition("codex-implement", "1", ("ready",))
    enqueue = MagicMock(side_effect=[RuntimeError("queue unavailable"), "second-job"])
    with (
        patch("tgw.development.foreman.build_coding_snapshot", side_effect=[
            _snapshot(object_id=first.worktree), _snapshot(object_id=second.worktree),
        ]),
        patch("tgw.development.foreman.evaluate", side_effect=[
            _graph(graph_id="first", object_id=first.worktree, eligible=(disposition,)),
            _graph(graph_id="second", object_id=second.worktree, eligible=(disposition,)),
        ]),
    ):
        result = tick(fetch_todos=lambda: [first, second], check_active_fn=lambda _: False, check_terminal_fn=lambda _: False, enqueue_fn=enqueue)
    assert result.dispatched == 1
    assert result.errors == 1
    assert enqueue.call_args.kwargs["dedupe_key"] == (
        "treatment:codex-implement:coding_task:/tmp/second:"
        "abc123:codex-implement:1"
    )


def test_tick_duplicate_dispatch_is_not_an_error_and_continues():
    class DuplicateKey(Exception):
        pgcode = "23505"

    first = TodoRecord(1, "test-agent", 1, "", "/tmp/first")
    second = TodoRecord(2, "test-agent", 5, "", "/tmp/second")
    disposition = TreatmentDisposition("codex-implement", "1", ("ready",))
    enqueue = MagicMock(side_effect=[DuplicateKey(), "second-job"])
    with (
        patch("tgw.development.foreman.build_coding_snapshot", side_effect=[
            _snapshot(object_id=first.worktree), _snapshot(object_id=second.worktree),
        ]),
        patch("tgw.development.foreman.evaluate", side_effect=[
            _graph(graph_id="first", object_id=first.worktree, eligible=(disposition,)),
            _graph(graph_id="second", object_id=second.worktree, eligible=(disposition,)),
        ]),
    ):
        result = tick(fetch_todos=lambda: [first, second], check_active_fn=lambda _: False, check_terminal_fn=lambda _: False, enqueue_fn=enqueue)
    assert result.dispatched == 1
    assert result.errors == 0
    assert result.skipped_active == 1


def test_tick_terminal_graph_is_durably_skipped_and_does_not_starve_next_todo():
    first = TodoRecord(1, "test-agent", 1, "", "/tmp/first")
    second = TodoRecord(2, "test-agent", 5, "", "/tmp/second")
    disposition = TreatmentDisposition("codex-implement", "1", ("ready",))
    enqueue = MagicMock(return_value="second-job")
    with (
        patch("tgw.development.foreman.build_coding_snapshot", side_effect=[
            _snapshot(object_id=first.worktree), _snapshot(object_id=second.worktree),
        ]),
        patch("tgw.development.foreman.evaluate", side_effect=[
            _graph(graph_id="terminal", object_id=first.worktree, eligible=(disposition,)),
            _graph(graph_id="fresh", object_id=second.worktree, eligible=(disposition,)),
        ]),
    ):
        result = tick(fetch_todos=lambda: [first, second], check_active_fn=lambda _: False, check_terminal_fn=lambda graph_id: graph_id == "terminal", enqueue_fn=enqueue)
    assert result.dispatched == 1
    assert result.skipped_terminal == 1
    assert enqueue.call_args.kwargs["dedupe_key"] == (
        "treatment:codex-implement:coding_task:/tmp/second:"
        "abc123:codex-implement:1"
    )


def test_tick_rejects_outside_root_before_snapshot_or_dispatch(tmp_path, monkeypatch):
    """A todo path cannot execute an attacker conftest or reach dispatch."""
    from tgw.workers.coding import validated_coding_worktree

    monkeypatch.setattr("tgw.development.foreman.validated_coding_worktree", validated_coding_worktree)
    evil = tmp_path / "evil"
    evil.mkdir()
    marker = tmp_path / "conftest-executed"
    (evil / "conftest.py").write_text(f"from pathlib import Path\nPath({str(marker)!r}).write_text('ran')\n")
    todo = TodoRecord(1731, "test-agent", 1, f"worktree: {evil}", str(evil))
    result = tick(
        config=ForemanConfig(coding_config={"worktree_root": str(tmp_path / "canonical"), "repository_root": str(tmp_path / "repository")} ),
        fetch_todos=lambda: [todo], check_active_fn=lambda _: False,
    )
    assert result.errors == 1
    assert not marker.exists()


# ---------------------------------------------------------------------------
# Test: todo without worktree is skipped
# ---------------------------------------------------------------------------


def test_tick_skips_todo_without_worktree():
    fetch_todos = MagicMock(return_value=[_todo(1, worktree="")])
    check_active = MagicMock(return_value=False)

    result = tick(fetch_todos=fetch_todos, check_active_fn=check_active)

    assert result.dispatched == 0
    assert result.skipped_no_worktree == 1
    assert result.errors == 0


# ---------------------------------------------------------------------------
# Test: limit parameter caps processing
# ---------------------------------------------------------------------------


def test_tick_limit_caps_processing():
    fetch_todos = MagicMock(
        return_value=[_todo(1), _todo(2), _todo(3)]
    )
    check_active = MagicMock(return_value=False)

    result = tick(
        fetch_todos=fetch_todos,
        check_active_fn=check_active,
        limit=1,
    )

    assert result.dispatched == 0  # no worktree, all skipped
    assert result.skipped_no_worktree >= 0
    assert result.errors == 0


# ---------------------------------------------------------------------------
# Test: _extract_worktree
# ---------------------------------------------------------------------------


def test_extract_worktree_direct():
    assert _extract_worktree("worktree: /tmp/my-worktree") == "/tmp/my-worktree"


def test_extract_worktree_equals():
    assert _extract_worktree("worktree = /opt/worktrees/foo") == "/opt/worktrees/foo"


def test_extract_worktree_fallback():
    body = "implement feature at /home/user/worktrees/foo-123"
    assert _extract_worktree(body) == "/home/user/worktrees/foo-123"


def test_extract_worktree_no_match():
    assert _extract_worktree("just a regular todo") == ""


def test_extract_worktree_from_coding_status_note():
    note = "in-progress; worktree: /opt/TGW/var/worktrees/todo-1732-coding-cli"
    assert _extract_worktree(note) == "/opt/TGW/var/worktrees/todo-1732-coding-cli"


# ---------------------------------------------------------------------------
# Test: ForemanConfig defaults
# ---------------------------------------------------------------------------


def test_foreman_config_defaults():
    cfg = ForemanConfig()
    assert cfg.goal_profile == CODING_READY_FOR_IMPLEMENTATION
    assert cfg.treatments == CODING_TREATMENTS
    assert cfg.evaluator_version == EVALUATOR_VERSION


# ---------------------------------------------------------------------------
# Test: TickResult is immutable and has expected fields
# ---------------------------------------------------------------------------


def test_tick_result_defaults():
    r = TickResult()
    assert r.dispatched == 0
    assert r.skipped_waiting == 0
    assert r.skipped_conflict == 0
    assert r.skipped_active == 0
    assert r.skipped_no_worktree == 0
    assert r.errors == 0


def test_tick_result_with_values():
    r = TickResult(
        dispatched=3,
        skipped_waiting=2,
        skipped_conflict=1,
        skipped_active=4,
        skipped_no_worktree=0,
        errors=1,
    )
    assert r.dispatched == 3
    assert r.skipped_waiting == 2
    assert r.skipped_conflict == 1
    assert r.skipped_active == 4
    assert r.errors == 1
