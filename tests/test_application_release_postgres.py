"""Real PostgreSQL 17 proof for the W09 destructive rollback boundary."""

from __future__ import annotations

import hashlib
import shutil
import socket
import stat
import subprocess
import tempfile
from pathlib import Path

import pytest

from tgw.application_release_remote import ApplicationReleaseRemoteError, HostRuntime

POSTGRES_BIN = Path("/usr/lib/postgresql/17/bin")
REQUIRED_TOOLS = ("initdb", "pg_ctl", "createdb", "psql", "pg_dump", "pg_restore")


def _tool(name: str) -> Path:
    system = POSTGRES_BIN / name
    if system.is_file():
        return system
    resolved = shutil.which(name)
    return Path(resolved) if resolved else system


def _binding(path: Path) -> dict[str, object]:
    raw = path.read_bytes()
    metadata = path.stat()
    return {
        "path": str(path.resolve()),
        "sha256": "sha256:" + hashlib.sha256(raw).hexdigest(),
        "uid": metadata.st_uid,
        "gid": metadata.st_gid,
        "mode": stat.S_IMODE(metadata.st_mode),
        "size": metadata.st_size,
    }


def _run(tool: str, *arguments: str) -> bytes:
    return subprocess.check_output([str(_tool(tool)), *arguments], stderr=subprocess.STDOUT)


@pytest.mark.skipif(
    any(not _tool(name).is_file() for name in REQUIRED_TOOLS),
    reason="requires local PostgreSQL 17 server and client tools",
)
def test_w09_restore_recreates_database_and_removes_post_backup_objects(tmp_path: Path):
    """The real restore drops/recreates the DB and proves byte-exact logical identity."""

    data = tmp_path / "postgres"
    socket_parent = Path("/opt/TGW/w/w11-worker-runtime/pg")
    socket_parent.mkdir(parents=True, exist_ok=True)
    socket_root = Path(tempfile.mkdtemp(prefix="w09-", dir=socket_parent))
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = str(probe.getsockname()[1])
    user = "w09_restore_owner"
    database = "w09_restore_db"
    _run("initdb", "-A", "trust", "-U", user, "-D", str(data))
    _run(
        "pg_ctl",
        "-D",
        str(data),
        "-o",
        f"-F -k {socket_root} -h '' -p {port}",
        "-l",
        str(tmp_path / "postgres.log"),
        "-w",
        "start",
    )
    try:
        common = ("-h", str(socket_root), "-p", port, "-U", user)
        _run("createdb", *common, database)
        _run(
            "psql",
            *common,
            "-d",
            database,
            "-v",
            "ON_ERROR_STOP=1",
            "-c",
            "CREATE TABLE retained(id integer PRIMARY KEY, value text NOT NULL); INSERT INTO retained VALUES (1, 'predecessor')",
        )
        config = {
            "database": {
                "name": database,
                "user": user,
                "host": str(socket_root),
                "port": int(port),
            },
            "executables": {name: _binding(_tool(name)) for name in ("psql", "pg_dump", "pg_restore")},
            "command_timeout_seconds": 30,
        }
        runtime = HostRuntime(config)
        predecessor_identity = runtime.database_identity()
        readiness = runtime.rollback_readiness()
        assert readiness["principal"] == f"{user}|true"
        assert readiness["database_identity_sha256"] == predecessor_identity
        assert readiness["backup_size"] > 0
        assert readiness["backup_listing_sha256"].startswith("sha256:")
        backup = tmp_path / "predecessor.dump"
        runtime.backup(backup)
        _run(
            "psql",
            *common,
            "-d",
            database,
            "-v",
            "ON_ERROR_STOP=1",
            "-c",
            "UPDATE retained SET value='successor'; CREATE TABLE post_backup_only(secret text); INSERT INTO post_backup_only VALUES ('must disappear')",
        )

        runtime.restore(backup)

        restored = (
            _run(
                "psql",
                *common,
                "-d",
                database,
                "-A",
                "-t",
                "-c",
                "SELECT value FROM retained WHERE id=1",
            )
            .decode()
            .strip()
        )
        post_backup_object = (
            _run(
                "psql",
                *common,
                "-d",
                database,
                "-A",
                "-t",
                "-c",
                "SELECT to_regclass('public.post_backup_only') IS NULL",
            )
            .decode()
            .strip()
        )
        assert restored == "predecessor"
        assert post_backup_object == "t"
        assert runtime.database_identity() == predecessor_identity

        _run(
            "psql",
            *common,
            "-d",
            "postgres",
            "-v",
            "ON_ERROR_STOP=1",
            "-c",
            "CREATE ROLE neighbor LOGIN",
        )
        wrong = dict(config)
        wrong["database"] = {**config["database"], "user": "neighbor"}
        with pytest.raises(
            ApplicationReleaseRemoteError,
            match="configured database principal cannot recreate",
        ):
            HostRuntime(wrong).restore(backup)
    finally:
        subprocess.run(
            [str(_tool("pg_ctl")), "-D", str(data), "-m", "immediate", "-w", "stop"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        shutil.rmtree(socket_root)
