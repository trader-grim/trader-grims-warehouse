"""audit#1143 (todo #1171): workers/ invariant + cohesion findings, batched.

- bundle_intake.py / ebay_draft.py / ebay_upload.py / ai_identify.py used to
  hand-assemble ItemData paths inline (cfg['itemdata_root'] / sku / ...)
  instead of the shared config.sku_dir()/sku_json() fence helpers (invariant
  A4). Now they call the shared helpers; these tests confirm the resulting
  paths are unchanged (no behavior change, just de-duplicated construction).
- itemdata_scrub.py's process_queue_job() took SKU and root_dir from job
  content with no validation at all -- a malformed/malicious job file could
  point writes at an arbitrary directory outside ItemData. Now root_dir is
  always the configured default_root, and an unsafe SKU (containing '..',
  '/', or '\\') is rejected before any file access.
- photo_history_recovery.py copied photos into live item folders with no
  catalog-refresh trigger -- catalog stayed stale until an unrelated write.
  Now enqueues a coalesced catalog_rebuild job after any real (--write) copy.
- ebay_publish.py / ebay_stage.py's _format_ebay_error() was byte-for-byte
  duplicated; now both import tgw.ebay.sync.format_ebay_error().

All external calls (eBay, LLM, postgres) are mocked -- tests pass completely
offline.
"""

from __future__ import annotations

import json

import tgw.config as config_mod

# ---------------------------------------------------------------------------
# Fence-path construction now goes through config.sku_dir()/sku_json()
# ---------------------------------------------------------------------------

def test_bundle_intake_prepare_dest_uses_config_sku_dir(tmp_path):
    from tgw.workers.bundle_intake import BundleIntakeWorker

    cfg = {"itemdata_root": tmp_path}
    w = BundleIntakeWorker.__new__(BundleIntakeWorker)
    w.config = cfg

    dest = w._prepare_dest("tgw20260101120000001")
    assert dest == config_mod.sku_dir(cfg, "tgw20260101120000001")
    assert dest.exists()


def test_bundle_intake_handle_symlink_uses_config_sku_dir(tmp_path, monkeypatch):
    from tgw.workers.bundle_intake import BundleIntakeWorker

    sku = "tgw20260101120000002"
    item_dir = tmp_path / sku
    item_dir.mkdir()
    cfg = {"itemdata_root": tmp_path}
    w = BundleIntakeWorker.__new__(BundleIntakeWorker)
    w.config = cfg

    enqueued = []
    monkeypatch.setattr(w, "_enqueue_downstream", lambda sku: enqueued.append(sku))

    symlink = tmp_path / "incoming-link"
    symlink.symlink_to(item_dir)
    w._handle_symlink(sku, symlink)

    assert enqueued == [sku]
    assert not symlink.exists()  # removed


def test_ebay_draft_handle_uses_config_sku_json(tmp_path, monkeypatch):
    import tgw.workers.ebay_draft as ebay_draft_mod

    sku = "tgw20260101120000003"
    d = tmp_path / sku
    d.mkdir()
    (d / f"{sku}.json").write_text(json.dumps({"sku": sku}), encoding="utf-8")

    cfg = {"itemdata_root": tmp_path}
    w = ebay_draft_mod.EbayDraftWorker.__new__(ebay_draft_mod.EbayDraftWorker)
    w.config = cfg

    # title is missing -> raises HardFailure right after reading json_path,
    # which is enough to prove the correct file was located via sku_json().
    import pytest

    from tgw.queue.worker_base import HardFailure
    with pytest.raises(HardFailure, match="no title"):
        w.handle({"payload_json": {"sku": sku}})


def test_ebay_upload_handle_uses_config_sku_json(tmp_path):
    import pytest

    import tgw.workers.ebay_upload as ebay_upload_mod
    from tgw.queue.worker_base import HardFailure

    w = ebay_upload_mod.EbayUploadWorker.__new__(ebay_upload_mod.EbayUploadWorker)
    w.config = {
        "itemdata_root": tmp_path,
        "workflow_migration": {"ebay_provider_identity": "test-seller"},
    }

    with pytest.raises(HardFailure, match="item JSON not found"):
        w.handle({"payload_json": {
            "sku": "tgw20260101120000004",
            "treatment_id": "ebay-upload", "treatment_version": "1",
            "graph_id": "graph", "goal_profile_id": "goal",
            "goal_profile_version": "1", "object_generation": "generation",
            "condition_hash": "condition", "operator_authority_id": "authority",
            "pre_authority_condition_hash": "pre-authority",
        }})


def test_ai_identify_handle_uses_config_sku_dir(tmp_path):
    import pytest

    import tgw.workers.ai_identify as ai_identify_mod
    from tgw.queue.worker_base import HardFailure

    w = ai_identify_mod.AIIdentifyWorker.__new__(ai_identify_mod.AIIdentifyWorker)
    w.config = {"itemdata_root": tmp_path}

    with pytest.raises(HardFailure, match="item JSON not found"):
        w.handle({"payload_json": {"sku": "tgw20260101120000005"}})


