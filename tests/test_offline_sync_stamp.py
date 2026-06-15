"""Tests for bin/tgw-offline-sync stamp placement (PP-BACKUP-001).

Verifies that .tgw-sync-stamp is written inside @data for btrfs drives
so that it appears in post-sync snapshots, and at the mount root for
legacy (flat) drives.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "bin" / "tgw-offline-sync"
_TEST_LABEL = "TGW-TEST-STAMP-PYTEST"
_MOUNT = Path(f"/media/tgw/{_TEST_LABEL}")


def _make_fake_bin(tmp_path: Path) -> Path:
    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    stubs = {
        "mountpoint": "#!/bin/bash\nexit 0\n",
        # rsync: succeed without copying anything; mkdir -p ensures dirs exist
        "rsync": "#!/bin/bash\nexit 0\n",
        "sync": "#!/bin/bash\nexit 0\n",
        # btrfs: create the snapshot target dir so the prune pipeline has entries
        # and ls @snapshots/*/ doesn't fail with set -euo pipefail.
        "btrfs": (
            "#!/bin/bash\n"
            "if [ \"$1\" = subvolume ] && [ \"$2\" = snapshot ]; then\n"
            "    mkdir -p \"${!#}\"\n"
            "fi\n"
            "exit 0\n"
        ),
    }
    for name, body in stubs.items():
        p = fake_bin / name
        p.write_text(body)
        p.chmod(0o755)
    return fake_bin


def _run_sync(fake_bin: Path) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["PATH"] = str(fake_bin) + ":" + env.get("PATH", "")
    return subprocess.run(
        ["bash", str(SCRIPT), _TEST_LABEL],
        env=env,
        capture_output=True,
        text=True,
    )


class TestStampPlacement:
    def setup_method(self):
        _MOUNT.mkdir(parents=True, exist_ok=True)

    def teardown_method(self):
        shutil.rmtree(_MOUNT, ignore_errors=True)

    def test_btrfs_layout_stamp_inside_data(self, tmp_path: Path) -> None:
        """btrfs drives: stamp must be inside @data so snapshots capture it."""
        (_MOUNT / "@data").mkdir()
        (_MOUNT / "@snapshots").mkdir()
        fake_bin = _make_fake_bin(tmp_path)

        result = _run_sync(fake_bin)
        assert result.returncode == 0, result.stderr

        assert (_MOUNT / "@data" / ".tgw-sync-stamp").exists(), (
            "Stamp must be inside @data for btrfs snapshot coverage"
        )
        assert not (_MOUNT / ".tgw-sync-stamp").exists(), (
            "Stamp must NOT be at mount root for btrfs layout"
        )

    def test_legacy_layout_stamp_at_mount_root(self, tmp_path: Path) -> None:
        """Legacy flat layout (no @data subvolume): stamp stays at mount root."""
        fake_bin = _make_fake_bin(tmp_path)

        result = _run_sync(fake_bin)
        assert result.returncode == 0, result.stderr

        assert (_MOUNT / ".tgw-sync-stamp").exists(), (
            "Stamp must be at mount root for legacy layout"
        )
        assert not (_MOUNT / "@data" / ".tgw-sync-stamp").exists()
