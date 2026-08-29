from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from tgw.development.partial_resume import (
    append_attempt,
    classify,
    history,
    make_attempt,
    preservation_manifest,
    source_fingerprint,
    source_tree,
)


def _repo(tmp_path: Path) -> tuple[Path, str, str]:
    root = tmp_path / "todo-1752-plan-test"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    (root / "base").write_bytes(b"base\n")
    subprocess.run(["git", "add", "base"], cwd=root, check=True)
    subprocess.run(["git", "-c", "user.name=T", "-c", "user.email=t@t", "commit", "-qm", "base"], cwd=root, check=True)
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    tree = subprocess.check_output(["git", "rev-parse", "HEAD^{tree}"], cwd=root, text=True).strip()
    return root, head, tree


def _binding(root: Path, head: str, tree: str, job: str = "job-1", count: int = 1) -> dict:
    return {
        "job_id": job,
        "attempt_count": count,
        "todo_id": 1752,
        "plan_commit": "a" * 40,
        "solution_hash": "sha256:" + "b" * 64,
        "source_commit": head,
        "source_tree": tree,
        "actor": "codex",
        "worktree": str(root),
        "treatment_id": "codex-implement",
        "treatment_version": "1",
    }


def test_partial_then_failure_remains_resumable_and_append_only(tmp_path: Path) -> None:
    root, head, tree = _repo(tmp_path)
    (root / "partial.py").write_bytes(b"PARTIAL = 1\n")
    first = make_attempt(_binding(root, head, tree), root, outcome="partial")
    first_path = append_attempt(root, first)
    before = first_path.read_bytes()
    second = make_attempt(_binding(root, head, tree, "job-2", 2), root, outcome="failed", predecessor=first["attempt_hash"])
    append_attempt(root, second)
    state = classify(root, {**_binding(root, head, tree), "job_id": None, "attempt_count": None})
    assert state["state"] == "RESUMABLE_PARTIAL"
    assert state["resume_of"] == first["attempt_hash"]
    assert state["predecessor"] == second["attempt_hash"]
    assert first_path.read_bytes() == before
    assert len(history(root)) == 2


def test_fingerprint_covers_index_binary_mode_rename_and_symlinks(tmp_path: Path) -> None:
    root, _head, _tree = _repo(tmp_path)
    (root / "base").write_bytes(b"\x00\xffchanged")
    subprocess.run(["git", "add", "base"], cwd=root, check=True)
    subprocess.run(["git", "-c", "user.name=T", "-c", "user.email=t@t", "commit", "-qm", "binary"], cwd=root, check=True)
    subprocess.run(["git", "mv", "base", "renamed"], cwd=root, check=True)
    os.chmod(root / "renamed", 0o755)
    os.symlink("missing-target", root / "dangling")
    state = source_fingerprint(root)
    assert state["status_nul_b64"] and state["index_delta_b64"]
    assert state["worktree_binary_delta_b64"] is not None
    assert any(item["type"] == "symlink" and item["target"] == "missing-target" for item in state["nodes"])
    assert any(item["mode"] == "0755" for item in state["nodes"] if item["type"] == "file")
    assert any("R" in item["xy"] and item.get("original_path") for item in state["status_entries"])


def test_fingerprint_excludes_every_workflow_receipt(tmp_path: Path) -> None:
    from tgw.development.partial_resume import RECEIPT_FILES

    root, _head, _tree = _repo(tmp_path)
    before = source_fingerprint(root)
    for name in RECEIPT_FILES:
        (root / name).write_text(f"{name}\n")
    after = source_fingerprint(root)

    assert after["fingerprint"] == before["fingerprint"]
    assert after["changed_paths"] == []


def test_every_partial_resume_git_probe_uses_exact_canonical_safe_directory(
    tmp_path: Path, monkeypatch
) -> None:
    from tgw.development import partial_resume

    root, head, tree = _repo(tmp_path)
    alias = tmp_path / "worktree-alias"
    alias.symlink_to(root, target_is_directory=True)
    canonical = root.resolve()
    real_run = subprocess.run
    probes: list[tuple[tuple[str, ...], Path]] = []

    def recording_run(command, **kwargs):
        if command[0] == "git":
            probes.append((tuple(command), Path(kwargs["cwd"])))
        return real_run(command, **kwargs)

    monkeypatch.setattr(partial_resume.subprocess, "run", recording_run)

    assert source_tree(alias, head) == tree
    fingerprint = source_fingerprint(alias)
    assert fingerprint["head"] == head
    assert classify(alias)["state"] == "ABANDONED_CLEAN"

    assert probes
    for argv, cwd in probes:
        assert argv[:3] == ("git", "-c", f"safe.directory={canonical}")
        assert argv[2] != "safe.directory=*"
        assert cwd == canonical
    status = next(argv for argv, _cwd in probes if "status" in argv)
    assert status[status.index("--") + 1] == "."


def test_tamper_refuses_resume_and_writes_bound_preservation(tmp_path: Path) -> None:
    root, head, tree = _repo(tmp_path)
    (root / "partial.py").write_text("one\n")
    binding = _binding(root, head, tree)
    append_attempt(root, make_attempt(binding, root, outcome="partial"))
    (root / "partial.py").write_text("tampered\n")
    state = classify(root, {**binding, "job_id": None, "attempt_count": None})
    assert state["state"] == "UNSAFE_DIRTY"
    manifest = preservation_manifest(root, state, binding)
    assert manifest.is_file() and (root / "partial.py").read_text() == "tampered\n"


def test_lineage_validation_requires_expected_source_tree(tmp_path: Path) -> None:
    root, head, tree = _repo(tmp_path)
    (root / "partial.py").write_text("one\n")
    binding = _binding(root, head, tree)
    append_attempt(root, make_attempt(binding, root, outcome="partial"))
    incomplete = {**binding, "job_id": None, "attempt_count": None}
    incomplete.pop("source_tree")

    state = classify(root, incomplete)

    assert state["state"] == "STALE_RECEIPT"
    assert "source_tree" in state["error"]


def test_runner_requires_exact_resume_hash_and_fingerprint(tmp_path: Path) -> None:
    from tgw.workers import codex_implement

    root, head, tree = _repo(tmp_path)
    (root / "partial.py").write_text("one\n")
    binding = _binding(root, head, tree)
    attempt = make_attempt(binding, root, outcome="partial")
    append_attempt(root, attempt)
    job = {
        "treatment_id": "codex-implement",
        "treatment_version": "1",
        "todo_id": 1752,
        "todo_agent": "codex",
        "job_id": "job-2",
        "attempt_count": 2,
        "plan_binding": {"plan_commit": "a" * 40, "solution_hash": "sha256:" + "b" * 64, "source_commit": head},
        "task_spec": {"schema": "coding-task/v1", "todo_id": 1752, "agent": "codex", "body": "continue"},
        "resume_of": "sha256:wrong",
        "resume_fingerprint": attempt["fingerprint"],
    }
    with pytest.raises(Exception, match="fingerprint are exact"):
        codex_implement._run_with_lease(job, root, invoke=lambda *a, **k: None)


