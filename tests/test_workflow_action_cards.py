import json
from unittest.mock import MagicMock

from tgw import http_server
from tgw.workflow.action_cards import build_item_action_card


def _item(tmp_path, condition="pre-owned"):
    path = tmp_path / "items" / "SKU-1" / "SKU-1.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"sku": "SKU-1", "condition": condition, "image": "a.jpg"}),
                    encoding="utf-8")
    return path


def test_projection_exposes_current_graph_evidence_waits_and_legal_actions(tmp_path):
    path = _item(tmp_path)
    card = build_item_action_card(path)
    assert card["goal"] == {"id": "tgw.ebay_listable", "version": "1"}
    assert len(card["object_generation"]) == 64
    assert len(card["graph_id"]) == 64
    assert card["fingerprints"]
    assert all(fp["evidence"] for fp in card["fingerprints"])
    assert card["waiting_treatments"]
    normalize = next(action for action in card["legal_actions"]
                     if action["treatment_id"] == "normalize-condition")
    assert normalize["action"] == "dispatch"
    assert card["blind_retry_allowed"] is False


def test_attempts_join_results_and_ambiguous_external_effect_becomes_gate(tmp_path):
    attempts = [{
        "job_id": "job-1", "queue_name": "ebay_publish", "state": "dead_letter",
        "attempt_count": 1, "max_attempts": 1, "error_detail": "ambiguous response",
        "payload_json": {
            "treatment_id": "ebay-publish", "treatment_version": "1",
            "graph_id": "old", "object_generation": "old-gen",
            "result": {"outcome": "ambiguous", "evidence": {"request_id": "provider-1"}},
        },
    }]
    card = build_item_action_card(_item(tmp_path, condition="Used"), attempts)
    assert card["attempts"][0]["result"]["outcome"] == "ambiguous"
    assert "listing.publish" in card["reconciliation_gates"]
    assert "listing.publish" in card["operator_gates"]
    assert card["blind_retry_allowed"] is False
    assert not any(action.get("action") == "retry" for action in card["legal_actions"])


def test_active_attempt_is_visible_separately(tmp_path):
    attempts = [{"job_id": "job-2", "queue_name": "normalize_condition",
                 "state": "running", "attempt_count": 1, "max_attempts": 3,
                 "payload_json": {"treatment_id": "normalize-condition",
                                  "graph_id": "graph-1", "object_generation": "gen-1"}}]
    card = build_item_action_card(_item(tmp_path), attempts)
    assert [item["job_id"] for item in card["active_attempts"]] == ["job-2"]


def test_same_generation_failed_attempt_suppresses_unchanged_dispatch(tmp_path):
    path = _item(tmp_path)
    current = build_item_action_card(path)
    attempt = {
        "job_id": "failed-1", "queue_name": "normalize_condition",
        "state": "dead_letter", "attempt_count": 1, "max_attempts": 1,
        "payload_json": {
            "treatment_id": "normalize-condition", "treatment_version": "1",
            "graph_id": current["graph_id"],
            "object_generation": current["object_generation"],
            "condition_hash": current["condition_hash"],
            "result": {"outcome": "failed"},
        },
    }
    card = build_item_action_card(path, [attempt])
    assert not any(
        action["treatment_id"] == "normalize-condition"
        for action in card["legal_actions"]
    )
    waiting = next(
        item for item in card["waiting_treatments"]
        if item["treatment_id"] == "normalize-condition"
    )
    assert "unchanged non-success attempt" in waiting["reasons"][0]


def test_matching_active_attempt_is_in_progress_not_dispatchable(tmp_path):
    path = _item(tmp_path)
    current = build_item_action_card(path)
    attempt = {
        "job_id": "active-1", "queue_name": "normalize_condition",
        "state": "running", "attempt_count": 1, "max_attempts": 3,
        "payload_json": {
            "treatment_id": "normalize-condition", "treatment_version": "1",
            "graph_id": current["graph_id"],
            "object_generation": current["object_generation"],
            "condition_hash": current["condition_hash"],
        },
    }
    card = build_item_action_card(path, [attempt])
    assert card["in_progress_treatments"] == [
        {"treatment_id": "normalize-condition", "treatment_version": "1"}
    ]
    assert not any(
        action["treatment_id"] == "normalize-condition"
        for action in card["legal_actions"]
    )


