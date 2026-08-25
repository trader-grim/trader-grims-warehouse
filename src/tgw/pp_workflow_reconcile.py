"""Fail-closed PP-WORKFLOW-001 reconciliation through native and Luet solvers."""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from tgw.plan_luet import conform, load_direct_development_luet_binding, verify_direct_development_luet
from tgw.plan_solver import solve

PLAN_COMMIT = "058e2f980201cc78245358e4901cf007063f2c29"
PP_REF = "PP-WORKFLOW-001"
PP_SOURCE_SHA256 = "sha256:ee5eac22eb072649ea601d77f398ee87e8397f9a84eb3675b4d61f1c32f81af9"
REJECTED_CANDIDATE = "f71793588565b7094c877bfb37b4ac8f0c865129"
REJECTED_PARENT = "5f33481abf7f77cac54446345c22b230fbe1e06b"
REJECTED_TREE = "b5c6e400392182ee343c94e94f89e86939b4113b"
ROOT = Path(__file__).resolve().parents[2]
CATALOG = ROOT / "agent-services/catalogs/pp-workflow-001-v1.json"
CATALOG_SHA256 = "sha256:6aa0788e5e42c9a48a300284f074839958faf055d909f85c2680b35be6675320"
APPROVED_PLAN_ROOT = Path("/opt/TGW/library/approved") / PLAN_COMMIT


class PPWorkflowReconcileError(ValueError):
    pass


def _sha(path: Path) -> str:
    try:
        return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise PPWorkflowReconcileError(f"evidence source is unavailable: {path}") from exc


def _git(*args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=False)
    if proc.returncode:
        raise PPWorkflowReconcileError("source Git identity is unavailable")
    return proc.stdout.strip()


def load_catalog(path: Path | str = CATALOG) -> dict[str, Any]:
    """Load the production catalog against its compiled, non-overridable hash."""
    if _sha(Path(path)) != CATALOG_SHA256:
        raise PPWorkflowReconcileError("whole PP capability catalog hash drift")
    try:
        value = json.loads(Path(path).read_bytes())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PPWorkflowReconcileError("PP capability catalog is unavailable") from exc
    expected = {
        "pp_ref": PP_REF, "plan_commit": PLAN_COMMIT,
        "plan_source_sha256": PP_SOURCE_SHA256,
        "rejected_candidate": REJECTED_CANDIDATE, "rejected_parent": REJECTED_PARENT,
        "rejected_tree": REJECTED_TREE,
    }
    if (not isinstance(value, Mapping)
            or value.get("schema") != "tgw-pp-capability-catalog/v2"
            or value.get("identity") != expected):
        raise PPWorkflowReconcileError("PP source/candidate/review identity drift")
    return dict(value)


def _verify_plan_source(plan_root: Path) -> None:
    if _sha(plan_root / "plan/pp/PP-WORKFLOW-001.md") != PP_SOURCE_SHA256:
        raise PPWorkflowReconcileError("approved PP source content hash drift")


def _repository_git(repository: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-c", f"safe.directory={repository.resolve()}", *args], cwd=repository,
        capture_output=True, text=True, check=False,
    )
    if proc.returncode:
        raise PPWorkflowReconcileError("selected runtime Git identity is unavailable")
    return proc.stdout.strip()


def _repository_bytes(repository: Path, commit: str, relative: str) -> bytes:
    proc = subprocess.run(
        ["git", "-c", f"safe.directory={repository.resolve()}", "show", f"{commit}:{relative}"],
        cwd=repository, capture_output=True, check=False,
    )
    if proc.returncode:
        raise PPWorkflowReconcileError(f"tracked evidence is absent from selected commit: {relative}")
    return proc.stdout


def _source_status(source_root: Path) -> list[str]:
    raw = _repository_git(source_root, "status", "--porcelain=v1", "--untracked-files=all")
    dirty = []
    for line in raw.splitlines():
        relative = line[3:].split(" -> ")[-1]
        # Codex creates this request-bound scratch below the worktree. It is
        # untracked and cannot alter a tracked evidence byte.
        path = source_root / relative
        known_scratch = (line.startswith("?? ")
                         and relative.startswith(".tgw-codex-implement-")
                         and "/" in relative)
        known_receipt = (line.startswith("?? ")
                         and relative in {"implementation-receipt.json",
                                          "controller-harness-receipt.json"}
                         and path.is_file() and not path.is_symlink())
        if not (known_scratch or known_receipt):
            dirty.append(line)
    return dirty


