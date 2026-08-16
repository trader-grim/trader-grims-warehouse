#!/usr/bin/env python3
"""Prove a PlanAuthority migration against an isolated PostgreSQL 17 cluster.

The proof deliberately reads the SQL from an immutable Git candidate, never
from the working tree.  It builds a representative v1 database, makes a
custom-format ``pg_dump`` backup, restores that backup into a fresh database,
then applies the candidate migration to the original.  The resulting receipt
is suitable for ``scripts/build_candidate_manifest.py --migration-receipt``.

Example:

    PYTHONPATH=src /opt/TGW/.venvs/controller/bin/python \
      scripts/prove_plan_authority_migration.py \
      --repo /opt/TGW/w/full-plan-integration \
      --base-commit <released-v1-commit> --commit <candidate-commit> \
      --output /tmp/plan-authority-migration-receipt.json
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
    create_plan_authority_migration_receipt,
    verify_plan_authority_migration_receipt,
)

MIGRATION_PATH = "src/tgw/plan_authority.sql"

# This is the deployed PlanAuthority v1 shape, including the eager receipt
# which a safe upgrade must preserve as an explicit legacy terminal result.
LEGACY_PLAN_AUTHORITY_SQL = """
CREATE TABLE plan_authority_requests (
    request_id text PRIMARY KEY,
    plan_commit text NOT NULL,
    solution_hash text NOT NULL,
    closure_hash text NOT NULL,
    graph_id text NOT NULL,
    object_generation text NOT NULL,
    effect_kind text NOT NULL,
    effect_generation text NOT NULL,
    effect_hash text NOT NULL,
    effect_parameters jsonb NOT NULL,
    summary text NOT NULL,
    evidence jsonb NOT NULL,
    requested_by text NOT NULL,
    expires_at timestamptz NOT NULL,
    requested_at timestamptz NOT NULL DEFAULT NOW()
);

CREATE TABLE plan_authority_decisions (
    decision_id text PRIMARY KEY,
    request_id text NOT NULL UNIQUE REFERENCES plan_authority_requests(request_id),
    decision_kind text CHECK (decision_kind IN ('approve','hold','reconcile')),
    decided_by text NOT NULL,
    reason text NOT NULL,
    decided_at timestamptz NOT NULL
);

CREATE TABLE plan_authority_effect_receipts (
    receipt_id uuid PRIMARY KEY,
    request_id text NOT NULL UNIQUE REFERENCES plan_authority_requests(request_id),
    effect_hash text NOT NULL,
    effect_generation text NOT NULL,
    consumed_at timestamptz NOT NULL
);

CREATE TABLE plan_authority_events (
    sequence bigserial PRIMARY KEY,
    request_id text NOT NULL REFERENCES plan_authority_requests(request_id),
    event_type text NOT NULL,
    details jsonb NOT NULL,
    occurred_at timestamptz NOT NULL DEFAULT NOW()
);

CREATE INDEX plan_authority_events_request_idx
    ON plan_authority_events(request_id, sequence);

ALTER TABLE plan_authority_requests
    ADD CONSTRAINT plan_authority_requests_effect_kind_check CHECK (effect_kind IN (
        'coding-release',
        'bounded-flake-push',
        'flake-switch-record-only',
        'dependency-resubmit',
        'authority-canary',
        'approval-platform-bootstrap-deployment'
    ));
