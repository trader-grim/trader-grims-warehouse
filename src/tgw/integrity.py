"""
tgw.integrity — shared file-integrity helpers (PP-DATAINTEGRITY-001 legs 2/3,
docs/ai-plans/photo-integrity-mitigation.md, todo #1154 follow-up).

Two independent guards, sharing this module because they're both prevention
for the same root incident (Feb-2022 bulk-copy truncation that sat silent
for 3.5 years — see the plan doc):

  verified_copy() / verify_copy_tree()
      Leg 2 — sha256 verify-after-copy for bulk-copy paths (usb-restore,
      future PP-DRIVE-INDEX-001 Phase 3 consolidation moves). Copies to a
      staging file next to the destination and only atomically renames it
      into place once the staged copy's sha256 matches the source's. A
      copy killed mid-flight (SIGKILL, power loss, disk full, ...) can
      never leave a truncated file at the final destination path — the
      destination either has the fully-verified file, or nothing at all.
      "Report success" and "the file is actually correct" can't come apart.

  decode_verify_image()
      Leg 3 — full PIL decode (im.load(), not just im.verify()) so a
      truncated/corrupt camera file is caught at intake, not discovered
      at listing time. Matches the plan doc's own recommendation (intake
      is not hot-path, so the extra decode cost is worth catching tail
      truncation that header-only verify() would miss) and the same
      im.load() approach already used by the leg-1 catalog-verify rule
      (`_check_photo_readable` in api.py).
"""

from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path
from typing import List, Optional, Tuple

log = logging.getLogger(__name__)

CHUNK_SIZE = 4 * 1024 * 1024  # 4 MiB

# Test-only instrumentation: when set (seconds, float), verified_copy()
# sleeps this long after writing each chunk. This is the only way to get a
# deterministic mid-copy window to SIGKILL a subprocess in during the
# acceptance test — SIGKILL cannot be caught/handled at all, so there is no
# way to "test the exception path" from inside the process; the only real
# test is killing a slow-enough copy and confirming the destination never
# ends up holding a partial file. Never set outside tests.
_TEST_CHUNK_SLEEP_ENV = "TGW_INTEGRITY_TEST_CHUNK_SLEEP_S"


class CopyIntegrityError(Exception):
    """Raised when a copy's destination hash does not match the source hash."""


def sha256_file(path: Path, chunk_size: int = CHUNK_SIZE) -> str:
    """Stream-hash a file without loading it fully into memory."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def verified_copy(src: Path, dest: Path, chunk_size: int = CHUNK_SIZE) -> str:
    """Copy *src* to *dest* with sha256 verify-after-copy (leg 2).

    Writes to a staging path (`<dest-dir>/.tmp-verify-<pid>-<name>`) first,
    hashes source and staged copy, and only `os.replace()`s the staged file
    into `dest` once the hashes match — `os.replace` is atomic on the same
    filesystem, so there is no window where `dest` exists half-written.

    If anything goes wrong mid-copy (exception, or the process being
    killed — SIGKILL prevents this function's own cleanup from running,
    but it *also* prevents the final `os.replace`, so the half-written
    staging file is simply orphaned next to `dest`, never becomes `dest`),
    the caller can never observe `dest` existing without it having been
    hash-verified. Raises CopyIntegrityError on a hash mismatch (leaving no
    file at `dest`); re-raises any copy-time OSError after best-effort
    staging cleanup.

    Returns the verified sha256 hex digest.
    """
    src = Path(src)
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    staging = dest.parent / f".tmp-verify-{os.getpid()}-{dest.name}"

    test_sleep = os.environ.get(_TEST_CHUNK_SLEEP_ENV)
    sleep_s = float(test_sleep) if test_sleep else 0.0

    src_hash = sha256_file(src, chunk_size)

    try:
        with open(src, "rb") as fsrc, open(staging, "wb") as fdst:
            while True:
                chunk = fsrc.read(chunk_size)
                if not chunk:
                    break
                fdst.write(chunk)
                fdst.flush()
                os.fsync(fdst.fileno())
                if sleep_s:
                    import time
                    time.sleep(sleep_s)
    except BaseException:
        try:
            staging.unlink(missing_ok=True)
        except Exception:
            pass
        raise

    dest_hash = sha256_file(staging, chunk_size)
    if dest_hash != src_hash:
        staging.unlink(missing_ok=True)
        raise CopyIntegrityError(
            f"verify-after-copy mismatch: {src} -> {dest} "
            f"(src sha256={src_hash} dest sha256={dest_hash})"
        )

    os.replace(staging, dest)  # atomic on same filesystem
    log.info("verified_copy ok: %s -> %s (sha256=%s)", src, dest, src_hash)
    return src_hash


def verify_copy_tree(src_dir: Path, dest_dir: Path) -> List[Tuple[Path, Path, str]]:
    """Recursively copy every file under src_dir into dest_dir with
    verified_copy(). Returns a list of (src, dest, sha256) for every file
    successfully verified+copied. Raises on the first failure (matches
    verified_copy's fail-loud contract — a partial tree copy is a finding,
    not something to paper over and continue past silently).
    """
    src_dir = Path(src_dir)
    dest_dir = Path(dest_dir)
    results: List[Tuple[Path, Path, str]] = []
    for src_path in sorted(src_dir.rglob("*")):
        if not src_path.is_file():
            continue
        rel = src_path.relative_to(src_dir)
        dest_path = dest_dir / rel
        digest = verified_copy(src_path, dest_path)
        results.append((src_path, dest_path, digest))
    return results


def decode_verify_image(path: Path) -> Optional[str]:
    """Full PIL decode (im.load()) of an image file (leg 3).

    Returns an error string if the file fails to decode (truncated,
    corrupt header, zero-byte, not actually an image, ...), else None.
    Deliberately uses `im.load()` (full decode) rather than `im.verify()`
    (header-only) per the plan doc's own recommendation: intake is not
    hot-path, and header-only verify() misses tail truncation — exactly
    the Feb-2022 bulk-copy failure mode this whole track exists to catch.
    Matches the im.load() approach already used by the leg-1 catalog-verify
    rule (`_check_photo_readable` in api.py).
    """
    try:
        from PIL import Image
        with Image.open(path) as im:
            im.load()
    except Exception as exc:
        return str(exc)
    return None


# ---------------------------------------------------------------------------
# CLI — thin wrapper so bash bulk-copy tooling (e.g. scripts/tgw-restore.sh's
# usb-restore path) can call the shared helper without a bash reimplementation
# of the hash/verify/atomic-rename logic.
# ---------------------------------------------------------------------------

def main() -> int:
    import argparse
    import sys

    parser = argparse.ArgumentParser(prog="tgw-integrity")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_tree = sub.add_parser("copy-tree", help="verify-after-copy an entire directory tree")
    p_tree.add_argument("src", type=Path)
    p_tree.add_argument("dest", type=Path)

    p_file = sub.add_parser("copy-file", help="verify-after-copy a single file")
    p_file.add_argument("src", type=Path)
    p_file.add_argument("dest", type=Path)

    args = parser.parse_args()

    try:
        if args.cmd == "copy-tree":
            results = verify_copy_tree(args.src, args.dest)
            print(f"verified {len(results)} file(s): {args.src} -> {args.dest}")
        elif args.cmd == "copy-file":
            digest = verified_copy(args.src, args.dest)
            print(f"verified: {args.src} -> {args.dest} (sha256={digest})")
    except (CopyIntegrityError, OSError) as exc:
        print(f"tgw-integrity: FAILED: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
