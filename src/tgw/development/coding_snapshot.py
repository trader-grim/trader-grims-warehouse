"""CodingTaskSnapshot builder — inspects a git worktree for coding
condition evidence.

Reads git state, runs pytest/ruff via subprocess, and checks receipt files.
No filesystem writes occur, but subprocess execution may produce side-effect
output (test results, lint reports)."""

from __future__ import annotations

import hashlib
import json
import os
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


def _git_is_clean(worktree: Path) -> Optional[bool]:
    """Return True if the worktree is clean, False if dirty, None if not a repo."""
    code, out, _ = _git(worktree, "status", "--porcelain")
    if code != 0:
        return None
    receipt_names = set(_RECEIPT_PATHS.values())
    changes = [line for line in out.splitlines() if line[3:] not in receipt_names]
    return not changes


def _git_source_fingerprint(worktree: Path) -> str:
    """Hash mutable source state while excluding workflow receipt evidence."""
    receipt_names = set(_RECEIPT_PATHS.values())
    # Exclude receipts from tracked changes too: an operator may have added a
    # receipt path to Git in an old worktree, and that must not make evidence
    # alter the source generation it attests.
    pathspecs = (".", *(f":(exclude){name}" for name in sorted(receipt_names)))
    _, tracked_diff, _ = _git(worktree, "diff", "--binary", "HEAD", "--", *pathspecs)
    _, untracked, _ = _git(worktree, "ls-files", "--others", "--exclude-standard")
    untracked_content: list[str] = []
    for relative in untracked.splitlines():
        if relative in receipt_names:
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
) -> tuple[FingerprintResult, tuple[str, ...], tuple[EvidenceReference, ...]]:
    """Task branch exists and diff is non-empty."""
    head = _git_rev_parse(worktree, "HEAD")
    if head is None:
        return FingerprintResult.UNKNOWN, ("not a git repository",), ()

    if baseline_commit is not None:
        if head != baseline_commit:
            return FingerprintResult.UNKNOWN, (
                "HEAD no longer matches the source-bound implementation baseline",
            ), (
                EvidenceReference(
                    identity=head,
                    source_class="git",
                    source_generation="HEAD",
                    freshness_identity=baseline_commit,
                ),
            )
        fingerprint = _git_source_fingerprint(worktree)
        empty = hashlib.sha256(b"|").hexdigest()
        implemented = fingerprint != empty
        return (
            FingerprintResult.TRUE if implemented else FingerprintResult.FALSE,
            (
                "working tree has implementation changes from the source-bound commit"
                if implemented
                else "working tree matches the source-bound commit",
            ),
            (
                EvidenceReference(
                    identity=head,
                    source_class="git",
                    source_generation="HEAD",
                    freshness_identity=fingerprint,
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
            [CONTROLLER_PYTHON, "-m", "pytest", "-q", "--tb=short"],
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
            [CONTROLLER_PYTHON, "-m", "ruff", "check", "."],
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

    # Content-addressed generation: hash only the source state.  A request-bound
    # worktree branch name is a location/claim identity, not source content; if
    # it participated here, a generation attested before provisioning could
    # never match the newly provisioned request worktree.
    head = _git_rev_parse(worktree, "HEAD") or "no-head"
    canonical = _find_canonical_branch(worktree)
    if canonical:
        diff_content = _git_diff_stat(worktree, canonical, "HEAD") or ""
    else:
        diff_content = ""
    source_fingerprint = _git_source_fingerprint(worktree)
    # Generation is source state only. Receipts attest this state but must
    # never change the state they attest (which would make every receipt stale
    # the instant it is written).
    gen_input = f"{head}|{diff_content}|{source_fingerprint}".encode()
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
                worktree, implementation_baseline_commit,
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
