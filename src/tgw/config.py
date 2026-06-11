"""
tgw.config — Config loading and canonical path resolution.

All code that needs TGW paths imports from here.  Nothing constructs
/opt/TGW paths by hand.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Set

DEFAULT_CONFIG = Path("/opt/TGW/config/tgw-api-config.json")


# ---------------------------------------------------------------------------
# JSON helpers used by config loading
# ---------------------------------------------------------------------------


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
    itemdata_root = p("itemdata_root", "/opt/TGW/data/ItemData")
    catalog_root = p("catalog_root", "/opt/TGW/data/ItemCatalog")
    incoming_path = p("incoming_path", "/opt/TGW/incoming")
    plan_vault_path = p("plan_vault_path", "/opt/TGW/src/trader-grims-warehouse/docs/TGW-Plan-Vault")

    full_catalog_path = p("full_catalog_path", str(catalog_root / "tgwcatalog.json"))
    search_catalog_path = p("search_catalog_path", str(catalog_root / "search-catalog.json"))
    location_tree_root = p("location_tree_root", str(catalog_root / "by-location"))
    full_catalog_csv_path = p("full_catalog_csv_path", str(catalog_root / "tgwcatalog.csv"))
    search_catalog_csv_path = p("search_catalog_csv_path", str(catalog_root / "searchcatalog.csv"))
    sqlite_catalog_path = p("sqlite_catalog_path", str(catalog_root / "tgwcatalog.db"))
    thumbnail_root = p("thumbnail_root", str(catalog_root / "thumbnails"))
    fingerprint_index_path = p("fingerprint_index_path", str(catalog_root / "fingerprints.db"))

    models_config_path = p("models_config_path", "/opt/TGW/config/tgw-models.json")

    ebay_token_path = secrets_root / "ebay-token.json"
    ebay_credentials_path = secrets_root / "ebay-credentials.json"
    openrouter_credentials_path = secrets_root / "openrouter-credentials.json"
    ebay_draft_csv_path = p("ebay_draft_csv_path", str(catalog_root / "ebay-draft-offline.csv"))

    postgres_dsn = raw.get("postgres_dsn", "dbname=state_machine user=tgw")

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
        "openrouter_credentials_path": openrouter_credentials_path,
        "ebay_draft_csv_path": ebay_draft_csv_path,
        "alt_text_provider": raw.get("alt_text_provider", "openrouter"),
        "alt_text_model": raw.get("alt_text_model", "google/gemini-2.5-flash"),
        "postgres_dsn": postgres_dsn,
        "itemdata_root": itemdata_root,
        "catalog_root": catalog_root,
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
        "reprice_stages": reprice_stages,
        "category_price_defaults": category_price_defaults,
        "category_groups_path": category_groups_path,
        "fulfillment_policy_id": fulfillment_policy_id,
        "payment_policy_id": payment_policy_id,
        "return_policy_id": return_policy_id,
        "fulfillment_policy_by_category": fulfillment_policy_by_category,
        "store_category_by_ebay_category": store_category_by_ebay_category,
        "ebay_sku_migrate": raw.get("ebay_sku_migrate", {}),
        "raw": raw,
    }


# ---------------------------------------------------------------------------
# Canonical path helpers — the only place paths are constructed
# ---------------------------------------------------------------------------


def sku_dir(cfg: Dict[str, Any], sku: str) -> Path:
    """Canonical directory for a SKU."""
    return cfg["itemdata_root"] / sku


def sku_json(cfg: Dict[str, Any], sku: str) -> Path:
    """Canonical JSON file path for a SKU."""
    return sku_dir(cfg, sku) / f"{sku}.json"


def sku_exists(cfg: Dict[str, Any], sku: str) -> bool:
    """True if the canonical JSON file for this SKU exists."""
    return sku_json(cfg, sku).exists()


def location_dir(cfg: Dict[str, Any], location: str) -> Path:
    """Canonical location directory in the symlink tree."""
    return cfg["location_tree_root"] / location


def queue_dir(cfg: Dict[str, Any], queue_name: str) -> Path:
    """Canonical path for a named queue directory."""
    runtime_root = Path(cfg["raw"].get("runtime_root", "/opt/TGW/runtime"))
    return runtime_root / "state" / "queues" / queue_name


def context_state_path(cfg: Dict[str, Any]) -> Path:
    """Canonical path for the current-item context state file."""
    runtime_root = Path(cfg["raw"].get("runtime_root", "/opt/TGW/runtime"))
    return runtime_root / "state" / "current-item.json"