def verify_selected_runtime(*, repository: Path, source_root: Path, commit: str,
                            tree: str, mode: str) -> dict[str, Any]:
    """Verify the caller-selected exact reachable source before any allocation."""
    if mode not in {"source-worktree", "immutable-release"}:
        raise PPWorkflowReconcileError("selected runtime mode is unsupported")
    if _repository_git(repository, "cat-file", "-t", commit) != "commit":
        raise PPWorkflowReconcileError("selected runtime commit is not reachable")
    observed_tree = _repository_git(repository, "rev-parse", f"{commit}^{{tree}}")
    if observed_tree != tree:
        raise PPWorkflowReconcileError("selected runtime commit/tree binding differs")
    root_common = Path(_repository_git(repository, "rev-parse", "--git-common-dir"))
    if not root_common.is_absolute():
        root_common = repository / root_common
    if mode == "source-worktree":
        common = Path(_repository_git(source_root, "rev-parse", "--git-common-dir"))
        if not common.is_absolute():
            common = source_root / common
        if common.resolve() != root_common.resolve():
            raise PPWorkflowReconcileError("selected runtime is not bound to the canonical repository")
        if Path(_repository_git(source_root, "rev-parse", "--show-toplevel")).resolve() != source_root.resolve():
            raise PPWorkflowReconcileError("selected source worktree root differs")
        if (_repository_git(source_root, "rev-parse", "HEAD") != commit or _source_status(source_root)):
            raise PPWorkflowReconcileError("selected source worktree is not the exact clean commit")
        evidence = {"verified": True, "manifest_source": "git-worktree", "file_count": None}
    else:
        # Reuse Doctor's full path/mode/blob verifier; PP reconciliation does
        # not maintain a second, weaker release verifier.
        from tgw.doctor_cli import DoctorError, DoctorPaths, _verify_release_tree
        try:
            evidence = _verify_release_tree(
                DoctorPaths(repository=repository, runtime_root=source_root.parent.parent),
                commit, source_root,
            )
        except DoctorError as exc:
            raise PPWorkflowReconcileError("selected immutable release differs from exact Git tree") from exc
    return {**evidence, "mode": mode, "commit": commit, "tree": tree,
            "repository": str(repository.resolve()), "source_root": str(source_root.resolve())}