def test_runner_accepts_exact_resume_preserves_bytes_and_excludes_evidence(tmp_path: Path, monkeypatch) -> None:
    from tgw.workers import codex_implement

    root, head, tree = _repo(tmp_path)
    archive = tmp_path / "preservation-archive"
    archive.mkdir()
    archive.chmod(0o2770)
    os.chown(archive, -1, __import__("grp").getgrnam("tgw-coders").gr_gid)
    monkeypatch.setenv("TGW_CODING_PRESERVATION_ARCHIVE_ROOT", str(archive))
    partial = root / "partial.py"
    partial.write_text("preserve = True\n")
    binding = _binding(root, head, tree)
    attempt = make_attempt(binding, root, outcome="partial")
    append_attempt(root, attempt)
    preservation_manifest(root, {"state": "RESUMABLE_PARTIAL"}, binding)
    auth = tmp_path / "auth.json"
    auth.write_text("{}")
    monkeypatch.setattr(codex_implement, "_codex_auth_path", lambda: auth)
    monkeypatch.setattr(codex_implement, "_codex_binary", lambda: "/bin/true")
    monkeypatch.setattr(codex_implement, "_write_isolated_config", lambda _home: None)
    job = {
        "treatment_id": "codex-implement",
        "treatment_version": "1",
        "todo_id": 1752,
        "todo_agent": "codex",
        "job_id": "job-2",
        "attempt_count": 2,
        "plan_binding": {"plan_commit": "a" * 40, "solution_hash": "sha256:" + "b" * 64, "source_commit": head},
        "task_spec": {"schema": "coding-task/v1", "todo_id": 1752, "agent": "codex", "body": "continue"},
        "resume_of": attempt["attempt_hash"],
        "resume_fingerprint": attempt["fingerprint"],
    }

    def invoke(command, **kwargs):
        assert "exact bounded continuation" in kwargs["input"]
        assert partial.read_text() == "preserve = True\n"
        (root / "finished.py").write_text("finished = True\n")
        output = Path(command[command.index("-o") + 1])
        output.write_text(json.dumps({"status": "implemented", "summary": "done", "tests": ["offline"]}))
        return subprocess.CompletedProcess(command, 0, "", "")

    result = codex_implement._run_with_lease(job, root, invoke=invoke)
    assert result["outcome"] == "satisfied"
    assert partial.read_text() == "preserve = True\n"
    assert subprocess.check_output(["git", "ls-tree", "-r", "--name-only", "HEAD"], cwd=root, text=True).splitlines() == ["base", "finished.py", "partial.py"]
    assert not (root / ".tgw-coding-preservation").exists()
    retirement = next(item for item in result["artifacts"] if item["kind"] == "preservation_retirement")
    assert Path(retirement["archive"]).is_dir()
    diff = next(item for item in result["artifacts"] if item["kind"] == "git_diff")
    assert "finished.py" in diff["changed_paths"]  # formerly untracked source is receipt evidence
    closed_binding = {**binding, "job_id": "job-2", "attempt_count": 2}
    closed_attempt = make_attempt(
        closed_binding, root, outcome="satisfied",
        predecessor=attempt["attempt_hash"], artifacts=result["artifacts"],
    )
    append_attempt(root, closed_attempt)
    classification = classify(root, {key: value for key, value in binding.items() if key not in {"job_id", "attempt_count"}})
    assert classification["state"] == "CLOSED_CANDIDATE", classification
    from tgw.development.coding_snapshot import _git_is_clean
    assert _git_is_clean(root)


def test_retirement_receipt_recovers_complete_temporary_without_partial_final(tmp_path: Path) -> None:
    from tgw.development import partial_resume

    expected = b'{"complete":true}\n'
    directory = tmp_path / "archive"
    directory.mkdir()
    (directory / ".retirement-receipt.json.tmp").write_bytes(expected)
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        partial_resume._publish_retirement_receipt(descriptor, expected)
    finally:
        os.close(descriptor)
    assert (directory / "retirement-receipt.json").read_bytes() == expected
    assert not (directory / ".retirement-receipt.json.tmp").exists()


def test_retirement_receipt_refuses_truncated_crash_temporary(tmp_path: Path) -> None:
    from tgw.development import partial_resume

    directory = tmp_path / "archive"
    directory.mkdir()
    (directory / ".retirement-receipt.json.tmp").write_bytes(b"truncated")
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        with pytest.raises(partial_resume.PartialResumeError, match="temporary receipt differs"):
            partial_resume._publish_retirement_receipt(descriptor, b'{"complete":true}\n')
    finally:
        os.close(descriptor)
    assert not (directory / "retirement-receipt.json").exists()


def test_retirement_receipt_recovers_post_link_crash_same_inode(tmp_path: Path) -> None:
    from tgw.development import partial_resume

    expected = b'{"complete":true}\n'
    directory = tmp_path / "archive"
    directory.mkdir()
    temporary = directory / ".retirement-receipt.json.tmp"
    final = directory / "retirement-receipt.json"
    temporary.write_bytes(expected)
    os.link(temporary, final)
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        partial_resume._publish_retirement_receipt(descriptor, expected)
    finally:
        os.close(descriptor)
    assert final.read_bytes() == expected
    assert final.stat().st_nlink == 1
    assert not temporary.exists()


@pytest.mark.parametrize("hostile", ["foreign-hardlink", "wrong-temporary", "replacement"])
def test_retirement_receipt_rejects_hostile_completed_names(
    tmp_path: Path, hostile: str,
) -> None:
    from tgw.development import partial_resume

    expected = b'{"complete":true}\n'
    directory = tmp_path / "archive"
    directory.mkdir()
    final = directory / "retirement-receipt.json"
    temporary = directory / ".retirement-receipt.json.tmp"
    final.write_bytes(expected)
    if hostile == "foreign-hardlink":
        os.link(final, directory / "foreign")
    elif hostile == "wrong-temporary":
        temporary.write_bytes(expected)
    else:
        temporary.write_bytes(expected)
        os.link(temporary, directory / "temporary-alias")
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        with pytest.raises(partial_resume.PartialResumeError):
            partial_resume._publish_retirement_receipt(descriptor, expected)
    finally:
        os.close(descriptor)
    assert final.read_bytes() == expected


def test_retirement_receipt_rejects_concurrent_foreign_publisher(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tgw.development import partial_resume

    expected = b'{"complete":true}\n'
    directory = tmp_path / "archive"
    directory.mkdir()
    original_rename = partial_resume._rename_noreplace

    def publish_foreign(*args, **kwargs):
        (directory / "retirement-receipt.json").write_bytes(b"foreign\n")
        raise partial_resume.PartialResumeError(
            "preservation archive destination already exists"
        )

    monkeypatch.setattr(partial_resume, "_rename_noreplace", publish_foreign)
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        with pytest.raises(partial_resume.PartialResumeError, match="receipt differs"):
            partial_resume._publish_retirement_receipt(descriptor, expected)
    finally:
        os.close(descriptor)
    monkeypatch.setattr(partial_resume, "_rename_noreplace", original_rename)
    assert (directory / "retirement-receipt.json").read_bytes() == b"foreign\n"
    assert not list(directory.glob(".retirement-receipt.json.tmp.*"))


@pytest.mark.parametrize("race", ["unique-temporaries", "shared-temporary"])
def test_two_real_same_receipt_publishers_converge_to_one_nlink1_final(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, race: str,
) -> None:
    from tgw.development import partial_resume

    expected = b'{"complete":true}\n'
    directory = tmp_path / "archive"
    directory.mkdir()
    descriptors = [
        os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        for _ in range(2)
    ]
    barrier = threading.Barrier(2)
    source_errors: list[int] = []
    if race == "unique-temporaries":
        original_listdir = partial_resume.os.listdir
        lock = threading.Lock()
        initial_calls = 0

        def synchronized_initial_scan(path):
            nonlocal initial_calls
            result = original_listdir(path)
            with lock:
                synchronize = initial_calls < 2
                initial_calls += 1
            if synchronize:
                barrier.wait(timeout=5)
            return result

        monkeypatch.setattr(partial_resume.os, "listdir", synchronized_initial_scan)
    else:
        (directory / ".retirement-receipt.json.tmp").write_bytes(expected)
        original_checkpoint = partial_resume._retirement_receipt_checkpoint
        original_rename = partial_resume._rename_noreplace
        lock = threading.Lock()
        before_calls = 0

        def synchronize_shared_rename(phase: str) -> None:
            nonlocal before_calls
            if phase == "before-publish":
                with lock:
                    synchronize = before_calls < 2
                    before_calls += 1
                if synchronize:
                    barrier.wait(timeout=5)
            original_checkpoint(phase)

        monkeypatch.setattr(
            partial_resume, "_retirement_receipt_checkpoint", synchronize_shared_rename,
        )

        def observe_source_enoent(*args):
            try:
                return original_rename(*args)
            except partial_resume.PartialResumeError as exc:
                if isinstance(exc.__cause__, OSError):
                    source_errors.append(exc.__cause__.errno)
                raise

        monkeypatch.setattr(partial_resume, "_rename_noreplace", observe_source_enoent)

    def invoke(descriptor: int):
        try:
            return partial_resume._publish_retirement_receipt(descriptor, expected)
        except Exception as exc:  # results and exceptions are deliberately collected
            return exc

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(invoke, descriptors))
    finally:
        for descriptor in descriptors:
            os.close(descriptor)
    assert results == [None, None]
    if race == "shared-temporary":
        assert source_errors == [__import__("errno").ENOENT]
    final = directory / "retirement-receipt.json"
    assert final.read_bytes() == expected
    assert final.stat().st_nlink == 1
    assert sorted(path.name for path in directory.iterdir()) == [final.name]


