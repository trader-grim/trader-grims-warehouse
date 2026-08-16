"""Pinned immutable receipt retrieval for governed candidate admission.

Candidate evidence is not accepted from a reviewer's worktree or from paths
named by the candidate.  The trust boundary is a separately provisioned sink
descriptor which pins an external Git commit and tree.  Every artifact is read
from that Git object, checked against the sink manifest, then checked again
against the bundle that names it.

This module deliberately has no writer and no default sink.  Installing a
trusted receipt service or its trust anchor remains an operational action;
without a configured, externally located sink the admission gate holds.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path, PurePosixPath
from typing import Any, Iterator, Mapping, Protocol

import yaml

from tgw.candidate_manifest import (
    CANONICAL_TEST_PLAN_PATH,
    CandidateManifestError,
    graph_hash,
    validate_candidate_test_plan,
    verify_luet_conformance_receipt,
    verify_migration_safety_receipt,
    verify_predecessor_release,
    verify_test_receipt,
)
from tgw.candidate_review import (
    CandidateReviewError,
    candidate_identity,
    validate_review_report,
    validate_review_result,
)
from tgw.governed_coding import admission_gate
from tgw.governed_execution_receipt import (
    GovernedExecutionReceiptError,
    verify_candidate_governed_execution_receipt,
)
from tgw.plan_catalog import compose_catalog
from tgw.plan_luet import (
    PINNED_LUET_BINARY_SHA256,
    PROVIDER_ID,
)
from tgw.plan_solver import PlanResolutionError, solve, validate_for_dispatch
from tgw.qualified_execution_service import (
    QualifiedExecutionError,
    validate_execution_proof,
    validate_execution_service_catalog,
)


class ProtectedGitReader(Protocol):
    """Exact held Git/repository authority used by production admission."""

    def run_git(self, *arguments: str) -> bytes: ...
    def run_git_status(self, *arguments: str) -> tuple[int, bytes]: ...
    def postcheck(self) -> None: ...


_PROTECTED_GIT_READERS: ContextVar[Mapping[Path, ProtectedGitReader] | None] = ContextVar(
    "tgw_protected_git_readers",
    default=None,
)

RECEIPT_SINK_SCHEMA = "tgw-pinned-git-candidate-receipt-sink/v1"
RECEIPT_SINK_MANIFEST_SCHEMA = "tgw-pinned-git-candidate-receipt-sink-manifest/v1"
PINNED_CANDIDATE_EVIDENCE_DESCRIPTOR_SCHEMA = "tgw-pinned-git-candidate-evidence-descriptor/v1"
CANDIDATE_EVIDENCE_DESCRIPTOR_SCHEMA = "tgw-candidate-evidence-descriptor/v4"
CANDIDATE_EVIDENCE_CARD_BINDING_SCHEMA = "tgw-candidate-evidence-descriptor-card-binding/v5"
PINNED_W06_PLAN_MATERIALIZATION_SCHEMA = "tgw-pinned-git-w06-plan-materialization/v3"
PINNED_W06_PLAN_SOURCE_SCHEMA = "tgw-pinned-git-w06-plan-source/v1"
PINNED_W06_PLAN_SOLUTION_SCHEMA = "tgw-pinned-git-w06-plan-solution/v1"
W06_PLAN_SOURCE_PATH = "plan/execution/GOVERNED-EXECUTION-PLATFORM-v1.yaml"
W06_PLAN_SOLUTION_PATH_PREFIX = "plan/execution/solutions/GOVERNED-EXECUTION-PLATFORM-"
W06_CANDIDATE_CATALOG_PATH = "agent-services/catalogs/governed-execution-platform-v1.json"
GOVERNED_EXECUTION_BUNDLE_SCHEMA = "tgw-candidate-governed-execution-bundle/v2"
INDEPENDENT_REVIEW_EVIDENCE_BUNDLE_SCHEMA = "tgw-candidate-independent-review-evidence-bundle/v4"
GOVERNED_CANDIDATE_ADMISSION_SCHEMA = "tgw-governed-candidate-admission-gate/v2"
GOVERNED_CANDIDATE_PLAN_AUTHORITY_SCHEMA = "tgw-governed-candidate-plan-authority/v1"
CANDIDATE_EVIDENCE_BUNDLE_SCHEMA = "tgw-candidate-evidence-bundle/v5"
ROLLBACK_MANIFEST_SCHEMA = "tgw-governed-candidate-rollback-manifest/v1"

GOVERNED_ROLES = (
    "implementation",
    "independent-review",
    "controller-verification",
)

_GIT_OBJECT = re.compile(r"[0-9a-f]{40}\Z")
_SHA256 = re.compile(r"sha256:[0-9a-f]{64}\Z")
_SINK_ID = re.compile(r"[a-z][a-z0-9-]{0,63}\Z")
_RELEASE_GENERATION = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}\Z")
_ARTIFACT_REF = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@=-]{0,255}\Z")
_PLAN_REF = re.compile(r"refs/[A-Za-z0-9][A-Za-z0-9._/-]{0,239}\Z")
_BUNDLE_ARTIFACTS = (
    "candidate_receipt",
    "card",
    "resource_receipt",
    "role_receipt",
    "resource_service_catalog",
)
_CANDIDATE_EVIDENCE_ARTIFACTS = (
    "candidate_manifest",
    "luet_conformance_receipt",
    "focused_test_receipt",
    "focused_test_output",
    "full_suite_test_receipt",
    "full_suite_test_output",
    "release_manifest",
    "rollback_manifest",
    "qualified_execution_catalog",
    "qualified_execution_runner_descriptor",
)
_INDEPENDENT_REVIEW_EVIDENCE_ARTIFACTS = (
    "review_packet",
    "review_report",
    "review_result",
    "qualified_execution_catalog",
    "qualified_execution_runner_descriptor",
    "review_execution_proof",
    "review_execution_transcript",
)


class CandidateReceiptSinkError(ValueError):
    """The configured immutable receipt sink cannot establish candidate evidence."""


@contextmanager
def protected_git_object_reads(
    readers: Mapping[Path, ProtectedGitReader],
) -> Iterator[None]:
    """Route every admission Git read through caller-held repository FDs."""

    normalized = {Path(root).resolve(strict=True): reader for root, reader in readers.items()}
    if len(normalized) != len(readers) or any(not all(callable(getattr(reader, name, None)) for name in ("run_git", "run_git_status", "postcheck")) for reader in normalized.values()):
        raise CandidateReceiptSinkError("protected Git reader mapping is invalid")
    token = _PROTECTED_GIT_READERS.set(normalized)
    try:
        for reader in normalized.values():
            reader.postcheck()
        yield
        for reader in normalized.values():
            reader.postcheck()
    finally:
        _PROTECTED_GIT_READERS.reset(token)


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()
    except (TypeError, ValueError) as exc:
        raise CandidateReceiptSinkError("receipt sink value is not canonical JSON data") from exc


def _hash_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _hash(value: Any) -> str:
    return _hash_bytes(_canonical(value))


def _safe_path(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise CandidateReceiptSinkError(f"{label} is invalid")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or str(path) != value:
        raise CandidateReceiptSinkError(f"{label} must be a contained Git path")
    return value


def _object(value: bytes, *, label: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CandidateReceiptSinkError(f"{label} is not JSON") from exc
    if not isinstance(parsed, dict):
        raise CandidateReceiptSinkError(f"{label} must be a JSON object")
    return parsed


def _git(repository: Path, *args: str) -> bytes:
    readers = _PROTECTED_GIT_READERS.get()
    if readers is not None:
        try:
            reader = readers[Path(repository).resolve(strict=True)]
        except (KeyError, OSError) as exc:
            raise CandidateReceiptSinkError("production admission attempted an unheld Git repository") from exc
        return reader.run_git(*args)
    try:
        return subprocess.run(
            ["git", *args],
            cwd=repository,
            check=True,
            capture_output=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        raise CandidateReceiptSinkError("pinned receipt sink Git object is unavailable") from exc


def _git_status(repository: Path, *args: str) -> tuple[int, bytes]:
    readers = _PROTECTED_GIT_READERS.get()
    if readers is not None:
        try:
            reader = readers[Path(repository).resolve(strict=True)]
        except (KeyError, OSError) as exc:
            raise CandidateReceiptSinkError("production admission attempted an unheld Git repository") from exc
        return reader.run_git_status(*args)
    try:
        result = subprocess.run(["git", *args], cwd=repository, check=False, capture_output=True)
    except OSError as exc:
        raise CandidateReceiptSinkError("pinned receipt sink Git object is unavailable") from exc
    return result.returncode, result.stdout


def _candidate_identity(repository: Path, candidate: str) -> tuple[str, str]:
    commit = _git(repository, "rev-parse", f"{candidate}^{{commit}}").decode().strip()
    tree = _git(repository, "rev-parse", f"{commit}^{{tree}}").decode().strip()
    if _GIT_OBJECT.fullmatch(commit) is None or _GIT_OBJECT.fullmatch(tree) is None:
        raise CandidateReceiptSinkError("candidate Git identity is invalid")
    return commit, tree


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _paths_overlap(left: Path, right: Path) -> bool:
    """Return whether either configured root contains the other.

    Receipt and Plan repositories are operator-controlled trust roots.  A
    candidate may not be their parent *or* their child: checking only the
    latter leaves a candidate nested beneath a supposedly external sink.
    """

    return _is_within(left, right) or _is_within(right, left)


def governed_execution_bundle_ref(source_commit: str, role: str) -> str:
    """Return the one sink reference reserved for a role's evidence bundle."""

    if _GIT_OBJECT.fullmatch(source_commit) is None or role not in GOVERNED_ROLES:
        raise CandidateReceiptSinkError("governed execution bundle identity is invalid")
    return f"candidate:{source_commit}:governed-execution:{role}"


def candidate_evidence_bundle_ref(source_commit: str) -> str:
    """Return the one S-store reference reserved for W08 candidate evidence.

    The name is derived solely from the candidate commit.  That prevents a
    candidate from choosing an unrelated, otherwise valid evidence package.
    """

    if _GIT_OBJECT.fullmatch(source_commit) is None:
        raise CandidateReceiptSinkError("candidate evidence bundle identity is invalid")
    return f"candidate:{source_commit}:candidate-evidence:v4"


