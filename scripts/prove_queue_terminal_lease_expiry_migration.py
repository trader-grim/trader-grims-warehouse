#!/usr/bin/env python3
"""Prove the queue terminal-lease migration on an isolated PostgreSQL 17 cluster.

The base queue ``live_schema.sql`` is used only to construct a representative
installed database for an isolated proof.  It is a pg_dump-style snapshot and
is never replayed as the migration.  The proof applies the candidate's explicit
idempotent migration twice, proves expired terminal leases are denied and live
ones still complete, then emits one exact-source backup/restore receipt.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import subprocess
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import Sequence

from tgw.candidate_manifest import (
    CandidateManifestError,
    create_migration_safety_receipt,
    verify_migration_safety_receipt,
)
from tgw.logging import announce_script_run

MIGRATION_PATH = "src/tgw/queue/migrations/20260815_terminal_lease_expiry_fence.sql"
SNAPSHOT_PATH = "src/tgw/queue/live_schema.sql"

_FUNCTION_RE = r"CREATE(?: OR REPLACE)? FUNCTION public\.{name}\(.*?AS \$\$(?P<body>.*?)\$\$;"


class MigrationProofError(RuntimeError):
    """The exact queue migration could not establish an isolated proof."""


def _run(command: Sequence[str], *, cwd: Path | None = None) -> bytes:
    try:
        return subprocess.run(
            list(command), cwd=cwd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        ).stdout
    except subprocess.CalledProcessError as error:
        stderr = error.stderr.decode(errors="replace").strip()
        raise MigrationProofError(
            f"command failed ({' '.join(command)}): {stderr or f'exit {error.returncode}'}"
        ) from error


def _git(repo: Path, *args: str) -> str:
    return _run(("git", *args), cwd=repo).decode().strip()


def _git_bytes(repo: Path, *args: str) -> bytes:
    return _run(("git", *args), cwd=repo)


def _free_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _postgres_bin(directory: Path | None) -> Path:
    candidates = [directory] if directory else [Path("/usr/lib/postgresql/17/bin")]
    required = ("initdb", "pg_ctl", "psql", "createdb", "pg_dump", "pg_restore")
    for candidate in candidates:
        if candidate is not None and all((candidate / name).is_file() for name in required):
            version = _run((str(candidate / "initdb"), "--version")).decode().strip()
            match = re.search(r"PostgreSQL\) (17(?:\.\d+)+(?: \([^)]*\))?)$", version)
            if match:
                return candidate
            raise MigrationProofError(f"isolated cluster must use PostgreSQL 17, found: {version}")
    searched = ", ".join(str(item) for item in candidates if item is not None)
    raise MigrationProofError(f"PostgreSQL 17 tools are unavailable (searched {searched})")


def _normalize_dump(body: bytes) -> bytes:
    text = body.decode("utf-8")
    text = re.sub(
        r"^\\(?:un)?restrict [^\n]+$",
        lambda _: r"\restriction-token",
        text,
        flags=re.MULTILINE,
    )
    return text.encode("utf-8")


def _db_command(binary: Path, socket_dir: Path, port: int, dbname: str, *arguments: str) -> tuple[str, ...]:
    return (
        str(binary / "psql"), "--no-psqlrc", "-X", "-v", "ON_ERROR_STOP=1",
        "-h", str(socket_dir), "-p", str(port), "-U", os.environ.get("USER", "codex"),
        "-d", dbname, *arguments,
    )


def _schema_dump(binary: Path, socket_dir: Path, port: int, dbname: str) -> bytes:
    return _normalize_dump(_run((
        str(binary / "pg_dump"), "--schema-only", "--no-owner", "--no-privileges",
        "-h", str(socket_dir), "-p", str(port), "-U", os.environ.get("USER", "codex"), dbname,
    )))


def _data_dump(binary: Path, socket_dir: Path, port: int, dbname: str) -> bytes:
    return _normalize_dump(_run((
        str(binary / "pg_dump"), "--data-only", "--inserts", "--no-owner", "--no-privileges",
        "-h", str(socket_dir), "-p", str(port), "-U", os.environ.get("USER", "codex"), dbname,
    )))


def _function_body(source: bytes, name: str) -> str:
    match = re.search(_FUNCTION_RE.format(name=re.escape(name)), source.decode("utf-8"), flags=re.DOTALL)
    if match is None:
        raise MigrationProofError(f"queue source does not define public.{name}")
    return " ".join(match.group("body").split())


def _assert_snapshot_matches_migration(snapshot: bytes, migration: bytes) -> None:
    for name in ("fail_job", "succeed_job"):
        if _function_body(snapshot, name) != _function_body(migration, name):
            raise MigrationProofError(
                f"candidate queue schema snapshot does not match executable migration for {name}"
            )


def _seed_legacy_queue(binary: Path, socket_dir: Path, port: int, dbname: str) -> None:
    # One job for each denial path and one for each successful terminal path.
    values = """