def test_retirement_receipt_fsyncs_file_then_directory_publication_and_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tgw.development import partial_resume

    directory = tmp_path / "archive"
    directory.mkdir()
    directory_identity = (directory.stat().st_dev, directory.stat().st_ino)
    events: list[tuple[int, int]] = []
    original_fsync = partial_resume.os.fsync

    def record(descriptor: int) -> None:
        state = os.fstat(descriptor)
        events.append((state.st_dev, state.st_ino))
        original_fsync(descriptor)

    monkeypatch.setattr(partial_resume.os, "fsync", record)
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        partial_resume._publish_retirement_receipt(descriptor, b'{"complete":true}\n')
    finally:
        os.close(descriptor)
    assert events[0] != directory_identity
    assert events[1:] == [directory_identity, directory_identity]


@pytest.mark.parametrize("state", ["existing", "post-link", "truncated", "new"])
def test_retirement_receipt_conditionally_closes_every_open_descriptor(
    tmp_path: Path, state: str,
) -> None:
    from tgw.development import partial_resume

    expected = b'{"complete":true}\n'
    directory = tmp_path / "archive"
    directory.mkdir()
    final = directory / "retirement-receipt.json"
    temporary = directory / ".retirement-receipt.json.tmp"
    if state in {"existing", "post-link"}:
        final.write_bytes(expected)
    if state == "post-link":
        os.link(final, temporary)
    if state == "truncated":
        temporary.write_bytes(b"truncated")
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    before = set(os.listdir("/proc/self/fd"))
    try:
        if state == "truncated":
            with pytest.raises(partial_resume.PartialResumeError):
                partial_resume._publish_retirement_receipt(descriptor, expected)
        else:
            partial_resume._publish_retirement_receipt(descriptor, expected)
        assert set(os.listdir("/proc/self/fd")) == before
    finally:
        os.close(descriptor)


def test_archive_rename_is_atomic_no_replace(tmp_path: Path) -> None:
    from tgw.development import partial_resume

    source = tmp_path / "source"
    destination = tmp_path / "destination"
    source.mkdir()
    destination.mkdir()
    source_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    destination_fd = os.dup(source_fd)
    try:
        with pytest.raises(partial_resume.PartialResumeError, match="already exists"):
            partial_resume._rename_noreplace(
                source_fd, source.name, destination_fd, destination.name,
            )
    finally:
        os.close(destination_fd)
        os.close(source_fd)
    assert source.is_dir()
    assert destination.is_dir()


@pytest.mark.parametrize("hostile", ["hardlink", "truncate", "replace"])
def test_archived_evidence_rejects_changed_or_linked_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, hostile: str,
) -> None:
    from tgw.development import partial_resume

    directory = tmp_path / "archive"
    directory.mkdir()
    evidence = directory / "evidence.json"
    evidence.write_bytes(b"complete evidence")
    if hostile == "hardlink":
        os.link(evidence, directory / "alias.json")
    else:
        original_fdopen = partial_resume.os.fdopen

        def mutate(descriptor, *args, **kwargs):
            stream = original_fdopen(descriptor, *args, **kwargs)
            if hostile == "truncate":
                evidence.write_bytes(b"")
            else:
                evidence.rename(directory / "old.json")
                evidence.write_bytes(b"replacement")
            return stream

        monkeypatch.setattr(partial_resume.os, "fdopen", mutate)
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises(partial_resume.PartialResumeError):
            partial_resume._archived_evidence(descriptor)
    finally:
        os.close(descriptor)


def test_archive_binding_failure_closes_already_open_descriptors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tgw.development import partial_resume

    opened = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    closed: list[int] = []
    monkeypatch.setattr(
        partial_resume, "_protected_archive_root",
        lambda *_args: (tmp_path, tmp_path.stat()),
    )
    calls = 0

    def open_then_fail(*_args):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise partial_resume.PartialResumeError("root replaced")
        return os.dup(opened)

    monkeypatch.setattr(partial_resume, "_open_bound_directory", open_then_fail)
    real_close = partial_resume.os.close

    def record_close(descriptor: int) -> None:
        closed.append(descriptor)
        real_close(descriptor)

    monkeypatch.setattr(partial_resume.os, "close", record_close)
    with pytest.raises(partial_resume.PartialResumeError, match="root replaced"):
        partial_resume.retire_preservation(tmp_path, todo_id=1, candidate_commit="a" * 40,
                                           archive_root=tmp_path)
    real_close(opened)
    assert len(closed) == 1


def test_archive_child_replacement_is_detected_through_pinned_descriptor(tmp_path: Path) -> None:
    from tgw.development import partial_resume

    child = tmp_path / "child"
    child.mkdir()
    archive_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    child_fd = os.open(child.name, os.O_RDONLY | os.O_DIRECTORY, dir_fd=archive_fd)
    child.rename(tmp_path / "detached")
    child.mkdir()
    try:
        with pytest.raises(partial_resume.PartialResumeError, match="binding changed"):
            partial_resume._verify_archive_child_binding(archive_fd, child.name, child_fd)
    finally:
        os.close(child_fd)
        os.close(archive_fd)


def _retirement_fixture(tmp_path: Path) -> tuple[Path, Path, str]:
    root, head, _tree = _repo(tmp_path)
    preservation = root / ".tgw-coding-preservation"
    preservation.mkdir()
    evidence = preservation / "attempt.json"
    evidence.write_bytes(b'{"evidence":true}\n')
    evidence.chmod(0o640)
    archive = tmp_path / "archive"
    archive.mkdir()
    os.chown(archive, -1, __import__("grp").getgrnam("tgw-coders").gr_gid)
    archive.chmod(0o2750)
    return root, archive, head


def test_retire_preservation_concurrent_destination_creation_is_non_destructive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tgw.development import partial_resume

    root, archive, head = _retirement_fixture(tmp_path)
    source = root / ".tgw-coding-preservation"
    original = partial_resume._rename_noreplace

    def race(source_fd: int, source_name: str, destination_fd: int, destination_name: str) -> None:
        os.mkdir(destination_name, mode=0o2750, dir_fd=destination_fd)
        original(source_fd, source_name, destination_fd, destination_name)

    monkeypatch.setattr(partial_resume, "_rename_noreplace", race)
    with pytest.raises(partial_resume.PartialResumeError, match="already exists"):
        partial_resume.retire_preservation(
            root, todo_id=1866, candidate_commit=head, archive_root=archive,
        )
    assert (source / "attempt.json").read_bytes() == b'{"evidence":true}\n'
    destinations = list(archive.iterdir())
    assert len(destinations) == 1 and destinations[0].is_dir()


