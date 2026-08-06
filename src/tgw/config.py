"""
tgw.config — Config loading and canonical path resolution.

All code that needs TGW paths imports from here.  Nothing constructs
/opt/TGW paths by hand.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, Set

DEFAULT_CONFIG = Path("/opt/TGW/config/tgw-api-config.json")


# ---------------------------------------------------------------------------
# JSON helpers used by config loading
# ---------------------------------------------------------------------------


def _load_secrets_env(secrets_root: Path) -> None:
    """Source secrets_root/tgw.env into the process environment — the
    single facility for provider API keys (Dave, 2026-07-09). Plain
    KEY=value lines, '#' comments allowed. Real environment variables
    always win (setdefault only) so a one-off shell export still overrides
    the file without editing it."""
    env_path = secrets_root / 'tgw.env'
    try:
        if not env_path.exists():
            return
        for line in env_path.read_text(encoding='utf-8').splitlines():
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, _, value = line.partition('=')
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key:
                os.environ.setdefault(key, value)
    except OSError:
        pass


def load_json_strict(path: Path) -> Any:
    """Load JSON, raising ValueError on duplicate keys."""

    def hook(pairs):
        out: Dict[str, Any] = {}
        seen: Set[str] = set()
        for k, v in pairs:
            if k in seen:
                raise ValueError(f"duplicate key {k!r} in {path}")
            seen.add(k)
            out[k] = v
        return out

    with path.open("r", encoding="utf-8") as f:
        return json.load(f, object_pairs_hook=hook)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def load_config(path: Path) -> Dict[str, Any]:
    """
    Load and normalise the TGW config file.

    All paths are resolved to absolute Path objects.  Missing keys fall back
    to well-known defaults so callers never need to guard for absence.
    """
    raw: Dict[str, Any] = load_json_strict(path) if path.exists() else {}

    def p(key: str, default: str) -> Path:
        return Path(os.path.expanduser(raw.get(key, default)))

    secrets_root = p("secrets_root", "/opt/TGW/secrets")
    _load_secrets_env(secrets_root)
    itemdata_root = p("itemdata_root", "/opt/TGW/data/ItemData")
    catalog_root = p("catalog_root", "/opt/TGW/data/ItemCatalog")
    archive_root = p("archive_root", "/opt/TGW/data/ItemArchive")
    incoming_path = p("incoming_path", "/opt/TGW/incoming")
    plan_vault_path = p("plan_vault_path", "/opt/TGW/src/trader-grims-warehouse/docs/TGW-Plan-Vault")

    full_catalog_path = p("full_catalog_path", str(catalog_root / "master-catalog.json"))
    search_catalog_path = p("search_catalog_path", str(catalog_root / "search-catalog.json"))
    location_tree_root = p("location_tree_root", str(catalog_root / "by-location"))
    full_catalog_csv_path = p("full_catalog_csv_path", str(catalog_root / "tgwcatalog.csv"))
    search_catalog_csv_path = p("search_catalog_csv_path", str(catalog_root / "searchcatalog.csv"))
    sqlite_catalog_path = p("sqlite_catalog_path", str(catalog_root / "tgwcatalog.db"))
    thumbnail_root = p("thumbnail_root", str(catalog_root / "thumbnails"))
    fingerprint_index_path = p("fingerprint_index_path", str(catalog_root / "fingerprints.db"))

    models_config_path = p("models_config_path", "/opt/TGW/config/tgw-models.json")

    # PP-PYIPC-001 — Syncthing integration
    syncthing_config_path = p("syncthing_config_path", "/opt/TGW/.local/syncthing/config.xml")
    syncthing_url = raw.get("syncthing_url", "http://127.0.0.1:8384")
    # PP-PORTABLE-CATALOG-001 P2 — Syncthing folder ID to rescan after export-catalog --push
    catalog_export_folder_id: str = raw.get("catalog_export_folder_id", "")
    # PP-CAPTURE-001 P2 — KDE Connect device ID for quiet-check push notification
    kdeconnect_device_id: str = raw.get("kdeconnect_device_id", "")

    # PP-PORTABLE-CATALOG-001 P3 — sync-conflict scan roots (default: vault + itemdata)
    _raw_sync_roots = raw.get("sync_conflict_roots")
    sync_conflict_roots: list = (
        [Path(os.path.expanduser(r)) for r in _raw_sync_roots]
        if _raw_sync_roots is not None
        else [plan_vault_path, itemdata_root]
    )

    ebay_token_path = secrets_root / "ebay-token.json"
    ebay_credentials_path = secrets_root / "ebay-credentials.json"

    _api_key_path = secrets_root / "tgw-api-key.json"
    _api_key = ""
    try:
        _api_key_present = _api_key_path.exists()
    except PermissionError:
        # secrets_root is 700 tgw:tgw — a non-tgw caller (e.g. `tgw clip`,
        # which the nix wrapper runs as the operator's own user, not tgw)
        # can't even stat() inside it. Treat as absent, same as a missing
        # key. Scoped to PermissionError only (code-review fix) — a
        # transient I/O error here (e.g. a flaky network-mounted
        # secrets_root) should not look identical to "key not present".
        _api_key_present = False
    if _api_key_present:
        try:
            _api_key = json.loads(_api_key_path.read_text(encoding="utf-8"))["api_key"]
        except Exception:
            # Malformed/unreadable key file — pre-existing tolerant
            # behavior, unrelated to the permission fix above.
            pass
    ebay_draft_csv_path = p("ebay_draft_csv_path", str(catalog_root / "ebay-draft-offline.csv"))

    postgres_dsn = raw.get("postgres_dsn", "dbname=state_machine user=tgw")

    # PP-BACKUP-001 — backup infrastructure paths (not in JSON config; fixed layout)
    backup_db_dir = Path('/opt/TGW/var/backups/trader_grims_warehouse/db')
    # backup_snapshot_root moved 2026-07-04 onto a genuinely separate physical
    # drive (/dev/sdc1, LABEL=tgw-db-backup) — the old path was on the same
    # nvme0n1p3 filesystem as backup_db_dir, a single point of failure this
    # tree was supposed to protect against.
    backup_snapshot_root = Path('/opt/TGW/mnt/tgw-db-backup/trader_grims_warehouse')
    backup_secrets_dir = backup_snapshot_root / 'secrets'
    backup_rclone_stamp = Path('/opt/TGW/var/log/rclone-sync-last-success')

    reprice_stages = raw.get(
        "reprice_stages",
        [
            {"days": 0, "percentile": "max", "label": "launch"},
            {"days": 3, "percentile": "p75", "label": "retail"},
            {"days": 17, "percentile": "p25", "label": "move"},
        ],
    )
    category_price_defaults: Dict[str, float] = {str(k): float(v) for k, v in raw.get("category_price_defaults", {}).items()}
    category_groups_path = p(
        "category_groups_path",
        str(Path(path).parent / "category-groups.json"),
    )
    fulfillment_policy_id = raw.get("fulfillment_policy_id")
    payment_policy_id = raw.get("payment_policy_id")
    return_policy_id = raw.get("return_policy_id")
    # PP-FREESHIP-001 — free shipping mode
    free_shipping_enabled: bool = bool(raw.get("free_shipping_enabled", False))
    default_shipping_cost: float = float(raw.get("default_shipping_cost", 0.0))
    fulfillment_policy_free_shipping = raw.get("fulfillment_policy_free_shipping")
    fulfillment_policy_by_category: Dict[str, str] = {str(k): str(v) for k, v in raw.get("fulfillment_policy_by_category", {}).items()}
    store_category_by_ebay_category: Dict[str, Any] = raw.get("store_category_by_ebay_category", {})

    search_fields = raw.get("search_catalog_fields", ["title", "location", "#STATUS", "status"])
    required = raw.get("search_catalog_required", ["sku"])
    pretty = bool(raw.get("pretty_json", True))
    skip_missing = bool(raw.get("skip_missing_files", True))
    thumbnail_size = raw.get("thumbnail_size", [256, 256])

    models: dict = {}
    if models_config_path.exists():
        try:
            raw_models = load_json_strict(models_config_path)
            models = {k: v for k, v in raw_models.items() if not k.startswith('_')}
        except Exception:
            pass

    return {
        "config_path": path,
        "models_config_path": models_config_path,
        "models": models,
        "secrets_root": secrets_root,
        "ebay_token_path": ebay_token_path,
        "ebay_credentials_path": ebay_credentials_path,
        "ebay_draft_csv_path": ebay_draft_csv_path,
        "api_key": _api_key,
        "postgres_dsn": postgres_dsn,
        "itemdata_root": itemdata_root,
        "catalog_root": catalog_root,
        "archive_root": archive_root,
        "full_catalog_path": full_catalog_path,
        "search_catalog_path": search_catalog_path,
        "full_catalog_csv_path": full_catalog_csv_path,
        "search_catalog_csv_path": search_catalog_csv_path,
        "location_tree_root": location_tree_root,
        "sqlite_catalog_path": sqlite_catalog_path,
        "thumbnail_root": thumbnail_root,
        "fingerprint_index_path": fingerprint_index_path,
        "thumbnail_size": thumbnail_size,
        "search_fields": ["sku", *[f for f in search_fields if f != "sku"]],
        "required": required,
        "pretty": pretty,
        "skip_missing": skip_missing,
        "incoming_path": incoming_path,
        "newitems_path": incoming_path / "newitems",
        "plan_vault_path": plan_vault_path,
        "plan_inbox_path": plan_vault_path / "inbox",
        "plan_master_path": plan_vault_path / "plan" / "TGW-Master-Plan.md",
        "pm_intake_delay_hours": float(raw.get("pm_intake_delay_hours", 4.0)),
        # PP-EDITOR-001 ready-state dole-out: publish pool/divisor items per cycle
        "dole_interval_s": int(raw.get("dole_interval_s", 3600)),
        "dole_divisor": int(raw.get("dole_divisor", 60)),
        # PP-DEADLETTER-001 zero-work watchdog: warn when a live worker completes
        # nothing for this long while eligible jobs wait
        "zero_work_stall_hours": float(raw.get("zero_work_stall_hours", 4.0)),
        "reprice_stages": reprice_stages,
        "category_price_defaults": category_price_defaults,
        "category_groups_path": category_groups_path,
        "fulfillment_policy_id": fulfillment_policy_id,
        "payment_policy_id": payment_policy_id,
        "return_policy_id": return_policy_id,
        "free_shipping_enabled": free_shipping_enabled,
        "default_shipping_cost": default_shipping_cost,
        "fulfillment_policy_free_shipping": fulfillment_policy_free_shipping,
        "fulfillment_policy_by_category": fulfillment_policy_by_category,
        "store_category_by_ebay_category": store_category_by_ebay_category,
        "ebay_sku_migrate": raw.get("ebay_sku_migrate", {}),
        "sync_conflict_roots": sync_conflict_roots,
        "syncthing_config_path": syncthing_config_path,
        "syncthing_url": syncthing_url,
        "catalog_export_folder_id": catalog_export_folder_id,
        "kdeconnect_device_id": kdeconnect_device_id,
        "backup_db_dir": backup_db_dir,
        "backup_snapshot_root": backup_snapshot_root,
        "backup_secrets_dir": backup_secrets_dir,
        "backup_rclone_stamp": backup_rclone_stamp,
        # Coding workers consume this normalized section directly.  Retain
        # ``raw`` below for compatibility with callers needing other custom
        # configuration.
        "coding": raw.get("coding", {}),
        "raw": raw,
    }


def load_coding_worker_config(path: Path) -> Dict[str, Any]:
    """Return the supported normalized config contract for coding workers.

    Coding workers must not reach back into ``raw``: that compatibility
    payload is intentionally not a worker configuration interface.  Keeping
    this small loader beside ``load_config`` makes the contract testable and
    preserves the validated normalized ``coding`` section.
    """
    config = load_config(path)
    coding = config.get("coding")
    if not isinstance(coding, dict):
        raise ValueError("coding configuration must be an object")
    config["coding"] = dict(coding)
    return config


# ---------------------------------------------------------------------------
# Canonical path helpers — the only place paths are constructed
# ---------------------------------------------------------------------------

_SAFE_SEGMENT_RE = re.compile(r'^[A-Za-z0-9_.-]+$')


def _safe_segment(root: Path, name: str, kind: str) -> Path:
    """Join *name* under *root* as a single path segment, raising
    ValueError if it isn't a safe, contained segment."""
    if not name or name in ('.', '..') or not _SAFE_SEGMENT_RE.match(name):
        raise ValueError(f"unsafe {kind} value: {name!r}")
    candidate = (root / name).resolve()
    root_resolved = root.resolve()
    if candidate != root_resolved and root_resolved not in candidate.parents:
        raise ValueError(f"{kind} {name!r} escapes {root}")
    return candidate


