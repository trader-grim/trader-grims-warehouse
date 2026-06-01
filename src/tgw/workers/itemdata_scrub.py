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
    return Path(itemdata_root) / sku / f"{sku}.json"


def process_queue_job(job_file: Path, rules: ScrubRules, default_root: Path) -> bool:
    sku = job_file.name
    if sku.endswith('.json'):
        sku = sku[:-5]

    try:
        # Protect against completely empty or zero-byte queue files
        if job_file.stat().st_size == 0:
            logger.warning(f"Queue job file {job_file.name} is empty (0 bytes). Using filename as SKU fallback.")
            root_dir = default_root
        else:
            job_content = job_file.read_text(encoding='utf-8').strip()
            root_dir = default_root
            if job_content:
                try:
                    msg = json.loads(job_content)
                    if isinstance(msg, dict):
                        sku = msg.get("sku") or msg.get("SKU") or sku
                        root_dir = Path(msg.get("root", msg.get("itemdata_root", default_root)))
                except json.JSONDecodeError:
                    if len(job_content) < 64 and '\n' not in job_content:
                        sku = job_content

        file_path = derive_item_path(root_dir, sku)

        if not file_path.exists():
            logger.error(f"Target data file for SKU {sku} not found at {file_path}")
            return False

        # Safe read of the actual item JSON data file
        if file_path.stat().st_size == 0:
            logger.error(f"Target data file {file_path} is completely empty. Skipping.")
            return False

        item_data = json.loads(file_path.read_text(encoding='utf-8'))
        cleaned_data, removed_paths = scrub_itemdata(item_data, rules)

        file_path.write_text(json.dumps(cleaned_data, indent=2, sort_keys=True) + '\n', encoding='utf-8')
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

    try:
        cfg = load_config(args.config)
        rules = load_rules(cfg)
        default_root = Path(cfg.get("itemdata_root", "/opt/TGW/data/items"))

        queue_dir = Path.cwd()
        job_files = [f for f in queue_dir.iterdir() if f.is_file() and not f.name.startswith('.')]

        if not job_files:
            logger.info("No job files found in queue directory.")
            return

        logger.info(f"Processing batch of {len(job_files)} queue files...")
        for job_file in sorted(job_files):
            success = process_queue_job(job_file, rules, default_root)
            if success:
                job_file.unlink()

        logger.info("Queue batch sweep finished.")

    except Exception as e:
        logger.exception("Fatal error in batch execution sequence")
        sys.exit(1)


if __name__ == "__main__":
    main()
