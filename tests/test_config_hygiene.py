"""Tests for ISS-003 + ISS-004 config hygiene fixes.

ISS-003: full_catalog_path code default must match JSON value (master-catalog.json).
ISS-004: ebay_sku_migrate block must be surfaced in the normalised config dict,
         not require callers to reach into cfg['raw'].

todo #1400 (PP-DEADLETTER-001): regression coverage for a real historic bug —
before commit 00cf9274 (2026-06-30), load_config()'s returned dict had NO
'api_key' key at all unless secrets_root/tgw-api-key.json happened to exist
(the key was only added to the dict inside an `if _api_key_path.exists():`
block). tgw.apis.fence._headers() does `cfg['api_key']` unconditionally —
every fence call from a worker whose cfg came from a load_config() call made
before that key file existed hit KeyError('api_key'). Fixed same-day by
00cf9274 (key file existence now only gates the *value*, not whether the key
is present at all). These tests pin the invariant so it can't regress
silently: 'api_key' must always be a key in the returned dict, present as an
empty string when the secret file is absent.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from tgw.config import load_config, load_operational_config


def _write_cfg(tmp_path: Path, data: dict) -> Path:
    # secrets_root must stay inside tmp_path — load_config() otherwise falls
    # back to the real /opt/TGW/secrets and dies with PermissionError for any
    # non-tgw test runner (secrets are correctly chmod 600 tgw-owned).
    merged = {"secrets_root": str(tmp_path / "secrets"), **data}
    p = tmp_path / "tgw-api-config.json"
    p.write_text(json.dumps(merged))
    return p


# ---------------------------------------------------------------------------
# ISS-003 — full_catalog_path default
# ---------------------------------------------------------------------------


def test_full_catalog_path_default_matches_json_canonical(tmp_path):
    """When full_catalog_path is absent from JSON, default must be master-catalog.json."""
    cfg_path = _write_cfg(tmp_path, {"catalog_root": str(tmp_path)})
    cfg = load_config(cfg_path)
    assert cfg["full_catalog_path"] == tmp_path / "master-catalog.json"


def test_full_catalog_path_explicit_override(tmp_path):
    """An explicit full_catalog_path in JSON must still be honoured."""
    override = str(tmp_path / "custom-catalog.json")
    cfg_path = _write_cfg(tmp_path, {"full_catalog_path": override})
    cfg = load_config(cfg_path)
    assert cfg["full_catalog_path"] == Path(override)


def test_log_root_is_resolved_once_at_the_config_boundary(tmp_path):
    defaulted = load_config(_write_cfg(tmp_path, {}))
    assert defaulted["log_root"] == Path("/opt/TGW/var/log")

    explicit = tmp_path / "logs"
    configured = load_config(_write_cfg(tmp_path, {"log_root": str(explicit)}))
    assert configured["log_root"] == explicit


def test_plan_roots_keep_mutable_and_authority_bindings_separate(tmp_path):
    cfg = load_config(_write_cfg(tmp_path, {}))
    assert cfg["plan_vault_path"] == Path("/opt/TGW/library/plans")
    assert cfg["standalone_plan_root"] == Path("/opt/TGW/library/plans")
    assert cfg["plan_repository_root"] == cfg["standalone_plan_root"]
    assert cfg["plan_inbox_path"] == cfg["plan_vault_path"] / "inbox"
    assert cfg["plan_master_path"] == (
        cfg["standalone_plan_root"] / "plan" / "TGW-Master-Plan.md"
    )
    assert cfg["plan_detail_root"] == cfg["standalone_plan_root"] / "plan" / "pp"
    assert cfg["plan_detail_roots"] == (
        cfg["standalone_plan_root"] / "plan" / "pp",
        cfg["standalone_plan_root"] / "pp",
    )
    assert cfg["plan_update_master_path"] == cfg["plan_master_path"]
    assert cfg["plan_render_root"] == Path("/opt/TGW/var/plan-render")
    assert cfg["sync_conflict_roots"] == [cfg["itemdata_root"]]


def test_legacy_plan_vault_is_filtered_from_operational_sync_scan(tmp_path):
    vault = tmp_path / "legacy-vault"
    catalog = tmp_path / "catalog"
    cfg = load_config(_write_cfg(tmp_path, {
        "plan_vault_path": str(vault),
        "catalog_root": str(catalog),
        "sync_conflict_roots": [str(vault), str(vault / "plan"), str(catalog)],
    }))
    assert cfg["sync_conflict_roots"] == [catalog]


def test_plan_authority_credentials_and_sessions_require_named_principals(tmp_path):
    cfg = load_config(_write_cfg(tmp_path, {
        "plan_authority_executor_credential_env": "TGW_TEST_AUTHORITY_EXECUTOR_TOKEN",
        "plan_authority_executor_principal": "executor:authority-runner",
        "plan_authority_operator_api_principal": "operator:alice",
        "plan_authority_operator_session_principal": "operator:alice",
    }))
    assert cfg["plan_authority_executor_principal"] == "executor:authority-runner"
    assert cfg["plan_authority_operator_api_principal"] == "operator:alice"

    with pytest.raises(ValueError, match="named person or service"):
        load_config(_write_cfg(tmp_path, {"plan_authority_operator_api_principal": "operator:api-key"}))
    with pytest.raises(ValueError, match="configured together"):
        load_config(_write_cfg(tmp_path, {
            "plan_authority_executor_credential_env": "TGW_TEST_AUTHORITY_EXECUTOR_TOKEN",
        }))


def test_runtime_python_does_not_depend_on_legacy_production_source_checkout() -> None:
    source_root = Path(__file__).parents[1] / "src" / "tgw"
    legacy_root = "/opt/TGW/src/" + "trader-grims-warehouse"
    offenders = [
        path.relative_to(source_root)
        for path in source_root.rglob("*.py")
        if legacy_root in path.read_text(encoding="utf-8")
    ]

    assert offenders == []


def test_approved_plan_content_must_be_exact_clean_commit(tmp_path):
    root = tmp_path / "approved"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "fixture@example.invalid"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Fixture"], cwd=root, check=True)
    (root / "plan").mkdir()
    (root / "plan" / "TGW-Master-Plan.md").write_text("approved\n")
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "approved"], cwd=root, check=True)
    approved = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, check=True,
        text=True, capture_output=True,
    ).stdout.strip()
    config_path = _write_cfg(tmp_path, {
        "standalone_plan_root": str(root),
        "plan_repository_root": str(tmp_path / "repository"),
        "plan_approved_commit": approved,
        "plan_approved_solution_hash": "sha256:" + "a" * 64,
        "plan_git_path": "git",
    })

    cfg = load_config(config_path)
    assert cfg["plan_master_path"] == root / "plan" / "TGW-Master-Plan.md"
    assert cfg["plan_update_master_path"] == (
        tmp_path / "repository" / "plan" / "TGW-Master-Plan.md"
    )
    assert load_operational_config(config_path)["plan_approved_commit"] == approved

    (root / "unapproved").write_text("dirty\n")
    with pytest.raises(ValueError, match="not clean"):
        load_config(config_path)


def test_operational_config_rejects_unbound_plan_authority(tmp_path):
    """Only generic library loads may omit immutable Plan approval pins."""
    path = _write_cfg(tmp_path, {})
    assert load_config(path)["plan_approved_commit"] is None
    with pytest.raises(ValueError, match="requires approved Plan commit and solution"):
        load_operational_config(path)


def test_approved_plan_requires_distinct_update_repository(tmp_path):
    root = tmp_path / "approved"
    root.mkdir()
    base = {
        "standalone_plan_root": str(root),
        "plan_approved_commit": "a" * 40,
    }
    with pytest.raises(ValueError, match="plan_repository_root is required"):
        load_config(_write_cfg(tmp_path, base))

    with pytest.raises(ValueError, match="must be distinct"):
        load_config(_write_cfg(tmp_path, {
            **base,
            "plan_repository_root": str(root),
        }))


def test_production_plan_projection_replaces_a_local_plan_checkout(tmp_path):
    protected = tmp_path / "releases"
    protected.mkdir(mode=0o700)
    projection = protected / "plan-projection.json"
    source = Path(__file__).parents[1] / "agent-services/plan-runtime/GOVERNED-EXECUTION-PLATFORM-f0a8cf22.json"
    projection.write_bytes(source.read_bytes())
    projection.chmod(0o400)
    cfg = load_config(_write_cfg(tmp_path, {
        "standalone_plan_root": "/run/tgw/no-local-plan",
        "plan_repository_root": "/run/tgw/no-local-plan",
        "plan_approved_commit": "f0a8cf22b2c7b2f064292a048ffcb8ee98919e99",
        "plan_approved_solution_hash": "sha256:1c3684135769e5dcabcaf130c55df160a4cecc0d3ebcee6ccd129ab97cdd709b",
        "plan_projection_path": str(projection),
        "plan_projection_root": str(protected),
        "plan_projection_trusted_uid": os.getuid(),
    }))
    assert cfg["plan_projection_path"] == projection
    assert cfg["plan_approved_commit"] == "f0a8cf22b2c7b2f064292a048ffcb8ee98919e99"


# ---------------------------------------------------------------------------
# ISS-004 — ebay_sku_migrate in normalised config
# ---------------------------------------------------------------------------


def test_ebay_sku_migrate_present_in_normalised_config(tmp_path):
    """ebay_sku_migrate must be a top-level key in the normalised config dict."""
    cfg_path = _write_cfg(tmp_path, {})
    cfg = load_config(cfg_path)
    assert "ebay_sku_migrate" in cfg


def test_ebay_sku_migrate_defaults_to_empty_dict(tmp_path):
    """When ebay_sku_migrate is absent from JSON, the normalised value is {}."""
    cfg_path = _write_cfg(tmp_path, {})
    cfg = load_config(cfg_path)
    assert cfg["ebay_sku_migrate"] == {}


def test_ebay_sku_migrate_block_surfaced_without_raw(tmp_path):
    """ebay_sku_migrate values are accessible via cfg key, not cfg['raw']."""
    migrate_block = {"enabled": True, "batch_size": 10}
    cfg_path = _write_cfg(tmp_path, {"ebay_sku_migrate": migrate_block})
    cfg = load_config(cfg_path)
    assert cfg["ebay_sku_migrate"] == migrate_block
    assert cfg["ebay_sku_migrate"]["enabled"] is True
    assert cfg["ebay_sku_migrate"]["batch_size"] == 10


def test_pinned_bootstrap_host_integration_is_surfaced_to_the_canonical_host(tmp_path):
    """The HTTP host receives deployment pins without reaching into ``raw``."""
    integration = {"schema": "tgw-pinned-bootstrap-host-integration/v1"}
    cfg = load_config(_write_cfg(tmp_path, {"pinned_bootstrap_host_integration": integration}))
    assert cfg["pinned_bootstrap_host_integration"] == integration
    assert load_config(_write_cfg(tmp_path, {}))["pinned_bootstrap_host_integration"] is None


# ---------------------------------------------------------------------------
# todo #1400 — 'api_key' must always be a key in the normalised config,
# regardless of whether secrets_root/tgw-api-key.json exists (regression for
# the KeyError('api_key') dead-letters fixed by 00cf9274)
# ---------------------------------------------------------------------------


def test_api_key_present_when_secret_file_missing(tmp_path):
    """No tgw-api-key.json on disk — 'api_key' key must still exist (empty),
    never be absent from the dict. This is the exact historic bug: fence.py's
    _headers() does cfg['api_key'] unconditionally, so a missing DICT KEY
    (not just an empty value) raised KeyError for every fence call made with
    this cfg."""
    cfg_path = _write_cfg(tmp_path, {})
    cfg = load_config(cfg_path)
    assert "api_key" in cfg
    assert cfg["api_key"] == ""
    assert cfg["machine_api_key"] == ""


def test_api_key_loaded_when_secret_file_present(tmp_path):
    """When the key file exists, its 'api_key' value is surfaced onto cfg."""
    secrets_root = tmp_path / "secrets"
    secrets_root.mkdir(parents=True, exist_ok=True)
    (secrets_root / "tgw-api-key.json").write_text(json.dumps({
        "api_key": "sekrit-123",
        "machine_api_key": "machine-sekrit-456",
    }))
    cfg_path = _write_cfg(tmp_path, {})
    cfg = load_config(cfg_path)
    assert cfg["api_key"] == "sekrit-123"
    assert cfg["machine_api_key"] == "machine-sekrit-456"


def test_api_key_present_when_secret_file_malformed(tmp_path):
    """A malformed/unreadable key file must degrade to empty string, never
    an absent dict key or an unhandled exception (pre-existing tolerant
    behavior around the read, unrelated to the key-presence fix itself)."""
    secrets_root = tmp_path / "secrets"
    secrets_root.mkdir(parents=True, exist_ok=True)
    (secrets_root / "tgw-api-key.json").write_text("{not valid json")
    cfg_path = _write_cfg(tmp_path, {})
    cfg = load_config(cfg_path)
    assert "api_key" in cfg
    assert cfg["api_key"] == ""
    assert cfg["machine_api_key"] == ""
