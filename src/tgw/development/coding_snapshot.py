"""CodingTaskSnapshot builder — inspects a git worktree for coding
condition evidence.

Reads git state, runs pytest/ruff via subprocess, and checks receipt files.
No filesystem writes occur, but subprocess execution may produce side-effect
output (test results, lint reports)."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
from pathlib import Path
from typing import Optional

from tgw.workflow_kernel.contracts import (
    EvidenceAssertion,
    EvidenceReference,
    FingerprintResult,
    GoalProfile,
    ObjectSnapshot,
    TreatmentContract,
)


def serialize_snapshot(snapshot: ObjectSnapshot) -> dict[str, object]:
    """Return the portable, canonical JSON form of a local coding snapshot.

    The provision worker may observe its own worktree, but it cannot choose a
    treatment.  This representation lets tgw-prod independently evaluate the
    exact facts the worker claimed without receiving a live filesystem path as
    an authority channel.
    """
    return {
        "schema_version": "coding-snapshot/v1",
        "object_id": snapshot.object_id,
        "generation": snapshot.generation,
        "assertions": [
            {
                "condition_id": assertion.condition_id,
                "result": assertion.result.value,
                "reasons": list(assertion.reasons),
                "evidence": [
                    {
                        "identity": evidence.identity,
                        "source_class": evidence.source_class,
                        "source_generation": evidence.source_generation,
                        "freshness_identity": evidence.freshness_identity,
                        "supersession_identity": evidence.supersession_identity,
                    }
                    for evidence in assertion.evidence
                ],
            }
            for assertion in snapshot.assertions
        ],
        "external_effect_ambiguities": list(snapshot.external_effect_ambiguities),
    }


def deserialize_snapshot(value: object) -> ObjectSnapshot:
    """Validate and decode a worker's portable coding snapshot claim."""
    if not isinstance(value, dict) or value.get("schema_version") != "coding-snapshot/v1":
        raise ValueError("coding snapshot claim has an unsupported schema")
    object_id = value.get("object_id")
    generation = value.get("generation")
    assertions_value = value.get("assertions")
    ambiguities = value.get("external_effect_ambiguities", [])
    if (not isinstance(object_id, str) or not object_id or not isinstance(generation, str)
            or not generation or not isinstance(assertions_value, list)
            or not isinstance(ambiguities, list) or not all(isinstance(item, str) for item in ambiguities)):
        raise ValueError("coding snapshot claim is malformed")
    assertions: list[EvidenceAssertion] = []
    for assertion in assertions_value:
        if not isinstance(assertion, dict):
            raise ValueError("coding snapshot assertion is malformed")
        condition_id, result = assertion.get("condition_id"), assertion.get("result")
        reasons, evidence_value = assertion.get("reasons", []), assertion.get("evidence", [])
        if (not isinstance(condition_id, str) or not condition_id or not isinstance(result, str)
                or not isinstance(reasons, list) or not all(isinstance(item, str) for item in reasons)
                or not isinstance(evidence_value, list)):
            raise ValueError("coding snapshot assertion is malformed")
        try:
            fingerprint_result = FingerprintResult(result)
        except ValueError as exc:
            raise ValueError("coding snapshot assertion has an invalid result") from exc
        evidence: list[EvidenceReference] = []
        for item in evidence_value:
            if not isinstance(item, dict) or any(
                not isinstance(item.get(field, ""), str)
                for field in ("identity", "source_class", "source_generation", "freshness_identity", "supersession_identity")
            ):
                raise ValueError("coding snapshot evidence is malformed")
            evidence.append(EvidenceReference(**{field: item.get(field, "") for field in (
                "identity", "source_class", "source_generation", "freshness_identity", "supersession_identity",
            )}))
        assertions.append(EvidenceAssertion(condition_id, fingerprint_result, tuple(reasons), tuple(evidence)))
    return ObjectSnapshot(object_id, generation, tuple(assertions), tuple(ambiguities))

# ---------------------------------------------------------------------------
# Condition checkers — one function per coding condition.
# Each returns (FingerprintResult, reasons, evidence_references).
# ---------------------------------------------------------------------------

CANONICAL_BRANCHES = frozenset({"main", "master"})