def sku_dir(cfg: Dict[str, Any], sku: str) -> Path:
    """Canonical directory for a SKU."""
    return _safe_segment(cfg["itemdata_root"], sku, "sku")


def sku_json(cfg: Dict[str, Any], sku: str) -> Path:
    """Canonical JSON file path for a SKU."""
    return sku_dir(cfg, sku) / f"{sku}.json"


def sku_exists(cfg: Dict[str, Any], sku: str) -> bool:
    """True if the canonical JSON file for this SKU exists."""
    return sku_json(cfg, sku).exists()


def location_dir(cfg: Dict[str, Any], location: str) -> Path:
    """Canonical location directory in the symlink tree."""
    return _safe_segment(cfg["location_tree_root"], location, "location")


def queue_dir(cfg: Dict[str, Any], queue_name: str) -> Path:
    """Canonical path for a named queue directory."""
    runtime_root = Path(cfg["raw"].get("runtime_root", "/opt/TGW/runtime"))
    return runtime_root / "state" / "queues" / queue_name


def context_state_path(cfg: Dict[str, Any]) -> Path:
    """Canonical path for the current-item context state file."""
    runtime_root = Path(cfg["raw"].get("runtime_root", "/opt/TGW/runtime"))
    return runtime_root / "state" / "current-item.json"