@pytest.mark.parametrize("boundary", ["root", "archive", "source", "destination"])
def test_retire_preservation_replacement_boundaries_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, boundary: str,
) -> None:
    from tgw.development import partial_resume

    root, archive, head = _retirement_fixture(tmp_path)
    source = root / ".tgw-coding-preservation"
    evidence_path = source / "attempt.json"
    evidence_before = evidence_path.read_bytes()
    evidence_meta = evidence_path.stat()
    original_verify = partial_resume._verify_bound_directory
    original_evidence = partial_resume._archived_evidence
    original_child = partial_resume._open_archive_child
    changed = False
    replacements: dict[str, tuple[Path, Path, os.stat_result, os.stat_result, bytes]] = {}
    detached_evidence: Path | None = None

    def replace_directory(path: Path) -> None:
        nonlocal detached_evidence
        detached = path.with_name(path.name + "-detached")
        path.rename(detached)
        replacement_mode = {
            "root": 0o701,
            "archive": 0o703,
            "source": 0o705,
            "destination": 0o707,
        }[boundary]
        sentinel_bytes = f"replacement-{boundary}\n".encode()
        path.mkdir(mode=replacement_mode)
        path.chmod(replacement_mode)
        sentinel = path / f"replacement-{boundary}.sentinel"
        sentinel.write_bytes(sentinel_bytes)
        sentinel.chmod(0o600 | len(boundary))
        replacements[boundary] = (
            path, sentinel, path.stat(), sentinel.stat(), sentinel_bytes,
        )
        if boundary == "root":
            detached_evidence = detached / source.relative_to(root) / evidence_path.name
        elif boundary == "archive":
            detached_evidence = evidence_path
        elif boundary == "source":
            detached_evidence = detached / evidence_path.name
        else:
            detached_evidence = detached / evidence_path.name

    def verify(path: Path, descriptor: int) -> None:
        nonlocal changed
        if not changed and boundary in {"root", "archive"}:
            selected = root if boundary == "root" else archive
            if path == selected:
                changed = True
                replace_directory(selected)
        original_verify(path, descriptor)

    def read_evidence(descriptor: int):
        nonlocal changed
        result = original_evidence(descriptor)
        if not changed and boundary == "source":
            changed = True
            replace_directory(source)
        return result

    def child(*args):
        nonlocal changed
        result = original_child(*args)
        if not changed and boundary == "destination":
            changed = True
            replace_directory(archive / args[1])
        return result

    monkeypatch.setattr(partial_resume, "_verify_bound_directory", verify)
    monkeypatch.setattr(partial_resume, "_archived_evidence", read_evidence)
    monkeypatch.setattr(partial_resume, "_open_archive_child", child)
    with pytest.raises(partial_resume.PartialResumeError) as failure:
        partial_resume.retire_preservation(
            root, todo_id=1866, candidate_commit=head, archive_root=archive,
        )
    assert changed, str(failure.value)
    replacement, sentinel, replacement_before, sentinel_before, sentinel_bytes = (
        replacements[boundary]
    )
    replacement_after = replacement.stat()
    sentinel_after = sentinel.stat()
    assert sentinel.read_bytes() == sentinel_bytes
    assert (
        replacement_after.st_dev, replacement_after.st_ino, replacement_after.st_mode,
        replacement_after.st_uid, replacement_after.st_gid,
    ) == (
        replacement_before.st_dev, replacement_before.st_ino, replacement_before.st_mode,
        replacement_before.st_uid, replacement_before.st_gid,
    )
    assert (
        sentinel_after.st_dev, sentinel_after.st_ino, sentinel_after.st_mode,
        sentinel_after.st_uid, sentinel_after.st_gid, sentinel_after.st_size,
        sentinel_after.st_mtime_ns,
    ) == (
        sentinel_before.st_dev, sentinel_before.st_ino, sentinel_before.st_mode,
        sentinel_before.st_uid, sentinel_before.st_gid, sentinel_before.st_size,
        sentinel_before.st_mtime_ns,
    )
    assert detached_evidence is not None
    evidence_after = detached_evidence.stat()
    assert detached_evidence.read_bytes() == evidence_before
    assert (
        evidence_after.st_mode, evidence_after.st_uid, evidence_after.st_gid,
        evidence_after.st_size, evidence_after.st_mtime_ns,
    ) == (
        evidence_meta.st_mode, evidence_meta.st_uid, evidence_meta.st_gid,
        evidence_meta.st_size, evidence_meta.st_mtime_ns,
    )


@pytest.mark.parametrize("ancestor_kind", ["root-parent", "archive-parent"])
def test_retire_preservation_ancestor_replacement_is_non_destructive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ancestor_kind: str,
) -> None:
    from tgw.development import partial_resume

    workspace_parent = tmp_path / "workspace-parent"
    workspace_parent.mkdir()
    root, _discarded_archive, head = _retirement_fixture(workspace_parent)
    archive_parent = tmp_path / "archive-parent"
    archive_parent.mkdir()
    archive = archive_parent / "archive"
    archive.mkdir(mode=0o2750)
    archive.chmod(0o2750)
    os.chown(archive, -1, __import__("grp").getgrnam("tgw-coders").gr_gid)
    source = root / ".tgw-coding-preservation"
    evidence = source / "attempt.json"
    evidence_before = evidence.read_bytes()
    evidence_meta = evidence.stat()
    selected = root.parent if ancestor_kind == "root-parent" else archive.parent
    original_verify = partial_resume._verify_bound_directory
    changed = False
    detached = selected.with_name(selected.name + "-detached")

    def verify(path: Path, descriptor: int) -> None:
        nonlocal changed
        trigger = root if ancestor_kind == "root-parent" else archive
        if not changed and path == trigger:
            changed = True
            selected.rename(detached)
            selected.mkdir(mode=0o711)
            (selected / "sentinel").write_bytes(b"replacement\n")
        original_verify(path, descriptor)

    monkeypatch.setattr(partial_resume, "_verify_bound_directory", verify)
    with pytest.raises(partial_resume.PartialResumeError) as failure:
        partial_resume.retire_preservation(
            root, todo_id=1867, candidate_commit=head, archive_root=archive,
        )
    assert changed, str(failure.value)
    replacement_meta = selected.stat()
    assert (selected / "sentinel").read_bytes() == b"replacement\n"
    assert stat.S_IMODE(replacement_meta.st_mode) == 0o711
    detached_evidence = (
        next(detached.rglob("attempt.json"))
        if ancestor_kind == "root-parent"
        else evidence
    )
    after = detached_evidence.stat()
    assert detached_evidence.read_bytes() == evidence_before
    assert (
        after.st_mode, after.st_uid, after.st_gid, after.st_size, after.st_mtime_ns,
    ) == (
        evidence_meta.st_mode, evidence_meta.st_uid, evidence_meta.st_gid,
        evidence_meta.st_size, evidence_meta.st_mtime_ns,
    )


