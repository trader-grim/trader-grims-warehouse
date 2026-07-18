#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# PP-FENCE-001 gap (audit#1143 #1235): scrub_itemdata applies recursive,
# pattern-based key removal across the whole doc. items.strip_fields()
# only removes a flat list of top-level field names in one call — it does
# not recurse into nested dicts/lists — so it cannot express this rule
# set (the production denylist has recursive=true, a real live-used
# capability, not dead code). This worker keeps writing via
# atomic_write_json directly (same documented gap class as
# multi_intake.py's key-deletion write) rather than a fence redesign.
# Path construction and reads, however, DO have canonical fence
# equivalents and are routed through them (audit#COHESION-2026-07 #1305).
from tgw import config as tgw_config
from tgw.items import atomic_write_json
from tgw.logging import announce_script_run
from tgw.resolver import find_current_sku, load_item_doc

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ScrubRules:
    recursive: bool = True
    remove_patterns: tuple[str, ...] = ()
    remove_keys: tuple[str, ...] = ()
    preserve_keys: tuple[str, ...] = ()


def log_item_debug(sku: str, path: Path | str, payload: Any = None, stdout: str = None, stderr: str = None, reason: str = None) -> None:
    print(f"SKU: {sku}", flush=True)
    print(f"Path: {path}", flush=True)
    if payload is not None:
        print(f"JSON: {payload}", flush=True)
    if stdout:
        print(f"STDOUT: {stdout}", flush=True)
    if stderr:
        print(f"STDERR: {stderr}", flush=True)
    if reason:
        print(f"Reason: {reason}", flush=True)


def load_config(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding='utf-8'))


def load_rules(cfg: dict[str, Any]) -> ScrubRules:
    return ScrubRules(
        recursive=bool(cfg.get('recursive', True)),
        remove_patterns=tuple(cfg.get('remove_patterns', [])),
        remove_keys=tuple(cfg.get('remove_keys', [])),
        preserve_keys=tuple(cfg.get('preserve_keys', [])),
    )


def _should_remove_key(key: str, rules: ScrubRules) -> bool:
    if key in rules.preserve_keys:
        return False
    if key in rules.remove_keys:
        return True
    return any(re.search(pattern, key) for pattern in rules.remove_patterns)


def scrub_value(value: Any, rules: ScrubRules, removed: list[str], prefix: str = '') -> Any:
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            key = str(k)
            path = f'{prefix}.{key}' if prefix else key
            if _should_remove_key(key, rules):
                removed.append(path)
                continue
            out[k] = scrub_value(v, rules, removed, path) if rules.recursive else v
        return out
    if isinstance(value, list):
        return [scrub_value(v, rules, removed, prefix) if rules.recursive else v for v in value]
    return value


def scrub_itemdata(data: dict[str, Any], rules: ScrubRules) -> tuple[dict[str, Any], list[str]]:
    removed: list[str] = []
    cleaned = scrub_value(data, rules, removed)
    return cleaned, removed


def derive_item_path(itemdata_root: Path, sku: str) -> Path:
    """Canonical item JSON path for *sku* under *itemdata_root*.

    Delegates to config.sku_json() — the single shared path-building
    helper used everywhere else in the fence — instead of hand-joining
    path components (audit#COHESION-2026-07 #1305). Raises ValueError for
    an unsafe sku (audit#1143 #1171, finding 11: SKU taken from job
    content had no validation at all) — the same canonical
    config._safe_segment() validator every other fence path helper relies
    on, rather than a locally duplicated character check. Callers must
    catch ValueError."""
    return tgw_config.sku_json({"itemdata_root": Path(itemdata_root)}, sku)