def _verified_graph(catalog: Mapping[str, Any], *, repository: Path, source_root: Path,
                    selected_commit: str,
                    todo_rows: Sequence[Mapping[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    graph = dict(catalog.get("graph", {}))
    providers = graph.get("providers")
    evidence = catalog.get("provider_evidence")
    if not isinstance(providers, list) or not isinstance(evidence, Mapping):
        raise PPWorkflowReconcileError("PP provider catalog is incomplete")
    rows = list(todo_rows)
    states: list[dict[str, Any]] = []
    observations = []
    for provider in providers:
        provider_id = provider.get("id")
        spec = evidence.get(provider_id) if isinstance(provider_id, str) else None
        if not isinstance(spec, Mapping):
            raise PPWorkflowReconcileError("PP provider evidence is missing")
        if "receipts" in spec:
            raise PPWorkflowReconcileError(
                "source-tree catalog receipts cannot independently admit capability")
        verified: list[str] = []
        for item in spec.get("sources", ()):
            if not isinstance(item, Mapping) or set(item) != {"path", "sha256"}:
                raise PPWorkflowReconcileError("source evidence identity is malformed")
            relative = str(item["path"])
            path = source_root / relative
            try:
                actual = path.read_bytes()
            except OSError as exc:
                raise PPWorkflowReconcileError(f"source evidence is unavailable: {relative}") from exc
            tracked = _repository_bytes(repository, selected_commit, relative)
            if actual != tracked or "sha256:" + hashlib.sha256(actual).hexdigest() != item["sha256"]:
                raise PPWorkflowReconcileError(f"source evidence content drift: {item['path']}")
            verified.append(f"source:{item['path']}@{item['sha256']}")
        for identity in spec.get("local_todos", ()):
            if not isinstance(identity, Mapping):
                raise PPWorkflowReconcileError("local Todo evidence identity is malformed")
            if next((row for row in rows if all(row.get(k) == v for k, v in identity.items())), None):
                verified.append("local-todo:" + hashlib.sha256(
                    json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest())
        # Source and Todo identity prove implementation evidence only. A JSON
        # file committed beside the source is source-authenticated and cannot
        # independently establish the PP target's admitted state.
        admitted = False
        evidence_state = "IMPLEMENTED_UNVERIFIED" if verified else "UNVERIFIED"
        states.append({"id": provider_id, "state": evidence_state, "evidence": verified})
        if admitted:
            for capability in provider.get("provides", ()):
                observations.append({"capability": capability, "provider": provider_id,
                                     "state": "admitted", "evidence": verified})
    graph["observations"] = observations
    return graph, states


def reconcile(*, catalog_path: Path | str = CATALOG, plan_root: Path = APPROVED_PLAN_ROOT,
              source_root: Path = ROOT, todo_rows: Sequence[Mapping[str, Any]] = (),
              conform_fn: Callable[..., Mapping[str, Any]] = conform,
              repository: Path = ROOT, selected_commit: str | None = None,
              selected_tree: str | None = None, runtime_mode: str = "source-worktree",
              runtime_verifier: Callable[..., Mapping[str, Any]] = verify_selected_runtime) -> dict[str, Any]:
    """Content-verify evidence and solve the same PP graph with native and pinned Luet."""
    catalog = load_catalog(catalog_path)
    _verify_plan_source(plan_root)
    commit = selected_commit or _repository_git(source_root, "rev-parse", "HEAD")
    tree = selected_tree or _repository_git(repository, "rev-parse", f"{commit}^{{tree}}")
    runtime = dict(runtime_verifier(repository=repository, source_root=source_root,
                                    commit=commit, tree=tree, mode=runtime_mode))
    graph, providers = _verified_graph(catalog, repository=repository, source_root=source_root,
                                       selected_commit=commit, todo_rows=todo_rows)
    binding = load_direct_development_luet_binding()
    if binding.plan_commit != PLAN_COMMIT:
        raise PPWorkflowReconcileError("pinned Luet Plan binding drift")
    try:
        verify_direct_development_luet(binding.executable_path, plan_commit=PLAN_COMMIT)
        luet = dict(conform_fn(graph, luet_binary=binding.executable_path,
                              expected_plan_commit=PLAN_COMMIT))
        solution = solve(graph, expected_plan_commit=PLAN_COMMIT, conformance_result=luet)
    except (OSError, ValueError) as exc:
        raise PPWorkflowReconcileError("PP native/Luet conformance failed closed") from exc
    if luet.get("status") != "AGREEMENT" or not solution.get("conformance_verified"):
        raise PPWorkflowReconcileError("PP native/Luet solver disagreement")
    complete = bool(solution.get("complete") and not solution.get("work_units"))
    return {
        "schema": "tgw-pp-runtime-projection/v2", "ok": complete, "pp_ref": PP_REF,
        "source_identity": {"plan_commit": PLAN_COMMIT, "plan_source_sha256": PP_SOURCE_SHA256,
                            "catalog_sha256": CATALOG_SHA256, "runtime": runtime,
                            "rejected_candidate": REJECTED_CANDIDATE,
                            "rejected_parent": REJECTED_PARENT, "rejected_tree": REJECTED_TREE},
        "resolver_binding": {"native": solution["resolver"], "luet": luet["provider_id"],
                             "agreement": "verified", "executable_path": str(binding.executable_path),
                             "executable_sha256": binding.sha256, "version": binding.version},
        "providers": providers,
        "unmet_capabilities": [item["capability"] for item in solution.get("work_units", ())],
        "solution": solution,
        "dimensions": {"reconciliation_complete": complete, "operation_success": True,
                       "materialization_attempted": False},
        "effects": {"todo_created": False, "worktree_created": False, "job_created": False,
                    "plan_publication": False},
    }
