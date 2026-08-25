#!/opt/TGW/.venvs/controller/bin/python3
"""Launch the shared read-only TGW context MCP on the Debian development host."""

from __future__ import annotations

import hashlib
import json
import os
import pwd
import re
import socket
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

RUNTIME_RELEASES = Path("/opt/TGW/tgw-lib/coding-runtime/releases")
CONTEXT_SOURCE = Path("/opt/TGW/tgw-lib/src/trader-grims-warehouse")
CATALOG = Path("/opt/TGW/tgw-lib/config/tgw-context-debian-v1.json")
CURRENT_CONTEXT = Path("/opt/TGW/tgw-lib/config/tgw-context-current.json")
HARNESS_ACTORS = frozenset({"codex", "claude", "deepseek"})
RETIRED_TOOLS = ("tgw_context_bundle", "tgw_context_confirm_rebind")
RUNTIME_OWNER_UID = 0
RUNTIME_OWNER_GID = 0
SYSTEM_CODE_OWNERS = frozenset({0, 65534})
_COMMIT = re.compile(r"[0-9a-f]{40}\Z")


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _stat_identity(value: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _protected_snapshot_raw() -> bytes:
    """Read one immutable snapshot without importing any TGW runtime code."""
    before = CURRENT_CONTEXT.stat(follow_symlinks=False)
    if (
        CURRENT_CONTEXT.is_symlink()
        or not stat.S_ISREG(before.st_mode)
        or before.st_mode & 0o022
        or before.st_size > 256 * 1024
    ):
        raise ValueError("current TGW context snapshot is not protected read-only data")
    raw = CURRENT_CONTEXT.read_bytes()
    after = CURRENT_CONTEXT.stat(follow_symlinks=False)
    if _stat_identity(before) != _stat_identity(after) or len(raw) != after.st_size:
        raise ValueError("current TGW context snapshot changed during startup")
    return raw


def _git_blob_oid(raw: bytes) -> str:
    framed = b"blob " + str(len(raw)).encode("ascii") + b"\0" + raw
    return hashlib.sha1(framed, usedforsecurity=False).hexdigest()


def _git(*args: str) -> bytes:
    executable = Path("/usr/bin/git").resolve(strict=True)
    observed = executable.stat(follow_symlinks=False)
    if (
        not stat.S_ISREG(observed.st_mode)
        or observed.st_uid not in SYSTEM_CODE_OWNERS
        or observed.st_gid not in SYSTEM_CODE_OWNERS
        or observed.st_mode & 0o022
        or not observed.st_mode & 0o111
    ):
        raise ValueError("system Git executable is not immutable host code")
    result = subprocess.run(
        [
            str(executable),
            "-c",
            f"safe.directory={CONTEXT_SOURCE}",
            "-c",
            "core.fsmonitor=false",
            "-c",
            "core.hooksPath=/dev/null",
            "-C",
            str(CONTEXT_SOURCE),
            *args,
        ],
        check=False,
        capture_output=True,
        env={
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_NO_REPLACE_OBJECTS": "1",
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "PATH": "/usr/bin:/bin",
        },
        timeout=30,
    )
    if result.returncode:
        raise ValueError("cannot verify immutable Context runtime against Git")
    return result.stdout


def _verify_bound_release(
    release: Path, source_commit: str, source_tree: str
) -> tuple[int, Path]:
    observed_commit = _git("rev-parse", f"{source_commit}^{{commit}}").decode().strip()
    if observed_commit != source_commit:
        raise ValueError("atomic Context snapshot source commit is not a Git commit")
    observed_tree = _git("rev-parse", f"{source_commit}^{{tree}}").decode().strip()
    if observed_tree != source_tree:
        raise ValueError("atomic Context snapshot source tree differs from Git")
    descriptor = os.open(
        release,
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
    )
    try:
        root_state = os.fstat(descriptor)
        if (
            root_state.st_uid != RUNTIME_OWNER_UID
            or root_state.st_gid != RUNTIME_OWNER_GID
            or root_state.st_mode & 0o022
        ):
            raise ValueError("immutable Context release root is not root:root protected")
        bound = Path(f"/proc/self/fd/{descriptor}")
        expected: dict[str, tuple[str, str]] = {}
        for record in _git("ls-tree", "-r", "-z", "--full-tree", source_commit).split(
            b"\0"
        ):
            if not record:
                continue
            metadata, separator, relative_raw = record.partition(b"\t")
            fields = metadata.decode("ascii").split()
            relative = relative_raw.decode("utf-8")
            if (
                not separator
                or len(fields) != 3
                or fields[1] != "blob"
                or fields[0] not in {"100644", "100755"}
                or Path(relative).is_absolute()
                or ".." in Path(relative).parts
            ):
                raise ValueError("Git tree contains an unsupported Context runtime entry")
            expected[relative] = (fields[0], fields[2])
        actual: set[str] = set()
        for path in bound.rglob("*"):
            relative = str(path.relative_to(bound))
            state = path.stat(follow_symlinks=False)
            if (
                path.is_symlink()
                or state.st_uid != RUNTIME_OWNER_UID
                or state.st_gid != RUNTIME_OWNER_GID
                or state.st_mode & 0o022
            ):
                raise ValueError(
                    f"immutable Context runtime entry is not root:root protected: {relative}"
                )
            if stat.S_ISDIR(state.st_mode):
                continue
            if not stat.S_ISREG(state.st_mode):
                raise ValueError(
                    f"immutable Context runtime entry has unsupported type: {relative}"
                )
            actual.add(relative)
        if actual != set(expected):
            raise ValueError("immutable Context runtime file set differs from Git")
        for relative, (mode, object_id) in expected.items():
            path = bound / relative
            before = path.stat(follow_symlinks=False)
            raw = path.read_bytes()
            after = path.stat(follow_symlinks=False)
            if _stat_identity(before) != _stat_identity(after):
                raise ValueError(
                    f"immutable Context runtime changed during verification: {relative}"
                )
            if bool(after.st_mode & 0o111) != (mode == "100755"):
                raise ValueError(
                    f"immutable Context runtime mode differs from Git: {relative}"
                )
            if _git_blob_oid(raw) != object_id:
                raise ValueError(
                    f"immutable Context runtime bytes differ from Git: {relative}"
                )
        root_after = os.fstat(descriptor)
        if _stat_identity(root_state) != _stat_identity(root_after):
            raise ValueError("immutable Context release root changed during verification")
        return descriptor, bound / "src"
    except Exception:
        os.close(descriptor)
        raise


def _bootstrap_runtime() -> tuple[Path, Path, int, bytes, str, str]:
    raw = _protected_snapshot_raw()
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("current TGW context snapshot is invalid") from exc
    if (
        not isinstance(value, dict)
        or value.get("schema") != "tgw-current-context-snapshot/v1"
    ):
        raise ValueError("current TGW context snapshot schema is invalid")
    claimed = value.get("snapshot_sha256")
    body = dict(value)
    body.pop("snapshot_sha256", None)
    observed = "sha256:" + hashlib.sha256(_canonical(body)).hexdigest()
    if claimed != observed:
        raise ValueError("current TGW context snapshot hash differs")
    source_commit = value.get("source_commit")
    source_tree = value.get("source_tree")
    if (
        not isinstance(source_commit, str)
        or _COMMIT.fullmatch(source_commit) is None
        or not isinstance(source_tree, str)
        or _COMMIT.fullmatch(source_tree) is None
    ):
        raise ValueError("current TGW context snapshot source identity is invalid")
    release = RUNTIME_RELEASES / source_commit
    release_descriptor, server_source = _verify_bound_release(
        release, source_commit, source_tree
    )
    record_sha256 = "sha256:" + hashlib.sha256(raw).hexdigest()
    return release, server_source, release_descriptor, raw, observed, record_sha256


(
    RUNTIME_RELEASE,
    SERVER_SOURCE,
    _RUNTIME_RELEASE_DESCRIPTOR,
    _STARTUP_CONTEXT_RAW,
    _STARTUP_SNAPSHOT_SHA256,
    _STARTUP_RECORD_SHA256,
) = _bootstrap_runtime()

sys.path.insert(0, str(SERVER_SOURCE))

from tgw.current_context_snapshot import CurrentContextError  # noqa: E402
from tgw.current_context_snapshot import parse as parse_snapshot  # noqa: E402


def _harness_actor() -> str:
    actor = pwd.getpwuid(os.geteuid()).pw_name
    if actor not in HARNESS_ACTORS:
        raise ValueError("MCP process account is not a registered TGW harness actor")
    return actor


def _current_context() -> dict[str, Any]:
    """Return the immutable startup snapshot and reject a later generation."""
    if _protected_snapshot_raw() != _STARTUP_CONTEXT_RAW:
        raise ValueError("TGW Context generation changed; restart this harness session")
    try:
        value = parse_snapshot(json.loads(_STARTUP_CONTEXT_RAW))
    except (json.JSONDecodeError, CurrentContextError) as exc:
        raise ValueError("current TGW context snapshot is invalid") from exc
    value["record_path"] = str(CURRENT_CONTEXT)
    value["record_sha256"] = _STARTUP_RECORD_SHA256
    return value


def _current_task() -> str:
    actor = _harness_actor()
    context = _current_context()
    value = context["task"]
    result = {
        **value,
        "actor": actor,
        "receiver": actor,
        "context": {
            key: context[key]
            for key in (
                "active_capability",
                "active_treatment",
                "plan_commit",
                "source_commit",
                "source_tree",
                "snapshot_sha256",
            )
        },
        "record_path": context["record_path"],
        "record_sha256": context["record_sha256"],
    }
    return json.dumps(result, sort_keys=True, separators=(",", ":"))


def _bind_context_receiver(context_server: Any) -> None:
    original_plan_graph = context_server.plan_graph

    def actor_plan_graph(
        task: str, receiver: str = "", operation: str = "brief", limit: int = 12
    ) -> dict[str, object]:
        context = _current_context()
        selected = context["active_capability"] if task in {"", "current"} else task
        result = original_plan_graph(selected, _harness_actor(), operation, limit)
        result["current_context"] = {
            key: context[key]
            for key in (
                "active_capability",
                "active_treatment",
                "plan_commit",
                "source_commit",
                "source_tree",
                "snapshot_sha256",
            )
        }
        return result

    context_server.plan_graph = actor_plan_graph


def _retire_obsolete_tools(mcp: Any) -> None:
    for name in RETIRED_TOOLS:
        mcp.remove_tool(name)


def context_server_bundle(context_server: Any, task: str, limit: int) -> str:
    """Compose only existing read-only local bindings for the current actor."""

    def build() -> dict[str, Any]:
        if not isinstance(task, str) or not task.strip() or len(task) > 1_000:
            raise context_server.ContextError("task must be ordinary non-empty text")
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= 50
        ):
            raise context_server.ContextError("limit must be between 1 and 50")
        actor = _harness_actor()
        snapshot = _current_context()
        selected = snapshot["active_capability"] if task == "current" else task
        status = context_server.context_status()
        plan_graph = context_server.plan_graph(selected, actor, "brief", limit)
        runbooks = context_server.runbooks(selected, "", 1, 200, limit, "all")
        code_graph = context_server.code_graph("status", "", limit)
        status_after = context_server.context_status()
        snapshot_after = _current_context()

        def require(condition: bool, detail: str) -> None:
            if not condition:
                raise context_server.ContextError(
                    f"Context bundle binding mismatch: {detail}"
                )

        require(status == status_after, "status changed during retrieval")
        require(snapshot == snapshot_after, "snapshot changed during retrieval")
        require(status.get("actor") == actor, "status actor differs from Linux actor")
        require(
            status.get("generation_status", {}).get("state") == "CURRENT",
            "status generation is not CURRENT",
        )
        status_plan = status.get("plan", {})
        status_source = status.get("source", {})
        status_code = status.get("code_graph", {})
        status_context = status.get("current_context", {})
        require(
            status_plan.get("approved_commit") == snapshot.get("plan_commit"),
            "Plan commit differs from atomic snapshot",
        )
        require(
            status_source.get("commit") == snapshot.get("source_commit")
            and status_source.get("tree") == snapshot.get("source_tree"),
            "source identity differs from atomic snapshot",
        )
        for key in (
            "active_capability",
            "active_treatment",
            "plan_commit",
            "source_commit",
            "source_tree",
            "snapshot_sha256",
        ):
            require(
                status_context.get(key) == snapshot.get(key),
                f"status current_context {key} differs",
            )
        require(plan_graph.get("receiver") == actor, "Plan Graph receiver differs")
        require(
            plan_graph.get("plan_commit") == status_plan.get("approved_commit"),
            "Plan Graph commit differs",
        )
        require(
            plan_graph.get("plan_tree") == status_plan.get("approved_tree"),
            "Plan Graph tree differs",
        )
        require(
            plan_graph.get("approved_solution_hash")
            == status_plan.get("approved_solution_hash"),
            "Plan Graph solution differs",
        )
        require(
            plan_graph.get("current_context")
            == {
                key: snapshot[key]
                for key in (
                    "active_capability",
                    "active_treatment",
                    "plan_commit",
                    "source_commit",
                    "source_tree",
                    "snapshot_sha256",
                )
            },
            "Plan Graph atomic context differs",
        )
        code_binding = code_graph.get("binding", {})
        for key in ("commit", "tree", "freshness_hash"):
            require(
                code_binding.get(key) == status_code.get(key),
                f"CodeGraph {key} differs",
            )
        expected_runbook_revisions = {
            "canonical-plan-runbook": (
                status_plan.get("evidence_head"),
                status_plan.get("evidence_tree"),
            ),
            "committed-application-runbook": (
                status_source.get("commit"),
                status_source.get("tree"),
            ),
        }
        revisions = runbooks.get("revisions")
        require(isinstance(revisions, list), "runbook revisions are missing")
        observed_authorities: set[str] = set()
        for revision in revisions:
            require(isinstance(revision, dict), "runbook revision is malformed")
            authority = revision.get("authority")
            expected = expected_runbook_revisions.get(authority)
            require(expected is not None, "runbook authority is unexpected")
            require(
                (revision.get("commit"), revision.get("tree")) == expected,
                f"{authority} identity differs",
            )
            observed_authorities.add(authority)
        require(
            observed_authorities == set(expected_runbook_revisions),
            "runbook authority set is incomplete",
        )
        result = {
            "schema": "tgw-context-bundle/v2-local-read-only",
            "ok": True,
            "task": selected,
            "actor": actor,
            "receiver": actor,
            "status": status,
            "plan_graph": plan_graph,
            "runbooks": runbooks,
            "code_graph": code_graph,
            "current_context": {
                key: snapshot[key]
                for key in (
                    "active_capability",
                    "active_treatment",
                    "plan_commit",
                    "source_commit",
                    "source_tree",
                    "snapshot_sha256",
                )
            },
            "runtime": {
                "release": str(RUNTIME_RELEASE),
                "source": str(SERVER_SOURCE),
                "snapshot_sha256": _STARTUP_SNAPSHOT_SHA256,
                "snapshot_record_sha256": _STARTUP_RECORD_SHA256,
            },
            "dependencies": {
                "authority": False,
                "grant": False,
                "approval": False,
                "admission": False,
                "dispatch": False,
                "execution": False,
                "database": False,
                "queue": False,
                "tgw_prod": False,
                "provider": False,
            },
        }
        result["bundle_sha256"] = (
            "sha256:"
            + hashlib.sha256(
                json.dumps(result, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
        )
        return result

    return context_server._json_call(build)


def main() -> None:
    if socket.gethostname().split(".", 1)[0] != "tgw-lib":
        raise SystemExit("tgw-context-mcp is available only on tgw-lib")
    for path in (SERVER_SOURCE, CONTEXT_SOURCE, CATALOG, CURRENT_CONTEXT):
        if not path.exists():
            raise SystemExit(f"required TGW context input is unavailable: {path}")

    with tempfile.TemporaryDirectory(
        prefix=f"tgw-context-{_harness_actor()}-"
    ) as runtime:
        os.environ.update(
            {
                "HOME": str(Path.home()),
                "LANG": "C.UTF-8",
                "LC_ALL": "C.UTF-8",
                "PATH": "/usr/bin:/bin",
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONPATH": str(SERVER_SOURCE),
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_GLOBAL": "/dev/null",
                "GIT_OPTIONAL_LOCKS": "0",
                "TGW_CONTEXT_PLAN_ROOT": "/opt/TGW/library/approved/058e2f980201cc78245358e4901cf007063f2c29",
                "TGW_CONTEXT_PLAN_REPOSITORY": "/opt/TGW/library/plans",
                "TGW_CONTEXT_PLAN_COMMIT": "058e2f980201cc78245358e4901cf007063f2c29",
                "TGW_CONTEXT_PLAN_SOLUTION": "sha256:ecce15aad2699492c0c5577bff1af7005ffbbec6ae6166b325b34c1cc7e70e9f",
                "TGW_CONTEXT_SOURCE_ROOT": str(CONTEXT_SOURCE),
                "TGW_CONTEXT_RUNTIME_ROOT": runtime,
                "TGW_CONTEXT_ENVIRONMENT_CATALOG": str(CATALOG),
                "TGW_CONTEXT_ENVIRONMENT_CATALOG_HASH": "sha256:8f81f755a25cb54b53c751f8fa6b554f5076cc8106dd91ee395fe9a8206e9894",
            }
        )
        from tgw import context_mcp_server
        from tgw.local_context_runtime import install as install_local_context

        install_local_context(
            context_mcp_server, current_context=_current_context, actor=_harness_actor
        )
        _bind_context_receiver(context_mcp_server)
        context_main = context_mcp_server.main
        mcp = context_mcp_server.mcp
        _retire_obsolete_tools(mcp)

        @mcp.tool()
        def tgw_context_bundle(task: str = "current", limit: int = 12) -> str:
            """Return the exact actor-bound local planning and source bindings."""
            return context_server_bundle(context_mcp_server, task, limit)

        @mcp.tool()
        def tgw_context_current_task() -> str:
            """Return the current TGW task bound to this Linux harness actor."""
            return _current_task()

        context_main()


if __name__ == "__main__":
    main()