# ---------------------------------------------------------------------------
# itemdata_scrub.py -- SKU/root validation
# ---------------------------------------------------------------------------

def test_itemdata_scrub_ignores_root_override_in_job_content(tmp_path):
    from tgw.workers.itemdata_scrub import ScrubRules, process_queue_job

    default_root = tmp_path / "ItemData"
    default_root.mkdir()
    sku = "tgw20260101120000006"
    item_dir = default_root / sku
    item_dir.mkdir()
    (item_dir / f"{sku}.json").write_text(json.dumps({"sku": sku, "secret_field": "x"}), encoding="utf-8")

    evil_root = tmp_path / "evil-outside-fence"
    evil_root.mkdir()

    job_file = tmp_path / "job1"
    job_file.write_text(json.dumps({"sku": sku, "root": str(evil_root)}), encoding="utf-8")

    rules = ScrubRules(remove_keys=("secret_field",))
    ok = process_queue_job(job_file, rules, default_root)

    assert ok is True
    # the write happened under default_root, NOT under the job-supplied 'root'
    updated = json.loads((item_dir / f"{sku}.json").read_text())
    assert "secret_field" not in updated
    assert not (evil_root / sku).exists()


def test_itemdata_scrub_rejects_unsafe_sku_with_dotdot(tmp_path):
    from tgw.workers.itemdata_scrub import ScrubRules, process_queue_job

    default_root = tmp_path / "ItemData"
    default_root.mkdir()

    job_file = tmp_path / "job2"
    job_file.write_text(json.dumps({"sku": "../../etc/passwd"}), encoding="utf-8")

    rules = ScrubRules()
    ok = process_queue_job(job_file, rules, default_root)
    assert ok is False


def test_itemdata_scrub_rejects_unsafe_sku_with_slash(tmp_path):
    from tgw.workers.itemdata_scrub import ScrubRules, process_queue_job

    default_root = tmp_path / "ItemData"
    default_root.mkdir()

    job_file = tmp_path / "job3"
    job_file.write_text(json.dumps({"sku": "foo/bar"}), encoding="utf-8")

    rules = ScrubRules()
    ok = process_queue_job(job_file, rules, default_root)
    assert ok is False


def test_itemdata_scrub_still_processes_valid_sku_from_job_content(tmp_path):
    from tgw.workers.itemdata_scrub import ScrubRules, process_queue_job

    default_root = tmp_path / "ItemData"
    default_root.mkdir()
    sku = "tgw20260101120000007"
    item_dir = default_root / sku
    item_dir.mkdir()
    (item_dir / f"{sku}.json").write_text(json.dumps({"sku": sku, "junk": 1}), encoding="utf-8")

    job_file = tmp_path / "job4"
    job_file.write_text(json.dumps({"sku": sku}), encoding="utf-8")

    rules = ScrubRules(remove_keys=("junk",))
    ok = process_queue_job(job_file, rules, default_root)
    assert ok is True
    updated = json.loads((item_dir / f"{sku}.json").read_text())
    assert "junk" not in updated


def test_itemdata_scrub_uses_canonical_sku_json_path_helper(tmp_path):
    """derive_item_path() must delegate to config.sku_json() rather than
    hand-join path components (audit#COHESION-2026-07 #1305) -- confirm
    byte-identical output to the old hand-built path for a normal sku."""
    from tgw import config as tgw_config
    from tgw.workers.itemdata_scrub import derive_item_path

    root = tmp_path / "ItemData"
    sku = "tgw20260101120000009"
    expected = tgw_config.sku_json({"itemdata_root": root}, sku)
    assert derive_item_path(root, sku) == expected
    assert derive_item_path(root, sku) == root / sku / f"{sku}.json"


def test_itemdata_scrub_resolves_old_sku_via_sku_old_fallback(tmp_path):
    """A queue job referencing a renamed item's OLD sku must resolve via
    the fence's find_current_sku() alias index instead of failing 'not
    found' (audit#COHESION-2026-07 #1305, mirrors revision.py #1313/#1316
    and mcp_server.py #1312 in the same cohesion batch)."""
    import tgw.resolver as resolver_mod
    from tgw.workers.itemdata_scrub import ScrubRules, process_queue_job

    resolver_mod._sku_old_index = None  # reset process-level cache

    default_root = tmp_path / "ItemData"
    default_root.mkdir()
    new_sku = "tgw20260101120000010"
    old_sku = "tgw001old"
    item_dir = default_root / new_sku
    item_dir.mkdir()
    (item_dir / f"{new_sku}.json").write_text(
        json.dumps({"sku": new_sku, "sku_old": old_sku, "junk": 1}),
        encoding="utf-8",
    )

    job_file = tmp_path / "job5"
    job_file.write_text(json.dumps({"sku": old_sku}), encoding="utf-8")

    rules = ScrubRules(remove_keys=("junk",))
    ok = process_queue_job(job_file, rules, default_root)
    assert ok is True
    updated = json.loads((item_dir / f"{new_sku}.json").read_text())
    assert "junk" not in updated

    resolver_mod._sku_old_index = None  # leave cache clean for other tests


