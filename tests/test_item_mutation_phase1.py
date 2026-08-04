"""Phase 1 item-mutation transaction contract (PP-ITEM-MUTATION-001)."""

import json
import multiprocessing
import os
import sqlite3
import threading
import zipfile
from pathlib import Path

import pytest

from tgw import item_mutation


def cfg(tmp_path):
    return {
        "itemdata_root": tmp_path / "ItemData",
        "archive_root": tmp_path / "ItemArchive",
        "location_tree_root": tmp_path / "by-location",
        "sqlite_catalog_path": tmp_path / "catalog.sqlite",
        "item_mutation_root": tmp_path / "mutation-journal",
        "pretty": False,
    }


def write_item(c, sku="tgw1", **fields):
    p = c["itemdata_root"] / sku / f"{sku}.json"
    p.parent.mkdir(parents=True)
    p.write_text(json.dumps({"sku": sku, **fields}), encoding="utf-8")
    return p


def test_stale_cas_has_no_effect_and_replay_mismatch(tmp_path):
    c = cfg(tmp_path)
    p = write_item(c, title="old")
    generation = item_mutation.generation_for_path(p)
    first = item_mutation.mutate_item(c, "op-1", "tgw1", "set", generation,
                                      {"fields": {"title": "new"}})
    assert first["status"] == "COMMITTED"
    replay = item_mutation.mutate_item(c, "op-1", "tgw1", "set", generation,
                                       {"fields": {"title": "new"}})
    assert replay == first
    before = p.read_bytes()
    conflict = item_mutation.mutate_item(c, "op-stale", "tgw1", "set", generation,
                                         {"fields": {"qty": 2}})
    assert conflict["status"] == "CONFLICT"
    assert p.read_bytes() == before
    mismatch = item_mutation.mutate_item(c, "op-1", "tgw1", "set", generation,
                                         {"fields": {"title": "other"}})
    assert mismatch["status"] == "CONFLICT"


@pytest.mark.parametrize(("old", "new"), [(None, True), (True, 1), (1, 1.0)])
def test_presence_and_concrete_type_are_exact(tmp_path, old, new):
    c = cfg(tmp_path)
    p = write_item(c, value=old)
    g = item_mutation.generation_for_path(p)
    r = item_mutation.mutate_item(c, f"op-{type(old).__name__}-{type(new).__name__}",
                                  "tgw1", "set", g, {"fields": {"value": new}})
    change = r["changes"]["value"]
    assert change["before"] == {"present": True, "value": old}
    assert change["after"] == {"present": True, "value": new}
    assert type(change["after"]["value"]) is type(new)


def test_missing_differs_from_null(tmp_path):
    c = cfg(tmp_path)
    p = write_item(c)
    r = item_mutation.mutate_item(c, "op-null", "tgw1", "set",
                                  item_mutation.generation_for_path(p),
                                  {"fields": {"value": None}})
    assert r["changes"]["value"]["before"] == {"present": False}
    assert r["changes"]["value"]["after"] == {"present": True, "value": None}


def test_disjoint_stale_updates_do_not_merge(tmp_path):
    c = cfg(tmp_path)
    p = write_item(c, left=0, right=0)
    stale = item_mutation.generation_for_path(p)
    assert item_mutation.mutate_item(c, "left", "tgw1", "set", stale,
                                     {"fields": {"left": 1}})["status"] == "COMMITTED"
    second = item_mutation.mutate_item(c, "right", "tgw1", "set", stale,
                                       {"fields": {"right": 1}})
    assert second["status"] == "CONFLICT"
    assert json.loads(p.read_text()) == {"sku": "tgw1", "left": 1, "right": 0}


def test_append_identity_noop_and_aborted_receipts_are_truthful(tmp_path):
    c = cfg(tmp_path)
    p = write_item(c, history=[True])
    g = item_mutation.generation_for_path(p)
    appended = item_mutation.mutate_item(c, "append-int", "tgw1", "append", g,
                                         {"field": "history", "event": 1})
    assert appended["status"] == "COMMITTED"
    assert json.loads(p.read_text())["history"] == [True, 1]
    g = appended["committed_generation"]
    noop = item_mutation.mutate_item(c, "append-noop", "tgw1", "append", g,
                                     {"field": "history", "event": 1})
    assert noop["status"] == "COMMITTED"
    assert noop["before_generation"] == noop["committed_generation"]
    assert json.loads(p.read_text())["history"] == [True, 1]
    before = p.read_bytes()
    aborted = item_mutation.mutate_item(c, "bad", "tgw1", "set",
                                        noop["committed_generation"], {"fields": {1: "bad"}})
    assert aborted["status"] == "ABORTED"
    assert p.read_bytes() == before