def independent_review_evidence_bundle_ref(source_commit: str) -> str:
    """Return the one X-store reference reserved for independent review output."""

    if _GIT_OBJECT.fullmatch(source_commit) is None:
        raise CandidateReceiptSinkError("independent review evidence bundle identity is invalid")
    return f"candidate:{source_commit}:independent-review-evidence:v3"


def validate_receipt_sink_descriptor(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a configuration which pins a Git receipt sink, not a mutable ref."""

    required = {
        "schema",
        "sink_id",
        "repository",
        "commit",
        "tree",
        "manifest_path",
        "manifest_content_sha256",
    }
    if not isinstance(value, Mapping) or set(value) != required or value.get("schema") != RECEIPT_SINK_SCHEMA:
        raise CandidateReceiptSinkError("pinned receipt sink descriptor is invalid")
    if not isinstance(value.get("sink_id"), str) or _SINK_ID.fullmatch(value["sink_id"]) is None:
        raise CandidateReceiptSinkError("pinned receipt sink identity is invalid")
    repository = value.get("repository")
    if not isinstance(repository, str) or not repository or not Path(repository).is_absolute():
        raise CandidateReceiptSinkError("pinned receipt sink repository must be absolute")
    for field in ("commit", "tree"):
        if not isinstance(value.get(field), str) or _GIT_OBJECT.fullmatch(value[field]) is None:
            raise CandidateReceiptSinkError("pinned receipt sink Git identity is invalid")
    if not isinstance(value.get("manifest_content_sha256"), str) or _SHA256.fullmatch(value["manifest_content_sha256"]) is None:
        raise CandidateReceiptSinkError("pinned receipt sink manifest content hash is invalid")
    return {
        "schema": RECEIPT_SINK_SCHEMA,
        "sink_id": value["sink_id"],
        "repository": repository,
        "commit": value["commit"],
        "tree": value["tree"],
        "manifest_path": _safe_path(value["manifest_path"], label="pinned receipt sink manifest path"),
        "manifest_content_sha256": value["manifest_content_sha256"],
    }


def load_receipt_sink_descriptor(path: str | Path, *, candidate_repository: Path) -> dict[str, Any]:
    """Load a separately configured sink descriptor and reject candidate-local config."""

    try:
        descriptor_path = Path(path).resolve(strict=True)
        candidate_root = candidate_repository.resolve(strict=True)
    except OSError as exc:
        raise CandidateReceiptSinkError("pinned receipt sink configuration is unavailable") from exc
    if _paths_overlap(descriptor_path, candidate_root):
        raise CandidateReceiptSinkError("pinned receipt sink configuration must be disjoint from the candidate repository")
    try:
        value = json.loads(descriptor_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CandidateReceiptSinkError("pinned receipt sink configuration is unreadable") from exc
    return validate_receipt_sink_descriptor(value)


def validate_pinned_candidate_evidence_descriptor(value: Mapping[str, Any]) -> dict[str, str]:
    """Validate an immutable Git pin for the external S descriptor object."""

    required = {"schema", "repository", "commit", "tree", "path", "content_sha256"}
    if not isinstance(value, Mapping) or set(value) != required or value.get("schema") != PINNED_CANDIDATE_EVIDENCE_DESCRIPTOR_SCHEMA:
        raise CandidateReceiptSinkError("pinned candidate evidence descriptor is invalid")
    repository = value.get("repository")
    if not isinstance(repository, str) or not repository or not Path(repository).is_absolute():
        raise CandidateReceiptSinkError("pinned candidate evidence descriptor repository must be absolute")
    for field in ("commit", "tree"):
        if not isinstance(value.get(field), str) or _GIT_OBJECT.fullmatch(value[field]) is None:
            raise CandidateReceiptSinkError("pinned candidate evidence descriptor Git identity is invalid")
    content_hash = value.get("content_sha256")
    if not isinstance(content_hash, str) or _SHA256.fullmatch(content_hash) is None:
        raise CandidateReceiptSinkError("pinned candidate evidence descriptor content hash is invalid")
    return {
        "schema": PINNED_CANDIDATE_EVIDENCE_DESCRIPTOR_SCHEMA,
        "repository": repository,
        "commit": value["commit"],
        "tree": value["tree"],
        "path": _safe_path(value.get("path"), label="pinned candidate evidence descriptor path"),
        "content_sha256": content_hash,
    }


def _validate_pinned_w06_git_artifact(
    value: Mapping[str, Any],
    *,
    schema: str,
    label: str,
) -> dict[str, str]:
    """Validate one immutable Git content pin owned by D."""

    required = {"schema", "repository", "commit", "tree", "path", "content_sha256"}
    if not isinstance(value, Mapping) or set(value) != required or value.get("schema") != schema:
        raise CandidateReceiptSinkError(f"pinned {label} descriptor is invalid")
    repository = value.get("repository")
    if not isinstance(repository, str) or not repository or not Path(repository).is_absolute():
        raise CandidateReceiptSinkError(f"pinned {label} repository must be absolute")
    for field in ("commit", "tree"):
        if not isinstance(value.get(field), str) or _GIT_OBJECT.fullmatch(value[field]) is None:
            raise CandidateReceiptSinkError(f"pinned {label} Git identity is invalid")
    content_hash = value.get("content_sha256")
    if not isinstance(content_hash, str) or _SHA256.fullmatch(content_hash) is None:
        raise CandidateReceiptSinkError(f"pinned {label} content hash is invalid")
    return {
        "schema": schema,
        "repository": repository,
        "commit": value["commit"],
        "tree": value["tree"],
        "path": _safe_path(value.get("path"), label=f"pinned {label} path"),
        "content_sha256": content_hash,
    }


def validate_pinned_w06_plan_materialization(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate D's pins to the approved source YAML and retained solution.

    The source pin must name the one execution document the approved ref
    authorizes.  A later solution artifact is evidence, never a replacement
    for that source intent.
    """

    required = {"schema", "plan_source", "solution"}
    if not isinstance(value, Mapping) or set(value) != required or value.get("schema") != PINNED_W06_PLAN_MATERIALIZATION_SCHEMA:
        raise CandidateReceiptSinkError("pinned W06 Plan materialization descriptor is invalid")
    plan_source = value.get("plan_source")
    solution = value.get("solution")
    if not isinstance(plan_source, Mapping) or not isinstance(solution, Mapping):
        raise CandidateReceiptSinkError("pinned W06 Plan source or solution descriptor is invalid")
    source_pin = _validate_pinned_w06_git_artifact(
        plan_source,
        schema=PINNED_W06_PLAN_SOURCE_SCHEMA,
        label="W06 Plan source",
    )
    if source_pin["path"] != W06_PLAN_SOURCE_PATH:
        raise CandidateReceiptSinkError("pinned W06 Plan source path is not allowed")
    solution_pin = _validate_pinned_w06_git_artifact(
        solution,
        schema=PINNED_W06_PLAN_SOLUTION_SCHEMA,
        label="W06 Plan solution",
    )
    expected_solution_path = f"{W06_PLAN_SOLUTION_PATH_PREFIX}{source_pin['commit'][:7]}.json"
    if solution_pin["path"] != expected_solution_path:
        raise CandidateReceiptSinkError("pinned W06 Plan solution path is not allowed")
    return {
        "schema": PINNED_W06_PLAN_MATERIALIZATION_SCHEMA,
        "plan_source": source_pin,
        "solution": solution_pin,
    }


class PinnedCandidateEvidenceDescriptor:
    """Load D from an independently pinned Git object before cards are created.

    D names the complete, immutable candidate-evidence S store.  It is not an
    artifact in S or in the later execution/review X store, which keeps the
    dependency graph acyclic: ``S -> D -> cards -> X``.
    """

    def __init__(self, pin: Mapping[str, Any], *, candidate_repository: Path) -> None:
        self._pin = validate_pinned_candidate_evidence_descriptor(pin)
        try:
            self._repository = Path(self._pin["repository"]).resolve(strict=True)
            candidate_root = candidate_repository.resolve(strict=True)
        except OSError as exc:
            raise CandidateReceiptSinkError("pinned candidate evidence descriptor repository is unavailable") from exc
        if _paths_overlap(self._repository, candidate_root):
            raise CandidateReceiptSinkError("pinned candidate evidence descriptor repository must be disjoint from the candidate repository")
        commit, tree = _candidate_identity(self._repository, self._pin["commit"])
        if commit != self._pin["commit"] or tree != self._pin["tree"]:
            raise CandidateReceiptSinkError("pinned candidate evidence descriptor Git identity mismatch")
        raw = _git(self._repository, "show", f"{commit}:{self._pin['path']}")
        if _hash_bytes(raw) != self._pin["content_sha256"]:
            raise CandidateReceiptSinkError("pinned candidate evidence descriptor content hash mismatch")
        value = _object(raw, label="pinned candidate evidence descriptor")
        required = {"schema", "candidate_evidence_sink", "w06_plan_materialization"}
        if (
            set(value) != required
            or value.get("schema") != CANDIDATE_EVIDENCE_DESCRIPTOR_SCHEMA
            or not isinstance(value.get("candidate_evidence_sink"), Mapping)
            or not isinstance(value.get("w06_plan_materialization"), Mapping)
        ):
            raise CandidateReceiptSinkError("candidate evidence descriptor is invalid")
        sink = validate_receipt_sink_descriptor(value["candidate_evidence_sink"])
        materialization = validate_pinned_w06_plan_materialization(value["w06_plan_materialization"])
        try:
            sink_repository = Path(sink["repository"]).resolve(strict=True)
            plan_source_repository = Path(materialization["plan_source"]["repository"]).resolve(strict=True)
            solution_repository = Path(materialization["solution"]["repository"]).resolve(strict=True)
        except OSError as exc:
            raise CandidateReceiptSinkError("candidate evidence, W06 Plan source, or solution repository is unavailable") from exc
        if _paths_overlap(self._repository, sink_repository):
            raise CandidateReceiptSinkError("candidate evidence descriptor authority and evidence sink must be disjoint")
        if _paths_overlap(self._repository, plan_source_repository):
            raise CandidateReceiptSinkError("candidate evidence descriptor authority and W06 Plan source must be disjoint")
        if _paths_overlap(self._repository, solution_repository):
            raise CandidateReceiptSinkError("candidate evidence descriptor authority and W06 Plan solution must be disjoint")
        if _paths_overlap(sink_repository, plan_source_repository):
            raise CandidateReceiptSinkError("candidate evidence sink and W06 Plan source must be disjoint")
        if _paths_overlap(sink_repository, solution_repository):
            raise CandidateReceiptSinkError("candidate evidence sink and W06 Plan solution must be disjoint")
        if _paths_overlap(candidate_root, plan_source_repository):
            raise CandidateReceiptSinkError("W06 Plan source must be disjoint from the candidate repository")
        if _paths_overlap(candidate_root, solution_repository):
            raise CandidateReceiptSinkError("W06 Plan solution must be disjoint from the candidate repository")
        if any(_paths_overlap(solution_repository, root) for root in (self._repository, sink_repository)):
            raise CandidateReceiptSinkError("W06 Plan solution must be disjoint from candidate evidence and descriptor roots")
        self._value = {
            "schema": CANDIDATE_EVIDENCE_DESCRIPTOR_SCHEMA,
            "pin": self._pin,
            "candidate_evidence_sink": sink,
            "w06_plan_materialization": materialization,
        }

    @property
    def candidate_evidence_sink_descriptor(self) -> dict[str, Any]:
        return dict(self._value["candidate_evidence_sink"])

    @property
    def pin(self) -> dict[str, Any]:
        """Return the externally supplied Git pin for protected revalidation."""

        return dict(self._pin)

    @property
    def authority_repository(self) -> Path:
        return self._repository

    @property
    def w06_plan_materialization_pin(self) -> dict[str, Any]:
        return dict(self._value["w06_plan_materialization"])

    @property
    def identity(self) -> dict[str, str]:
        return {
            "repository": self._pin["repository"],
            "commit": self._pin["commit"],
            "tree": self._pin["tree"],
            "path": self._pin["path"],
            "content_sha256": self._pin["content_sha256"],
            "descriptor_hash": _hash(self._value),
        }

    def card_binding(self) -> dict[str, str]:
        """Return the v5 card binding over D, S, and source-bound W06 evidence."""

        sink = self._value["candidate_evidence_sink"]
        content = {"schema": CANDIDATE_EVIDENCE_CARD_BINDING_SCHEMA, "descriptor": self._value}
        return {
            "ref": f"candidate-evidence:{sink['sink_id']}:descriptor:v5",
            "hash": _hash(content),
        }


def load_pinned_candidate_evidence_descriptor(
    path: str | Path,
    *,
    candidate_repository: Path,
) -> PinnedCandidateEvidenceDescriptor:
    """Load the external D pin; a candidate-local or loose descriptor is refused."""

    try:
        descriptor_path = Path(path).resolve(strict=True)
        candidate_root = candidate_repository.resolve(strict=True)
    except OSError as exc:
        raise CandidateReceiptSinkError("pinned candidate evidence descriptor configuration is unavailable") from exc
    if _paths_overlap(descriptor_path, candidate_root):
        raise CandidateReceiptSinkError("pinned candidate evidence descriptor configuration must be disjoint from the candidate repository")
    try:
        pin = json.loads(descriptor_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CandidateReceiptSinkError("pinned candidate evidence descriptor configuration is unreadable") from exc
    return PinnedCandidateEvidenceDescriptor(pin, candidate_repository=candidate_repository)


def _load_w06_plan_materialization(
    descriptor: PinnedCandidateEvidenceDescriptor,
    *,
    plan_commit: str,
    plan_repository: Path,
    candidate_repository: Path,
    candidate_commit: str,
) -> dict[str, Any]:
    """Derive W06 from approved YAML and compare it with retained evidence.

    D may retain a later solution because a solution cannot be committed into
    the source commit it describes.  It cannot, however, introduce a later
    graph: the only graph eligible for Luet receipt verification is composed
    from the exact approved YAML and the closed candidate's catalog.
    """

    pin = descriptor.w06_plan_materialization_pin
    source_pin = pin["plan_source"]
    solution_pin = pin["solution"]
    try:
        expected_plan_repository = plan_repository.resolve(strict=True)
        source_repository = Path(source_pin["repository"]).resolve(strict=True)
        solution_repository = Path(solution_pin["repository"]).resolve(strict=True)
    except OSError as exc:
        raise CandidateReceiptSinkError("pinned W06 Plan source or solution repository is unavailable") from exc
    if source_repository != expected_plan_repository or solution_repository != expected_plan_repository:
        raise CandidateReceiptSinkError("W06 Plan source and solution must be retained in the configured Plan/evidence repository")
    source_commit, source_tree = _candidate_identity(source_repository, source_pin["commit"])
    if source_commit != plan_commit or source_commit != source_pin["commit"] or source_tree != source_pin["tree"]:
        raise CandidateReceiptSinkError("pinned W06 Plan source must be the exact approved Plan commit")
    source_raw = _git(source_repository, "show", f"{source_commit}:{source_pin['path']}")
    if _hash_bytes(source_raw) != source_pin["content_sha256"]:
        raise CandidateReceiptSinkError("pinned W06 Plan source content hash mismatch")
    solution_commit, solution_tree = _candidate_identity(solution_repository, solution_pin["commit"])
    if solution_commit != solution_pin["commit"] or solution_tree != solution_pin["tree"]:
        raise CandidateReceiptSinkError("pinned W06 Plan solution Git identity mismatch")
    solution_raw = _git(solution_repository, "show", f"{solution_commit}:{solution_pin['path']}")
    if _hash_bytes(solution_raw) != solution_pin["content_sha256"]:
        raise CandidateReceiptSinkError("pinned W06 Plan solution content hash mismatch")
    solution = _object(solution_raw, label="pinned W06 Plan solution")
    try:
        execution = yaml.safe_load(source_raw)
    except (UnicodeDecodeError, yaml.YAMLError) as exc:
        raise CandidateReceiptSinkError("pinned W06 Plan source YAML is invalid") from exc
    if not isinstance(execution, Mapping):
        raise CandidateReceiptSinkError("pinned W06 Plan source YAML must be an object")
    catalog_raw = _git(candidate_repository, "show", f"{candidate_commit}:{W06_CANDIDATE_CATALOG_PATH}")
    catalog = _object(catalog_raw, label="candidate W06 provider catalog")
    try:
        canonical_graph = compose_catalog(execution, catalog, plan_commit=plan_commit)
        expected_solution = solve(
            canonical_graph,
            expected_plan_commit=plan_commit,
            conformance_result={
                "provider_id": PROVIDER_ID,
                "available": True,
                "closure_hash": solution["closure_hash"],
            },
        )
        validate_for_dispatch(solution, current_plan_commit=plan_commit)
    except (KeyError, PlanResolutionError, ValueError) as exc:
        raise CandidateReceiptSinkError("W06 source-derived Plan solution is not dispatchable") from exc
    if dict(solution) != expected_solution:
        raise CandidateReceiptSinkError("W06 Plan solution does not match the source-derived canonical graph")
    return {
        "pin": pin,
        "materialization_hash": _hash({"plan_source": source_pin, "solution": solution_pin}),
        "graph": canonical_graph,
        "graph_hash": graph_hash(canonical_graph),
        "solution_hash": solution["solution_hash"],
        "closure_hash": solution["closure_hash"],
        "provider_id": PROVIDER_ID,
        "binary_sha256": PINNED_LUET_BINARY_SHA256,
    }


def _verify_card_candidate_evidence_binding(
    card: Mapping[str, Any],
    descriptor: PinnedCandidateEvidenceDescriptor,
) -> None:
    bindings = card.get("bindings") if isinstance(card, Mapping) else None
    binding = bindings.get("candidate_evidence") if isinstance(bindings, Mapping) else None
    if binding != descriptor.card_binding():
        raise CandidateReceiptSinkError("execution card candidate-evidence descriptor binding is missing, legacy, or mismatched")


def _validate_manifest(value: Mapping[str, Any], *, descriptor: Mapping[str, Any]) -> dict[str, Any]:
    required = {"schema", "sink_id", "artifacts", "manifest_hash"}
    if not isinstance(value, Mapping) or set(value) != required or value.get("schema") != RECEIPT_SINK_MANIFEST_SCHEMA:
        raise CandidateReceiptSinkError("pinned receipt sink manifest is invalid")
    if value.get("sink_id") != descriptor["sink_id"]:
        raise CandidateReceiptSinkError("pinned receipt sink manifest identity mismatch")
    artifacts = value.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise CandidateReceiptSinkError("pinned receipt sink manifest is empty")
    seen_refs: set[str] = set()
    seen_paths: set[str] = set()
    normalized: dict[str, dict[str, str]] = {}
    for artifact in artifacts:
        if not isinstance(artifact, Mapping) or set(artifact) != {"ref", "path", "content_sha256"}:
            raise CandidateReceiptSinkError("pinned receipt sink artifact is invalid")
        ref = artifact["ref"]
        if not isinstance(ref, str) or _ARTIFACT_REF.fullmatch(ref) is None or ref in seen_refs:
            raise CandidateReceiptSinkError("pinned receipt sink artifact reference is invalid")
        artifact_path = _safe_path(artifact["path"], label="pinned receipt sink artifact path")
        if artifact_path in seen_paths:
            raise CandidateReceiptSinkError("pinned receipt sink artifact path is duplicated")
        digest = artifact["content_sha256"]
        if not isinstance(digest, str) or _SHA256.fullmatch(digest) is None:
            raise CandidateReceiptSinkError("pinned receipt sink artifact hash is invalid")
        seen_refs.add(ref)
        seen_paths.add(artifact_path)
        normalized[ref] = {"path": artifact_path, "content_sha256": digest}
    unsigned = dict(value)
    claimed = unsigned.pop("manifest_hash")
    if not isinstance(claimed, str) or _SHA256.fullmatch(claimed) is None or claimed != _hash(unsigned):
        raise CandidateReceiptSinkError("pinned receipt sink manifest hash is invalid")
    return {
        "schema": RECEIPT_SINK_MANIFEST_SCHEMA,
        "sink_id": descriptor["sink_id"],
        "artifacts": normalized,
        "manifest_hash": claimed,
    }


class PinnedGitReceiptSink:
    """Read artifact blobs only from one externally pinned Git tree."""

    def __init__(self, descriptor: Mapping[str, Any], *, candidate_repository: Path) -> None:
        self._descriptor = validate_receipt_sink_descriptor(descriptor)
        try:
            self._repository = Path(self._descriptor["repository"]).resolve(strict=True)
            candidate_root = candidate_repository.resolve(strict=True)
        except OSError as exc:
            raise CandidateReceiptSinkError("pinned receipt sink repository is unavailable") from exc
        if _paths_overlap(self._repository, candidate_root):
            raise CandidateReceiptSinkError("pinned receipt sink repository must be disjoint from the candidate repository")
        commit, tree = _candidate_identity(self._repository, self._descriptor["commit"])
        if commit != self._descriptor["commit"] or tree != self._descriptor["tree"]:
            raise CandidateReceiptSinkError("pinned receipt sink Git identity mismatch")
        raw_manifest = self._show(self._descriptor["manifest_path"])
        if _hash_bytes(raw_manifest) != self._descriptor["manifest_content_sha256"]:
            raise CandidateReceiptSinkError("pinned receipt sink manifest content hash mismatch")
        self._manifest = _validate_manifest(_object(raw_manifest, label="pinned receipt sink manifest"), descriptor=self._descriptor)

    def _show(self, path: str) -> bytes:
        return _git(self._repository, "show", f"{self._descriptor['commit']}:{path}")

    @property
    def identity(self) -> dict[str, str]:
        return {
            "sink_id": self._descriptor["sink_id"],
            "commit": self._descriptor["commit"],
            "tree": self._descriptor["tree"],
            "manifest_hash": self._manifest["manifest_hash"],
        }

    @property
    def descriptor(self) -> dict[str, Any]:
        """Return the validated external descriptor, not a candidate artifact."""

        return dict(self._descriptor)

    @property
    def repository(self) -> Path:
        """Return the resolved externally pinned repository root."""

        return self._repository

    def fetch_bytes(self, ref: str) -> bytes:
        """Retrieve a manifest-listed blob and verify its immutable content hash."""

        if not isinstance(ref, str) or _ARTIFACT_REF.fullmatch(ref) is None:
            raise CandidateReceiptSinkError("pinned receipt sink artifact reference is invalid")
        try:
            artifact = self._manifest["artifacts"][ref]
        except KeyError as exc:
            raise CandidateReceiptSinkError("pinned receipt sink artifact is not retained") from exc
        value = self._show(artifact["path"])
        if _hash_bytes(value) != artifact["content_sha256"]:
            raise CandidateReceiptSinkError("pinned receipt sink artifact content hash mismatch")
        return value

    def fetch_object(self, ref: str) -> dict[str, Any]:
        return _object(self.fetch_bytes(ref), label="pinned receipt sink artifact")


def validate_governed_execution_bundle(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the compact pointer bundle stored by a receipt sink."""

    required = {
        "schema",
        "source_commit",
        "source_tree",
        "plan_commit",
        "role",
        *_BUNDLE_ARTIFACTS,
        "bundle_hash",
    }
    if not isinstance(value, Mapping) or set(value) != required or value.get("schema") != GOVERNED_EXECUTION_BUNDLE_SCHEMA:
        raise CandidateReceiptSinkError("governed execution evidence bundle is invalid")
    for field in ("source_commit", "source_tree", "plan_commit"):
        if not isinstance(value.get(field), str) or _GIT_OBJECT.fullmatch(value[field]) is None:
            raise CandidateReceiptSinkError("governed execution evidence bundle Git binding is invalid")
    if value.get("role") not in GOVERNED_ROLES:
        raise CandidateReceiptSinkError("governed execution evidence bundle role is invalid")
    result = dict(value)
    for name in _BUNDLE_ARTIFACTS:
        pointer = value.get(name)
        if not isinstance(pointer, Mapping) or set(pointer) != {"ref", "content_sha256"}:
            raise CandidateReceiptSinkError("governed execution evidence bundle artifact pointer is invalid")
        ref = pointer["ref"]
        digest = pointer["content_sha256"]
        if not isinstance(ref, str) or _ARTIFACT_REF.fullmatch(ref) is None or not isinstance(digest, str) or _SHA256.fullmatch(digest) is None:
            raise CandidateReceiptSinkError("governed execution evidence bundle artifact pointer is invalid")
        result[name] = {"ref": ref, "content_sha256": digest}
    unsigned = dict(value)
    claimed = unsigned.pop("bundle_hash")
    if not isinstance(claimed, str) or _SHA256.fullmatch(claimed) is None or claimed != _hash(unsigned):
        raise CandidateReceiptSinkError("governed execution evidence bundle hash is invalid")
    return result


def _bundle_artifact(sink: PinnedGitReceiptSink, pointer: Mapping[str, str]) -> dict[str, Any]:
    value = sink.fetch_bytes(pointer["ref"])
    if _hash_bytes(value) != pointer["content_sha256"]:
        raise CandidateReceiptSinkError("governed execution evidence bundle artifact hash mismatch")
    return _object(value, label="governed execution evidence bundle artifact")


def _artifact_pointer(value: Any, *, label: str) -> dict[str, str]:
    """Normalize one hash-bound pointer into the independently pinned sink."""

    if not isinstance(value, Mapping) or set(value) != {"ref", "content_sha256"}:
        raise CandidateReceiptSinkError(f"{label} artifact pointer is invalid")
    ref = value.get("ref")
    digest = value.get("content_sha256")
    if not isinstance(ref, str) or _ARTIFACT_REF.fullmatch(ref) is None or not isinstance(digest, str) or _SHA256.fullmatch(digest) is None:
        raise CandidateReceiptSinkError(f"{label} artifact pointer is invalid")
    return {"ref": ref, "content_sha256": digest}


def _candidate_blob(repository: Path, commit: str, path: str) -> bytes:
    if not isinstance(path, str) or not path or PurePosixPath(path).is_absolute() or ".." in PurePosixPath(path).parts:
        raise CandidateReceiptSinkError("candidate manifest SQL path is invalid")
    return _git(repository, "show", f"{commit}:{path}")


def _candidate_archive_sha256(repository: Path, commit: str) -> str:
    return _hash_bytes(_git(repository, "archive", "--format=tar", commit))


def _candidate_changed_paths(repository: Path, base_commit: str, source_commit: str) -> list[str]:
    raw = _git(repository, "diff", "--name-only", base_commit, source_commit).decode()
    paths = sorted(path for path in raw.splitlines() if path)
    if any(PurePosixPath(path).is_absolute() or ".." in PurePosixPath(path).parts for path in paths):
        raise CandidateReceiptSinkError("candidate changed path is invalid")
    return paths


def _candidate_file_hashes(repository: Path, commit: str) -> dict[str, str]:
    """Return the exact regular-file content map release installation retains."""

    records = _git(repository, "ls-tree", "-r", "-z", commit).split(b"\0")
    files: dict[str, str] = {}
    for record in records:
        if not record:
            continue
        try:
            header, raw_path = record.split(b"\t", 1)
            _mode, object_type, object_id = header.split(b" ", 2)
            path = raw_path.decode("utf-8")
        except (UnicodeDecodeError, ValueError) as exc:
            raise CandidateReceiptSinkError("candidate release file identity is invalid") from exc
        if object_type != b"blob" or not path or path in files:
            raise CandidateReceiptSinkError("candidate release file identity is invalid")
        files[path] = hashlib.sha256(_git(repository, "cat-file", "blob", object_id.decode())).hexdigest()
    if not files:
        raise CandidateReceiptSinkError("candidate release manifest files are invalid")
    return dict(sorted(files.items()))


def _validate_release_manifest(
    value: Mapping[str, Any],
    *,
    repository: Path,
    commit: str,
    tree: str,
) -> dict[str, Any]:
    """Check a release-installer manifest against the exact committed source.

    The release installer writes this document while materializing an immutable
    generation.  W08 does not install the candidate, but it must preserve the
    exact manifest that installation will consume.  Checking its source
    identity and archive digest here rejects a release manifest copied from a
    neighboring candidate before deployment can begin.
    """

    required = {
        "schema",
        "generation",
        "commit",
        "tree",
        "git_tree",
        "src_root",
        "archive_sha256",
        "content_manifest_sha256",
        "file_count",
        "files",
    }
    if not isinstance(value, Mapping) or set(value) != required or value.get("schema") != "tgw-release-manifest-v1":
        raise CandidateReceiptSinkError("candidate release manifest schema is invalid")
    generation = value.get("generation")
    if not isinstance(generation, str) or _RELEASE_GENERATION.fullmatch(generation) is None:
        raise CandidateReceiptSinkError("candidate release manifest generation is invalid")
    if value.get("commit") != commit or value.get("git_tree") != tree:
        raise CandidateReceiptSinkError("candidate release manifest source binding mismatch")
    if value.get("tree") != f"exact-git-archive:{commit}" or value.get("src_root") != "src":
        raise CandidateReceiptSinkError("candidate release manifest archive identity is invalid")
    archive_sha256 = _candidate_archive_sha256(repository, commit).removeprefix("sha256:")
    if value.get("archive_sha256") != archive_sha256:
        raise CandidateReceiptSinkError("candidate release manifest archive hash mismatch")
    files = value.get("files")
    if not isinstance(files, Mapping) or not files or not isinstance(value.get("file_count"), int) or value["file_count"] != len(files):
        raise CandidateReceiptSinkError("candidate release manifest files are invalid")
    for path, digest in files.items():
        if (
            not isinstance(path, str)
            or not path
            or PurePosixPath(path).is_absolute()
            or ".." in PurePosixPath(path).parts
            or not isinstance(digest, str)
            or re.fullmatch(r"[0-9a-f]{64}", digest) is None
        ):
            raise CandidateReceiptSinkError("candidate release manifest files are invalid")
    if dict(files) != _candidate_file_hashes(repository, commit):
        raise CandidateReceiptSinkError("candidate release manifest files do not match source")
    expected_content_hash = hashlib.sha256((json.dumps(dict(sorted(files.items())), sort_keys=True, separators=(",", ":")) + "\n").encode()).hexdigest()
    if value.get("content_manifest_sha256") != expected_content_hash:
        raise CandidateReceiptSinkError("candidate release manifest content hash is invalid")
    return dict(value)


def validate_candidate_evidence_bundle(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate S's compact immutable candidate-evidence pointer bundle."""

    required = {
        "schema",
        "source_commit",
        "source_tree",
        "plan_commit",
        *_CANDIDATE_EVIDENCE_ARTIFACTS,
        "migration_receipts",
        "execution_proofs",
        "bundle_hash",
    }
    if not isinstance(value, Mapping) or set(value) != required or value.get("schema") != CANDIDATE_EVIDENCE_BUNDLE_SCHEMA:
        raise CandidateReceiptSinkError("candidate evidence bundle is invalid")
    for field in ("source_commit", "source_tree", "plan_commit"):
        if not isinstance(value.get(field), str) or _GIT_OBJECT.fullmatch(value[field]) is None:
            raise CandidateReceiptSinkError("candidate evidence bundle Git binding is invalid")
    result = dict(value)
    refs: set[str] = set()
    for name in _CANDIDATE_EVIDENCE_ARTIFACTS:
        pointer = _artifact_pointer(value[name], label=f"candidate evidence {name}")
        if pointer["ref"] in refs:
            raise CandidateReceiptSinkError("candidate evidence bundle reuses an artifact")
        refs.add(pointer["ref"])
        result[name] = pointer
    migrations = value.get("migration_receipts")
    if not isinstance(migrations, list):
        raise CandidateReceiptSinkError("candidate migration receipt pointers are invalid")
    normalized_migrations: list[dict[str, str]] = []
    for migration in migrations:
        pointer = _artifact_pointer(migration, label="candidate migration receipt")
        if pointer["ref"] in refs:
            raise CandidateReceiptSinkError("candidate evidence bundle reuses an artifact")
        refs.add(pointer["ref"])
        normalized_migrations.append(pointer)
    result["migration_receipts"] = normalized_migrations
    execution_proofs = value.get("execution_proofs")
    if not isinstance(execution_proofs, list) or not execution_proofs:
        raise CandidateReceiptSinkError("candidate qualified execution proof pointers are invalid")
    normalized_proofs: list[dict[str, dict[str, str]]] = []
    for item in execution_proofs:
        if not isinstance(item, Mapping) or set(item) != {"proof", "transcript"}:
            raise CandidateReceiptSinkError("candidate qualified execution proof pointers are invalid")
        proof = _artifact_pointer(item["proof"], label="candidate qualified execution proof")
        transcript = _artifact_pointer(item["transcript"], label="candidate qualified execution transcript")
        if proof["ref"] in refs or transcript["ref"] in refs or proof["ref"] == transcript["ref"]:
            raise CandidateReceiptSinkError("candidate evidence bundle reuses an artifact")
        refs.update((proof["ref"], transcript["ref"]))
        normalized_proofs.append({"proof": proof, "transcript": transcript})
    result["execution_proofs"] = normalized_proofs
    unsigned = dict(value)
    claimed = unsigned.pop("bundle_hash")
    if not isinstance(claimed, str) or _SHA256.fullmatch(claimed) is None or claimed != _hash(unsigned):
        raise CandidateReceiptSinkError("candidate evidence bundle hash is invalid")
    return result


def validate_independent_review_evidence_bundle(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate X's review pointers, which are deliberately outside S."""

    required = {
        "schema",
        "source_commit",
        "source_tree",
        "plan_commit",
        *_INDEPENDENT_REVIEW_EVIDENCE_ARTIFACTS,
        "bundle_hash",
    }
    if not isinstance(value, Mapping) or set(value) != required or value.get("schema") != INDEPENDENT_REVIEW_EVIDENCE_BUNDLE_SCHEMA:
        raise CandidateReceiptSinkError("independent review evidence bundle is invalid")
    for field in ("source_commit", "source_tree", "plan_commit"):
        if not isinstance(value.get(field), str) or _GIT_OBJECT.fullmatch(value[field]) is None:
            raise CandidateReceiptSinkError("independent review evidence bundle Git binding is invalid")
    result = dict(value)
    refs: set[str] = set()
    for name in _INDEPENDENT_REVIEW_EVIDENCE_ARTIFACTS:
        pointer = _artifact_pointer(value[name], label=f"independent review {name}")
        if pointer["ref"] in refs:
            raise CandidateReceiptSinkError("independent review evidence bundle reuses an artifact")
        refs.add(pointer["ref"])
        result[name] = pointer
    unsigned = dict(value)
    claimed = unsigned.pop("bundle_hash")
    if not isinstance(claimed, str) or _SHA256.fullmatch(claimed) is None or claimed != _hash(unsigned):
        raise CandidateReceiptSinkError("independent review evidence bundle hash is invalid")
    return result


def _verify_candidate_manifest_evidence(
    manifest: Mapping[str, Any],
    *,
    repository: Path,
    source_commit: str,
    source_tree: str,
    plan_commit: str,
    w06_plan_materialization: Mapping[str, Any],
    luet_conformance_receipt: Mapping[str, Any],
    focused_receipt: Mapping[str, Any],
    full_suite_receipt: Mapping[str, Any],
    focused_output_artifact: Mapping[str, Any],
    full_suite_output_artifact: Mapping[str, Any],
    migration_receipts: list[Mapping[str, Any]],
    qualified_execution_catalog: Mapping[str, Any],
    qualified_execution_runner_descriptor: Mapping[str, Any],
    execution_proofs: list[tuple[Mapping[str, Any], Mapping[str, Any]]],
) -> dict[str, Any]:
    """Re-derive every W08 manifest binding from exact source and sink blobs."""

    try:
        manifest_hash = candidate_identity(manifest)
    except CandidateReviewError as exc:
        raise CandidateReceiptSinkError("candidate manifest cannot establish candidate identity") from exc
    source = manifest.get("source")
    plan = manifest.get("plan")
    conformance = manifest.get("conformance")
    tests = manifest.get("tests")
    database = manifest.get("database")
    if (
        not isinstance(source, Mapping)
        or set(source) != {"commit", "tree", "archive_sha256", "base_commit", "changed_paths"}
        or not isinstance(plan, Mapping)
        or set(plan) != {"commit", "solution_hash", "closure_hash"}
        or not isinstance(conformance, Mapping)
        or set(conformance) != {"status", "receipt_hash"}
        or not isinstance(tests, Mapping)
        or set(tests) != {"focused", "full_suite"}
        or not isinstance(database, Mapping)
        or set(database)
        != {
            "changed_sql_paths",
            "migration_paths",
            "schema_snapshot_paths",
            "backup_restore",
        }
    ):
        raise CandidateReceiptSinkError("candidate manifest W08 fields are invalid")
    if source.get("commit") != source_commit or source.get("tree") != source_tree:
        raise CandidateReceiptSinkError("candidate manifest source binding mismatch")
    if source.get("archive_sha256") != _candidate_archive_sha256(repository, source_commit):
        raise CandidateReceiptSinkError("candidate manifest archive hash mismatch")
    base_commit = source.get("base_commit")
    if not isinstance(base_commit, str) or _GIT_OBJECT.fullmatch(base_commit) is None:
        raise CandidateReceiptSinkError("candidate manifest predecessor binding is invalid")
    base_tree = _git(repository, "rev-parse", f"{base_commit}^{{tree}}").decode().strip()
    if _GIT_OBJECT.fullmatch(base_tree) is None:
        raise CandidateReceiptSinkError("candidate manifest predecessor binding is invalid")
    ancestry_status, _ = _git_status(
        repository,
        "merge-base",
        "--is-ancestor",
        base_commit,
        source_commit,
    )
    if ancestry_status:
        raise CandidateReceiptSinkError("candidate manifest predecessor is not an ancestor")
    predecessor = manifest.get("predecessor_release")
    if (
        not isinstance(predecessor, Mapping)
        or set(predecessor)
        != {
            "generation",
            "commit",
            "tree",
            "archive_sha256",
            "release_manifest_hash",
        }
        or not isinstance(predecessor.get("generation"), str)
        or predecessor.get("commit") != base_commit
        or predecessor.get("tree") != base_tree
        or not isinstance(predecessor.get("archive_sha256"), str)
        or _SHA256.fullmatch(predecessor["archive_sha256"]) is None
        or not isinstance(predecessor.get("release_manifest_hash"), str)
        or _SHA256.fullmatch(predecessor["release_manifest_hash"]) is None
    ):
        raise CandidateReceiptSinkError("candidate manifest predecessor release is invalid")
    if plan.get("commit") != plan_commit:
        raise CandidateReceiptSinkError("candidate manifest Plan binding mismatch")
    if not all(isinstance(plan.get(field), str) and _SHA256.fullmatch(plan[field]) for field in ("solution_hash", "closure_hash")):
        raise CandidateReceiptSinkError("candidate manifest Plan solution binding is invalid")
    if plan["solution_hash"] != w06_plan_materialization["solution_hash"] or plan["closure_hash"] != w06_plan_materialization["closure_hash"]:
        raise CandidateReceiptSinkError("candidate manifest does not bind the approved W06 Plan solution")
    if conformance.get("status") != "VERIFIED" or conformance.get("receipt_hash") != luet_conformance_receipt.get("receipt_hash") or manifest.get("dispatchable") is not True:
        raise CandidateReceiptSinkError("candidate manifest is not Luet-conformance verified and dispatchable")
    try:
        verified_luet = verify_luet_conformance_receipt(
            luet_conformance_receipt,
            graph=w06_plan_materialization["graph"],
            plan_commit=plan_commit,
            closure_hash=w06_plan_materialization["closure_hash"],
            source_commit=source_commit,
            source_tree=source_tree,
        )
    except CandidateManifestError as exc:
        raise CandidateReceiptSinkError("retained Luet conformance receipt is invalid") from exc
    if verified_luet["provider_id"] != w06_plan_materialization["provider_id"] or verified_luet["binary_sha256"] != w06_plan_materialization["binary_sha256"]:
        raise CandidateReceiptSinkError("retained Luet conformance receipt provider binding is invalid")
    if manifest.get("candidate_closed") is not True or manifest.get("installed") is not False:
        raise CandidateReceiptSinkError("candidate manifest must be closed and uninstalled")
    if tests.get("focused") != focused_receipt or tests.get("full_suite") != full_suite_receipt:
        raise CandidateReceiptSinkError("sink test receipts do not match the candidate manifest")
    try:
        test_plan_source = _candidate_blob(repository, source_commit, CANONICAL_TEST_PLAN_PATH)
        try:
            test_plan_value = json.loads(test_plan_source)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CandidateManifestError("candidate canonical test plan is unavailable") from exc
        if not isinstance(test_plan_value, Mapping) or not isinstance(test_plan_value.get("runner"), Mapping):
            raise CandidateManifestError("candidate canonical test plan is invalid")
        runner_path = _safe_path(
            test_plan_value["runner"].get("path"),
            label="candidate test plan runner path",
        )
        test_plan = validate_candidate_test_plan(
            test_plan_value,
            source=test_plan_source,
            runner_source=_candidate_blob(repository, source_commit, runner_path),
        )
        verified_focused = verify_test_receipt(
            focused_receipt,
            scope="focused",
            source_commit=source_commit,
            source_tree=source_tree,
            test_plan=test_plan,
            output_artifact=focused_output_artifact,
        )
        verified_full = verify_test_receipt(
            full_suite_receipt,
            scope="full",
            source_commit=source_commit,
            source_tree=source_tree,
            test_plan=test_plan,
            output_artifact=full_suite_output_artifact,
        )
    except CandidateManifestError as exc:
        raise CandidateReceiptSinkError("candidate test evidence is invalid") from exc
    changed_paths = _candidate_changed_paths(repository, base_commit, source_commit)
    if source.get("changed_paths") != changed_paths:
        raise CandidateReceiptSinkError("candidate manifest changed path binding mismatch")
    changed_sql = [path for path in changed_paths if path.endswith(".sql") or "/migrations/" in path]
    if database.get("changed_sql_paths") != changed_sql:
        raise CandidateReceiptSinkError("candidate manifest SQL path binding mismatch")
    stored_migrations = database.get("backup_restore")
    if not isinstance(stored_migrations, list) or len(stored_migrations) != len(migration_receipts):
        raise CandidateReceiptSinkError("candidate migration evidence is incomplete")
    if any(not isinstance(receipt, Mapping) for receipt in stored_migrations):
        raise CandidateReceiptSinkError("candidate migration evidence is invalid")
    if list(stored_migrations) != migration_receipts:
        raise CandidateReceiptSinkError("sink migration receipts do not match the candidate manifest")
    verified_migrations = []
    migration_paths: set[str] = set()
    snapshot_paths: set[str] = set()
    for receipt in migration_receipts:
        path = receipt.get("migration_path")
        if not isinstance(path, str) or path in migration_paths or path not in changed_sql:
            raise CandidateReceiptSinkError("candidate migration evidence does not cover exact SQL changes")
        snapshot_path = receipt.get("schema_snapshot_path")
        snapshot = _candidate_blob(repository, source_commit, snapshot_path) if isinstance(snapshot_path, str) and snapshot_path in changed_sql else None
        if snapshot_path is not None and snapshot is None:
            raise CandidateReceiptSinkError("candidate migration snapshot binding is invalid")
        try:
            verified = verify_migration_safety_receipt(
                receipt,
                candidate_commit=source_commit,
                candidate_tree=source_tree,
                base_commit=base_commit,
                base_tree=base_tree,
                migration_paths=(path,),
                migration_source=_candidate_blob(repository, source_commit, path),
                schema_snapshot_source=snapshot,
            )
        except CandidateManifestError as exc:
            raise CandidateReceiptSinkError("candidate migration evidence is invalid") from exc
        verified_migrations.append(verified)
        migration_paths.add(path)
        if verified.schema_snapshot_path is not None:
            snapshot_paths.add(verified.schema_snapshot_path)
    if migration_paths | snapshot_paths != set(changed_sql):
        raise CandidateReceiptSinkError("candidate migration evidence does not cover every SQL change")
    if database.get("migration_paths") != sorted(migration_paths) or database.get("schema_snapshot_paths") != sorted(snapshot_paths):
        raise CandidateReceiptSinkError("candidate migration path summaries are invalid")
    _verify_qualified_candidate_execution(
        catalog=qualified_execution_catalog,
        runner_descriptor=qualified_execution_runner_descriptor,
        proofs=execution_proofs,
        repository=repository,
        source_commit=source_commit,
        source_tree=source_tree,
        base_commit=base_commit,
        base_tree=base_tree,
        plan_commit=plan_commit,
        test_plan=test_plan,
        focused_receipt=focused_receipt,
        full_suite_receipt=full_suite_receipt,
        focused_output=focused_output_artifact,
        full_suite_output=full_suite_output_artifact,
        migration_receipts=migration_receipts,
    )
    return {
        "manifest": dict(manifest),
        "manifest_hash": manifest_hash,
        "luet_conformance_receipt_hash": verified_luet["receipt_hash"],
        "w06_plan_materialization_hash": w06_plan_materialization["materialization_hash"],
        "plan_graph_hash": w06_plan_materialization["graph_hash"],
        "plan_solution_hash": w06_plan_materialization["solution_hash"],
        "base_commit": base_commit,
        "base_tree": base_tree,
        "focused_receipt": verified_focused,
        "full_suite_receipt": verified_full,
        "focused_output_artifact_hash": verified_focused["output_artifact_hash"],
        "full_suite_output_artifact_hash": verified_full["output_artifact_hash"],
        "migration_receipts": verified_migrations,
        "qualified_execution_proof_hashes": sorted(proof["proof_hash"] for proof, _ in execution_proofs),
    }


def _service_has_execution_capability(
    catalog: Mapping[str, Any],
    *,
    service_id: str,
    client_id: str,
    capability: str,
) -> None:
    try:
        normalized = validate_execution_service_catalog(catalog)
    except QualifiedExecutionError as exc:
        raise CandidateReceiptSinkError("qualified execution catalog is invalid") from exc
    service = next(
        (item for item in normalized["services"] if item["id"] == service_id and item["client_id"] == client_id),
        None,
    )
    if service is None or capability not in service["capabilities"]:
        raise CandidateReceiptSinkError("qualified execution service lacks required capability")


def _verified_execution_proof(
    proof: Mapping[str, Any],
    transcript: Mapping[str, Any],
    *,
    catalog: Mapping[str, Any],
    runner_descriptor: Mapping[str, Any],
    expected: Mapping[str, Any],
    capability: str,
) -> dict[str, Any]:
    try:
        normalized = validate_execution_proof(
            proof,
            transcript,
            catalog=catalog,
            runner_descriptor=runner_descriptor,
            expected=expected,
        )
    except QualifiedExecutionError as exc:
        raise CandidateReceiptSinkError("candidate qualified execution proof is invalid") from exc
    if normalized["status"] != "PASS":
        raise CandidateReceiptSinkError("candidate qualified execution did not pass")
    _service_has_execution_capability(
        catalog,
        service_id=normalized["service_id"],
        client_id=normalized["client_id"],
        capability=capability,
    )
    return normalized


def _verify_qualified_candidate_execution(
    *,
    catalog: Mapping[str, Any],
    runner_descriptor: Mapping[str, Any],
    proofs: list[tuple[Mapping[str, Any], Mapping[str, Any]]],
    repository: Path,
    source_commit: str,
    source_tree: str,
    base_commit: str,
    base_tree: str,
    plan_commit: str,
    test_plan: Mapping[str, Any],
    focused_receipt: Mapping[str, Any],
    full_suite_receipt: Mapping[str, Any],
    focused_output: Mapping[str, Any],
    full_suite_output: Mapping[str, Any],
    migration_receipts: list[Mapping[str, Any]],
) -> None:
    """Require one signed service run for every retained test/migration fact."""

    common = {
        "candidate_commit": source_commit,
        "candidate_tree": source_tree,
        "base_commit": base_commit,
        "base_tree": base_tree,
        "plan_commit": plan_commit,
    }
    expected_tests = {
        "focused": {
            "scope": "focused",
            "test_plan_path": test_plan["path"],
            "test_plan_sha256": test_plan["sha256"],
            "test_runner_path": test_plan["runner_path"],
            "test_runner_sha256": test_plan["runner_sha256"],
            "test_receipt_hash": focused_receipt["receipt_hash"],
            "test_output_artifact_hash": focused_output["artifact_hash"],
        },
        "full": {
            "scope": "full",
            "test_plan_path": test_plan["path"],
            "test_plan_sha256": test_plan["sha256"],
            "test_runner_path": test_plan["runner_path"],
            "test_runner_sha256": test_plan["runner_sha256"],
            "test_receipt_hash": full_suite_receipt["receipt_hash"],
            "test_output_artifact_hash": full_suite_output["artifact_hash"],
        },
    }
    expected_migration_inputs: dict[str, dict[str, Any]] = {}
    for receipt in migration_receipts:
        path = receipt["migration_path"]
        snapshot_path = receipt["schema_snapshot_path"]
        expected_migration_inputs[receipt["migration_path"]] = {
            "migration_path": path,
            "migration_sha256": _hash_bytes(_candidate_blob(repository, source_commit, path)),
            "schema_snapshot_path": snapshot_path,
            "schema_snapshot_sha256": (_hash_bytes(_candidate_blob(repository, source_commit, snapshot_path)) if snapshot_path is not None else None),
            "migration_receipt_hash": receipt["receipt_hash"],
        }
    found_tests: dict[str, str] = {}
    found_migrations: dict[str, str] = {}
    seen_proofs: set[str] = set()
    for proof, transcript in proofs:
        kind = proof.get("kind") if isinstance(proof, Mapping) else None
        if kind == "test":
            normalized = _verified_execution_proof(
                proof,
                transcript,
                catalog=catalog,
                runner_descriptor=runner_descriptor,
                expected=common,
                capability="candidate-test-execution",
            )
            inputs = normalized["inputs"]
            scope = inputs.get("scope")
            if scope not in expected_tests or inputs != expected_tests[scope] or scope in found_tests:
                raise CandidateReceiptSinkError("qualified test proof inputs are not exact")
            found_tests[scope] = normalized["proof_hash"]
        elif kind == "migration":
            normalized = _verified_execution_proof(
                proof,
                transcript,
                catalog=catalog,
                runner_descriptor=runner_descriptor,
                expected=common,
                capability="postgresql-migration-execution",
            )
            inputs = normalized["inputs"]
            path = inputs.get("migration_path")
            expected_inputs = expected_migration_inputs.get(path)
            if expected_inputs is None or path in found_migrations:
                raise CandidateReceiptSinkError("qualified migration proof inputs are not exact")
            if any(inputs.get(field) != item for field, item in expected_inputs.items()):
                raise CandidateReceiptSinkError("qualified migration proof inputs are not exact")
            if (
                set(inputs) != set(expected_inputs) | {"runner_path", "runner_sha256"}
                or not isinstance(inputs.get("runner_path"), str)
                or not inputs["runner_path"]
                or not _SHA256.fullmatch(str(inputs.get("runner_sha256")))
            ):
                raise CandidateReceiptSinkError("qualified migration proof runner binding is invalid")
            found_migrations[path] = normalized["proof_hash"]
        else:
            raise CandidateReceiptSinkError("candidate evidence includes a non-test/non-migration execution proof")
        proof_hash = proof.get("proof_hash") if isinstance(proof, Mapping) else None
        if not isinstance(proof_hash, str) or proof_hash in seen_proofs:
            raise CandidateReceiptSinkError("candidate qualified execution proof is reused")
        seen_proofs.add(proof_hash)
    if set(found_tests) != set(expected_tests) or set(found_migrations) != set(expected_migration_inputs):
        raise CandidateReceiptSinkError("candidate qualified execution coverage is incomplete")


def _verify_rollback_manifest(
    value: Mapping[str, Any],
    *,
    candidate: Mapping[str, Any],
    release_manifest: Mapping[str, Any],
    repository: Path,
    source_commit: str,
    source_tree: str,
) -> dict[str, Any]:
    """Require a hash-bound rollback target equal to the selected predecessor."""

    required = {
        "schema",
        "candidate_commit",
        "candidate_tree",
        "candidate_manifest_hash",
        "release_manifest_hash",
        "rollback_release_manifest",
        "manifest_hash",
    }
    if not isinstance(value, Mapping) or set(value) != required or value.get("schema") != ROLLBACK_MANIFEST_SCHEMA:
        raise CandidateReceiptSinkError("candidate rollback manifest schema is invalid")
    unsigned = dict(value)
    claimed = unsigned.pop("manifest_hash")
    if not isinstance(claimed, str) or _SHA256.fullmatch(claimed) is None or claimed != _hash(unsigned):
        raise CandidateReceiptSinkError("candidate rollback manifest hash is invalid")
    if (
        value.get("candidate_commit") != source_commit
        or value.get("candidate_tree") != source_tree
        or value.get("candidate_manifest_hash") != candidate["manifest_hash"]
        or value.get("release_manifest_hash") != _hash(release_manifest)
    ):
        raise CandidateReceiptSinkError("candidate rollback manifest binding mismatch")
    rollback_release = value.get("rollback_release_manifest")
    if not isinstance(rollback_release, Mapping):
        raise CandidateReceiptSinkError("candidate rollback release manifest is invalid")
    try:
        predecessor = verify_predecessor_release(
            rollback_release,
            base_commit=candidate["base_commit"],
            base_tree=candidate["base_tree"],
        )
    except CandidateManifestError as exc:
        raise CandidateReceiptSinkError("candidate rollback release manifest is invalid") from exc
    if predecessor != candidate["manifest"]["predecessor_release"]:
        raise CandidateReceiptSinkError("candidate rollback target does not match the selected predecessor")
    _validate_release_manifest(
        rollback_release,
        repository=repository,
        commit=candidate["base_commit"],
        tree=candidate["base_tree"],
    )
    return dict(value)


def verify_candidate_evidence_bundle(
    sink: PinnedGitReceiptSink,
    *,
    candidate_evidence_descriptor: PinnedCandidateEvidenceDescriptor,
    repository: Path,
    source_commit: str,
    source_tree: str,
    plan_commit: str,
    plan_repository: Path,
) -> dict[str, Any]:
    """Verify W08 evidence retained by exact candidate-evidence store S.

    Candidate-local receipt paths are never an input.  This routine obtains a
    complete bundle from S, rechecks it against committed Git objects, and
    accepts only pre-execution candidate, test, migration, release and rollback
    evidence.  Review execution is retained separately in X.
    """

    if not isinstance(candidate_evidence_descriptor, PinnedCandidateEvidenceDescriptor):
        raise CandidateReceiptSinkError("candidate evidence descriptor must pin W06 Plan materialization")
    if sink.descriptor != candidate_evidence_descriptor.candidate_evidence_sink_descriptor:
        raise CandidateReceiptSinkError("candidate evidence sink is not the descriptor-pinned S store")
    materialization = _load_w06_plan_materialization(
        candidate_evidence_descriptor,
        plan_commit=plan_commit,
        plan_repository=plan_repository,
        candidate_repository=repository,
        candidate_commit=source_commit,
    )
    bundle = validate_candidate_evidence_bundle(sink.fetch_object(candidate_evidence_bundle_ref(source_commit)))
    if bundle["source_commit"] != source_commit or bundle["source_tree"] != source_tree or bundle["plan_commit"] != plan_commit:
        raise CandidateReceiptSinkError("candidate evidence bundle binding mismatch")
    artifacts = {name: _bundle_artifact(sink, bundle[name]) for name in _CANDIDATE_EVIDENCE_ARTIFACTS}
    migration_artifacts = [_bundle_artifact(sink, pointer) for pointer in bundle["migration_receipts"]]
    execution_proofs = [
        (
            _bundle_artifact(sink, pointer["proof"]),
            _bundle_artifact(sink, pointer["transcript"]),
        )
        for pointer in bundle["execution_proofs"]
    ]
    candidate = _verify_candidate_manifest_evidence(
        artifacts["candidate_manifest"],
        repository=repository,
        source_commit=source_commit,
        source_tree=source_tree,
        plan_commit=plan_commit,
        w06_plan_materialization=materialization,
        luet_conformance_receipt=artifacts["luet_conformance_receipt"],
        focused_receipt=artifacts["focused_test_receipt"],
        full_suite_receipt=artifacts["full_suite_test_receipt"],
        focused_output_artifact=artifacts["focused_test_output"],
        full_suite_output_artifact=artifacts["full_suite_test_output"],
        migration_receipts=migration_artifacts,
        qualified_execution_catalog=artifacts["qualified_execution_catalog"],
        qualified_execution_runner_descriptor=artifacts["qualified_execution_runner_descriptor"],
        execution_proofs=execution_proofs,
    )
    release_manifest = _validate_release_manifest(
        artifacts["release_manifest"],
        repository=repository,
        commit=source_commit,
        tree=source_tree,
    )
    rollback_manifest = _verify_rollback_manifest(
        artifacts["rollback_manifest"],
        candidate=candidate,
        release_manifest=release_manifest,
        repository=repository,
        source_commit=source_commit,
        source_tree=source_tree,
    )
    return {
        "candidate_manifest_hash": candidate["manifest_hash"],
        "luet_conformance_receipt_hash": candidate["luet_conformance_receipt_hash"],
        "w06_plan_materialization_hash": candidate["w06_plan_materialization_hash"],
        "plan_graph_hash": candidate["plan_graph_hash"],
        "plan_solution_hash": candidate["plan_solution_hash"],
        "focused_test_receipt_hash": candidate["focused_receipt"]["receipt_hash"],
        "full_suite_test_receipt_hash": candidate["full_suite_receipt"]["receipt_hash"],
        "focused_test_output_artifact_hash": candidate["focused_output_artifact_hash"],
        "full_suite_test_output_artifact_hash": candidate["full_suite_output_artifact_hash"],
        "migration_receipt_hashes": sorted(receipt.receipt_hash for receipt in candidate["migration_receipts"]),
        "qualified_execution_proof_hashes": candidate["qualified_execution_proof_hashes"],
        "release_generation": release_manifest["generation"],
        "release_manifest_hash": _hash(release_manifest),
        "rollback_generation": rollback_manifest["rollback_release_manifest"]["generation"],
        "rollback_manifest_hash": rollback_manifest["manifest_hash"],
        "bundle_hash": bundle["bundle_hash"],
    }


def verify_independent_review_evidence_bundle(
    sink: PinnedGitReceiptSink,
    *,
    source_commit: str,
    source_tree: str,
    plan_commit: str,
    candidate_manifest_hash: str,
    independent_review_receipt: Mapping[str, Any],
) -> dict[str, Any]:
    """Verify semantic/security review from X against S's candidate identity."""

    bundle = validate_independent_review_evidence_bundle(sink.fetch_object(independent_review_evidence_bundle_ref(source_commit)))
    if bundle["source_commit"] != source_commit or bundle["source_tree"] != source_tree or bundle["plan_commit"] != plan_commit:
        raise CandidateReceiptSinkError("independent review evidence bundle binding mismatch")
    artifacts = {name: _bundle_artifact(sink, bundle[name]) for name in _INDEPENDENT_REVIEW_EVIDENCE_ARTIFACTS}
    try:
        report = validate_review_report(artifacts["review_packet"], artifacts["review_report"])
        review = validate_review_result(artifacts["review_packet"], artifacts["review_report"], artifacts["review_result"])
    except CandidateReviewError as exc:
        raise CandidateReceiptSinkError("candidate semantic/security review is invalid") from exc
    if review["status"] != "PASS" or review["candidate_manifest_hash"] != candidate_manifest_hash:
        raise CandidateReceiptSinkError("candidate semantic/security review did not pass")
    if artifacts["review_result"].get("governed_review_receipt") != independent_review_receipt:
        raise CandidateReceiptSinkError("candidate review does not use the retained independent review receipt")
    common = {
        "candidate_commit": source_commit,
        "candidate_tree": source_tree,
        "plan_commit": plan_commit,
    }
    normalized_proof = _verified_execution_proof(
        artifacts["review_execution_proof"],
        artifacts["review_execution_transcript"],
        catalog=artifacts["qualified_execution_catalog"],
        runner_descriptor=artifacts["qualified_execution_runner_descriptor"],
        expected=common,
        capability="candidate-review-execution",
    )
    inputs = normalized_proof["inputs"]
    expected_inputs = {
        "review_packet_content_sha256": _hash(artifacts["review_packet"]),
        "review_packet_hash": review["packet_hash"],
        "review_report_content_sha256": _hash(artifacts["review_report"]),
        "review_report_hash": report["report_hash"],
    }
    if normalized_proof["kind"] != "review" or any(inputs.get(field) != item for field, item in expected_inputs.items()):
        raise CandidateReceiptSinkError("qualified review proof does not bind retained packet/result")
    if (
        set(inputs) != set(expected_inputs) | {"runner_path", "runner_sha256"}
        or not isinstance(inputs.get("runner_path"), str)
        or not inputs["runner_path"]
        or not _SHA256.fullmatch(str(inputs.get("runner_sha256")))
    ):
        raise CandidateReceiptSinkError("qualified review proof runner binding is invalid")
    if artifacts["review_result"].get("qualified_execution_proof_hash") != normalized_proof["proof_hash"]:
        raise CandidateReceiptSinkError("candidate review result does not bind the retained qualified proof")
    return {
        "candidate_manifest_hash": candidate_manifest_hash,
        "review_packet_hash": review["packet_hash"],
        "review_result_hash": review["result_hash"],
        "qualified_execution_proof_hash": normalized_proof["proof_hash"],
        "bundle_hash": bundle["bundle_hash"],
    }


def verify_governed_execution_bundle(
    execution_sink: PinnedGitReceiptSink,
    *,
    candidate_evidence_descriptor: PinnedCandidateEvidenceDescriptor,
    source_commit: str,
    source_tree: str,
    plan_commit: str,
    role: str,
) -> dict[str, Any]:
    """Fetch X evidence and bind every card to exact external S descriptor D."""

    bundle = validate_governed_execution_bundle(execution_sink.fetch_object(governed_execution_bundle_ref(source_commit, role)))
    if bundle["source_commit"] != source_commit or bundle["source_tree"] != source_tree or bundle["plan_commit"] != plan_commit or bundle["role"] != role:
        raise CandidateReceiptSinkError("governed execution evidence bundle candidate binding mismatch")
    artifacts = {name: _bundle_artifact(execution_sink, bundle[name]) for name in _BUNDLE_ARTIFACTS}
    _verify_card_candidate_evidence_binding(artifacts["card"], candidate_evidence_descriptor)
    try:
        receipt = verify_candidate_governed_execution_receipt(
            artifacts["candidate_receipt"],
            card=artifacts["card"],
            resource_receipt=artifacts["resource_receipt"],
            role_receipt=artifacts["role_receipt"],
            resource_service_catalog=artifacts["resource_service_catalog"],
            source_commit=source_commit,
            source_tree=source_tree,
            plan_commit=plan_commit,
        )
    except GovernedExecutionReceiptError as exc:
        raise CandidateReceiptSinkError("governed execution evidence bundle cannot be verified") from exc
    if receipt["role"] != role or artifacts["role_receipt"].get("role") != role:
        raise CandidateReceiptSinkError("governed execution evidence bundle role mismatch")
    return {"receipt": receipt, "role_receipt": artifacts["role_receipt"], "bundle_hash": bundle["bundle_hash"]}


def resolve_approved_plan_authority(
    repository: Path,
    *,
    approved_ref: str,
    candidate_repository: Path,
) -> dict[str, str]:
    """Resolve one externally configured approved Plan reference.

    ``approved_ref`` must name a direct commit ref in an external Plan
    repository.  Candidate-supplied commit strings are deliberately not
    accepted as a substitute for this lookup.
    """

    if not isinstance(approved_ref, str) or _PLAN_REF.fullmatch(approved_ref) is None:
        raise CandidateReceiptSinkError("approved Plan reference is invalid")
    try:
        plan_repository = repository.resolve(strict=True)
        candidate_root = candidate_repository.resolve(strict=True)
    except OSError as exc:
        raise CandidateReceiptSinkError("approved Plan repository is unavailable") from exc
    if _paths_overlap(plan_repository, candidate_root):
        raise CandidateReceiptSinkError("approved Plan repository must be disjoint from the candidate repository")
    try:
        object_type = _git(plan_repository, "cat-file", "-t", approved_ref).decode().strip()
        commit = _git(plan_repository, "rev-parse", "--verify", f"{approved_ref}^{{commit}}").decode().strip()
    except CandidateReceiptSinkError as exc:
        raise CandidateReceiptSinkError("approved Plan reference is unavailable") from exc
    if object_type != "commit" or _GIT_OBJECT.fullmatch(commit) is None:
        raise CandidateReceiptSinkError("approved Plan reference must resolve directly to a commit")
    return {
        "schema": GOVERNED_CANDIDATE_PLAN_AUTHORITY_SCHEMA,
        "repository": str(plan_repository),
        "approved_ref": approved_ref,
        "approved_commit": commit,
    }


def candidate_admission_gate(
    repository: Path,
    *,
    candidate: str,
    plan_repository: Path,
    plan_approved_ref: str,
    candidate_evidence_descriptor: PinnedCandidateEvidenceDescriptor,
    execution_sink: PinnedGitReceiptSink,
) -> dict[str, Any]:
    """Fail closed unless immutable S, D and X evidence form an acyclic graph."""

    try:
        repo = repository.resolve(strict=True)
    except OSError as exc:
        raise CandidateReceiptSinkError("candidate repository is unavailable") from exc
    plan_authority = resolve_approved_plan_authority(
        plan_repository,
        approved_ref=plan_approved_ref,
        candidate_repository=repo,
    )
    if not isinstance(candidate_evidence_descriptor, PinnedCandidateEvidenceDescriptor):
        raise CandidateReceiptSinkError("candidate evidence descriptor must be externally pinned")
    if not isinstance(execution_sink, PinnedGitReceiptSink):
        raise CandidateReceiptSinkError("execution evidence sink must be externally pinned")
    candidate_sink = PinnedGitReceiptSink(
        candidate_evidence_descriptor.candidate_evidence_sink_descriptor,
        candidate_repository=repo,
    )
    try:
        w06_plan_source_repository = Path(candidate_evidence_descriptor.w06_plan_materialization_pin["plan_source"]["repository"]).resolve(strict=True)
        w06_solution_repository = Path(candidate_evidence_descriptor.w06_plan_materialization_pin["solution"]["repository"]).resolve(strict=True)
        approved_plan_repository = Path(plan_authority["repository"]).resolve(strict=True)
    except OSError as exc:
        raise CandidateReceiptSinkError("W06 Plan source, solution, or approved Plan repository is unavailable") from exc
    if w06_plan_source_repository != approved_plan_repository or w06_solution_repository != approved_plan_repository:
        raise CandidateReceiptSinkError("W06 Plan source and solution must use the approved Plan/evidence repository")
    receipt_roots = (
        candidate_sink.repository,
        candidate_evidence_descriptor.authority_repository,
        execution_sink.repository,
    )
    if any(_paths_overlap(left, right) for index, left in enumerate(receipt_roots) for right in receipt_roots[index + 1 :]):
        raise CandidateReceiptSinkError("candidate evidence, descriptor, and execution evidence roots must be disjoint")
    if any(_paths_overlap(approved_plan_repository, root) for root in receipt_roots):
        raise CandidateReceiptSinkError("approved Plan repository must be disjoint from candidate evidence, descriptor, and execution evidence roots")
    plan_commit = plan_authority["approved_commit"]
    source_commit, source_tree = _candidate_identity(repo, candidate)
    reasons: list[str] = []
    candidate_evidence: dict[str, Any] | None = None
    try:
        candidate_evidence = verify_candidate_evidence_bundle(
            candidate_sink,
            candidate_evidence_descriptor=candidate_evidence_descriptor,
            repository=repo,
            source_commit=source_commit,
            source_tree=source_tree,
            plan_commit=plan_commit,
            plan_repository=approved_plan_repository,
        )
    except CandidateReceiptSinkError:
        reasons.append("missing-or-invalid-candidate-evidence")
    governed_receipts: list[dict[str, Any]] = []
    role_receipts: list[dict[str, Any]] = []
    bundle_hashes: list[str] = []
    for role in GOVERNED_ROLES:
        try:
            verified = verify_governed_execution_bundle(
                execution_sink,
                candidate_evidence_descriptor=candidate_evidence_descriptor,
                source_commit=source_commit,
                source_tree=source_tree,
                plan_commit=plan_commit,
                role=role,
            )
        except CandidateReceiptSinkError:
            reasons.append(f"missing-or-invalid-governed-evidence:{role}")
            continue
        governed_receipts.append(verified["receipt"])
        role_receipts.append(verified["role_receipt"])
        bundle_hashes.append(verified["bundle_hash"])
    independent_review_evidence: dict[str, Any] | None = None
    independent_review_receipt = next(
        (receipt for receipt in role_receipts if receipt.get("role") == "independent-review"),
        None,
    )
    if independent_review_receipt is None or candidate_evidence is None:
        reasons.append("missing-or-invalid-independent-review-evidence")
    else:
        try:
            independent_review_evidence = verify_independent_review_evidence_bundle(
                execution_sink,
                source_commit=source_commit,
                source_tree=source_tree,
                plan_commit=plan_commit,
                candidate_manifest_hash=candidate_evidence["candidate_manifest_hash"],
                independent_review_receipt=independent_review_receipt,
            )
        except CandidateReceiptSinkError:
            reasons.append("missing-or-invalid-independent-review-evidence")
    role_gate: dict[str, Any] | None = None
    if len(role_receipts) == len(GOVERNED_ROLES):
        try:
            role_gate = admission_gate(role_receipts)
        except ValueError:
            reasons.append("invalid-governed-role-receipt")
        else:
            reasons.extend(f"role-gate:{reason}" for reason in role_gate["reasons"])
    unsigned = {
        "schema": GOVERNED_CANDIDATE_ADMISSION_SCHEMA,
        "source_commit": source_commit,
        "source_tree": source_tree,
        "plan_commit": plan_commit,
        "plan_authority": plan_authority,
        "candidate_evidence_descriptor": candidate_evidence_descriptor.identity,
        "candidate_evidence_sink": candidate_sink.identity,
        "execution_evidence_sink": execution_sink.identity,
        "allowed": not reasons,
        "reasons": sorted(set(reasons)),
        "governed_execution_receipt_hashes": sorted(receipt["receipt_hash"] for receipt in governed_receipts),
        "bundle_hashes": sorted(bundle_hashes),
        "luet_conformance_receipt_hash": (candidate_evidence["luet_conformance_receipt_hash"] if candidate_evidence else None),
        "w06_plan_materialization_hash": (candidate_evidence["w06_plan_materialization_hash"] if candidate_evidence else None),
        "plan_graph_hash": candidate_evidence["plan_graph_hash"] if candidate_evidence else None,
        "plan_solution_hash": candidate_evidence["plan_solution_hash"] if candidate_evidence else None,
        "candidate_evidence": candidate_evidence,
        "independent_review_evidence": independent_review_evidence,
        "role_gate": role_gate,
    }
    return {**unsigned, "gate_hash": _hash(unsigned)}
