from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from tgw.item_mutation import item_generation, mutate_item, operation_identity


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _run(tmp_path: Path, item: Path, expected: str, payload, mutate, project=lambda _sku, _doc: None, operation_id=None):
    return mutate_item(
        item_path=item,
        archive_root=tmp_path / "archive",
        journal_root=tmp_path / "journal",
        sku=item.parent.name,
        kind="normalize-condition",
        expected_generation=expected,
        payload=payload,
        mutate=mutate,
        project=project,
        operation_id=operation_id,
    )


def test_operation_identity_exact_json_binding():
    base = dict(sku="A", kind="patch", expected_generation="g")
    assert operation_identity(**base, payload={}) != operation_identity(**base, payload={"x": None})
    assert operation_identity(**base, payload={"x": True}) != operation_identity(**base, payload={"x": 1})
    assert operation_identity(**base, payload={"x": 1}) != operation_identity(**base, payload={"x": 1.0})
    assert operation_identity(**base, payload={"b": 2, "a": 1}) == operation_identity(**base, payload={"a": 1, "b": 2})


def test_commit_archives_projects_and_exact_replay_is_inert(tmp_path):
    item = tmp_path / "items" / "A" / "A.json"
    _write(item, {"sku": "A", "condition": "old"})
    expected = item_generation({"sku": "A", "condition": "old"})
    projections = []
    receipt = _run(
        tmp_path,
        item,
        expected,
        {"condition": "new"},
        lambda doc: {**doc, "condition": "new"},
        lambda sku, doc: projections.append((sku, doc)),
    )
    replay = _run(
        tmp_path,
        item,
        expected,
        {"condition": "new"},
        lambda _doc: (_ for _ in ()).throw(AssertionError("replayed mutation")),
        lambda *_: (_ for _ in ()).throw(AssertionError("replayed projection")),
    )
    assert receipt.status == "COMMITTED"
    assert replay == receipt
    assert json.loads(item.read_text())["condition"] == "new"
    assert projections == [("A", {"sku": "A", "condition": "new"})]
    assert (tmp_path / "archive" / "A.zip").exists()


def test_stale_cas_has_no_item_archive_or_projection_effect(tmp_path):
    item = tmp_path / "items" / "A" / "A.json"
    original = {"sku": "A", "v": 1}
    _write(item, original)
    calls = []
    receipt = _run(tmp_path, item, "stale", {}, lambda doc: {**doc, "v": 2}, lambda *_: calls.append(True))
    assert receipt.status == "CONFLICT"
    assert json.loads(item.read_text()) == original
    assert not (tmp_path / "archive").exists()
    assert calls == []


def test_supplied_operation_id_mismatch_is_durable_and_inert(tmp_path):
    item = tmp_path / "items" / "A" / "A.json"
    _write(item, {"sku": "A"})
    calls = []
    receipt = _run(tmp_path, item, item_generation({"sku": "A"}), {}, lambda doc: calls.append(True) or doc, operation_id="wrong")
    replay = _run(tmp_path, item, item_generation({"sku": "A"}), {}, lambda doc: calls.append(True) or doc, operation_id="wrong")
    assert receipt.status == "CONFLICT"
    assert replay == receipt
    assert calls == []


def test_projection_failure_is_truthfully_repair_required(tmp_path):
    item = tmp_path / "items" / "A" / "A.json"
    _write(item, {"sku": "A", "v": 1})
    receipt = _run(
        tmp_path,
        item,
        item_generation({"sku": "A", "v": 1}),
        {"v": 2},
        lambda doc: {**doc, "v": 2},
        lambda *_: (_ for _ in ()).throw(RuntimeError("sqlite unavailable")),
    )
    assert receipt.status == "REPAIR_REQUIRED"
    assert receipt.resulting_generation == item_generation({"sku": "A", "v": 2})
    assert json.loads(item.read_text())["v"] == 2


def test_intent_recovers_write_projection_seam_without_remutating(tmp_path):
    item = tmp_path / "items" / "A" / "A.json"
    before = {"sku": "A", "v": 1}
    after = {"sku": "A", "v": 2}
    expected = item_generation(before)
    payload = {"v": 2}
    operation_id = operation_identity(
        sku="A",
        kind="normalize-condition",
        expected_generation=expected,
        payload=payload,
    )
    intent = {
        "binding": {
            "expected_generation": expected,
            "kind": "normalize-condition",
            "operation_id": operation_id,
            "payload": payload,
            "sku": "A",
        },
        "planned_resulting_generation": item_generation(after),
        "recorded_at": "crash-before-receipt",
    }
    intent_path = tmp_path / "journal" / "operations" / operation_id[:2] / operation_id / "intent.json"
    _write(intent_path, intent)
    _write(item, after)
    projections = []
    receipt = _run(
        tmp_path,
        item,
        expected,
        payload,
        lambda _doc: (_ for _ in ()).throw(AssertionError("must not remutate")),
        lambda sku, doc: projections.append((sku, doc)),
    )
    assert receipt.status == "COMMITTED"
    assert "recovered" in receipt.detail
    assert projections == [("A", after)]


def test_same_item_concurrency_serializes_and_disjoint_items_progress(tmp_path):
    items = []
    for sku in ("A", "B"):
        path = tmp_path / "items" / sku / f"{sku}.json"
        _write(path, {"sku": sku, "v": 0})
        items.append(path)
    generation_a = item_generation({"sku": "A", "v": 0})
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [
            pool.submit(_run, tmp_path, items[0], generation_a, {"v": value}, lambda doc, value=value: {**doc, "v": value})
            for value in (1, 2)
        ]
        futures.append(pool.submit(_run, tmp_path, items[1], item_generation({"sku": "B", "v": 0}), {"v": 1}, lambda doc: {**doc, "v": 1}))
    statuses = [future.result().status for future in futures]
    assert sorted(statuses[:2]) == ["COMMITTED", "CONFLICT"]
    assert statuses[2] == "COMMITTED"