def test_local_coding_worker_persists_partial_before_compatibility_and_failure(tmp_path: Path, monkeypatch) -> None:
    from tgw.workers.coding import CodingWorker

    root, head, _tree = _repo(tmp_path)
    plan_binding = {"plan_commit": "a" * 40, "solution_hash": "sha256:" + "b" * 64, "source_commit": head, "worktree": str(root), "worktree_identity": {}}
    payload = {
        "treatment_id": "codex-implement",
        "treatment_version": "1",
        "todo_id": 1752,
        "todo_agent": "codex",
        "graph_id": "graph",
        "object_generation": "generation",
        "worktree": str(root),
        "object_id": str(root),
        "plan_binding": plan_binding,
    }
    config = {"coding": {"worktree_root": str(root.parent), "repository_root": str(root)}}

    def partial(_treatment, _payload, _worktree):
        (root / "partial.py").write_text("partial = True\n")
        return {"outcome": "partial", "established_conditions": [], "artifacts": [{"kind": "crash"}]}

    worker = CodingWorker("codex-implement", config, launcher=partial)
    monkeypatch.setattr(worker, "_validated_plan_binding", lambda _payload, _worktree: plan_binding)
    with pytest.raises(Exception, match="reported partial"):
        worker.handle({"job_id": "job-1", "attempt_count": 1, "payload_json": payload})
    fixed = (root / "implementation-receipt.json").read_bytes()
    state = classify(root, {**_binding(root, head, subprocess.check_output(["git", "rev-parse", "HEAD^{tree}"], cwd=root, text=True).strip()), "job_id": None, "attempt_count": None})

    resumed = {**payload, "resume_of": state["resume_of"], "resume_fingerprint": state["fingerprint"]}
    failing = CodingWorker("codex-implement", config, launcher=lambda *_args: (_ for _ in ()).throw(RuntimeError("later crash")))
    monkeypatch.setattr(failing, "_validated_plan_binding", lambda _payload, _worktree: plan_binding)
    with pytest.raises(Exception, match="later crash"):
        failing.handle({"job_id": "job-2", "attempt_count": 2, "payload_json": resumed})
    assert (root / "implementation-receipt.json").read_bytes() == fixed
    assert [item["outcome"] for item in history(root)] == ["partial", "failed"]


def test_recordless_resume_preserves_projection_across_second_partial(
    tmp_path: Path, monkeypatch
) -> None:
    from tgw.errors import TreatmentFailure
    from tgw.workers.coding import CodingWorker

    root, head, tree = _repo(tmp_path)
    plan_binding = {
        "plan_commit": "a" * 40,
        "solution_hash": "sha256:" + "b" * 64,
        "source_commit": head,
        "worktree": str(root),
        "worktree_identity": {},
    }
    payload = {
        "treatment_id": "codex-implement",
        "treatment_version": "1",
        "todo_id": 1752,
        "todo_agent": "codex",
        "graph_id": "graph",
        "object_generation": "generation",
        "worktree": str(root),
        "object_id": str(root),
        "plan_binding": plan_binding,
    }
    config = {
        "coding": {
            "worktree_root": str(root.parent),
            "repository_root": str(root),
        }
    }

    def partial(_treatment, _payload, _worktree):
        (root / "partial.py").write_text("partial = True\n")
        return {
            "outcome": "partial",
            "established_conditions": [],
            "artifacts": [{"kind": "recordless_partial"}],
        }

    first = CodingWorker("codex-implement", config, launcher=partial)
    monkeypatch.setattr(
        first, "_validated_plan_binding", lambda _payload, _worktree: plan_binding
    )
    with pytest.raises(TreatmentFailure, match="reported partial"):
        first.handle(
            {"job_id": "job-1", "attempt_count": 1, "payload_json": payload}
        )
    fixed = (root / "implementation-receipt.json").read_bytes()
    state = classify(
        root,
        {
            **_binding(root, head, tree),
            "job_id": None,
            "attempt_count": None,
        },
    )
    resumed = {
        **payload,
        "resume_of": state["resume_of"],
        "resume_fingerprint": state["fingerprint"],
    }
    second = CodingWorker("codex-implement", config, launcher=partial)
    monkeypatch.setattr(
        second, "_validated_plan_binding", lambda _payload, _worktree: plan_binding
    )
    with pytest.raises(TreatmentFailure, match="reported partial") as failure:
        second.handle(
            {"job_id": "job-2", "attempt_count": 2, "payload_json": resumed}
        )

    attempts = history(root)
    assert (root / "implementation-receipt.json").read_bytes() == fixed
    assert [item["outcome"] for item in attempts] == ["partial", "partial"]
    assert failure.value.result["implementation_attempt_hash"] == attempts[-1][
        "attempt_hash"
    ]
    assert "coding_lifecycle" not in failure.value.result


@pytest.mark.parametrize(("old_lifecycle", "new_lifecycle"), [(None, {}), ({}, None)])
def test_recordless_receipt_preservation_cannot_cross_lifecycle_boundary(
    tmp_path: Path,
    old_lifecycle,
    new_lifecycle,
) -> None:
    from tgw.errors import HardFailure
    from tgw.workers.coding import _write_receipt

    predecessor = "sha256:" + "1" * 64
    plan = {"plan_commit": "a" * 40, "worktree": str(tmp_path)}
    prior = {
        "status": "FAIL",
        "treatment_id": "codex-implement",
        "outcome": "partial",
        "implementation_attempt_hash": predecessor,
        "plan_binding": plan,
        "object_id": str(tmp_path),
    }
    replacement = {
        **prior,
        "implementation_attempt_hash": "sha256:" + "2" * 64,
    }
    if old_lifecycle is not None:
        prior["coding_lifecycle"] = old_lifecycle
    if new_lifecycle is not None:
        replacement["coding_lifecycle"] = new_lifecycle
    path = tmp_path / "implementation-receipt.json"
    path.write_text(json.dumps(prior, sort_keys=True) + "\n")
    fixed = path.read_bytes()

    with pytest.raises(
        HardFailure,
        match="does not bind the archived generation",
    ):
        _write_receipt(path, replacement, predecessor=predecessor)
    assert path.read_bytes() == fixed


def test_recordless_receipt_preservation_rejects_forged_predecessor(
    tmp_path: Path,
) -> None:
    from tgw.errors import HardFailure
    from tgw.workers.coding import _write_receipt

    predecessor = "sha256:" + "1" * 64
    plan = {"plan_commit": "a" * 40, "worktree": str(tmp_path)}
    prior = {
        "status": "FAIL",
        "treatment_id": "codex-implement",
        "outcome": "partial",
        "implementation_attempt_hash": "sha256:" + "f" * 64,
        "plan_binding": plan,
        "object_id": str(tmp_path),
    }
    path = tmp_path / "implementation-receipt.json"
    path.write_text(json.dumps(prior, sort_keys=True) + "\n")
    fixed = path.read_bytes()

    with pytest.raises(
        HardFailure,
        match="does not bind the archived generation",
    ):
        _write_receipt(path, dict(prior), predecessor=predecessor)
    assert path.read_bytes() == fixed