def _create_worker(c, start, out, op):
    start.wait()
    out.put(item_mutation.mutate_item(c, op, "tgw1", "create",
                                      item_mutation.ABSENT_GENERATION,
                                      {"data": {"title": op}})["status"])


def _crash_worker(c, boundary, generation):
    os.environ["TGW_ITEM_MUTATION_CRASH_AFTER"] = boundary
    item_mutation.mutate_item(c, f"crash-{boundary}", "tgw1", "set", generation,
                              {"fields": {"title": "after", "location": "B"}})


def _reconcile_worker(c, out):
    out.put(item_mutation.reconcile_pending(c))


def _operation_worker(c, start, out, sku, op, prior_barrier=None):
    if prior_barrier is not None:
        original = item_mutation._prior
        def synchronized_prior(config, operation_id):
            result = original(config, operation_id)
            try:
                prior_barrier.wait(timeout=0.5)
            except threading.BrokenBarrierError:
                pass
            return result
        item_mutation._prior = synchronized_prior
    start.wait()
    result = item_mutation.mutate_item(c, op, sku, "create",
                                       item_mutation.ABSENT_GENERATION,
                                       {"data": {"title": sku}})
    out.put((sku, result["status"]))


def test_finding_1_post_publication_failure_never_aborted(tmp_path, monkeypatch):
    c = cfg(tmp_path)
    p = write_item(c, title="old")
    original = item_mutation._append
    fired = False
    def fail_after_publish(config, event):
        nonlocal fired
        if event.get("boundary") == "canonical" and not fired:
            fired = True
            raise OSError("journal fault after publication")
        return original(config, event)
    monkeypatch.setattr(item_mutation, "_append", fail_after_publish)
    result = item_mutation.mutate_item(c, "post-publish", "tgw1", "set",
                                       item_mutation.generation_for_path(p),
                                       {"fields": {"title": "new"}})
    assert json.loads(p.read_text())["title"] == "new"
    assert result["status"] in {"REPAIR_REQUIRED", "COMMITTED"}


def test_finding_2_exact_retry_resumes_unfinished_intent(tmp_path):
    c = cfg(tmp_path)
    p = write_item(c, title="old", location="A")
    g = item_mutation.generation_for_path(p)
    child = multiprocessing.Process(target=_crash_worker, args=(c, "canonical", g))
    child.start()
    child.join(10)
    assert child.exitcode == 86
    mismatch = item_mutation.mutate_item(c, "crash-canonical", "other-sku", "set", g,
                                         {"fields": {"title": "poison"}})
    assert mismatch["status"] == "CONFLICT"
    retry = item_mutation.mutate_item(c, "crash-canonical", "tgw1", "set", g,
                                      {"fields": {"title": "after", "location": "B"}})
    assert retry["status"] == "COMMITTED"
    assert (c["location_tree_root"] / "B" / "tgw1").is_symlink()


def test_finding_3_operation_id_global_collision_and_positive_concurrency(tmp_path):
    c = cfg(tmp_path)
    start = multiprocessing.Event()
    out = multiprocessing.Queue()
    prior_barrier = multiprocessing.Barrier(2)
    ps = [multiprocessing.Process(target=_operation_worker,
            args=(c, start, out, sku, "same-op", prior_barrier)) for sku in ("skuA", "skuB")]
    for process in ps:
        process.start()
    start.set()
    for process in ps:
        process.join(10)
    assert sorted(status for _, status in (out.get() for _ in ps)) == ["COMMITTED", "CONFLICT"]
    c2 = cfg(tmp_path / "positive")
    start = multiprocessing.Event()
    out = multiprocessing.Queue()
    ps = [multiprocessing.Process(target=_operation_worker,
            args=(c2, start, out, sku, "op-" + sku)) for sku in ("skuA", "skuB")]
    for process in ps:
        process.start()
    start.set()
    for process in ps:
        process.join(10)
    assert sorted(out.get()[1] for _ in ps) == ["COMMITTED", "COMMITTED"]