"""

LEGACY_PLAN_AUTHORITY_DATA_SQL = """
INSERT INTO plan_authority_requests (
    request_id, plan_commit, solution_hash, closure_hash, graph_id,
    object_generation, effect_kind, effect_generation, effect_hash,
    effect_parameters, summary, evidence, requested_by, expires_at, requested_at
) VALUES (
    'proof-request', 'a111111111111111111111111111111111111111',
    'sha256:solution', 'sha256:closure', 'proof-graph', 'generation-1',
    'authority-canary', 'effect-generation-1', 'sha256:effect',
    '{"safe":true}'::jsonb, 'migration proof', '["v1"]'::jsonb,
    'migration-proof', '2030-01-02T00:00:00Z', '2030-01-01T00:00:00Z'
);
INSERT INTO plan_authority_decisions (
    decision_id, request_id, decision_kind, decided_by, reason, decided_at
) VALUES (
    'proof-decision', 'proof-request', 'approve', 'migration-proof',
    'approved before upgrade', '2030-01-01T00:01:00Z'
);
INSERT INTO plan_authority_effect_receipts (
    receipt_id, request_id, effect_hash, effect_generation, consumed_at
) VALUES (
    '00000000-0000-0000-0000-000000000001', 'proof-request', 'sha256:effect',
    'effect-generation-1', '2030-01-01T00:02:00Z'
);
INSERT INTO plan_authority_events (request_id, event_type, details, occurred_at)
VALUES ('proof-request', 'approved', '{"source":"v1"}'::jsonb, '2030-01-01T00:01:00Z');
"""

DATA_FINGERPRINT_SQL = """
SELECT jsonb_build_object(
    'requests', COALESCE((
        SELECT jsonb_agg(to_jsonb(row) ORDER BY row.request_id)
        FROM plan_authority_requests AS row
    ), '[]'::jsonb),
    'decisions', COALESCE((
        SELECT jsonb_agg(to_jsonb(row) ORDER BY row.decision_id)
        FROM plan_authority_decisions AS row
    ), '[]'::jsonb),
    'effect_receipts', COALESCE((
        SELECT jsonb_agg(to_jsonb(row) ORDER BY row.receipt_id)
        FROM plan_authority_effect_receipts AS row
    ), '[]'::jsonb),
    'events', COALESCE((
        SELECT jsonb_agg(to_jsonb(row) ORDER BY row.sequence)
        FROM plan_authority_events AS row
    ), '[]'::jsonb)
)::text;
"""


class MigrationProofError(RuntimeError):
    """The candidate could not establish an isolated migration proof."""


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
    for candidate in candidates:
        if candidate is not None and all((candidate / name).is_file() for name in (
            "initdb", "pg_ctl", "psql", "createdb", "pg_dump", "pg_restore",
        )):
            version = _run((str(candidate / "initdb"), "--version")).decode().strip()
            match = re.search(r"PostgreSQL\) (17(?:\.\d+)+(?: \([^)]*\))?)$", version)
            if match:
                return candidate
            raise MigrationProofError(f"isolated cluster must use PostgreSQL 17, found: {version}")
    searched = ", ".join(str(item) for item in candidates if item is not None)
    raise MigrationProofError(f"PostgreSQL 17 tools are unavailable (searched {searched})")


def _normalize_schema(body: bytes) -> bytes:
    """Remove pg_dump's random PostgreSQL 17 restore-restriction nonce."""
    text = body.decode("utf-8")
    text = re.sub(r"^\\(?:un)?restrict [^\n]+$", r"\\restriction-token", text, flags=re.MULTILINE)
    return text.encode("utf-8")


def _db_command(binary: Path, socket_dir: Path, port: int, dbname: str, *arguments: str) -> tuple[str, ...]:
    return (
        str(binary / "psql"), "--no-psqlrc", "-X", "-v", "ON_ERROR_STOP=1",
        "-h", str(socket_dir), "-p", str(port), "-U", os.environ.get("USER", "codex"),
        "-d", dbname, *arguments,
    )


def _schema_dump(binary: Path, socket_dir: Path, port: int, dbname: str) -> bytes:
    return _normalize_schema(_run((
        str(binary / "pg_dump"), "--schema-only", "--no-owner", "--no-privileges",
        "-h", str(socket_dir), "-p", str(port), "-U", os.environ.get("USER", "codex"), dbname,
    )))


def _data_fingerprint(binary: Path, socket_dir: Path, port: int, dbname: str) -> bytes:
    return _run(_db_command(binary, socket_dir, port, dbname, "-A", "-t", "-c", DATA_FINGERPRINT_SQL)).strip()


def _assert_upgraded(binary: Path, socket_dir: Path, port: int, dbname: str) -> None:
    checks = """
SELECT
    EXISTS (
        SELECT 1 FROM information_schema.columns
         WHERE table_name = 'plan_authority_decisions'
           AND column_name = 'reconciliation_evidence'
    )
    AND EXISTS (
        SELECT 1 FROM information_schema.columns
         WHERE table_name = 'plan_authority_effect_receipts'
           AND column_name = 'outcome'
    )
    AND NOT EXISTS (
        SELECT 1 FROM pg_constraint con
        JOIN pg_class relation ON relation.oid = con.conrelid
        JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace
        WHERE namespace.nspname = 'public'
          AND relation.relname = 'plan_authority_decisions'
          AND con.contype = 'u'
          AND con.conkey = ARRAY[
              (SELECT attnum FROM pg_attribute
                WHERE attrelid = relation.oid AND attname = 'request_id' AND NOT attisdropped)
          ]
    )
    AND (SELECT outcome = 'legacy-consumed' AND completed_at IS NOT NULL
           FROM plan_authority_effect_receipts WHERE request_id = 'proof-request')
    AND (SELECT count(*) = 1 FROM plan_authority_requests WHERE request_id = 'proof-request');
"""
    result = _run(_db_command(binary, socket_dir, port, dbname, "-A", "-t", "-c", checks)).strip()
    if result != b"t":
        raise MigrationProofError("candidate did not safely upgrade the representative PlanAuthority v1 state")


