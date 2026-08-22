import hashlib
import json

import pytest

from tgw.receipt_inspector import (
    ReceiptInspectionError,
    inspect_receipt,
    list_receipts,
)


def _hash(value):
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _config(root):
    return {
        "receipt_inspection": {
            "schema": "tgw-receipt-inspection-config/v1",
            "roots": {"fleet": str(root)},
        }
    }


def test_list_and_show_verify_claimed_receipt_hash(durable_path):
    body = {"schema": "example/v1", "status": "PASS", "evidence": ["proof"]}
    receipt = {**body, "receipt_hash": _hash(body)}
    raw = json.dumps(receipt).encode()
    (durable_path / "one.json").write_bytes(raw)

    listed = list_receipts(_config(durable_path), root_id="fleet")
    assert listed["receipts"] == [{
        "id": "one", "schema": "example/v1", "status": "PASS",
        "status_semantics": "UNTRUSTED_REPORTED_FIELD",
        "content_sha256": "sha256:" + hashlib.sha256(raw).hexdigest(),
        "claimed_receipt_hash": receipt["receipt_hash"],
        "receipt_hash_valid": True,
        "signature_authority_verified": False,
        "current_state_verified": False,
        "admission_authority": False,
    }]
    shown = inspect_receipt(_config(durable_path), root_id="fleet", receipt_id="one")
    assert shown["receipt"] == receipt
    assert shown["summary"] == listed["receipts"][0]


def test_forged_hash_is_reported_without_trusting_status(durable_path):
    receipt = {"schema": "example/v1", "status": "PASS", "receipt_hash": "sha256:" + "0" * 64}
    (durable_path / "forged.json").write_text(json.dumps(receipt))
    summary = list_receipts(_config(durable_path), root_id="fleet")["receipts"][0]
    assert summary["status"] == "PASS"
    assert summary["receipt_hash_valid"] is False
    assert summary["status_semantics"] == "UNTRUSTED_REPORTED_FIELD"
    assert summary["admission_authority"] is False


def test_unregistered_traversal_symlink_and_tmp_roots_fail_closed(durable_path):
    (durable_path / "one.json").write_text("{}")
    outside = durable_path.parent / (durable_path.name + "-outside.json")
    outside.write_text("{}")
    try:
        (durable_path / "linked.json").symlink_to(outside)
        with pytest.raises(ReceiptInspectionError):
            inspect_receipt(_config(durable_path), root_id="missing", receipt_id="one")
        with pytest.raises(ReceiptInspectionError):
            inspect_receipt(_config(durable_path), root_id="fleet", receipt_id="../outside")
        with pytest.raises(ReceiptInspectionError):
            inspect_receipt(_config(durable_path), root_id="fleet", receipt_id="linked")
        with pytest.raises(ReceiptInspectionError):
            list_receipts(_config("/tmp"), root_id="fleet")
    finally:
        outside.unlink()


def test_invalid_json_is_visible_in_list_and_refused_for_show(durable_path):
    (durable_path / "broken.json").write_text("{")
    listed = list_receipts(_config(durable_path), root_id="fleet")
    assert listed["receipts"][0]["status"] == "INVALID"
    with pytest.raises(ReceiptInspectionError):
        inspect_receipt(_config(durable_path), root_id="fleet", receipt_id="broken")
