"""Tests for the rclone hang watchdog on bin/tgw-cloud-sync and
bin/tgw-itemdata-sync (todo #1517, PP-BACKUP-001).

2026-07-16 incident: tgw-cloud-sync hung indefinitely after a Google Drive
403 RATE_LIMIT_EXCEEDED instead of exhausting rclone's own retries and
exiting (that normal path is confirmed working on the 2026-07-18 run) --
the hung process held the shared flock for ~1.5 days, starving
tgw-itemdata-sync. These tests simulate a hung rclone subprocess (a fake
`rclone` binary on PATH that never returns) and confirm:

  (a) the watchdog fires, kills the stuck subprocess, and the script exits
      non-zero with a clear log line -- it does not hang forever or
      silently retry into another hang.
  (b) the flock is released once the script exits (proven by acquiring it
      again immediately afterwards).
  (c) the normal (fast, successful) path is unaffected by the watchdog
      wrapper -- same-shape stub, but one that returns quickly and cleanly.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

REPO_ROOT = Path(__file__).parents[1]
CLOUD_SYNC = REPO_ROOT / "bin" / "tgw-cloud-sync"
ITEMDATA_SYNC = REPO_ROOT / "bin" / "tgw-itemdata-sync"


def _fake_bin(tmp_path: Path, rclone_body: str) -> Path:
    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir(exist_ok=True)
    rclone = fake_bin / "rclone"
    rclone.write_text(rclone_body)
    rclone.chmod(0o755)
    return fake_bin


def _base_env(tmp_path: Path, fake_bin: Path) -> dict:
    env = os.environ.copy()
    env["PATH"] = str(fake_bin) + ":" + env.get("PATH", "")
    env["TGW_RCLONE_CONF"] = str(tmp_path / "rclone.conf")
    env["TGW_RCLONE_LOCK"] = str(tmp_path / "tgw-rclone-gdrive.lock")
    return env


class TestCloudSyncWatchdog:
    def test_hung_rclone_is_killed_and_script_exits_nonzero(self, tmp_path: Path) -> None:
        # Never returns, and ignores SIGTERM so the watchdog's timeout
        # (not a graceful subprocess exit) is what actually ends it.
        fake_bin = _fake_bin(
            tmp_path,
            "#!/usr/bin/env bash\ntrap '' TERM\nsleep 300\n",
        )
        env = _base_env(tmp_path, fake_bin)
        env["RCLONE_TIMEOUT_SECS"] = "1"
        env["RCLONE_KILL_AFTER_SECS"] = "2"
        log = tmp_path / "cloud-sync.log"
        stamp = tmp_path / "last-success"
        env["TGW_RCLONE_SYNC_LOG"] = str(log)
        env["TGW_RCLONE_SYNC_STAMP"] = str(stamp)

        start = time.monotonic()
        result = subprocess.run(
            ["bash", str(CLOUD_SYNC)],
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        elapsed = time.monotonic() - start

        assert result.returncode != 0, (result.stdout, result.stderr)
        # timeout=1s + kill-after=2s grace, must not run anywhere near the
        # 300s hang -- proves the watchdog, not the fake process, ended it.
        assert elapsed < 20, f"watchdog did not bound runtime: {elapsed}s"
        assert not stamp.exists(), "stamp must not be written on a timed-out run"
        log_text = log.read_text()
        assert "watchdog fired" in log_text

    def test_lock_is_released_after_watchdog_kill(self, tmp_path: Path) -> None:
        """After a watchdog-triggered exit, the flock must be free again --
        proving the hung run cannot starve tgw-itemdata-sync indefinitely."""
        fake_bin = _fake_bin(
            tmp_path,
            "#!/usr/bin/env bash\ntrap '' TERM\nsleep 300\n",
        )
        env = _base_env(tmp_path, fake_bin)
        env["RCLONE_TIMEOUT_SECS"] = "1"
        env["RCLONE_KILL_AFTER_SECS"] = "2"
        env["TGW_RCLONE_SYNC_LOG"] = str(tmp_path / "cloud-sync.log")
        env["TGW_RCLONE_SYNC_STAMP"] = str(tmp_path / "last-success")

        subprocess.run(
            ["bash", str(CLOUD_SYNC)],
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )

        lock_path = Path(env["TGW_RCLONE_LOCK"])
        with open(lock_path, "w") as fh:
            import fcntl

            # Non-blocking exclusive lock must succeed immediately -- if the
            # hung run's script exit didn't release the lock this raises.
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)

    def test_normal_fast_rclone_run_unaffected_by_watchdog(self, tmp_path: Path) -> None:
        """A real (or fast no-op) rclone call must still complete normally
        with the watchdog wrapper in place -- the ceiling must not change
        normal-path timing/behavior."""
        fake_bin = _fake_bin(
            tmp_path,
            "#!/usr/bin/env bash\nexit 0\n",
        )
        env = _base_env(tmp_path, fake_bin)
        # Ceiling stays high (production default) -- proves a short/normal
        # run isn't touched by the watchdog at all.
        log = tmp_path / "cloud-sync.log"
        stamp = tmp_path / "last-success"
        env["TGW_RCLONE_SYNC_LOG"] = str(log)
        env["TGW_RCLONE_SYNC_STAMP"] = str(stamp)

        result = subprocess.run(
            ["bash", str(CLOUD_SYNC)],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )

        assert result.returncode == 0, (result.stdout, result.stderr)
        assert stamp.exists(), "success stamp must still be written on normal completion"
        if log.exists():
            assert "watchdog fired" not in log.read_text()

    def test_normal_retry_exhaustion_path_still_exits_nonzero_without_watchdog_wording(
        self, tmp_path: Path
    ) -> None:
        """A real rclone failure (e.g. retries exhausted, exit 1) that
        returns promptly must still be reported as a normal failure, not
        misattributed to the watchdog -- confirms the two failure modes
        stay distinguishable in the log."""
        fake_bin = _fake_bin(
            tmp_path,
            "#!/usr/bin/env bash\nexit 1\n",
        )
        env = _base_env(tmp_path, fake_bin)
        env["RCLONE_TIMEOUT_SECS"] = "60"
        log = tmp_path / "cloud-sync.log"
        env["TGW_RCLONE_SYNC_LOG"] = str(log)
        env["TGW_RCLONE_SYNC_STAMP"] = str(tmp_path / "last-success")

        result = subprocess.run(
            ["bash", str(CLOUD_SYNC)],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )

        assert result.returncode != 0
        log_text = log.read_text()
        assert "watchdog fired" not in log_text
        assert "exited with code 1" in log_text


class TestItemdataSyncWatchdog:
    def test_hung_rclone_cycle_is_bounded_and_lock_recovers(self, tmp_path: Path) -> None:
        """A hung rclone call inside one cycle of the continuous loop must
        not hold the shared flock past RCLONE_TIMEOUT_SECS -- otherwise
        tgw-cloud-sync's own (now-bounded) blocking flock wait would still
        eventually time out too, but only after needlessly waiting the
        full ceiling. Proves the loop keeps advancing instead of wedging."""
        fake_bin = _fake_bin(
            tmp_path,
            "#!/usr/bin/env bash\ntrap '' TERM\nsleep 300\n",
        )
        env = _base_env(tmp_path, fake_bin)
        env["RCLONE_TIMEOUT_SECS"] = "1"
        env["RCLONE_KILL_AFTER_SECS"] = "1"
        env["TGW_ITEMDATA_SYNC_INTERVAL"] = "1"
        local = tmp_path / "ItemData"
        local.mkdir()
        env["TGW_ITEMDATA_LOCAL"] = str(local)
        log = tmp_path / "itemdata-sync.log"
        status = tmp_path / "itemdata-sync-status.json"
        env["TGW_RCLONE_ITEMDATA_LOG"] = str(log)
        env["TGW_RCLONE_ITEMDATA_STATUS"] = str(status)

        proc = subprocess.Popen(
            ["bash", str(ITEMDATA_SYNC)],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            # One hung cycle (~1s timeout + immediate SIGTERM exit since the
            # fake stub only ignores TERM for the "must be SIGKILLed" test
            # above; here just prove the loop survives past cycle 1).
            time.sleep(6)
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=10)

        log_text = log.read_text() if log.exists() else ""
        assert "cycle 1 started" in log_text
        assert "watchdog fired" in log_text
        # Loop must have advanced to a second cycle -- proves the flock (and
        # the loop itself) recovered instead of wedging on the first hang.
        assert "cycle 2 started" in log_text or "cycle 2 skipped" in log_text

        lock_path = Path(env["TGW_RCLONE_LOCK"])
        if lock_path.exists():
            with open(lock_path, "w") as fh:
                import fcntl

                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


class TestFlockTimeout:
    def test_cloud_sync_gives_up_if_lock_held_forever(self, tmp_path: Path) -> None:
        """If the shared lock is held by some other (stuck) process for
        longer than FLOCK_TIMEOUT_SECS, tgw-cloud-sync must give up and
        exit non-zero instead of blocking forever waiting for it."""
        fake_bin = _fake_bin(tmp_path, "#!/usr/bin/env bash\nexit 0\n")
        env = _base_env(tmp_path, fake_bin)
        env["FLOCK_TIMEOUT_SECS"] = "1"
        log = tmp_path / "cloud-sync.log"
        env["TGW_RCLONE_SYNC_LOG"] = str(log)
        env["TGW_RCLONE_SYNC_STAMP"] = str(tmp_path / "last-success")

        lock_path = Path(env["TGW_RCLONE_LOCK"])
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        holder = subprocess.Popen(
            ["python3", "-c",
             f"import fcntl, time; f=open({str(lock_path)!r}, 'w'); "
             "fcntl.flock(f.fileno(), fcntl.LOCK_EX); time.sleep(30)"],
        )
        try:
            time.sleep(1)  # let the holder actually acquire the lock first
            result = subprocess.run(
                ["bash", str(CLOUD_SYNC)],
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
            )
            assert result.returncode != 0
            assert "could not acquire cloud-sync lock" in log.read_text()
        finally:
            holder.kill()
            holder.wait(timeout=10)
