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
    assert not any(action["treatment_id"] == "ebay-upload"
                   for action in card["legal_actions"])
    upload = next(wait for wait in card["waiting_treatments"]
                  if wait["treatment_id"] == "ebay-upload")
    assert any("operator_authorized_upload=false" in reason
               for reason in upload["reasons"])


def test_missing_provider_contract_does_not_hide_list_command(tmp_path):
    path = _item(tmp_path, condition="Used")
    path.write_text(
        json.dumps(
            {
                "sku": "SKU-1",
                "condition": "Used",
                "image": "a.jpg",
                "ebay_category_id": "123",
                "draft_listing": {
                    "title": "Example",
                    "category_id": "123",
                    "price": 10,
                },
            }
        ),
        encoding="utf-8",
    )

    card = build_item_action_card(path)

    upload = next(
        waiting
        for waiting in card["waiting_treatments"]
        if waiting["treatment_id"] == "ebay-upload"
    )
    assert len(upload["reasons"]) == 1
    assert upload["reasons"][0].startswith("operator_authorized_upload=false:")
    assert card["operator_projection"]["commands"]["list-item"] == {
        "enabled": True,
        "reason": None,
    }


def test_published_inventory_projection_exposes_update_not_list(tmp_path):
    path = _item(tmp_path, condition="Used")
    item = json.loads(path.read_text(encoding="utf-8"))
    item.update(
        ebay_offer={"offer_id": "offer-1", "status": "PUBLISHED"},
        ebay_listing={
            "listing_id": "listing-1",
            "status": "PUBLISHED",
        },
    )
    path.write_text(json.dumps(item), encoding="utf-8")

    card = build_item_action_card(path)
    commands = card["operator_projection"]["commands"]

    assert card["operator_projection"]["state"] == "published"
    assert commands["list-item"]["enabled"] is False
    assert commands["update-item"] == {"enabled": True, "reason": None}


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
    assert card["operator_projection"]["commands"]["list-item"]["enabled"] is False
    assert card["operator_projection"]["commands"]["update-item"]["enabled"] is False


def test_legacy_unbound_reconciliation_receipt_still_blocks_retry(tmp_path):
    attempts = [{
        "job_id": "legacy-publish", "queue_name": "ebay_publish",
        "state": "dead_letter", "attempt_count": 1, "max_attempts": 3,
        "payload_json": {
            "sku": "SKU-1",
            "result": {
                "treatment_id": "ebay-publish", "treatment_version": "1",
                "outcome": "reconciliation_required",
                "evidence": {"listing_id": "L1"},
            },
        },
    }]
    card = build_item_action_card(_item(tmp_path, condition="Used"), attempts)
    row = card["attempts"][0]
    assert row["treatment_id"] == "ebay-publish"
    assert row["retry_allowed"] is False
    assert "listing.publish" in card["reconciliation_gates"]


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


def test_operational_projection_uses_authoritative_stage_receipt(tmp_path, monkeypatch):
    path = _item(tmp_path, condition="Used")
    item = json.loads(path.read_text(encoding="utf-8"))
    item["draft_listing"] = {"title": "Example", "category_id": "123"}
    item["price"] = 10
    item["image"] = "a.jpg"
    item["ebay_offer"] = {
        "offer_id": "offer-1", "status": "UNPUBLISHED",
        "provider_effect_id": "effect-1",
    }
    from tgw.workflow.operator_authority import listing_content_identity

    item["ebay_offer"]["stage_content_identity"] = listing_content_identity(item)
    path.write_text(json.dumps(item), encoding="utf-8")
    monkeypatch.setattr(
        "tgw.workflow.listing_migration._authoritative_stage_lookup",
        lambda item, provider: lambda sku: {
            "receipt_id": "effect-1",
            "content_identity": item["ebay_offer"]["stage_content_identity"],
            "offer_id": "offer-1",
        },
    )

    card = build_item_action_card(path, provider_identity="account-1")
    staged = next(
        fingerprint for fingerprint in card["fingerprints"]
        if fingerprint["condition_id"] == "staged_content_current"
    )
    assert staged["result"] == "true"


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


def test_reconciliation_query_exposes_only_allowlisted_ledger_columns(monkeypatch):
    cursor = MagicMock()
    cursor.__enter__.return_value = cursor
    cursor.fetchall.side_effect = [
        [{"effect_id": "effect-1", "state": "succeeded"}],
        [{"authority_id": "authority-1", "provider_identity": "ebay:account"}],
        [{"observation_id": "observation-1", "outcome": "corroborated"}],
    ]
    connection = MagicMock()
    connection.__enter__.return_value = connection
    connection.cursor.return_value = cursor
    monkeypatch.setitem(http_server._cfg, "postgres_dsn", "test-dsn")
    monkeypatch.setattr(http_server.psycopg2, "connect", lambda *args, **kwargs: connection)

    rows = http_server._workflow_reconciliation_rows("SKU-1")

    assert rows["effects"][0]["effect_id"] == "effect-1"
    assert rows["authorities"][0]["authority_id"] == "authority-1"
    assert rows["observations"][0]["observation_id"] == "observation-1"
    statements = "\n".join(call.args[0] for call in cursor.execute.call_args_list)
    assert "request_json" not in statements
    assert "authority_json" not in statements
    assert "result_json" not in statements
    assert all(call.args[1] == ("SKU-1", 100) for call in cursor.execute.call_args_list)


def test_reconciliation_api_binds_provider_identity_and_canonical_markers(
    tmp_path, monkeypatch,
):
    path = _item(tmp_path, condition="Used")
    item = json.loads(path.read_text(encoding="utf-8"))
    item["ebay_offer"] = {
        "provider_effect_id": "stage-effect", "offer_id": "offer-1",
        "stage_content_identity": "content-1",
    }
    item["ebay_listing"] = {
        "provider_effect_id": "publish-effect", "listing_id": "listing-1",
        "offer_id": "offer-1", "published_at": "2026-08-11T00:00:00Z",
    }
    path.write_text(json.dumps(item), encoding="utf-8")
    monkeypatch.setitem(http_server._cfg, "itemdata_root", path.parents[1])
    monkeypatch.setitem(
        http_server._cfg, "workflow_migration",
        {"ebay_provider_identity": "ebay:account"},
    )
    monkeypatch.setattr(
        http_server, "_workflow_reconciliation_rows",
        lambda sku: {"effects": [], "authorities": [], "observations": []},
    )

    response = http_server.item_workflow_reconciliation("SKU-1")

    assert response["schema"] == "workflow-reconciliation/v1"
    assert response["provider_identity"] == "ebay:account"
    assert response["canonical_markers"]["stage"] == {
        "provider_effect_id": "stage-effect", "offer_id": "offer-1",
        "stage_content_identity": "content-1",
    }
    assert response["canonical_markers"]["publish"]["listing_id"] == "listing-1"
    assert set(response) == {
        "ok", "schema", "entity_id", "provider_identity", "canonical_markers",
        "effects", "authorities", "observations",
    }


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