INSERT INTO queue_jobs (
    job_id, entity_type, entity_id, operation, handler_family, queue_name,
    state, payload_json, attempt_count, max_attempts, lease_owner, lease_token,
    lease_expires_at, started_at
) VALUES
('00000000-0000-0000-0000-000000000101', 'proof', 'expired-fail', 'proof', 'proof', 'proof',
 'running', '{}'::jsonb, 1, 3, 'proof-worker', '00000000-0000-0000-0000-000000000201',
 NOW() - interval '1 second', NOW()),
('00000000-0000-0000-0000-000000000102', 'proof', 'expired-succeed', 'proof', 'proof', 'proof',
 'running', '{}'::jsonb, 1, 3, 'proof-worker', '00000000-0000-0000-0000-000000000202',
 NOW() - interval '1 second', NOW()),
('00000000-0000-0000-0000-000000000103', 'proof', 'live-fail', 'proof', 'proof', 'proof',
 'running', '{}'::jsonb, 1, 3, 'proof-worker', '00000000-0000-0000-0000-000000000203',
 NOW() + interval '1 hour', NOW()),
('00000000-0000-0000-0000-000000000104', 'proof', 'live-succeed', 'proof', 'proof', 'proof',
 'running', '{}'::jsonb, 1, 3, 'proof-worker', '00000000-0000-0000-0000-000000000204',
 NOW() + interval '1 hour', NOW());