def prove_migration(*, repo: Path, candidate: str, base: str, output: Path, pg_bin: Path | None = None) -> dict[str, object]:
    repo = repo.resolve()
    exact_candidate = _git(repo, "rev-parse", f"{candidate}^{{commit}}")
    candidate_tree = _git(repo, "rev-parse", f"{exact_candidate}^{{tree}}")
    exact_base = _git(repo, "rev-parse", f"{base}^{{commit}}")
    base_tree = _git(repo, "rev-parse", f"{exact_base}^{{tree}}")
    if _run(("git", "merge-base", "--is-ancestor", exact_base, exact_candidate), cwd=repo) != b"":
        # git's successful no-output response is intentional; this branch is unreachable.
        raise AssertionError("unexpected merge-base output")
    changed = tuple(sorted(line for line in _git(repo, "diff", "--name-only", exact_base, exact_candidate).splitlines() if line))
    migrations = tuple(path for path in changed if path.endswith(".sql") or "/migrations/" in path)
    if migrations != (MIGRATION_PATH,):
        raise MigrationProofError("candidate must change exactly src/tgw/plan_authority.sql for this proof")
    migration_source = _git_bytes(repo, "show", f"{exact_candidate}:{MIGRATION_PATH}")
    postgres_bin = _postgres_bin(pg_bin)
    version_output = _run((str(postgres_bin / "initdb"), "--version")).decode().strip()
    version_match = re.search(r"PostgreSQL\) (17(?:\.\d+)+(?: \([^)]*\))?)$", version_output)
    if version_match is None:  # _postgres_bin already checks; retain a local proof invariant.
        raise MigrationProofError(f"could not identify PostgreSQL 17 version: {version_output}")
    postgres_version = f"PostgreSQL {version_match.group(1)}"

    with tempfile.TemporaryDirectory(prefix="tgw-plan-authority-pg17-") as temporary:
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
            _run(_db_command(postgres_bin, socket_dir, port, "proof_source", "-c", LEGACY_PLAN_AUTHORITY_SQL))
            _run(_db_command(postgres_bin, socket_dir, port, "proof_source", "-c", LEGACY_PLAN_AUTHORITY_DATA_SQL))
            source_schema = _schema_dump(postgres_bin, socket_dir, port, "proof_source")
            source_data = _data_fingerprint(postgres_bin, socket_dir, port, "proof_source")
            backup_path = temporary_path / "plan-authority-v1.dump"
            _run((
                str(postgres_bin / "pg_dump"), "--format=custom", "--no-owner", "--no-privileges",
                "-h", str(socket_dir), "-p", str(port), "-U", os.environ.get("USER", "codex"),
                "--file", str(backup_path), "proof_source",
            ))
            backup = backup_path.read_bytes()
            migration_path = temporary_path / "candidate-plan-authority.sql"
            migration_path.write_bytes(migration_source)
            _run(_db_command(
                postgres_bin, socket_dir, port, "proof_source", "-f", str(migration_path),
            ))
            _assert_upgraded(postgres_bin, socket_dir, port, "proof_source")
            migrated_schema = _schema_dump(postgres_bin, socket_dir, port, "proof_source")
            migrated_data = _data_fingerprint(postgres_bin, socket_dir, port, "proof_source")
            _run((
                str(postgres_bin / "pg_restore"), "--no-owner", "--no-privileges",
                "-h", str(socket_dir), "-p", str(port), "-U", os.environ.get("USER", "codex"),
                "-d", "proof_restored", str(backup_path),
            ))
            restored_schema = _schema_dump(postgres_bin, socket_dir, port, "proof_restored")
            restored_data = _data_fingerprint(postgres_bin, socket_dir, port, "proof_restored")
            if source_schema != restored_schema or source_data != restored_data:
                raise MigrationProofError("PostgreSQL backup/restore did not preserve the v1 schema and data")
        finally:
            if started:
                _run((str(postgres_bin / "pg_ctl"), "-D", str(data_dir), "-m", "immediate", "-w", "stop"))

    receipt = create_plan_authority_migration_receipt(
        candidate_commit=exact_candidate,
        candidate_tree=candidate_tree,
        base_commit=exact_base,
        base_tree=base_tree,
        migration_path=MIGRATION_PATH,
        migration_source=migration_source,
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
        verified = verify_plan_authority_migration_receipt(
            receipt,
            candidate_commit=exact_candidate,
            candidate_tree=candidate_tree,
            base_commit=exact_base,
            base_tree=base_tree,
            migration_paths=migrations,
            migration_source=migration_source,
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
    parser.add_argument("--base-commit", required=True, help="released v1 predecessor commit")
    parser.add_argument("--output", type=Path, required=True, help="receipt JSON output path")
    parser.add_argument("--pg-bin", type=Path, help="PostgreSQL 17 binary directory (default: /usr/lib/postgresql/17/bin)")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
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
