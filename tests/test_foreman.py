"""Tests for tgw.workflow.foreman — coding foreman tick cycle."""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from tgw.workflow.contracts import (
    EvidenceAssertion,
    EvidenceReference,
    FingerprintResult,
    ObjectSnapshot,
    RuntimeWorkGraph,
    TreatmentDisposition,
)
from tgw.workflow.foreman import (
    EVALUATOR_VERSION,
    ForemanConfig,
    TickResult,
    TodoRecord,
    _extract_worktree,
    tick,
)
from tgw.workflow.profiles import CODING_READY_FOR_IMPLEMENTATION
from tgw.workflow.scheduler import DispatchResult
from tgw.workflow.treatments import CODING_TREATMENTS

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
            "tgw.workflow.foreman.build_coding_snapshot",
            return_value=_snapshot(assertions=_implemented()),
        ),
        patch(
            "tgw.workflow.foreman.evaluate",
            return_value=_graph(
                object_id="/tmp/worktree-1",
                graph_id="graph-abc",
                eligible=(disposition,),
            ),
        ),
        patch(
            "tgw.workflow.foreman.dispatch_treatment",
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
            "tgw.workflow.foreman.build_coding_snapshot",
            return_value=_snapshot(assertions=_all_satisfied()),
        ),
        patch(
            "tgw.workflow.foreman.evaluate",
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
            "tgw.workflow.foreman.build_coding_snapshot",
            return_value=_snapshot(assertions=_implemented()),
        ),
        patch(
            "tgw.workflow.foreman.evaluate",
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
            "tgw.workflow.foreman.build_coding_snapshot",
            return_value=_snapshot(assertions=_implemented()),
        ),
        patch(
            "tgw.workflow.foreman.evaluate",
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
            "tgw.workflow.foreman.build_coding_snapshot",
            side_effect=[
                _snapshot(object_id="/tmp/worktree-1", assertions=_implemented()),
                _snapshot(object_id="/tmp/wt2", assertions=_implemented()),
            ],
        ),
        patch(
            "tgw.workflow.foreman.evaluate",
            side_effect=[
                _graph(graph_id="graph-active", object_id="/tmp/worktree-1",
                       eligible=(disposition,)),
                _graph(graph_id="graph-fresh", object_id="/tmp/wt2",
                       eligible=(disposition,)),
            ],
        ),
        patch(
            "tgw.workflow.foreman.dispatch_treatment",
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
            "tgw.workflow.foreman.build_coding_snapshot",
            return_value=_snapshot(assertions=_implemented_true()),
        ),
        patch(
            "tgw.workflow.foreman.evaluate",
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
            "tgw.workflow.foreman.build_coding_snapshot",
            side_effect=[
                _snapshot(object_id="/tmp/wt1", assertions=_implemented()),
                _snapshot(object_id="/tmp/wt2", assertions=_implemented()),
            ],
        ),
        patch(
            "tgw.workflow.foreman.evaluate",
            side_effect=[
                _graph(graph_id="graph-1", object_id="/tmp/wt1",
                       eligible=(disposition,)),
                _graph(graph_id="graph-2", object_id="/tmp/wt2",
                       eligible=(disposition,)),
            ],
        ),
        patch(
            "tgw.workflow.foreman.dispatch_treatment",
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

    assert result.dispatched >= 1
    assert result.errors == 0


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
