"""PP-DATAINTEGRITY-001 legs 2/3 (todos #1266/#1267,
docs/ai-plans/photo-integrity-mitigation.md).

Leg 2: sha256 verify-after-copy helper (tgw.integrity.verified_copy).
Acceptance bar per the plan doc is explicit: "Bulk-copy helper refuses to
report success on a mid-file kill (test with SIGKILL mid-copy)" — a
happy-path-only test would not actually prove the guarantee, so
test_verified_copy_refuses_success_on_sigkill actually forks a subprocess,
SIGKILLs it mid-copy, and asserts the destination path never exists.

Leg 3: decode-verify at intake (tgw.integrity.decode_verify_image).
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tgw import integrity


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_verified_copy_happy_path(tmp_path):
    src = tmp_path / "src.bin"
    dest = tmp_path / "dest.bin"
    src.write_bytes(os.urandom(2 * 1024 * 1024))

    digest = integrity.verified_copy(src, dest)

    assert dest.exists()
    assert digest == _sha256(src) == _sha256(dest)
    # no orphaned staging file left behind on a clean run
    assert not list(tmp_path.glob(".tmp-verify-*"))


def test_verified_copy_rejects_hash_mismatch(tmp_path, monkeypatch):
    src = tmp_path / "src.bin"
    dest = tmp_path / "dest.bin"
    src.write_bytes(os.urandom(1024))

    # Force a corrupted staged copy by truncating the file after it's
    # written but before verification — simulate a copy that "succeeded"
    # at the OS-call level but landed on-disk content different from src.
    real_sha256_file = integrity.sha256_file
    call_count = {"n": 0}

    def flaky_sha256_file(path, chunk_size=integrity.CHUNK_SIZE):
        call_count["n"] += 1
        if call_count["n"] == 2:
            # second call is the staged-copy hash; corrupt it in-flight
            return "0" * 64
        return real_sha256_file(path, chunk_size)

    monkeypatch.setattr(integrity, "sha256_file", flaky_sha256_file)

    with pytest.raises(integrity.CopyIntegrityError):
        integrity.verified_copy(src, dest)

    assert not dest.exists()
    assert not list(tmp_path.glob(".tmp-verify-*"))  # cleaned up on mismatch


def test_verified_copy_refuses_success_on_sigkill(tmp_path):
    """Live acceptance test for the plan doc's explicit SIGKILL bar.

    A ~64 MiB source with an artificial per-chunk sleep (test-only env var)
    gives a deterministic window to kill the copy subprocess mid-flight.
    After the kill: the destination path must NOT exist (a truncated file
    can never be mistaken for a verified copy) — only an orphaned staging
    file may remain, and even that must not be the final `dest` name.
    """
    src = tmp_path / "bigsrc.bin"
    dest = tmp_path / "bigdest.bin"
    src.write_bytes(os.urandom(64 * 1024 * 1024))

    env = dict(os.environ)
    env[integrity._TEST_CHUNK_SLEEP_ENV] = "0.05"

    proc = subprocess.Popen(
        [sys.executable, "-c",
         "from tgw.integrity import verified_copy; "
         f"verified_copy({str(src)!r}, {str(dest)!r}, chunk_size=1024*1024)"],
        env=env,
    )
    time.sleep(0.4)  # let a few chunks land (well inside the 64 MiB / 1 MiB chunks)
    assert proc.poll() is None, "copy finished before we could kill it — increase src size"
    proc.send_signal(9)  # SIGKILL — cannot be trapped/handled by the child at all
    proc.wait(timeout=5)

    assert proc.returncode == -9

    # The core acceptance bar: dest must never exist in a truncated state.
    assert not dest.exists(), (
        "verified_copy left a file at the final destination path after "
        "being SIGKILLed mid-copy — a mid-file kill was reported as success"
    )

    # Only the orphaned staging file (never dest itself) may remain, and if
    # present it must be smaller than the source (proof the kill really did
    # land mid-copy, not after).
    staging_files = list(tmp_path.glob(".tmp-verify-*"))
    for stg in staging_files:
        assert stg.name != dest.name
        assert stg.stat().st_size < src.stat().st_size


def test_verify_copy_tree(tmp_path):
    src_dir = tmp_path / "src"
    dest_dir = tmp_path / "dest"
    (src_dir / "sub").mkdir(parents=True)
    (src_dir / "a.txt").write_bytes(os.urandom(100))
    (src_dir / "sub" / "b.txt").write_bytes(os.urandom(200))

    results = integrity.verify_copy_tree(src_dir, dest_dir)

    assert len(results) == 2
    assert (dest_dir / "a.txt").read_bytes() == (src_dir / "a.txt").read_bytes()
    assert (dest_dir / "sub" / "b.txt").read_bytes() == (src_dir / "sub" / "b.txt").read_bytes()


# ---------------------------------------------------------------------------
# Leg 3 — decode_verify_image
# ---------------------------------------------------------------------------

def test_decode_verify_image_accepts_good_photo(tmp_path):
    pytest.importorskip("PIL")
    from PIL import Image

    path = tmp_path / "good.jpg"
    Image.new("RGB", (32, 32), color="red").save(path, "JPEG")

    assert integrity.decode_verify_image(path) is None


def test_decode_verify_image_rejects_truncated_photo(tmp_path):
    pytest.importorskip("PIL")
    from PIL import Image

    full = tmp_path / "full.jpg"
    Image.new("RGB", (256, 256), color="blue").save(full, "JPEG")
    full_bytes = full.read_bytes()

    truncated = tmp_path / "truncated.jpg"
    # Cut off the tail — header-only verify() would miss this; full im.load()
    # must catch it. This mirrors the real Feb-2022 incident's failure mode.
    truncated.write_bytes(full_bytes[: len(full_bytes) // 2])

    error = integrity.decode_verify_image(truncated)
    assert error is not None


def test_decode_verify_image_rejects_zero_byte_file(tmp_path):
    path = tmp_path / "empty.jpg"
    path.write_bytes(b"")

    error = integrity.decode_verify_image(path)
    assert error is not None
