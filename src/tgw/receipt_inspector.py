"""Supported, read-only inspection of configured production receipt sinks."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Mapping

from tgw.config import DEFAULT_CONFIG, load_operational_config

SCHEMA = "tgw-receipt-inspection-config/v1"
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,191}\Z")
_MAX_RECEIPT_BYTES = 8 * 1024 * 1024


class ReceiptInspectionError(ValueError):
    """A configured sink or selected receipt cannot be inspected safely."""


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False,
    ).encode()


def _hash_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _root(config: Mapping[str, Any], root_id: str) -> Path:
    raw = config.get("receipt_inspection")
    if (
        not isinstance(raw, Mapping)
        or set(raw) != {"schema", "roots"}
        or raw.get("schema") != SCHEMA
        or not isinstance(raw.get("roots"), Mapping)
    ):
        raise ReceiptInspectionError("receipt inspection configuration is invalid")
    if not isinstance(root_id, str) or _ID.fullmatch(root_id) is None:
        raise ReceiptInspectionError("receipt root identity is invalid")
    selected = raw["roots"].get(root_id)
    if not isinstance(selected, str):
        raise ReceiptInspectionError("receipt root is not registered")
    path = Path(selected)
    if (
        not path.is_absolute()
        or path == Path("/tmp")
        or Path("/tmp") in path.parents
        or path.is_symlink()
        or not path.is_dir()
    ):
        raise ReceiptInspectionError("receipt root is unavailable or not durable")
    return path.resolve(strict=True)


def _receipt_path(root: Path, receipt_id: str) -> Path:
    if not isinstance(receipt_id, str) or _ID.fullmatch(receipt_id) is None:
        raise ReceiptInspectionError("receipt identity is invalid")
    path = root / f"{receipt_id}.json"
    if path.is_symlink() or not path.is_file() or path.parent.resolve(strict=True) != root:
        raise ReceiptInspectionError("receipt is unavailable")
    if path.stat().st_size > _MAX_RECEIPT_BYTES:
        raise ReceiptInspectionError("receipt exceeds the inspection size limit")
    return path


def _load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReceiptInspectionError("receipt is not valid JSON") from exc
    if not isinstance(value, dict):
        raise ReceiptInspectionError("receipt is not an object")
    return value


def _summary(receipt_id: str, value: Mapping[str, Any], raw: bytes) -> dict[str, Any]:
    claimed = value.get("receipt_hash")
    body = dict(value)
    body.pop("receipt_hash", None)
    hash_valid = None
    if claimed is not None:
        hash_valid = isinstance(claimed, str) and claimed == _hash_bytes(_canonical(body))
    status = next(
        (value.get(field) for field in ("status", "outcome", "decision") if isinstance(value.get(field), str)),
        "UNKNOWN",
    )
    return {
        "id": receipt_id,
        "schema": value.get("schema"),
        "status": status,
        "content_sha256": _hash_bytes(raw),
        "claimed_receipt_hash": claimed,
        "receipt_hash_valid": hash_valid,
    }


def inspect_receipt(config: Mapping[str, Any], *, root_id: str, receipt_id: str) -> dict[str, Any]:
    root = _root(config, root_id)
    path = _receipt_path(root, receipt_id)
    raw = path.read_bytes()
    value = _load(path)
    return {
        "schema": "tgw-receipt-inspection-result/v1",
        "root_id": root_id,
        "summary": _summary(receipt_id, value, raw),
        "receipt": value,
    }


def list_receipts(config: Mapping[str, Any], *, root_id: str) -> dict[str, Any]:
    root = _root(config, root_id)
    summaries = []
    for path in sorted(root.glob("*.json"), key=lambda item: item.name):
        receipt_id = path.stem
        if _ID.fullmatch(receipt_id) is None or path.is_symlink() or path.stat().st_size > _MAX_RECEIPT_BYTES:
            continue
        raw = path.read_bytes()
        try:
            value = _load(path)
        except ReceiptInspectionError:
            summaries.append({
                "id": receipt_id, "schema": None, "status": "INVALID",
                "content_sha256": _hash_bytes(raw), "claimed_receipt_hash": None,
                "receipt_hash_valid": False,
            })
        else:
            summaries.append(_summary(receipt_id, value, raw))
    return {
        "schema": "tgw-receipt-inspection-list/v1",
        "root_id": root_id,
        "receipts": summaries,
    }


def main() -> int:
    parser = argparse.ArgumentParser(prog="tgw-receipt-inspect")
    parser.add_argument("--config", type=Path, default=Path(os.environ.get("TGW_CONFIG", DEFAULT_CONFIG)))
    parser.add_argument("--root", required=True, dest="root_id")
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--list", action="store_true")
    selection.add_argument("--receipt")
    args = parser.parse_args()
    try:
        config = load_operational_config(args.config)
        result = (
            list_receipts(config, root_id=args.root_id)
            if args.list
            else inspect_receipt(config, root_id=args.root_id, receipt_id=args.receipt)
        )
    except (OSError, ValueError) as exc:
        print(json.dumps({"schema": "tgw-receipt-inspection-error/v1", "status": "HOLD", "reason": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