"""
    _run(_db_command(binary, socket_dir, port, dbname, "-c", values))


def _expect_database_denial(command: Sequence[str], *, label: str) -> None:
    result = subprocess.run(list(command), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode == 0:
        raise MigrationProofError(f"candidate queue migration did not deny {label}")


def _assert_upgraded(binary: Path, socket_dir: Path, port: int, dbname: str) -> None:
    _expect_database_denial(
        _db_command(
            binary, socket_dir, port, dbname, "-c",
            "SELECT fail_job('00000000-0000-0000-0000-000000000101', 'proof-worker', "
            "'00000000-0000-0000-0000-000000000201', 'proof', 'expired');",
        ),
        label="expired fail_job lease",
    )
    _expect_database_denial(
        _db_command(
            binary, socket_dir, port, dbname, "-c",
            "SELECT succeed_job('00000000-0000-0000-0000-000000000102', 'proof-worker', "
            "'00000000-0000-0000-0000-000000000202', '{\"proof\":true}'::jsonb);",
        ),
        label="expired succeed_job lease",
    )
    for job_id, token, function, arguments in (
        (
            "00000000-0000-0000-0000-000000000103",
            "00000000-0000-0000-0000-000000000203",
            "fail_job",
            "'proof-worker', '00000000-0000-0000-0000-000000000203', 'proof', 'delayed'",
        ),
        (
            "00000000-0000-0000-0000-000000000104",
            "00000000-0000-0000-0000-000000000204",
            "succeed_job",
            "'proof-worker', '00000000-0000-0000-0000-000000000204', '{\"proof\":true}'::jsonb",
        ),
    ):
        # NOW() remains the value at BEGIN.  The delay starts before the
        # lease's 100ms expiry, so this assertion fails with the old NOW()
        # predicate but passes only if the terminal statement checks current
        # wall-clock time after waiting.
        _expect_database_denial(
            _db_command(
                binary, socket_dir, port, dbname, "-c",
                "BEGIN; "
                f"UPDATE queue_jobs SET lease_expires_at = NOW() + interval '100 milliseconds' "
                f"WHERE job_id = '{job_id}'; "
                "SELECT pg_sleep(0.2); "
                f"SELECT {function}('{job_id}', {arguments}); COMMIT;",
            ),
            label=f"terminal lease that expired after transaction start ({function}, {token})",
        )
    failed = _run(_db_command(
        binary, socket_dir, port, dbname, "-A", "-t", "-c",
        "SELECT (fail_job('00000000-0000-0000-0000-000000000103', 'proof-worker', "
        "'00000000-0000-0000-0000-000000000203', 'proof', 'live')).state;",
    )).strip()
    succeeded = _run(_db_command(
        binary, socket_dir, port, dbname, "-A", "-t", "-c",
        "SELECT (succeed_job('00000000-0000-0000-0000-000000000104', 'proof-worker', "
        "'00000000-0000-0000-0000-000000000204', '{\"proof\":true}'::jsonb)).state;",
    )).strip()
    if failed != b"retry_wait" or succeeded != b"succeeded":
        raise MigrationProofError("candidate queue migration broke valid terminal transitions")


def prove_migration(*, repo: Path, candidate: str, base: str, output: Path, pg_bin: Path | None = None) -> dict[str, object]:
    repo = repo.resolve()
    exact_candidate = _git(repo, "rev-parse", f"{candidate}^{{commit}}")
    candidate_tree = _git(repo, "rev-parse", f"{exact_candidate}^{{tree}}")
    exact_base = _git(repo, "rev-parse", f"{base}^{{commit}}")
    base_tree = _git(repo, "rev-parse", f"{exact_base}^{{tree}}")
    _run(("git", "merge-base", "--is-ancestor", exact_base, exact_candidate), cwd=repo)
    changed = tuple(sorted(
        line for line in _git(repo, "diff", "--name-only", exact_base, exact_candidate).splitlines() if line
    ))
    if MIGRATION_PATH not in changed or SNAPSHOT_PATH not in changed:
        raise MigrationProofError(
            "candidate must change both the explicit queue migration and its queue schema snapshot"
        )
    legacy_snapshot = _git_bytes(repo, "show", f"{exact_base}:{SNAPSHOT_PATH}")
    migration_source = _git_bytes(repo, "show", f"{exact_candidate}:{MIGRATION_PATH}")
    candidate_snapshot = _git_bytes(repo, "show", f"{exact_candidate}:{SNAPSHOT_PATH}")
    _assert_snapshot_matches_migration(candidate_snapshot, migration_source)
    postgres_bin = _postgres_bin(pg_bin)
    version_output = _run((str(postgres_bin / "initdb"), "--version")).decode().strip()
    match = re.search(r"PostgreSQL\) (17(?:\.\d+)+(?: \([^)]*\))?)$", version_output)
    if match is None:
        raise MigrationProofError(f"could not identify PostgreSQL 17 version: {version_output}")
    postgres_version = f"PostgreSQL {match.group(1)}"

    with tempfile.TemporaryDirectory(prefix="tgw-queue-pg17-") as temporary:
        temporary_path = Path(temporary)
        data_dir = temporary_path / "data"
        socket_dir = temporary_path / "socket"
        socket_dir.mkdir()
        port = _free_local_port()
        _run((
            str(postgres_bin / "initdb"), "--no-locale", "--encoding=UTF8",
            "--auth-local=trust", "--auth-host=reject", "-D", str(data_dir),
        ))
        log_path = temporary_path / "postgres.log"
        started = False
        try:
            _run((
                str(postgres_bin / "pg_ctl"), "-D", str(data_dir), "-l", str(log_path),
                "-o", f"-k {socket_dir} -p {port} -c listen_addresses=''", "-w", "start",
            ))
            started = True
            for dbname in ("proof_source", "proof_restored"):
                _run((
                    str(postgres_bin / "createdb"), "-h", str(socket_dir), "-p", str(port),
                    "-U", os.environ.get("USER", "codex"), dbname,
                ))
            legacy_path = temporary_path / "base-queue-snapshot.sql"
            legacy_path.write_bytes(legacy_snapshot)
            _run(_db_command(postgres_bin, socket_dir, port, "proof_source", "-f", str(legacy_path)))
            _seed_legacy_queue(postgres_bin, socket_dir, port, "proof_source")
            source_schema = _schema_dump(postgres_bin, socket_dir, port, "proof_source")
            source_data = _data_dump(postgres_bin, socket_dir, port, "proof_source")
            backup_path = temporary_path / "queue-before-migration.dump"
            _run((
                str(postgres_bin / "pg_dump"), "--format=custom", "--no-owner", "--no-privileges",
                "-h", str(socket_dir), "-p", str(port), "-U", os.environ.get("USER", "codex"),
                "--file", str(backup_path), "proof_source",
            ))
            backup = backup_path.read_bytes()
            _run((
                str(postgres_bin / "pg_restore"), "--no-owner", "--no-privileges",
                "-h", str(socket_dir), "-p", str(port), "-U", os.environ.get("USER", "codex"),
                "-d", "proof_restored", str(backup_path),
            ))
            restored_schema = _schema_dump(postgres_bin, socket_dir, port, "proof_restored")
            restored_data = _data_dump(postgres_bin, socket_dir, port, "proof_restored")
            if source_schema != restored_schema or source_data != restored_data:
                raise MigrationProofError("PostgreSQL backup/restore did not preserve the legacy queue schema and data")
            migration_path = temporary_path / "candidate-queue-migration.sql"
            migration_path.write_bytes(migration_source)
            for _ in range(2):
                _run(_db_command(postgres_bin, socket_dir, port, "proof_source", "-f", str(migration_path)))
            _assert_upgraded(postgres_bin, socket_dir, port, "proof_source")
            migrated_schema = _schema_dump(postgres_bin, socket_dir, port, "proof_source")
            migrated_data = _data_dump(postgres_bin, socket_dir, port, "proof_source")
        finally:
            if started:
                _run((str(postgres_bin / "pg_ctl"), "-D", str(data_dir), "-m", "immediate", "-w", "stop"))

    receipt = create_migration_safety_receipt(
        candidate_commit=exact_candidate,
        candidate_tree=candidate_tree,
        base_commit=exact_base,
        base_tree=base_tree,
        migration_path=MIGRATION_PATH,
        migration_source=migration_source,
        schema_snapshot_path=SNAPSHOT_PATH,
        schema_snapshot_source=candidate_snapshot,
        postgres_version=postgres_version,
        backup=backup,
        source_schema=source_schema,
        restored_schema=restored_schema,
        source_data=source_data,
        restored_data=restored_data,
        migrated_schema=migrated_schema,
        migrated_data=migrated_data,
        verified=True,
    )
    try:
        verified = verify_migration_safety_receipt(
            receipt,
            candidate_commit=exact_candidate,
            candidate_tree=candidate_tree,
            base_commit=exact_base,
            base_tree=base_tree,
            migration_paths=(MIGRATION_PATH,),
            migration_source=migration_source,
            schema_snapshot_source=candidate_snapshot,
        )
    except CandidateManifestError as error:
        raise MigrationProofError(f"generated receipt did not self-verify: {error}") from error
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(asdict(verified), sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return asdict(verified)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True, help="Git repository containing the candidate")
    parser.add_argument("--commit", required=True, help="candidate commit (not a mutable worktree path)")
    parser.add_argument("--base-commit", required=True, help="released predecessor commit")
    parser.add_argument("--output", type=Path, required=True, help="receipt JSON output path")
    parser.add_argument("--pg-bin", type=Path, help="PostgreSQL 17 binary directory")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    announce_script_run(
        "prove_queue_terminal_lease_expiry_migration.py",
        "prove the explicit queue terminal-lease migration on an isolated PostgreSQL 17 cluster",
        candidate=args.commit,
        base_commit=args.base_commit,
    )
    try:
        prove_migration(
            repo=args.repo, candidate=args.commit, base=args.base_commit,
            output=args.output, pg_bin=args.pg_bin,
        )
    except (MigrationProofError, OSError, subprocess.SubprocessError) as error:
        print(f"migration proof failed: {error}", file=os.sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