def test_future_timer_is_active_in_progress_for_exact_current_binding(tmp_path):
    path = _item(tmp_path, condition="Used")
    current = build_item_action_card(path)
    timer = {
        "job_id": "timer-1", "queue_name": "ebay_upload", "state": "queued",
        "not_before": "2099-01-01T00:00:00+00:00",
        "attempt_count": 0, "max_attempts": 3,
        "payload_json": {
            "treatment_id": "ebay-upload", "treatment_version": "1",
            "graph_id": current["graph_id"],
            "object_generation": current["object_generation"],
            "condition_hash": current["condition_hash"],
        },
    }
    card = build_item_action_card(path, [timer])
    assert timer["job_id"] in [row["job_id"] for row in card["active_attempts"]]
    assert {"treatment_id": "ebay-upload", "treatment_version": "1"} in (
        card["in_progress_treatments"]
    )
    assert card["active_attempts"][0]["not_before"] == timer["not_before"]


def test_stale_or_malformed_attempt_remains_history_without_suppression(tmp_path):
    path = _item(tmp_path)
    current = build_item_action_card(path)
    stale = {
        "job_id": "stale-1", "queue_name": "normalize_condition",
        "state": "dead_letter", "attempt_count": 1, "max_attempts": 1,
        "payload_json": {
            "treatment_id": "normalize-condition", "treatment_version": "1",
            "object_generation": "stale-generation",
            "condition_hash": current["condition_hash"],
            "result": {"outcome": "failed"},
        },
    }
    card = build_item_action_card(path, [stale])
    assert any(
        action["treatment_id"] == "normalize-condition"
        for action in card["legal_actions"]
    )


def test_api_projection_uses_canonical_attempt_query(tmp_path, monkeypatch):
    path = _item(tmp_path)
    monkeypatch.setitem(http_server._cfg, "itemdata_root", path.parents[1])
    rows = [{"job_id": "job-entity", "queue_name": "normalize_condition",
             "state": "queued", "payload_json": {}, "attempt_count": 0,
             "max_attempts": 3}]
    monkeypatch.setattr(http_server, "_workflow_attempt_rows", lambda sku: rows)
    response = http_server.item_workflow("SKU-1")
    assert response["ok"] is True
    assert response["workflow"]["active_attempts"][0]["job_id"] == "job-entity"


def test_attempt_query_uses_entity_identity_with_legacy_sku_fallback(monkeypatch):
    cursor = MagicMock()
    cursor.__enter__.return_value = cursor
    cursor.fetchall.return_value = [{
        "job_id": "job-1", "state": "dead_letter",
        "payload_json": {"treatment_id": "normalize-condition", "graph_id": "graph-1",
                         "object_generation": "gen-1"},
    }]
    connection = MagicMock()
    connection.__enter__.return_value = connection
    connection.cursor.return_value = cursor
    monkeypatch.setitem(http_server._cfg, "postgres_dsn", "test-dsn")
    monkeypatch.setattr(http_server.psycopg2, "connect", lambda *args, **kwargs: connection)
    rows = http_server._workflow_attempt_rows("SKU-1")
    sql, params = cursor.execute.call_args.args
    assert "entity_type = 'item' AND entity_id = %s" in sql
    assert "payload_json->>'sku' = %s" in sql
    assert params[:2] == ("SKU-1", "SKU-1")
    assert rows[0]["retry_allowed"] is False


def test_item_detail_renders_clear_workflow_action_card(tmp_path):
    card = build_item_action_card(_item(tmp_path))
    html = http_server._render_item_detail_html(
        "SKU-1", {"sku": "SKU-1", "title": "Example"}, [], [], [], workflow_card=card,
    )
    assert 'id="workflow-action-card"' in html
    assert "tgw.ebay_listable" in html
    assert "normalize-condition" in html
    assert "Legal actions" in html


def test_item_detail_hides_blind_retry_for_governed_ambiguous_dead_letter():
    jobs = [{"job_id": "job-1", "queue_name": "ebay_publish", "state": "dead_letter",
             "retry_allowed": False, "error_detail": "ambiguous provider response"}]
    html = http_server._render_item_detail_html(
        "SKU-1", {"sku": "SKU-1", "title": "Example"}, [], [], jobs,
    )
    assert "ambiguous provider response" in html
    assert "retryJob('job-1')" not in html