def test_exact_1747_migration_uses_copy_and_binds_both_jobs(tmp_path: Path, monkeypatch) -> None:
    from tgw.development import partial_resume

    root, head, tree = _repo(tmp_path)
    for relative in ("src/tgw/coding_cli.py", "src/tgw/pp_workflow_reconcile.py", "tests/test_pp_workflow_reconcile.py"):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(relative + "\n")
    (root / "implementation-receipt.json").write_text('{"outcome":"partial"}\n')
    binding = {**_binding(root, head, tree), "todo_id": 1747}
    monkeypatch.setattr(partial_resume, "LEGACY_1747", root.resolve())
    monkeypatch.setattr(partial_resume, "LEGACY_1747_SOURCE_COMMIT", head)
    monkeypatch.setattr(partial_resume, "LEGACY_1747_SOURCE_TREE", tree)
    monkeypatch.setattr(partial_resume, "LEGACY_1747_PLAN_COMMIT", binding["plan_commit"])
    monkeypatch.setattr(partial_resume, "LEGACY_1747_SOLUTION_HASH", binding["solution_hash"])
    monkeypatch.setattr(
        partial_resume,
        "LEGACY_1747_FINGERPRINT",
        partial_resume.source_fingerprint(root)["fingerprint"],
    )
    monkeypatch.setattr(
        partial_resume,
        "LEGACY_1747_RECEIPT_SHA256",
        __import__("hashlib").sha256(
            (root / "implementation-receipt.json").read_bytes()
        ).hexdigest(),
    )

    def job(job_id: str, outcome: str, count: int) -> dict:
        return {
            "job_id": job_id,
            "outcome": outcome,
            "attempt_count": count,
            "state": "dead_letter",
            "error_code": "HARD_FAILURE",
            "error_detail": f"HardFailure('coding treatment reported {outcome}')",
            "payload": {
                "todo_id": 1747,
                "todo_agent": "codex",
                "worktree": str(root),
                "object_id": str(root),
                "treatment_id": "codex-implement",
                "plan_binding": {
                    **{key: binding[key] for key in ("plan_commit", "solution_hash", "source_commit")},
                    "worktree": str(root),
                    "worktree_identity": {
                        "actor": "codex", "worktree": str(root), "head": head,
                    },
                },
            },
        }

    jobs = [job("dfdfd643-312e-46ef-a33c-1542340e9b9c", "partial", 1), job("2b1f9f04-a09f-489e-aade-f21ab1e4aaa9", "failed", 1)]
    manifest = partial_resume.migrate_todo_1747(root, binding, jobs)
    assert manifest.is_file()
    assert [item["job_id"] for item in history(root)] == [jobs[0]["job_id"], jobs[1]["job_id"]]
    assert (root / "implementation-receipt.json").read_text() == '{"outcome":"partial"}\n'
    assert partial_resume.migrate_todo_1747(root, binding, jobs) == manifest
    missing_count = json.loads(json.dumps(jobs))
    missing_count[1].pop("attempt_count")
    with pytest.raises(Exception):
        partial_resume.migrate_todo_1747(root, binding, missing_count)
    with pytest.raises(Exception):
        partial_resume.migrate_todo_1747(root, binding, [jobs[0], jobs[0]])
    with pytest.raises(Exception):
        partial_resume.migrate_todo_1747(
            root, {**binding, "source_tree": "f" * 40}, jobs
        )
    jobs[1]["payload"]["todo_agent"] = "claude"
    with pytest.raises(Exception):
        partial_resume.migrate_todo_1747(root, binding, jobs)


def test_1747_manifest_survives_closed_receipt_and_rejects_tampering(tmp_path: Path, monkeypatch) -> None:
    """Exercise the real filesystem/Git migration, closure, and repeat transition."""
    from tgw.development import partial_resume

    root, head, tree = _repo(tmp_path)
    changed = [
        "src/tgw/coding_cli.py",
        "src/tgw/pp_workflow_reconcile.py",
        "tests/test_pp_workflow_reconcile.py",
    ]
    for relative in changed:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(relative + "\n")
    receipt = root / "implementation-receipt.json"
    receipt.write_bytes(b'{"outcome":"partial"}\n')
    binding = {**_binding(root, head, tree), "todo_id": 1747}
    monkeypatch.setattr(partial_resume, "LEGACY_1747", root.resolve())
    monkeypatch.setattr(partial_resume, "LEGACY_1747_SOURCE_COMMIT", head)
    monkeypatch.setattr(partial_resume, "LEGACY_1747_SOURCE_TREE", tree)
    monkeypatch.setattr(partial_resume, "LEGACY_1747_PLAN_COMMIT", binding["plan_commit"])
    monkeypatch.setattr(partial_resume, "LEGACY_1747_SOLUTION_HASH", binding["solution_hash"])
    monkeypatch.setattr(partial_resume, "LEGACY_1747_FINGERPRINT", partial_resume.source_fingerprint(root)["fingerprint"])
    monkeypatch.setattr(partial_resume, "LEGACY_1747_RECEIPT_SHA256", hashlib.sha256(receipt.read_bytes()).hexdigest())

    def job(job_id: str, outcome: str) -> dict:
        return {
            "job_id": job_id, "outcome": outcome, "attempt_count": 1,
            "state": "dead_letter", "error_code": "HARD_FAILURE",
            "error_detail": f"HardFailure('coding treatment reported {outcome}')",
            "payload": {
                "todo_id": 1747, "todo_agent": "codex", "worktree": str(root),
                "object_id": str(root), "treatment_id": "codex-implement",
                "plan_binding": {
                    **{key: binding[key] for key in ("plan_commit", "solution_hash", "source_commit")},
                    "worktree": str(root),
                    "worktree_identity": {"actor": "codex", "worktree": str(root), "head": head},
                },
            },
        }

    jobs = [job("dfdfd643-312e-46ef-a33c-1542340e9b9c", "partial"), job("2b1f9f04-a09f-489e-aade-f21ab1e4aaa9", "failed")]
    manifest = partial_resume.migrate_todo_1747(root, binding, jobs)
    initial = {path: path.read_bytes() for path in [manifest, *sorted((root / partial_resume.HISTORY).glob("*.json"))]}
    subprocess.run(["git", "add", *changed], cwd=root, check=True)
    subprocess.run(["git", "-c", "user.name=T", "-c", "user.email=t@t", "commit", "-qm", "candidate"], cwd=root, check=True)
    candidate = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    candidate_tree = subprocess.check_output(["git", "rev-parse", "HEAD^{tree}"], cwd=root, text=True).strip()
    satisfied = partial_resume.make_attempt(
        {**binding, "job_id": "resume-job", "attempt_count": 1}, root,
        outcome="satisfied", predecessor=partial_resume.history(root)[-1]["attempt_hash"],
        artifacts=[{"kind": "closed_candidate", "commit": candidate, "tree": candidate_tree,
                    "base_commit": head, "changed_paths": sorted(changed)}],
    )
    partial_resume.append_attempt(root, satisfied)
    closed_receipt = b'{"outcome":"satisfied","candidate":"closed"}\n'
    receipt.write_bytes(closed_receipt)

    assert partial_resume.classify(root, {**binding, "job_id": None, "attempt_count": None})["state"] == "CLOSED_CANDIDATE"
    assert partial_resume.migrate_todo_1747(root, binding, jobs) == manifest
    assert receipt.read_bytes() == closed_receipt
    assert all(path.read_bytes() == content for path, content in initial.items())

    pristine = manifest.read_bytes()
    value = json.loads(pristine)
    value["source"]["nodes"] = []
    unsigned = dict(value)
    unsigned.pop("manifest_hash")
    value["manifest_hash"] = "sha256:" + hashlib.sha256(partial_resume._canonical(unsigned)).hexdigest()
    manifest.chmod(0o640)
    manifest.write_text(json.dumps(value, sort_keys=True) + "\n")
    with pytest.raises(Exception, match="manifest differs"):
        partial_resume.migrate_todo_1747(root, binding, jobs)
    manifest.write_bytes(pristine)
    value = json.loads(pristine)
    value["tampered"] = True
    unsigned = dict(value)
    unsigned.pop("manifest_hash")
    value["manifest_hash"] = "sha256:" + hashlib.sha256(partial_resume._canonical(unsigned)).hexdigest()
    manifest.write_text(json.dumps(value, sort_keys=True) + "\n")
    with pytest.raises(Exception, match="manifest differs"):
        partial_resume.migrate_todo_1747(root, binding, jobs)
    manifest.write_bytes(pristine)
    history_path = sorted((root / partial_resume.HISTORY).glob("*.json"))[0]
    history_path.chmod(0o640)
    history_path.write_bytes(
        history_path.read_bytes().replace(b'"partial"', b'"failed"', 1)
    )
    with pytest.raises(Exception, match="lineage"):
        partial_resume.migrate_todo_1747(root, binding, jobs)


