"""Tests for bin/tgw-offline-sync stamp placement (PP-BACKUP-001).

Verifies that .tgw-sync-stamp is written inside @data for btrfs drives
so that it appears in post-sync snapshots, and at the mount root for
legacy (flat) drives.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "bin" / "tgw-offline-sync"
_TEST_LABEL = "TGW-TEST-STAMP-PYTEST"


def _make_fake_bin(tmp_path: Path) -> Path:
    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    stubs = {
        "mountpoint": "#!/usr/bin/env bash\nexit 0\n",
        "rsync": "#!/usr/bin/env bash\nexit 0\n",
        "sync": "#!/usr/bin/env bash\nexit 0\n",
        # btrfs: create the snapshot target dir so the prune pipeline has entries
        # and ls @snapshots/*/ doesn't fail with set -euo pipefail.
        "btrfs": (
            "#!/usr/bin/env bash\n"
            'if [ "$1" = subvolume ] && [ "$2" = snapshot ]; then\n'
            '    mkdir -p "${!#}"\n'
            "fi\n"
            "exit 0\n"
        ),
    }
    for name, body in stubs.items():
        p = fake_bin / name
        p.write_text(body)
        p.chmod(0o755)
    return fake_bin


def _run_sync(fake_bin: Path, mount_base: Path) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["PATH"] = str(fake_bin) + ":" + env.get("PATH", "")
    env["TGW_OFFLINE_MOUNT_BASE"] = str(mount_base)
    return subprocess.run(
        ["bash", str(SCRIPT), _TEST_LABEL],
        env=env,
        capture_output=True,
        text=True,
    )


class TestStampPlacement:
    def test_btrfs_layout_stamp_inside_data(self, tmp_path: Path) -> None:
        """btrfs drives: stamp must be inside @data so snapshots capture it."""
        mount = tmp_path / _TEST_LABEL
        mount.mkdir()
        (mount / "@data").mkdir()
        (mount / "@snapshots").mkdir()
        fake_bin = _make_fake_bin(tmp_path)

        result = _run_sync(fake_bin, tmp_path)
        assert result.returncode == 0, result.stderr

        assert (mount / "@data" / ".tgw-sync-stamp").exists(), (
            "Stamp must be inside @data for btrfs snapshot coverage"
        )
        assert not (mount / ".tgw-sync-stamp").exists(), (
            "Stamp must NOT be at mount root for btrfs layout"
        )

    def test_legacy_layout_stamp_at_mount_root(self, tmp_path: Path) -> None:
        """Legacy flat layout (no @data subvolume): stamp stays at mount root."""
        mount = tmp_path / _TEST_LABEL
        mount.mkdir()
        fake_bin = _make_fake_bin(tmp_path)

        result = _run_sync(fake_bin, tmp_path)
        assert result.returncode == 0, result.stderr

        assert (mount / ".tgw-sync-stamp").exists(), (
            "Stamp must be at mount root for legacy layout"
        )
        assert not (mount / "@data" / ".tgw-sync-stamp").exists()