def process_queue_job(job_file: Path, rules: ScrubRules, default_root: Path,
                       archive_root: Path | None = None) -> bool:
    sku = job_file.name
    if sku.endswith('.json'):
        sku = sku[:-5]

    # root_dir is always the configured default_root — a queue job must
    # never be able to redirect writes elsewhere (audit#1143 #1171, finding
    # 11: job content used to be able to set an arbitrary 'root'/
    # 'itemdata_root', bypassing the fence's single canonical ItemData root
    # entirely).
    root_dir = default_root

    try:
        # Protect against completely empty or zero-byte queue files
        if job_file.stat().st_size == 0:
            logger.warning(f"Queue job file {job_file.name} is empty (0 bytes). Using filename as SKU fallback.")
        else:
            job_content = job_file.read_text(encoding='utf-8').strip()
            if job_content:
                try:
                    msg = json.loads(job_content)
                    if isinstance(msg, dict):
                        sku = msg.get("sku") or msg.get("SKU") or sku
                except json.JSONDecodeError:
                    if len(job_content) < 64 and '\n' not in job_content:
                        sku = job_content

        try:
            file_path = derive_item_path(root_dir, sku)
        except ValueError:
            logger.error(f"Rejecting unsafe SKU {sku!r} from job file {job_file.name}")
            return False

        if not file_path.exists():
            # Fence-consistent alias fallback (audit#COHESION-2026-07
            # #1305, mirrors revision.py #1313/#1316 and mcp_server.py
            # #1312 in the same batch): a job may still reference an
            # item's OLD sku after a rename, resolve it via the sku_old
            # index before concluding "not found."
            cfg = {"itemdata_root": Path(root_dir)}
            current = find_current_sku(cfg, sku)
            if current:
                try:
                    file_path = derive_item_path(root_dir, current)
                except ValueError:
                    logger.error(f"Rejecting unsafe resolved SKU {current!r} from job file {job_file.name}")
                    return False
            if not file_path.exists():
                logger.error(f"Target data file for SKU {sku} not found at {file_path}")
                return False

        # Safe read of the actual item JSON data file
        if file_path.stat().st_size == 0:
            logger.error(f"Target data file {file_path} is completely empty. Skipping.")
            return False

        item_data = load_item_doc(file_path)
        cleaned_data, removed_paths = scrub_itemdata(item_data, rules)

        atomic_write_json(file_path, cleaned_data, archive_root=archive_root,
                         sort_keys=True)
        logger.info(f"Successfully scrubbed SKU {sku}. Removed {len(removed_paths)} paths.")
        return True

    except json.JSONDecodeError as je:
        logger.error(f"Skipping job {job_file.name}: JSON syntax error encountered while reading contents: {je}")
        return False
    except Exception as e:
        logger.exception(f"Failed to process queue job file {job_file.name}: {e}")
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description="TGW Queue Worker: Item Data Scrubbing Batch Run")
    parser.add_argument("--config", type=Path, required=True, help="Path to worker configuration.")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    announce_script_run(
        'itemdata_scrub.py (workers/ file-queue batch variant)',
        'sweep and process file-based itemdata_scrub queue jobs from cwd (not systemd-scheduled — ad hoc batch run)',
        config=str(args.config),
    )

    try:
        cfg = load_config(args.config)
        rules = load_rules(cfg)
        default_root = Path(cfg.get("itemdata_root", "/opt/TGW/data/items"))
        archive_root = Path(cfg["archive_root"]) if cfg.get("archive_root") else None

        queue_dir = Path.cwd()
        job_files = [f for f in queue_dir.iterdir() if f.is_file() and not f.name.startswith('.')]

        if not job_files:
            logger.info("No job files found in queue directory.")
            return

        logger.info(f"Processing batch of {len(job_files)} queue files...")
        for job_file in sorted(job_files):
            success = process_queue_job(job_file, rules, default_root, archive_root)
            if success:
                job_file.unlink()

        logger.info("Queue batch sweep finished.")

    except Exception:
        logger.exception("Fatal error in batch execution sequence")
        sys.exit(1)


if __name__ == "__main__":
    main()
