"""Real-PostgreSQL proofs for the continual-harness durable ledger.

Todo 1916 leaf 11.1 (L11.1 core). These are the acceptance fixtures from
LEAF-11-1-CONTINUAL-HARNESS-CORE-v1.yaml:

  1. terminate a worker mid-job and resume exactly from the ledger with
     identical cursor and status
  2. two concurrent jobs cannot both own one cursor
  3. sessions / worker containers / task-local microservices remain disposable;
     terminating them never loses durable harness state

Tests use unique UUID task ids and delete only their own rows — they never
truncate shared state. They skip when no PostgreSQL with the shared schema is
reachable.
"""

from __future__ import annotations

import os
import subprocess
import threading
import uuid

import psycopg2
import pytest

from tgw.development import harness_git, harness_ledger

_CANDIDATE_DSNS = (
    os.environ.get("TGW_TEST_STATE_MACHINE_DSN"),
    "dbname=tgw_lib_dev_state_machine user=tgw_coding",
    "dbname=state_machine_test user=tgw",
)


def _reachable_dsn() -> str | None:
    for dsn in _CANDIDATE_DSNS:
        if not dsn:
            continue
        try:
            with psycopg2.connect(dsn) as connection:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT 1")
            return dsn
        except Exception:
            continue
    return None


DSN = _reachable_dsn()
pytestmark = pytest.mark.skipif(DSN is None, reason="no PostgreSQL reachable for the harness ledger")


@pytest.fixture
def task_ids():
    ids: list[str] = []
    harness_ledger.init(DSN)
    yield ids
    if ids:
        with psycopg2.connect(DSN) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "DELETE FROM harness_ledger_task WHERE task_id = ANY(%s)", (ids,)
                )


def _new_task(ids: list[str], **seed) -> str:
    task_id = f"test-ledger-{uuid.uuid4()}"
    ids.append(task_id)
    harness_ledger.ensure_task(task_id, **seed)
    return task_id


# --------------------------------------------------------------------------- #
# acceptance 1 — resume exactly from the ledger after a mid-job "kill"
# --------------------------------------------------------------------------- #

def test_cursor_and_status_survive_a_worker_kill_and_resume_identically(task_ids):
    task_id = _new_task(task_ids)

    # a session acquires the cursor and records progress, then vanishes without
    # releasing anything (the "kill").
    acquired = harness_ledger.acquire_cursor(task_id, "session-A", lease_seconds=60)
    assert acquired is not None
    harness_ledger.write_cursor(
        task_id, "session-A", acquired["lease_id"],
        cursor={"stage": "implementation", "step": 3, "resume_of": "sha256:abc"},
        status="open",
        context={"branch": "coding/x", "rounds": 1},
    )
    # session-A process is gone. Nothing calls release_cursor.

    # a fresh session reads the ledger with no knowledge of session-A.
    resumed = harness_ledger.read_task(task_id)
    assert resumed["cursor"] == {"stage": "implementation", "step": 3, "resume_of": "sha256:abc"}
    assert resumed["status"] == "open"
    assert resumed["context"] == {"branch": "coding/x", "rounds": 1}
    assert resumed["generation"] == 1

    # and once session-A's lease expires the fresh session can take the cursor
    # and continue from exactly that point.
    with psycopg2.connect(DSN) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE harness_ledger_task SET lease_expires_at = now() - interval '1 second' "
                "WHERE task_id = %s",
                (task_id,),
            )
    taken = harness_ledger.acquire_cursor(task_id, "session-B", lease_seconds=60)
    assert taken is not None
    assert taken["cursor"]["step"] == 3
    assert taken["owner"] == "session-B"


# --------------------------------------------------------------------------- #
# acceptance 2 — one cursor, one owner
# --------------------------------------------------------------------------- #

def test_two_owners_cannot_both_hold_one_cursor(task_ids):
    task_id = _new_task(task_ids)

    first = harness_ledger.acquire_cursor(task_id, "job-1", lease_seconds=60)
    assert first is not None

    # a second job cannot take the cursor while job-1's lease is live.
    assert harness_ledger.acquire_cursor(task_id, "job-2", lease_seconds=60) is None

    # job-1's writes keep working; job-2 still cannot write (no lease).
    harness_ledger.write_cursor(task_id, "job-1", first["lease_id"], status="blocked")
    with pytest.raises(harness_ledger.LedgerLeaseError):
        harness_ledger.write_cursor(task_id, "job-2", first["lease_id"], status="open")

    # after job-1 releases, job-2 gets it — and job-1's stale lease is now dead.
    harness_ledger.release_cursor(task_id, "job-1", first["lease_id"])
    second = harness_ledger.acquire_cursor(task_id, "job-2", lease_seconds=60)
    assert second is not None
    with pytest.raises(harness_ledger.LedgerLeaseError):
        harness_ledger.write_cursor(task_id, "job-1", first["lease_id"], status="open")