def test_finding_4_receipt_symlink_and_nonregular_rejected(tmp_path):
    c = cfg(tmp_path)
    root = c["item_mutation_root"]
    root.mkdir(parents=True)
    victim = tmp_path / "victim"
    victim.write_text("safe")
    os.symlink(victim, root / "receipts.jsonl")
    with pytest.raises(OSError):
        item_mutation.mutate_item(c, "symlink", "x", "create", "absent", {"data": {}})
    assert victim.read_text() == "safe"


def test_finding_5_short_write_completed_and_interrupted_tail_recovered(tmp_path, monkeypatch):
    c = cfg(tmp_path)
    real_write = os.write
    monkeypatch.setattr(os, "write", lambda fd, data: real_write(fd, data[:max(1, len(data)//3)]))
    result = item_mutation.mutate_item(c, "short", "x", "create", "absent", {"data": {}})
    assert result["status"] == "COMMITTED"
    monkeypatch.undo()
    journal = c["item_mutation_root"] / "receipts.jsonl"
    with journal.open("ab") as stream:
        stream.write(b'{"interrupted":')
    assert item_mutation.mutate_item(c, "tail", "y", "create", "absent", {"data": {}})["status"] == "COMMITTED"


def test_finding_6_archive_gap_reconciliation_does_not_duplicate(tmp_path):
    c = cfg(tmp_path)
    p = write_item(c, title="before")
    def gap_worker():
        original = item_mutation._archive_once
        def archive_then_die(*args):
            original(*args)
            os._exit(87)
        item_mutation._archive_once = archive_then_die
        item_mutation.mutate_item(c, "archive-gap", "tgw1", "set",
                item_mutation.generation_for_path(p), {"fields": {"title": "after"}})
    child = multiprocessing.Process(target=gap_worker)
    child.start()
    child.join(10)
    assert child.exitcode == 87
    archive = c["archive_root"] / "tgw1.zip"
    with zipfile.ZipFile(archive) as zf:
        before = len(zf.namelist())
    item_mutation.reconcile_pending(c)
    with zipfile.ZipFile(archive) as zf:
        after = len(zf.namelist())
    assert (before, after) == (1, 1)


def test_finding_7_projection_helpers_are_verified_from_persisted_state(tmp_path, monkeypatch):
    c = cfg(tmp_path)
    p = write_item(c, title="old", location="A")
    monkeypatch.setattr(item_mutation, "_project_sqlite", lambda *_: None)
    monkeypatch.setattr(item_mutation, "_project_location", lambda *_: None)
    result = item_mutation.mutate_item(c, "lying-projections", "tgw1", "set",
            item_mutation.generation_for_path(p), {"fields": {"title": "new", "location": "B"}})
    assert result["status"] == "REPAIR_REQUIRED"
    assert not result["projections"]["sqlite"]["ok"]
    assert not result["projections"]["location"]["ok"]


def test_finding_7_sqlite_readback_requires_exact_json_types(tmp_path, monkeypatch):
    original = item_mutation._project_sqlite
    for index, (committed, contradictory) in enumerate(((True, 1), (1, 1.0), (1.0, True))):
        c = cfg(tmp_path / str(index))
        p = write_item(c, value=False)

        def persist_contradictory_type(config, doc, value=contradictory):
            original(config, doc)
            con = sqlite3.connect(config["sqlite_catalog_path"])
            try:
                persisted = json.loads(con.execute(
                    "select data from catalog where sku='tgw1'"
                ).fetchone()[0])
                persisted["value"] = value
                con.execute("update catalog set data=? where sku='tgw1'",
                            (json.dumps(persisted),))
                con.commit()
            finally:
                con.close()

        monkeypatch.setattr(item_mutation, "_project_sqlite", persist_contradictory_type)
        result = item_mutation.mutate_item(
            c, f"sqlite-type-{index}", "tgw1", "set",
            item_mutation.generation_for_path(p), {"fields": {"value": committed}},
        )
        assert result["status"] == "REPAIR_REQUIRED"
        assert result["projections"]["sqlite"]["ok"] is False


def test_finding_7_absent_location_rejects_stale_old_link(tmp_path, monkeypatch):
    c = cfg(tmp_path)
    p = write_item(c, location="A")
    old_dir = c["location_tree_root"] / "A"
    old_dir.mkdir(parents=True)
    os.symlink(p.parent, old_dir / "tgw1")
    monkeypatch.setattr(item_mutation, "_project_location", lambda *_: None)

    result = item_mutation.mutate_item(
        c, "absent-location-stale-old-link", "tgw1", "delete",
        item_mutation.generation_for_path(p), {"fields": ["location"]},
    )

    assert result["status"] == "REPAIR_REQUIRED"
    assert result["projections"]["location"]["ok"] is False
    assert (old_dir / "tgw1").is_symlink()


def test_finding_8_operation_kind_presence_preconditions(tmp_path):
    c = cfg(tmp_path)
    p = write_item(c, title="original")
    create = item_mutation.mutate_item(c, "create-existing", "tgw1", "create",
            item_mutation.generation_for_path(p), {"data": {"title": "replacement"}})
    set_absent = item_mutation.mutate_item(c, "set-absent", "missing", "set",
            item_mutation.ABSENT_GENERATION, {"fields": {"title": "new"}})
    assert create["status"] in {"ABORTED", "CONFLICT"}
    assert set_absent["status"] in {"ABORTED", "CONFLICT"}
    assert json.loads(p.read_text())["title"] == "original"


def test_concurrent_create_has_one_winner_across_processes(tmp_path):
    c = cfg(tmp_path)
    start = multiprocessing.Event()
    out = multiprocessing.Queue()
    ps = [multiprocessing.Process(target=_create_worker, args=(c, start, out, f"op-{i}"))
          for i in range(2)]
    for p in ps:
        p.start()
    start.set()
    for p in ps:
        p.join(10)
    assert sorted(out.get() for _ in ps) == ["COMMITTED", "CONFLICT"]


def test_projection_failure_is_repair_required_then_reconciles_twice(tmp_path, monkeypatch):
    c = cfg(tmp_path)
    p = write_item(c, title="old", location="A")
    g = item_mutation.generation_for_path(p)
    monkeypatch.setattr(item_mutation, "_project_sqlite", lambda *_: (_ for _ in ()).throw(OSError("full")))
    r = item_mutation.mutate_item(c, "op-repair", "tgw1", "set", g,
                                  {"fields": {"title": "new", "location": "B"}})
    assert r["status"] == "REPAIR_REQUIRED"
    assert json.loads(p.read_text())["title"] == "new"
    monkeypatch.undo()
    one = item_mutation.reconcile_pending(c)
    two = item_mutation.reconcile_pending(c)
    assert one[0]["status"] == "COMMITTED"
    assert two == []
    con = sqlite3.connect(c["sqlite_catalog_path"])
    assert json.loads(con.execute("select data from catalog where sku='tgw1'").fetchone()[0])["title"] == "new"
    assert (c["location_tree_root"] / "B" / "tgw1").is_symlink()


@pytest.mark.parametrize("boundary", ["intent", "archive", "canonical", "sqlite",
                                       "location_remove", "location_add"])
def test_fresh_process_reconciles_every_crash_prefix_twice(tmp_path, boundary):
    c = cfg(tmp_path / boundary)
    p = write_item(c, title="before", location="A")
    old_dir = c["location_tree_root"] / "A"
    old_dir.mkdir(parents=True)
    os.symlink(p.parent, old_dir / "tgw1")
    generation = item_mutation.generation_for_path(p)
    child = multiprocessing.Process(target=_crash_worker,
                                    args=(c, boundary, generation))
    child.start()
    child.join(10)
    assert child.exitcode == 86
    out = multiprocessing.Queue()
    reconciler = multiprocessing.Process(target=_reconcile_worker, args=(c, out))
    reconciler.start()
    reconciler.join(10)
    assert reconciler.exitcode == 0
    first = out.get()
    again = multiprocessing.Process(target=_reconcile_worker, args=(c, out))
    again.start()
    again.join(10)
    assert again.exitcode == 0
    second = out.get()
    assert first == [{**first[0], "status": "COMMITTED"}]
    assert second == []
    assert json.loads(p.read_text())["title"] == "after"
    assert (c["location_tree_root"] / "B" / "tgw1").is_symlink()


def test_phase1_wrappers_have_no_raw_canonical_write_bypass():
    text = (Path(__file__).parents[1] / "src/tgw/items.py").read_text()
    for name in ("create_item", "_write_field", "strip_fields", "set_fields",
                 "update_items", "locationupdate", "verifiedupdate"):
        body = text.split(f"def {name}(", 1)[1].split("\ndef ", 1)[0]
        assert "atomic_write_json(" not in body