def test_satisfied_attempt_closes_only_its_exact_candidate(tmp_path: Path) -> None:
    root, baseline, tree = _repo(tmp_path)
    (root / "candidate.py").write_text("candidate = 1\n")
    subprocess.run(["git", "add", "candidate.py"], cwd=root, check=True)
    subprocess.run(
        ["git", "-c", "user.name=T", "-c", "user.email=t@t", "commit", "-qm", "candidate"],
        cwd=root,
        check=True,
    )
    candidate = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    candidate_tree = subprocess.check_output(["git", "rev-parse", "HEAD^{tree}"], cwd=root, text=True).strip()
    binding = _binding(root, baseline, tree)
    attempt = make_attempt(
        binding,
        root,
        outcome="satisfied",
        artifacts=[{"kind": "closed_candidate", "commit": candidate, "tree": candidate_tree,
                    "base_commit": baseline, "changed_paths": ["candidate.py"]}],
    )
    append_attempt(root, attempt)
    expected = {**binding, "job_id": None, "attempt_count": None}

    assert classify(root, expected)["state"] == "CLOSED_CANDIDATE"
    (root / "candidate.py").write_text("tampered = 2\n")
    assert classify(root, expected)["state"] == "UNSAFE_DIRTY"
    subprocess.run(["git", "restore", "candidate.py"], cwd=root, check=True)
    (root / "unrelated.py").write_text("unrelated = True\n")
    subprocess.run(["git", "add", "unrelated.py"], cwd=root, check=True)
    subprocess.run(
        ["git", "-c", "user.name=T", "-c", "user.email=t@t", "commit", "-qm", "unrelated"],
        cwd=root,
        check=True,
    )
    assert classify(root, expected)["state"] == "STALE_RECEIPT"


@pytest.mark.parametrize(
    "mutation",
    [
        lambda artifact: artifact.pop("base_commit"),
        lambda artifact: artifact.update(base_commit="f" * 40),
        lambda artifact: artifact.pop("changed_paths"),
        lambda artifact: artifact.update(changed_paths=[]),
        lambda artifact: artifact.update(changed_paths=["candidate.py", "candidate.py"]),
    ],
    ids=["missing-base", "wrong-base", "missing-paths", "wrong-paths", "noncanonical-paths"],
)
def test_closed_candidate_rejects_inexact_implementation_evidence(
    tmp_path: Path, mutation
) -> None:
    root, baseline, tree = _repo(tmp_path)
    (root / "candidate.py").write_text("candidate = 1\n")
    subprocess.run(["git", "add", "candidate.py"], cwd=root, check=True)
    subprocess.run(
        ["git", "-c", "user.name=T", "-c", "user.email=t@t", "commit", "-qm", "candidate"],
        cwd=root,
        check=True,
    )
    candidate = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    candidate_tree = subprocess.check_output(["git", "rev-parse", "HEAD^{tree}"], cwd=root, text=True).strip()
    artifact = {"kind": "closed_candidate", "commit": candidate, "tree": candidate_tree,
                "base_commit": baseline, "changed_paths": ["candidate.py"]}
    mutation(artifact)
    binding = _binding(root, baseline, tree)
    append_attempt(root, make_attempt(binding, root, outcome="satisfied", artifacts=[artifact]))
    assert classify(root, {**binding, "job_id": None, "attempt_count": None})["state"] == "STALE_RECEIPT"


def test_worker_recovers_exact_closed_candidate_without_launcher(tmp_path: Path, monkeypatch) -> None:
    from tgw.workers.coding import CodingWorker

    root, baseline, tree = _repo(tmp_path)
    (root / "candidate.py").write_text("candidate = True\n")
    subprocess.run(["git", "add", "candidate.py"], cwd=root, check=True)
    subprocess.run(
        ["git", "-c", "user.name=T", "-c", "user.email=t@t", "commit", "-qm", "candidate"],
        cwd=root,
        check=True,
    )
    candidate = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    candidate_tree = subprocess.check_output(["git", "rev-parse", "HEAD^{tree}"], cwd=root, text=True).strip()
    binding = _binding(root, baseline, tree)
    append_attempt(
        root,
        make_attempt(
            binding,
            root,
            outcome="satisfied",
            artifacts=[{"kind": "closed_candidate", "commit": candidate, "tree": candidate_tree,
                        "base_commit": baseline, "changed_paths": ["candidate.py"]}],
        ),
    )
    plan_binding = {
        "plan_commit": binding["plan_commit"],
        "solution_hash": binding["solution_hash"],
        "source_commit": baseline,
        "worktree": str(root),
        "worktree_identity": {},
    }
    launched = False

    def launcher(*_args):
        nonlocal launched
        launched = True
        raise AssertionError("closed candidate reran launcher")

    worker = CodingWorker(
        "codex-implement",
        {"coding": {"worktree_root": str(root.parent), "repository_root": str(root)}},
        launcher=launcher,
    )
    monkeypatch.setattr(worker, "_validated_plan_binding", lambda *_args: plan_binding)
    monkeypatch.setattr(
        "tgw.queue.worker_base.state_machine.get_job",
        lambda _job_id: {"state": "running"},
    )
    receipt = worker.handle(
        {
            "job_id": "stale-retry",
            "attempt_count": 2,
            "payload_json": {
                "treatment_id": "codex-implement",
                "treatment_version": "1",
                "todo_id": 1752,
                "todo_agent": "codex",
                "graph_id": "graph",
                "object_generation": "generation",
                "worktree": str(root),
                "object_id": str(root),
                "plan_binding": plan_binding,
            },
        }
    )

    assert not launched
    assert receipt["outcome"] == "satisfied"
    assert len(history(root)) == 1


def test_worker_holds_lease_through_attempt_append(tmp_path: Path, monkeypatch) -> None:
    from tgw.workers.coding import CodingWorker

    root, head, _tree = _repo(tmp_path)
    plan_binding = {
        "plan_commit": "a" * 40,
        "solution_hash": "sha256:" + "b" * 64,
        "source_commit": head,
        "worktree": str(root),
        "worktree_identity": {},
    }
    script = tmp_path / "cooperating-writer.py"
    script.write_text(
        "import pathlib,sys\n"
        "from tgw.development.worktree_lease import exclusive_worktree_lease\n"
        "try:\n"
        "  with exclusive_worktree_lease(pathlib.Path(sys.argv[1])):\n"
        "    (pathlib.Path(sys.argv[1])/'concurrent.py').write_text('bad\\n')\n"
        "except Exception:\n"
        "  raise SystemExit(17)\n"
    )

    def partial(_treatment, _payload, _worktree):
        environment = {**os.environ, "PYTHONPATH": str(Path(__file__).parents[1] / "src")}
        blocked = subprocess.run(
            [__import__("sys").executable, str(script), str(root)],
            env=environment,
            check=False,
        )
        assert blocked.returncode == 17
        assert not (root / "concurrent.py").exists()
        (root / "partial.py").write_text("partial = True\n")
        return {"outcome": "partial", "established_conditions": [], "artifacts": []}

    worker = CodingWorker(
        "codex-implement",
        {"coding": {"worktree_root": str(root.parent), "repository_root": str(root)}},
        launcher=partial,
    )
    monkeypatch.setattr(worker, "_validated_plan_binding", lambda *_args: plan_binding)
    with pytest.raises(Exception, match="reported partial"):
        worker.handle(
            {
                "job_id": "job-1",
                "attempt_count": 1,
                "payload_json": {
                    "treatment_id": "codex-implement",
                    "treatment_version": "1",
                    "todo_id": 1752,
                    "todo_agent": "codex",
                    "graph_id": "graph",
                    "object_generation": "generation",
                    "worktree": str(root),
                    "object_id": str(root),
                    "plan_binding": plan_binding,
                },
            }
        )
    assert history(root)[0]["fingerprint"] == source_fingerprint(root)["fingerprint"]