def test_concurrent_acquire_has_exactly_one_winner(task_ids):
    task_id = _new_task(task_ids)
    winners: list[str] = []
    barrier = threading.Barrier(8)

    def contend(n: int) -> None:
        harness_ledger.init(DSN)
        barrier.wait()
        row = harness_ledger.acquire_cursor(task_id, f"contender-{n}", lease_seconds=60)
        if row is not None:
            winners.append(f"contender-{n}")

    threads = [threading.Thread(target=contend, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(winners) == 1


# --------------------------------------------------------------------------- #
# acceptance 3 — disposable sessions lose no durable history
# --------------------------------------------------------------------------- #

def test_append_only_history_is_gap_free_and_survives(task_ids):
    task_id = _new_task(task_ids)

    seqs = [
        harness_ledger.append(task_id, "attempt", {"round": 1}, actor="codex"),
        harness_ledger.append(task_id, "review_finding", {"message": "still failing"}, actor="claude"),
        harness_ledger.append(task_id, "remediation", {"round": 2}, actor="codex"),
        harness_ledger.append(task_id, "operator_correction", {"note": "use tgw-plan"}, actor="dave"),
        harness_ledger.append(task_id, "next_action", {"do": "port 13507c886"}),
    ]
    assert seqs == [1, 2, 3, 4, 5]

    # a completely fresh reader reconstructs the whole story from the ledger.
    full = harness_ledger.history(task_id)
    assert [e["kind"] for e in full] == [
        "attempt", "review_finding", "remediation", "operator_correction", "next_action",
    ]
    assert full[3]["body"] == {"note": "use tgw-plan"}
    assert full[3]["actor"] == "dave"

    assert [e["seq"] for e in harness_ledger.history(task_id, after_seq=3)] == [4, 5]
    assert [e["kind"] for e in harness_ledger.history(task_id, kinds=["review_finding"])] == [
        "review_finding"
    ]


def test_concurrent_appends_stay_gap_free(task_ids):
    task_id = _new_task(task_ids)
    barrier = threading.Barrier(10)

    def record(n: int) -> None:
        harness_ledger.init(DSN)
        barrier.wait()
        harness_ledger.append(task_id, "note", {"n": n})

    threads = [threading.Thread(target=record, args=(n,)) for n in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    seqs = sorted(e["seq"] for e in harness_ledger.history(task_id))
    assert seqs == list(range(1, 11))


# --------------------------------------------------------------------------- #
# contract edges
# --------------------------------------------------------------------------- #

def test_ensure_task_is_idempotent_and_non_destructive(task_ids):
    task_id = _new_task(task_ids)
    got = harness_ledger.acquire_cursor(task_id, "s", lease_seconds=60)
    harness_ledger.write_cursor(task_id, "s", got["lease_id"], cursor={"k": "v"}, status="blocked")

    again = harness_ledger.ensure_task(task_id, cursor={"seed": "ignored"}, context={"seed": "ignored"})
    assert again["cursor"] == {"k": "v"}
    assert again["status"] == "blocked"
    assert again["owner"] == "s"


def test_acquire_unknown_task_raises(task_ids):
    with pytest.raises(harness_ledger.UnknownTaskError):
        harness_ledger.acquire_cursor(f"test-ledger-{uuid.uuid4()}", "s")


def test_stolen_lease_holder_cannot_clobber_new_owner(task_ids):
    task_id = _new_task(task_ids)
    stale = harness_ledger.acquire_cursor(task_id, "old", lease_seconds=60)
    harness_ledger.write_cursor(task_id, "old", stale["lease_id"], cursor={"progress": "half"})

    # "old" is presumed crashed; force-expire and let "new" take over.
    with psycopg2.connect(DSN) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE harness_ledger_task SET lease_expires_at = now() - interval '1 second' "
                "WHERE task_id = %s",
                (task_id,),
            )
    new = harness_ledger.acquire_cursor(task_id, "new", lease_seconds=60)
    harness_ledger.write_cursor(task_id, "new", new["lease_id"], cursor={"progress": "full"})

    with pytest.raises(harness_ledger.LedgerLeaseError):
        harness_ledger.write_cursor(task_id, "old", stale["lease_id"], cursor={"progress": "half"})
    assert harness_ledger.read_task(task_id)["cursor"] == {"progress": "full"}


def test_lapsed_window_without_a_steal_stays_the_owners_to_renew_and_write(task_ids):
    # a single implement->test->review round can outlast one lease window; as
    # long as no other owner stole the cursor it is still ours.
    task_id = _new_task(task_ids)
    acquired = harness_ledger.acquire_cursor(task_id, "harness", lease_seconds=60)
    lease = acquired["lease_id"]
    with psycopg2.connect(DSN) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE harness_ledger_task SET lease_expires_at = now() - interval '5 minutes' "
                "WHERE task_id = %s",
                (task_id,),
            )
    # nobody stole it -> renew and write both still succeed
    renewed = harness_ledger.renew_cursor(task_id, "harness", lease, lease_seconds=60)
    assert renewed["task_id"] == task_id
    harness_ledger.write_cursor(task_id, "harness", lease, cursor={"round": 3})
    assert harness_ledger.read_task(task_id)["cursor"] == {"round": 3}

    # but once another owner acquires the lapsed lease, the old owner is out
    with psycopg2.connect(DSN) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE harness_ledger_task SET lease_expires_at = now() - interval '1 second' "
                "WHERE task_id = %s",
                (task_id,),
            )
    harness_ledger.acquire_cursor(task_id, "other", lease_seconds=60)
    with pytest.raises(harness_ledger.LedgerLeaseError):
        harness_ledger.renew_cursor(task_id, "harness", lease, lease_seconds=60)
    with pytest.raises(harness_ledger.LedgerLeaseError):
        harness_ledger.write_cursor(task_id, "harness", lease, cursor={"round": 99})