# ---------------------------------------------------------------------------
# photo_history_recovery.py -- catalog_rebuild enqueue after real copies
# ---------------------------------------------------------------------------

def test_photo_history_recovery_no_longer_enqueues_catalog_rebuild_on_copy(tmp_path, monkeypatch, capsys):
    """PP-CATALOG-INCR-001 CI-4 (2026-07-18): enqueue_catalog_rebuild() is now
    a no-op — the per-item SQLite catalog stays live via CI-2's synchronous
    fence-write upsert, and the remaining JSON artifacts are refreshed by an
    hourly timer instead of a per-write trigger. This still calls
    state_machine.enqueue_catalog_rebuild() internally (harmless no-op, no
    call site needed editing), so no enqueue_job call should be recorded."""
    import tgw.workers.photo_history_recovery as phr_mod

    itemdata_root = tmp_path / "ItemData"
    sku = "tgw20260101120000008"
    item_dir = itemdata_root / sku
    item_dir.mkdir(parents=True)
    (item_dir / f"{sku}.json").write_text(json.dumps({"photo_refs": "front.jpg"}), encoding="utf-8")

    search_root = tmp_path / "search"
    search_root.mkdir()
    (search_root / "front.jpg").write_bytes(b"fake-photo")

    cfg_path = tmp_path / "phr-config.json"
    cfg_path.write_text(json.dumps({
        "itemdata_root": str(itemdata_root),
        "default_search_roots": [str(search_root)],
        "photo_reference_keys": ["photo_refs"],
        "destination": {"overwrite": False, "copy_if_missing": True},
    }), encoding="utf-8")

    calls = []
    monkeypatch.setattr(phr_mod.state_machine, "init", lambda dsn: calls.append(("init", dsn)))
    monkeypatch.setattr(phr_mod.state_machine, "enqueue_job", lambda **kw: calls.append(("enqueue", kw)) or "job-1")

    import sys
    argv = ["photo_history_recovery.py", "--config", str(cfg_path),
            "--report", str(tmp_path / "report.jsonl"), "--write"]
    monkeypatch.setattr(sys, "argv", argv)
    rc = phr_mod.main()

    assert rc == 0
    enqueue_calls = [c for c in calls if c[0] == "enqueue"]
    assert len(enqueue_calls) == 0


def test_photo_history_recovery_dry_run_does_not_enqueue(tmp_path, monkeypatch):
    import tgw.workers.photo_history_recovery as phr_mod

    itemdata_root = tmp_path / "ItemData"
    sku = "tgw20260101120000009"
    item_dir = itemdata_root / sku
    item_dir.mkdir(parents=True)
    (item_dir / f"{sku}.json").write_text(json.dumps({"photo_refs": "front.jpg"}), encoding="utf-8")

    search_root = tmp_path / "search"
    search_root.mkdir()
    (search_root / "front.jpg").write_bytes(b"fake-photo")

    cfg_path = tmp_path / "phr-config.json"
    cfg_path.write_text(json.dumps({
        "itemdata_root": str(itemdata_root),
        "default_search_roots": [str(search_root)],
        "photo_reference_keys": ["photo_refs"],
        "destination": {"overwrite": False, "copy_if_missing": True},
    }), encoding="utf-8")

    calls = []
    monkeypatch.setattr(phr_mod.state_machine, "init", lambda dsn: calls.append(("init", dsn)))
    monkeypatch.setattr(phr_mod.state_machine, "enqueue_job", lambda **kw: calls.append(("enqueue", kw)) or "job-1")

    import sys
    argv = ["photo_history_recovery.py", "--config", str(cfg_path),
            "--report", str(tmp_path / "report.jsonl")]  # no --write: dry-run
    monkeypatch.setattr(sys, "argv", argv)
    rc = phr_mod.main()

    assert rc == 0
    assert not (item_dir / "front.jpg").exists()
    assert calls == []


# ---------------------------------------------------------------------------
# tgw.ebay.sync.format_ebay_error -- shared error formatter
# ---------------------------------------------------------------------------

def test_format_ebay_error_extracts_long_message():
    from tgw.ebay.sync import format_ebay_error

    body = json.dumps({"errors": [{"longMessage": "Category not found", "message": "short"}]})
    assert format_ebay_error(body, 400) == "Category not found"


def test_format_ebay_error_falls_back_to_raw_body():
    from tgw.ebay.sync import format_ebay_error

    assert format_ebay_error("not json", 500) == "HTTP 500: not json"


def test_ebay_publish_and_ebay_stage_share_format_ebay_error():
    import tgw.ebay.sync as sync_mod
    import tgw.workers.ebay_publish as publish_mod
    import tgw.workers.ebay_stage as stage_mod

    assert publish_mod._format_ebay_error is sync_mod.format_ebay_error
    assert stage_mod._format_ebay_error is sync_mod.format_ebay_error
