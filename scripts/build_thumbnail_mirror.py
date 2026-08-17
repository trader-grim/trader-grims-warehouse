#!/usr/bin/env python3
"""Build a development thumbnail mirror of an ItemData media tree.

The mirror preserves the SKU directory and complete relative filename.
``ItemData/<SKU>/photo.png`` becomes ``<destination>/<SKU>/photo.png.jpg``;
appending ``.jpg`` prevents collisions with ``photo.jpg`` in the same SKU.
It never modifies ItemData or deletes destination files.  Source and
destination are deliberately required so this has no production-config default.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterator

try:
    from PIL import Image, UnidentifiedImageError
except ImportError as exc:  # pragma: no cover
    raise SystemExit("Pillow is required: install the project's thumbnails extra") from exc


IMAGE_SUFFIXES = {".avif", ".bmp", ".gif", ".heic", ".jpeg", ".jpg", ".png", ".tif", ".tiff", ".webp"}


@dataclass
class MirrorRecord:
    sku: str
    source: str
    destination: str
    action: str
    source_sha256: str | None = None
    thumbnail_sha256: str | None = None
    source_size: int | None = None
    thumbnail_size: int | None = None
    width: int | None = None
    height: int | None = None
    error: str | None = None


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def iter_source_images(source_root: Path) -> Iterator[tuple[str, Path, Path]]:
    """Yield ``(sku, image, path_relative_to_sku)`` without following links."""
    for sku_dir in sorted(source_root.iterdir()):
        if not sku_dir.is_dir() or sku_dir.is_symlink():
            continue
        for candidate in sorted(sku_dir.rglob("*")):
            if candidate.is_file() and not candidate.is_symlink() and candidate.suffix.lower() in IMAGE_SUFFIXES:
                yield sku_dir.name, candidate, candidate.relative_to(sku_dir)


def destination_for(destination_root: Path, sku: str, source_relative: Path) -> Path:
    return destination_root / sku / source_relative.parent / f"{source_relative.name}.jpg"


def write_thumbnail(source: Path, destination: Path, *, max_size: tuple[int, int], quality: int) -> tuple[int, int]:
    """Write a JPEG atomically beside *destination*, never under /tmp."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{destination.name}.", suffix=".partial", dir=destination.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as output, Image.open(source) as image:
            image.thumbnail(max_size, Image.LANCZOS)
            rgb = image.convert("RGB")
            rgb.save(output, "JPEG", quality=quality, optimize=True)
            output.flush()
            os.fsync(output.fileno())
            dimensions = rgb.size
        os.replace(temporary, destination)
        return dimensions
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def build_mirror(source_root: Path, destination_root: Path, *, max_size: tuple[int, int], quality: int, force: bool, dry_run: bool, limit: int | None) -> list[MirrorRecord]:
    records: list[MirrorRecord] = []
    for sku, source, relative in iter_source_images(source_root):
        if limit is not None and len(records) >= limit:
            break
        destination = destination_for(destination_root, sku, relative)
        record = MirrorRecord(sku=sku, source=str(source), destination=str(destination), action="")
        try:
            source_stat = source.stat()
            record.source_size = source_stat.st_size
            if destination.exists() and not force and destination.stat().st_mtime_ns >= source_stat.st_mtime_ns:
                record.action = "skipped_up_to_date"
            elif dry_run:
                record.action = "would_generate"
            else:
                record.width, record.height = write_thumbnail(source, destination, max_size=max_size, quality=quality)
                record.action = "generated"
                record.source_sha256 = sha256_file(source)
                record.thumbnail_sha256 = sha256_file(destination)
                record.thumbnail_size = destination.stat().st_size
        except (OSError, UnidentifiedImageError, ValueError) as exc:
            record.action, record.error = "error", str(exc)
        records.append(record)
    return records


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path, help="existing ItemData root; never modified")
    parser.add_argument("--destination", required=True, type=Path, help="thumbnail mirror root; created as needed")
    parser.add_argument("--manifest", type=Path, help="write JSON Lines records here")
    parser.add_argument("--max-size", type=int, default=1024, help="largest width or height (default: 1024)")
    parser.add_argument("--quality", type=int, default=85, choices=range(1, 96), metavar="1..95")
    parser.add_argument("--limit", type=int, help="process at most this many images")
    parser.add_argument("--force", action="store_true", help="regenerate newer destination files")
    parser.add_argument("--dry-run", action="store_true", help="report intended work without writing files")
    args = parser.parse_args(argv)
    if args.max_size < 1 or args.limit is not None and args.limit < 1:
        parser.error("--max-size and --limit must be positive")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    source, destination = args.source.resolve(), args.destination.resolve()
    if not source.is_dir():
        print(f"source ItemData root does not exist or is not a directory: {source}", file=sys.stderr)
        return 2
    if source == destination or source in destination.parents:
        print("destination must not be the source ItemData root or one of its parents", file=sys.stderr)
        return 2
    records = build_mirror(source, destination, max_size=(args.max_size, args.max_size), quality=args.quality, force=args.force, dry_run=args.dry_run, limit=args.limit)
    if args.manifest:
        args.manifest.parent.mkdir(parents=True, exist_ok=True)
        with args.manifest.open("w", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(asdict(record), sort_keys=True) + "\n")
    summary = {
        "source": str(source),
        "destination": str(destination),
        "records": len(records),
        "generated": sum(record.action == "generated" for record in records),
        "skipped": sum(record.action == "skipped_up_to_date" for record in records),
        "errors": sum(record.action == "error" for record in records),
        "dry_run": args.dry_run,
    }
    print(json.dumps(summary, sort_keys=True))
    return 1 if summary["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