def test_worker_rejects_satisfied_result_without_exact_closed_candidate(
    tmp_path: Path, monkeypatch
) -> None:
    from tgw.workers.coding import CodingWorker

    root, head, _tree = _repo(tmp_path)
    plan_binding = {
        "plan_commit": "a" * 40,
        "solution_hash": "sha256:" + "b" * 64,
        "source_commit": head,
        "worktree": str(root),
        "worktree_identity": {},
    }
    worker = CodingWorker(
        "codex-implement",
        {"coding": {"worktree_root": str(root.parent), "repository_root": str(root)}},
        launcher=lambda *_args: {
            "outcome": "satisfied",
            "established_conditions": ["implemented"],
            "artifacts": [],
        },
    )
    monkeypatch.setattr(worker, "_validated_plan_binding", lambda *_args: plan_binding)

    with pytest.raises(Exception, match="not the exact closed source descendant"):
        worker.handle(
            {
                "job_id": "job-1",
                "attempt_count": 1,
                "payload_json": {
                    "treatment_id": "codex-implement",
                    "treatment_version": "1",
                    "todo_id": 1752,
                    "todo_agent": "codex",
                    "graph_id": "graph",
                    "object_generation": "generation",
                    "worktree": str(root),
                    "object_id": str(root),
                    "plan_binding": plan_binding,
                },
            }
        )
    assert [item["outcome"] for item in history(root)] == ["failed"]


def test_configured_subprocess_runner_and_worker_share_one_lease(tmp_path: Path, monkeypatch) -> None:
    from tgw.workers.coding import CodingWorker

    root, head, _tree = _repo(tmp_path)
    runner = tmp_path / "lease-aware-runner.py"
    runner.write_text(
        "import fcntl,json,os,pathlib\n"
        "fd=int(os.environ['TGW_CODING_WORKTREE_LEASE_FD'])\n"
        "os.fstat(fd)\n"
        "fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)\n"
        "(pathlib.Path.cwd()/'partial.py').write_text('from_child = True\\n')\n"
        "print(json.dumps({'outcome':'partial','established_conditions':[],'artifacts':[{'kind':'child'}]}))\n"
    )
    executable = __import__("sys").executable
    diagnostic_root = tmp_path / "runner-control"
    diagnostic_root.mkdir()
    diagnostic_root.chmod(0o2770)
    os.chown(diagnostic_root, -1, __import__("grp").getgrnam("tgw-coders").gr_gid)
    plan_binding = {
        "plan_commit": "a" * 40,
        "solution_hash": "sha256:" + "b" * 64,
        "source_commit": head,
        "worktree": str(root),
        "worktree_identity": {},
    }
    worker = CodingWorker(
        "codex-implement",
        {
            "coding": {
                "worktree_root": str(root.parent),
                "repository_root": str(root),
                "commands": {"codex-implement": [executable, str(runner)]},
                "allowed_runners": [executable],
                "timeout_s": 30,
                "runner_state_root": str(diagnostic_root),
            }
        },
    )
    monkeypatch.setattr(worker, "_validated_plan_binding", lambda *_args: plan_binding)
    monkeypatch.setattr(
        "tgw.queue.worker_base.state_machine.get_job",
        lambda _job_id: {"state": "running"},
    )

    with pytest.raises(Exception, match="reported partial"):
        worker.handle(
            {
                "job_id": "job-1",
                "attempt_count": 1,
                "payload_json": {
                    "treatment_id": "codex-implement",
                    "treatment_version": "1",
                    "todo_id": 1752,
                    "todo_agent": "codex",
                    "graph_id": "graph",
                    "object_generation": "generation",
                    "worktree": str(root),
                    "object_id": str(root),
                    "plan_binding": plan_binding,
                },
            }
        )

    recorded = history(root)
    assert [item["outcome"] for item in recorded] == ["partial"]
    assert recorded[0]["fingerprint"] == source_fingerprint(root)["fingerprint"]


def test_owner_resume_queues_exactly_one_resume_identity(tmp_path: Path, monkeypatch) -> None:
    from tgw.development.foreman import ForemanConfig, TodoRecord, tick
    from tgw.development.plan_binding import execution_root_hash
    from tgw.workflow_kernel.contracts import RuntimeWorkGraph, TreatmentDisposition

    root, head, tree = _repo(tmp_path)
    (root / "partial.py").write_text("partial = True\n")
    attempt_binding = _binding(root, head, tree)
    attempt = make_attempt(attempt_binding, root, outcome="partial")
    append_attempt(root, attempt)
    plan_commit = attempt_binding["plan_commit"]
    execution_root = {
        "schema": "tgw-execution-root/v1",
        "kind": "todo",
        "todo_id": 1752,
    }
    execution_root["identity_hash"] = execution_root_hash(execution_root)
    plan_binding = {
        "schema": "tgw-plan-coding-todo/v1",
        "plan_commit": plan_commit,
        "solution_hash": attempt_binding["solution_hash"],
        "closure_hash": "sha256:" + "c" * 64,
        "capability": "workflow.condition-derived-convergence@1",
        "treatment_id": "establish:workflow.condition-derived-convergence@1",
        "source_commit": head,
        "idempotency_key": "sha256:" + "d" * 64,
        "worktree": str(root),
        "worktree_identity": {"worktree": str(root)},
        "execution_root": execution_root,
    }
    todo = TodoRecord(1752, "codex", 1, "continue exact partial", str(root), plan_binding)
    snapshot = type("Snapshot", (), {"generation": "generation"})()
    disposition = TreatmentDisposition("codex-implement", "1", ("implemented=false",))
    graph = RuntimeWorkGraph(
        schema_version="runtime-work-graph/v1",
        graph_id="graph",
        object_id=str(root),
        object_generation="generation",
        goal_profile_id="coding.ready_for_implementation",
        goal_profile_version="1",
        evaluator_version="foreman/v1",
        evidence_set_hash="evidence",
        condition_hash="condition",
        treatment_registry_hash="registry",
        fingerprints=(),
        satisfied_requirements=(),
        unmet_requirements=(),
        explicit_requirements=(),
        eligible_treatments=(disposition,),
        waiting_treatments=(),
        ownership_conflicts=(),
        reconciliation_gates=(),
        next_event_classes=(),
    )
    monkeypatch.setattr("tgw.development.foreman.build_coding_snapshot", lambda *_args, **_kwargs: snapshot)
    monkeypatch.setattr("tgw.development.foreman.evaluate", lambda **_kwargs: graph)
    queued = []

    def enqueue(**kwargs):
        queued.append(kwargs)
        return "resume-job"

    config = ForemanConfig(
        coding_config={
            "worktree_root": str(root.parent),
            "repository_root": str(root),
        },
        resume_bindings={
            1752: {
                "resume_of": attempt["attempt_hash"],
                "resume_fingerprint": attempt["fingerprint"],
            }
        },
    )
    first = tick(
        config,
        todo_ids={todo.todo_id},
        fetch_todos=lambda: [todo],
        check_active_fn=lambda _key: False,
        check_worktree_active_fn=lambda _path: False,
        check_terminal_fn=lambda key: any(row["dedupe_key"] == key for row in queued),
        enqueue_fn=enqueue,
    )
    second = tick(
        config,
        todo_ids={todo.todo_id},
        fetch_todos=lambda: [todo],
        check_active_fn=lambda _key: False,
        check_worktree_active_fn=lambda _path: False,
        check_terminal_fn=lambda key: any(row["dedupe_key"] == key for row in queued),
        enqueue_fn=enqueue,
    )

    assert first.dispatched == 1
    assert second.dispatched == 0 and second.skipped_terminal == 1
    assert len(queued) == 1
    assert ":resume:" in queued[0]["dedupe_key"]
    assert queued[0]["payload"]["resume_of"] == attempt["attempt_hash"]