_RECEIPT_PATHS: dict[str, str] = {
    "implemented": "implementation-receipt.json",
    "tested": "controller-harness-receipt.json",
    "linted": "controller-harness-receipt.json",
    "reviewed": "review-receipt.json",
    "controller_verified": "controller-harness-receipt.json",
    "deployed": "deployment-receipt.json",
    # Stitch emits an audit receipt too.  It is evidence, not source, and
    # must therefore be ignored by both cleanliness and generation checks.
    "stitched": "stitch-receipt.json",
    "operator_admission_pending": "operator-admit-pending.json",
}

CONTROLLER_PYTHON = "/opt/TGW/.venvs/controller/bin/python"
_ATTEMPT_EVIDENCE_ROOTS = (".tgw-coding-history/", ".tgw-coding-preservation/")
_PRESERVATION_NAME = re.compile(r"[0-9a-f]{64}\.json")
_COMMIT = re.compile(r"[0-9a-f]{40}")


def _git(
    worktree: Path,
    *args: str,
    timeout: int = 30,
) -> tuple[int, str, str]:
    """Run a git command in the worktree, returning (exit_code, stdout, stderr)."""
    try:
        proc = subprocess.run(
            [
                "git", "-c", f"safe.directory={worktree.resolve()}",
                "-C", str(worktree), *args,
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
        return proc.returncode, proc.stdout.strip(), proc.stderr.strip()
    except FileNotFoundError:
        return -1, "", "git not found"
    except subprocess.TimeoutExpired:
        return -2, "", "git timed out"


def _git_rev_parse(worktree: Path, ref: str) -> Optional[str]:
    """Return the full SHA for `ref`, or None."""
    code, out, _ = _git(worktree, "rev-parse", "--verify", ref)
    return out if code == 0 else None


def _git_branch(worktree: Path) -> Optional[str]:
    """Return the current branch name, or None (detached HEAD / not a repo)."""
    code, out, _ = _git(worktree, "rev-parse", "--abbrev-ref", "HEAD")
    return out if code == 0 and out != "HEAD" else None


def _preservation_evidence(worktree: Path, relative: str) -> bytes | None:
    """Return exact valid preservation bytes, never a path-shaped exemption."""
    prefix = ".tgw-coding-preservation/"
    if not relative.startswith(prefix):
        return None
    name = relative.removeprefix(prefix)
    if "/" in name or _PRESERVATION_NAME.fullmatch(name) is None:
        return None
    path = worktree / relative
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or path.is_symlink():
            return None
        if path.resolve(strict=True).parent != (worktree / prefix).resolve(strict=True):
            return None
        raw = path.read_bytes()
        value = json.loads(raw)
    except (OSError, RuntimeError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(value, dict) or set(value) != {
        "schema", "binding", "classification", "source", "manifest_hash",
    } or value.get("schema") != "tgw-coding-preservation-manifest/v2":
        return None
    if raw != (json.dumps(value, sort_keys=True) + "\n").encode():
        return None
    binding = value.get("binding")
    required = {
        "todo_id", "plan_commit", "solution_hash", "source_commit", "source_tree",
        "actor", "worktree", "treatment_id", "treatment_version",
    }
    if (not isinstance(binding, dict) or not required.issubset(binding)
            or binding.get("worktree") != str(worktree.resolve())
            or binding.get("treatment_id") != "codex-implement"
            or binding.get("treatment_version") != "1"
            or not isinstance(binding.get("todo_id"), int)
            or binding["todo_id"] <= 0
            or _COMMIT.fullmatch(str(binding.get("plan_commit", ""))) is None
            or _COMMIT.fullmatch(str(binding.get("source_commit", ""))) is None
            or _COMMIT.fullmatch(str(binding.get("source_tree", ""))) is None
            or re.fullmatch(r"sha256:[0-9a-f]{64}", str(binding.get("solution_hash", ""))) is None
            or not isinstance(binding.get("actor"), str) or not binding["actor"]
            or value.get("classification") not in {
                "UNSAFE_DIRTY", "STALE_RECEIPT", "RESUMABLE_PARTIAL",
                "ABANDONED_CLEAN", "CLOSED_CANDIDATE",
            }
            or not isinstance(value.get("source"), dict)):
        return None
    unsigned = dict(value)
    claimed = unsigned.pop("manifest_hash", None)
    actual = "sha256:" + hashlib.sha256(json.dumps(
        unsigned, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    ).encode()).hexdigest()
    if claimed != actual or name != actual.removeprefix("sha256:") + ".json":
        return None
    try:
        from tgw.development.partial_resume import source_fingerprint
        if value.get("source") != source_fingerprint(worktree):
            return None
    except (OSError, RuntimeError, ValueError):
        return None
    return raw


def _workflow_evidence_fingerprint(worktree: Path) -> str:
    root = worktree / ".tgw-coding-preservation"
    if root.exists():
        try:
            if root.is_symlink() or not root.is_dir():
                return "invalid"
            for path in root.iterdir():
                relative = path.relative_to(worktree).as_posix()
                if _preservation_evidence(worktree, relative) is None:
                    return "invalid"
                ignored, _, _ = _git(worktree, "check-ignore", "-q", "--", relative)
                if ignored == 0:
                    return "invalid"
        except (OSError, RuntimeError, ValueError):
            return "invalid"
    code, out, _ = _git(
        worktree, "status", "--porcelain=v1", "--untracked-files=all",
        "--", ".tgw-coding-preservation",
    )
    if code != 0:
        return "invalid"
    evidence = []
    for line in out.splitlines():
        relative = line[3:]
        raw = _preservation_evidence(worktree, relative) if line.startswith("?? ") else None
        if raw is None:
            return "invalid"
        evidence.append(relative.encode() + b"\0" + raw)
    return hashlib.sha256(b"\0".join(sorted(evidence))).hexdigest()


def _git_is_clean(worktree: Path) -> Optional[bool]:
    """Return True if the worktree is clean, False if dirty, None if not a repo."""
    code, out, _ = _git(
        worktree,
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
        "--ignored=matching",
    )
    if code != 0:
        return None
    if _workflow_evidence_fingerprint(worktree) == "invalid":
        return False
    receipt_names = set(_RECEIPT_PATHS.values())
    changes = []
    for line in out.splitlines():
        relative = line[3:]
        if relative in receipt_names:
            continue
        if relative.startswith(".tgw-coding-history/"):
            continue
        if (line.startswith("?? ") and _preservation_evidence(worktree, relative)
                is not None):
            continue
        changes.append(line)
    return not changes


def _git_source_fingerprint(worktree: Path) -> str:
    """Hash mutable source state while excluding workflow receipt evidence."""
    receipt_names = set(_RECEIPT_PATHS.values())
    # Exclude receipts from tracked changes too: an operator may have added a
    # receipt path to Git in an old worktree, and that must not make evidence
    # alter the source generation it attests.
    pathspecs = (".", *(f":(exclude){name}" for name in sorted(receipt_names)),
                 ":(exclude).tgw-coding-history")
    _, tracked_diff, _ = _git(worktree, "diff", "--binary", "HEAD", "--", *pathspecs)
    _, untracked, _ = _git(worktree, "ls-files", "--others", "--exclude-standard")
    _, ignored, _ = _git(
        worktree,
        "ls-files",
        "--others",
        "--ignored",
        "--exclude-standard",
    )
    untracked_content: list[str] = []
    for relative in (*untracked.splitlines(), *ignored.splitlines()):
        if relative in receipt_names or relative.startswith(".tgw-coding-history/"):
            continue
        if _preservation_evidence(worktree, relative) is not None:
            continue
        candidate = worktree / relative
        if candidate.is_file():
            try:
                untracked_content.append(f"{relative}:{candidate.read_bytes().hex()}")
            except OSError:
                untracked_content.append(f"{relative}:unreadable")
    return hashlib.sha256(
        (tracked_diff + "|" + "|".join(sorted(untracked_content))).encode()
    ).hexdigest()


def _git_merge_base(worktree: Path, ref_a: str, ref_b: str) -> Optional[str]:
    """Return the merge-base SHA, or None."""
    code, out, _ = _git(worktree, "merge-base", ref_a, ref_b)
    return out if code == 0 else None


def _git_diff_stat(worktree: Path, base_ref: str, head_ref: str) -> Optional[str]:
    """Return `git diff --stat` between two refs, or None."""
    code, out, _ = _git(worktree, "diff", "--stat", f"{base_ref}...{head_ref}")
    return out if code == 0 else None


def _git_log_canonical_contains(
    worktree: Path, commit: str, canonical: str
) -> bool:
    """Return True if `commit` is an ancestor of `canonical`."""
    # Use merge-base --is-ancestor (most reliable method)
    code, _, _ = _git(
        worktree, "merge-base", "--is-ancestor", commit, canonical,
    )
    return code == 0


def _find_canonical_branch(worktree: Path) -> Optional[str]:
    """Locate a tracking remote branch that is main/master."""
    # Try remote tracking branches first
    for remote_branch in ("origin/main", "origin/master"):
        if _git_rev_parse(worktree, remote_branch):
            return remote_branch
    # Fall back to local branches
    for local in ("main", "master"):
        if _git_rev_parse(worktree, local):
            return local
    return None


def _check_implemented(
    worktree: Path,
    baseline_commit: str | None = None,
    expected_implementation: dict[str, object] | None = None,
) -> tuple[FingerprintResult, tuple[str, ...], tuple[EvidenceReference, ...]]:
    """Task branch exists and diff is non-empty."""
    head = _git_rev_parse(worktree, "HEAD")
    if head is None:
        return FingerprintResult.UNKNOWN, ("not a git repository",), ()

    if baseline_commit is not None:
        if _git_rev_parse(worktree, baseline_commit) != baseline_commit:
            return FingerprintResult.UNKNOWN, (
                "source-bound implementation baseline is unavailable",
            ), ()
        if head == baseline_commit:
            return FingerprintResult.FALSE, (
                "HEAD still matches the source-bound implementation baseline",
            ), (
                EvidenceReference(
                    identity=head,
                    source_class="git",
                    source_generation="HEAD",
                    supersession_identity=baseline_commit,
                ),
            )
        code, _, _ = _git(worktree, "merge-base", "--is-ancestor", baseline_commit, head)
        if code != 0:
            return FingerprintResult.UNKNOWN, (
                "HEAD is not a successor of the source-bound implementation baseline",
            ), (
                EvidenceReference(
                    identity=head,
                    source_class="git",
                    source_generation="HEAD",
                    freshness_identity=baseline_commit,
                ),
            )
        if _git_is_clean(worktree) is not True:
            return FingerprintResult.FALSE, (
                "successor source is not closed in a clean commit",
            ), (
                EvidenceReference(
                    identity=head,
                    source_class="git",
                    source_generation="HEAD",
                    supersession_identity=baseline_commit,
                ),
            )
        tree = _git_rev_parse(worktree, "HEAD^{tree}")
        baseline_tree = _git_rev_parse(worktree, f"{baseline_commit}^{{tree}}")
        if tree is None or baseline_tree is None:
            return FingerprintResult.UNKNOWN, ("cannot resolve successor tree",), ()
        if tree == baseline_tree:
            return FingerprintResult.FALSE, (
                "successor commit has no implementation tree change",
            ), (
                EvidenceReference(
                    identity=head,
                    source_class="git",
                    source_generation=tree,
                    supersession_identity=baseline_commit,
                ),
            )
        try:
            from tgw.development.partial_resume import validate_implementation_lineage
            receipt = json.loads((worktree / "implementation-receipt.json").read_text(encoding="utf-8"))
            if not isinstance(receipt, dict):
                raise ValueError("implementation receipt is not an object")
            latest = validate_implementation_lineage(
                worktree, base_commit=baseline_commit,
                candidate_commit=head, candidate_tree=tree, receipt=receipt,
                expected=expected_implementation,
            )
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            return FingerprintResult.FALSE, (
                f"exact implementation lineage is absent or stale: {exc}",
            ), ()
        return (
            FingerprintResult.TRUE,
            ("clean committed successor has exact latest implementation lineage",),
            (
                EvidenceReference(
                    identity=head,
                    source_class="git",
                    source_generation=tree,
                    freshness_identity=str(latest["attempt_hash"]),
                    supersession_identity=baseline_commit,
                ),
            ),
        )

    branch = _git_branch(worktree)
    if branch is None:
        return FingerprintResult.FALSE, ("detached HEAD — no task branch",), (
            EvidenceReference(
                identity=head, source_class="git", source_generation="HEAD"
            ),
        )

    if branch in CANONICAL_BRANCHES:
        return FingerprintResult.FALSE, (
            f"on canonical branch '{branch}' — no task branch",
        ), (
            EvidenceReference(
                identity=head,
                source_class="git",
                source_generation=f"HEAD@{branch}",
            ),
        )

    # Find a canonical ancestor to diff against
    canonical = _find_canonical_branch(worktree)
    if canonical is None:
        return FingerprintResult.FALSE, (
            "cannot find canonical branch to diff against",
        ), (
            EvidenceReference(
                identity=head,
                source_class="git",
                source_generation=f"HEAD@{branch}",
            ),
            EvidenceReference(
                identity="canonical-not-found",
                source_class="git",
                source_generation="unknown",
            ),
        )

    base = _git_merge_base(worktree, canonical, "HEAD")
    if base is None:
        return FingerprintResult.FALSE, (
            f"no common ancestor with {canonical}",
        ), (
            EvidenceReference(
                identity=head,
                source_class="git",
                source_generation=f"HEAD@{branch}",
            ),
            EvidenceReference(
                identity=canonical,
                source_class="git",
                source_generation="canonical",
            ),
        )

    diff = _git_diff_stat(worktree, canonical, "HEAD")
    if diff:
        return FingerprintResult.TRUE, (
            f"branch '{branch}' has changes vs {canonical}",
        ), (
            EvidenceReference(
                identity=head,
                source_class="git",
                source_generation=f"HEAD@{branch}",
                freshness_identity=base,
                supersession_identity=canonical,
            ),
        )
    else:
        return FingerprintResult.FALSE, (
            f"branch '{branch}' has no changes vs {canonical}",
        ), (
            EvidenceReference(
                identity=head,
                source_class="git",
                source_generation=f"HEAD@{branch}",
            ),
        )


def _check_tested(
    worktree: Path,
) -> tuple[FingerprintResult, tuple[str, ...], tuple[EvidenceReference, ...]]:
    """Pytest passes."""
    try:
        proc = subprocess.run(
            [
                CONTROLLER_PYTHON,
                "-m",
                "pytest",
                "-q",
                "--tb=short",
                "-p",
                "no:cacheprovider",
            ],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=str(worktree),
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        exit_code = proc.returncode
    except FileNotFoundError:
        return FingerprintResult.UNKNOWN, ("pytest not found",), ()
    except subprocess.TimeoutExpired:
        return FingerprintResult.UNKNOWN, ("pytest timed out",), ()

    identity = hashlib.sha256(
        f"pytest-{worktree}-{exit_code}".encode()
    ).hexdigest()[:16]

    if exit_code == 0:
        return FingerprintResult.TRUE, ("pytest passed",), (
            EvidenceReference(
                identity=identity,
                source_class="pytest",
                source_generation=str(exit_code),
            ),
        )
    elif exit_code == 5:  # no tests collected
        return FingerprintResult.UNKNOWN, ("pytest: no tests collected",), (
            EvidenceReference(
                identity=identity,
                source_class="pytest",
                source_generation=str(exit_code),
            ),
        )
    else:
        return FingerprintResult.FALSE, (
            f"pytest failed (exit {exit_code})",
        ), (
            EvidenceReference(
                identity=identity,
                source_class="pytest",
                source_generation=str(exit_code),
            ),
        )


def _check_linted(
    worktree: Path,
) -> tuple[FingerprintResult, tuple[str, ...], tuple[EvidenceReference, ...]]:
    """Ruff passes."""
    try:
        proc = subprocess.run(
            [CONTROLLER_PYTHON, "-m", "ruff", "check", "--no-cache", "."],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=str(worktree),
        )
        exit_code = proc.returncode
    except FileNotFoundError:
        return FingerprintResult.UNKNOWN, ("ruff not found",), ()
    except subprocess.TimeoutExpired:
        return FingerprintResult.UNKNOWN, ("ruff timed out",), ()

    identity = hashlib.sha256(
        f"ruff-{worktree}-{exit_code}".encode()
    ).hexdigest()[:16]

    if exit_code == 0:
        return FingerprintResult.TRUE, ("ruff passed",), (
            EvidenceReference(
                identity=identity,
                source_class="ruff",
                source_generation=str(exit_code),
            ),
        )
    else:
        return FingerprintResult.FALSE, (
            f"ruff found issues (exit {exit_code})",
        ), (
            EvidenceReference(
                identity=identity,
                source_class="ruff",
                source_generation=str(exit_code),
            ),
        )


def _check_receipt(
    worktree: Path, condition_id: str, object_id: str | None = None,
    object_generation: str | None = None,
) -> tuple[FingerprintResult, tuple[str, ...], tuple[EvidenceReference, ...]]:
    """Check for a receipt file containing PASS status."""
    receipt_rel = _RECEIPT_PATHS.get(condition_id)
    if receipt_rel is None:
        return (
            FingerprintResult.NOT_APPLICABLE,
            (f"no receipt path configured for {condition_id}",),
            (),
        )

    receipt_path = worktree / receipt_rel
    if not receipt_path.is_file():
        return FingerprintResult.FALSE, (
            f"receipt file '{receipt_rel}' not found",
        ), ()

    try:
        content = receipt_path.read_text(encoding="utf-8")
        data = json.loads(content)
    except Exception as exc:
        return FingerprintResult.FALSE, (
            f"receipt file unreadable: {exc}",
        ), ()

    status = data.get("status", "").upper()
    identity = hashlib.sha256(content.encode()).hexdigest()[:16]

    bound = (
        isinstance(data.get("graph_id"), str)
        and bool(data.get("graph_id"))
        and data.get("object_id") == object_id
        and data.get("object_generation") == object_generation
    )
    outcome = data.get("outcome")
    established = data.get("established_conditions")
    valid_outcome = outcome == "satisfied"
    establishes_condition = (
        isinstance(established, list)
        and all(isinstance(item, str) for item in established)
        and condition_id in established
    )
    if status == "PASS" and bound and valid_outcome and establishes_condition:
        return FingerprintResult.TRUE, (
            f"{condition_id} receipt: PASS",
        ), (
            EvidenceReference(
                identity=identity,
                source_class="receipt",
                source_generation=receipt_rel,
            ),
        )
    if not bound:
        return FingerprintResult.STALE, (
            f"{condition_id} receipt is not bound to this worktree generation",
        ), (
            EvidenceReference(
                identity=identity,
                source_class="receipt",
                source_generation=receipt_rel,
            ),
        )
    details: list[str] = []
    if status != "PASS":
        details.append(f"status {status or 'missing'} (expected PASS)")
    if not valid_outcome:
        details.append(f"outcome {outcome!r} (expected 'satisfied')")
    if not establishes_condition:
        details.append(f"does not establish {condition_id}")
    return FingerprintResult.FALSE, (f"{condition_id} receipt: {'; '.join(details)}",), (
        EvidenceReference(
            identity=identity,
            source_class="receipt",
            source_generation=receipt_rel,
        ),
    )


def _check_admitted(
    worktree: Path,
) -> tuple[FingerprintResult, tuple[str, ...], tuple[EvidenceReference, ...]]:
    """Current commit is on the canonical branch."""
    head = _git_rev_parse(worktree, "HEAD")
    if head is None:
        return FingerprintResult.UNKNOWN, ("not a git repository",), ()

    canonical = _find_canonical_branch(worktree)
    if canonical is None:
        return FingerprintResult.UNKNOWN, ("no canonical branch found",), (
            EvidenceReference(
                identity=head, source_class="git", source_generation="HEAD"
            ),
        )

    admitted = _git_log_canonical_contains(worktree, head, canonical)
    if admitted:
        return FingerprintResult.TRUE, (
            f"commit {head[:8]} is on {canonical}",
        ), (
            EvidenceReference(
                identity=head, source_class="git", source_generation="HEAD"
            ),
            EvidenceReference(
                identity=canonical,
                source_class="git",
                source_generation="canonical",
            ),
        )
    else:
        return FingerprintResult.FALSE, (
            f"commit {head[:8]} not on {canonical}",
        ), (
            EvidenceReference(
                identity=head, source_class="git", source_generation="HEAD"
            ),
            EvidenceReference(
                identity=canonical,
                source_class="git",
                source_generation="canonical",
            ),
        )


def _check_committed(
    worktree: Path,
) -> tuple[FingerprintResult, tuple[str, ...], tuple[EvidenceReference, ...]]:
    """Working tree is clean (nothing staged or unstaged)."""
    head = _git_rev_parse(worktree, "HEAD")
    if head is None:
        return FingerprintResult.UNKNOWN, ("not a git repository",), ()

    clean = _git_is_clean(worktree)
    if clean is None:
        return FingerprintResult.UNKNOWN, (
            "cannot determine clean status",
        ), (
            EvidenceReference(
                identity=head, source_class="git", source_generation="HEAD"
            ),
        )
    elif clean:
        return FingerprintResult.TRUE, ("working tree clean",), (
            EvidenceReference(
                identity=head, source_class="git", source_generation="HEAD"
            ),
        )
    else:
        return FingerprintResult.FALSE, ("working tree dirty",), (
            EvidenceReference(
                identity=head, source_class="git", source_generation="HEAD"
            ),
        )


# ---------------------------------------------------------------------------
# Condition dispatch table
# ---------------------------------------------------------------------------

_CHECKERS: dict[str, callable] = {
    "implemented": _check_implemented,
    "tested": _check_tested,
    "linted": _check_linted,
    "reviewed": lambda wp, oid=None, gen=None: _check_receipt(wp, "reviewed", oid, gen),
    "controller_verified": lambda wp, oid=None, gen=None: _check_receipt(wp, "controller_verified", oid, gen),
    "admitted": _check_admitted,
    "committed": _check_committed,
    "deployed": lambda wp, oid=None, gen=None: _check_receipt(wp, "deployed", oid, gen),
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_coding_snapshot(
    worktree_path: str | Path,
    goal_profile: GoalProfile,
    treatments: tuple[TreatmentContract, ...] = (),
    *,
    implementation_baseline_commit: str | None = None,
    expected_implementation: dict[str, object] | None = None,
    receipt_backed_conditions: frozenset[str] = frozenset(),
) -> ObjectSnapshot:
    """Build a read-only ObjectSnapshot by checking coding conditions in a
    git worktree.

    For every condition named by ``goal_profile`` or the active treatment
    graph, the corresponding
    checker runs (git inspection, pytest, ruff, or receipt-file lookup) and
    produces an EvidenceAssertion. No filesystem writes occur.

    Returns an ObjectSnapshot whose ``object_id`` is the resolved absolute
    path of the worktree and whose ``assertions`` hold one
    EvidenceAssertion per required condition (plus any conditions that
    could not be checked — those produce FingerprintResult.UNKNOWN).
    """
    worktree = Path(worktree_path).resolve()

    object_id = str(worktree)

    # A candidate generation is the immutable commit/tree identity. Receipts
    # are deliberately outside it and dirty bytes can never become a candidate.
    head = _git_rev_parse(worktree, "HEAD") or "no-head"
    tree = _git_rev_parse(worktree, "HEAD^{tree}") or "no-tree"
    # Dirty state is not a candidate, but it still needs a distinct generation
    # so a receipt for the last closed commit becomes stale immediately.
    dirty = "" if _git_is_clean(worktree) is True else f"|dirty:{_git_source_fingerprint(worktree)}"
    gen_input = f"{head}|{tree}{dirty}".encode()
    generation = hashlib.sha256(gen_input).hexdigest()[:16]

    assertions: list[EvidenceAssertion] = []
    condition_ids = set(goal_profile.required)
    for treatment in treatments:
        condition_ids.update(requirement.condition_id for requirement in treatment.requires)
        condition_ids.update(treatment.may_establish)

    for condition_id in sorted(condition_ids):
        checker = _CHECKERS.get(condition_id)
        if checker is None:
            assertions.append(
                EvidenceAssertion(
                    condition_id=condition_id,
                    result=FingerprintResult.UNKNOWN,
                    reasons=(
                        f"no checker registered for '{condition_id}'",
                    ),
                )
            )
            continue

        if condition_id in receipt_backed_conditions:
            result, reasons, evidence = _check_receipt(
                worktree,
                condition_id,
                object_id,
                generation,
            )
        elif condition_id == "implemented":
            result, reasons, evidence = checker(
                worktree, implementation_baseline_commit, expected_implementation,
            )
        elif condition_id in {"reviewed", "controller_verified", "deployed"}:
            result, reasons, evidence = checker(worktree, object_id, generation)
        else:
            result, reasons, evidence = checker(worktree)
        assertions.append(
            EvidenceAssertion(
                condition_id=condition_id,
                result=result,
                reasons=reasons,
                evidence=evidence,
            )
        )

    return ObjectSnapshot(
        object_id=object_id,
        generation=generation,
        assertions=tuple(assertions),
    )