def test_rejects_bad_kind_and_status(task_ids):
    task_id = _new_task(task_ids)
    row = harness_ledger.acquire_cursor(task_id, "s", lease_seconds=60)
    with pytest.raises(ValueError):
        harness_ledger.append(task_id, "not-a-kind", {})
    with pytest.raises(ValueError):
        harness_ledger.write_cursor(task_id, "s", row["lease_id"], status="weird")


# --------------------------------------------------------------------------- #
# L11.1.LEDGER-AND-GIT-DISCIPLINE — the ledger holds the process, git holds
# only the one accepted result; the full story is reconstructable from the
# ledger with no git archaeology.
# --------------------------------------------------------------------------- #

def _sh(cwd, *args):
    return subprocess.run(
        ["git", "-c", "user.name=T", "-c", "user.email=t@e", *args],
        cwd=cwd, check=True, text=True, capture_output=True,
        env={"GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null",
             "PATH": "/usr/bin:/bin", "HOME": str(cwd)},
    ).stdout.strip()


def test_three_rounds_in_the_ledger_land_as_one_commit(task_ids, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _sh(repo, "init", "-q", "-b", "main")
    (repo / "seed").write_text("0\n")
    _sh(repo, "add", "-A")
    _sh(repo, "commit", "-q", "-m", "initial")
    base = _sh(repo, "rev-parse", "main")

    task_id = _new_task(task_ids)
    got = harness_ledger.acquire_cursor(task_id, "harness", lease_seconds=120)
    lease = got["lease_id"]
    wt = tmp_path / "wt"
    _sh(repo, "worktree", "add", "-q", "-b", f"coding/{task_id}", str(wt))

    # the harness runs three rounds. Each round's outcome goes to the LEDGER;
    # only the working state accumulates in the ephemeral worktree.
    for rnd in range(1, 4):
        (wt / "impl.py").write_text(f"attempt = {rnd}\n")
        _sh(wt, "add", "-A")
        _sh(wt, "commit", "-q", "-m", f"round {rnd} wip")
        harness_ledger.append(task_id, "attempt", {"round": rnd}, actor="codex")
        harness_ledger.append(
            task_id, "review_finding", {"round": rnd, "message": f"issue {rnd}"}, actor="claude"
        )
        harness_ledger.write_cursor(task_id, "harness", lease, cursor={"round": rnd})

    harness_ledger.append(task_id, "operator_correction", {"note": "final shape agreed"}, actor="dave")

    landed = harness_git.land_accepted_task(
        task_id, repository=repo, worktree=wt, message=f"{task_id}: implement",
    )
    harness_ledger.append(task_id, "next_action", {"landed_commit": landed["commit"]})
    harness_ledger.write_cursor(task_id, "harness", lease, status="done")
    harness_ledger.release_cursor(task_id, "harness", lease)

    # git: exactly one commit for the task, a real ancestor, branch gone
    assert _sh(repo, "rev-parse", "main^") == base
    assert _sh(repo, "log", "--oneline", "--format=%s").splitlines() == [
        f"{task_id}: implement", "initial",
    ]
    assert _sh(repo, "branch", "--list", "coding/*") == ""

    # ledger: the whole story survives, in order, no git needed
    story = harness_ledger.history(task_id)
    assert [e["kind"] for e in story] == [
        "attempt", "review_finding", "attempt", "review_finding",
        "attempt", "review_finding", "operator_correction", "next_action",
    ]
    assert story[-1]["body"]["landed_commit"] == landed["commit"]
    assert harness_ledger.read_task(task_id)["status"] == "done"
