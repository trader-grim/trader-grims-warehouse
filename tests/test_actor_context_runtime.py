import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tgw import context_source_guard
from tgw.context_source_guard import ContextSourceGuardError

ROOT = Path(__file__).resolve().parents[1]


def _git(root: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _retained_source_fixture(durable_path: Path) -> tuple[Path, Path, str, str]:
    context_update = durable_path / "context-update"
    retained = context_update / "retained-sources"
    transactions = context_update / "transactions"
    source = retained / "candidate-generation"
    source.mkdir(parents=True)
    transactions.mkdir(mode=0o700)
    (source / "nested").mkdir()
    (source / "nested/context.py").write_text("BOUND = True\n")
    _git(source, "init", "-q")
    _git(source, "add", ".")
    _git(
        source,
        "-c",
        "user.name=Context Guard Test",
        "-c",
        "user.email=context-guard@example.invalid",
        "commit",
        "-qm",
        "retained source",
    )
    commit = _git(source, "rev-parse", "HEAD^{commit}")
    tree = _git(source, "rev-parse", "HEAD^{tree}")
    durable_path.chmod(0o755)
    context_update.chmod(0o755)
    retained.chmod(0o755)
    for path in source.rglob("*"):
        path.chmod(0o555 if path.is_dir() else 0o444)
    source.chmod(0o555)
    return retained, transactions, commit, tree


def _simulate_root_owned_retained_tree(
    monkeypatch: pytest.MonkeyPatch,
    retained: Path,
    *,
    stat_overrides: dict[Path, tuple[int, int]] | None = None,
) -> None:
    real_stat = Path.stat
    protected_state = retained.parents[1]
    stat_overrides = stat_overrides or {}

    def root_owned_stat(path, *args, **kwargs):
        observed = real_stat(path, *args, **kwargs)
        candidate = Path(path)
        if (
            candidate == protected_state
            or protected_state in candidate.parents
            or candidate in protected_state.parents
        ):
            values = list(observed)
            values[4] = 0
            if candidate in protected_state.parents:
                values[0] = (values[0] & ~0o7777) | 0o755
            if candidate in stat_overrides:
                mode, uid = stat_overrides[candidate]
                values[0] = (values[0] & ~0o7777) | mode
                values[4] = uid
            return os.stat_result(values)
        return observed

    monkeypatch.setattr(context_source_guard, "_RETAINED_SOURCE_ROOT", retained)
    monkeypatch.setattr(
        context_source_guard,
        "_PROTECTED_STATE_ROOT",
        protected_state,
    )
    monkeypatch.setattr(Path, "stat", root_owned_stat)


def _make_retained_tree_writable(source: Path) -> None:
    for path in source.rglob("*"):
        if path.is_dir():
            path.chmod(0o755)
        else:
            path.chmod(0o644)
    source.chmod(0o755)


def test_context_source_guard_accepts_only_actor_readable_retained_source(
    durable_path,
    monkeypatch,
):
    retained, transactions, commit, tree = _retained_source_fixture(durable_path)
    source = retained / "candidate-generation"
    _simulate_root_owned_retained_tree(monkeypatch, retained)
    try:
        assert context_source_guard.validate_context_source(
            source,
            Path("/usr/bin/git"),
            expected_commit=commit,
            expected_tree=tree,
        ) == (source, commit, tree)
        with pytest.raises(
            ContextSourceGuardError,
            match="canonical retained-source root",
        ):
            context_source_guard.validate_context_source(
                transactions,
                Path("/usr/bin/git"),
            )
    finally:
        _make_retained_tree_writable(source)


@pytest.mark.parametrize(
    ("relative_path", "mode"),
    (("nested", 0o500), ("nested/context.py", 0o400)),
)
def test_context_source_guard_rejects_retained_source_not_actor_readable(
    durable_path,
    monkeypatch,
    relative_path,
    mode,
):
    retained, _transactions, commit, tree = _retained_source_fixture(durable_path)
    source = retained / "candidate-generation"
    target = source / relative_path
    target.chmod(mode)
    _simulate_root_owned_retained_tree(monkeypatch, retained)
    try:
        with pytest.raises(
            ContextSourceGuardError,
            match="not actor-readable material",
        ):
            context_source_guard.validate_context_source(
                source,
                Path("/usr/bin/git"),
                expected_commit=commit,
                expected_tree=tree,
            )
    finally:
        _make_retained_tree_writable(source)


@pytest.mark.parametrize(
    ("parent_name", "drift"),
    (
        ("context-update", "writable"),
        ("context-update", "foreign-owner"),
        ("protected-state", "writable"),
        ("protected-state", "foreign-owner"),
        ("parent-above-protected-state", "writable"),
        ("parent-above-protected-state", "foreign-owner"),
    ),
)
def test_context_source_guard_rejects_unprotected_fixed_parent_ancestry(
    durable_path,
    monkeypatch,
    parent_name,
    drift,
):
    retained, _transactions, commit, tree = _retained_source_fixture(durable_path)
    source = retained / "candidate-generation"
    target = {
        "context-update": retained.parent,
        "protected-state": retained.parents[1],
        "parent-above-protected-state": retained.parents[2],
    }[parent_name]
    observed_mode = target.stat().st_mode & 0o7777
    override = (
        (observed_mode | 0o020, 0)
        if drift == "writable"
        else (observed_mode, os.getuid())
    )
    _simulate_root_owned_retained_tree(
        monkeypatch,
        retained,
        stat_overrides={target: override},
    )
    try:
        with pytest.raises(
            ContextSourceGuardError,
            match="not root-owned immutable material",
        ):
            context_source_guard.validate_context_source(
                source,
                Path("/usr/bin/git"),
                expected_commit=commit,
                expected_tree=tree,
            )
    finally:
        _make_retained_tree_writable(source)


def test_context_runtime_crosses_real_execve_into_exact_candidate(tmp_path):
    source_root = tmp_path / "candidate"
    candidate = source_root / "scripts" / "tgw_actor_startup.py"
    candidate.parent.mkdir(parents=True)
    candidate.write_text(
        "import json\n"
        "import os\n"
        "import sys\n"
        "from pathlib import Path\n"
        "payload = {\n"
        "    'pid': os.getpid(),\n"
        "    'argv': sys.argv,\n"
        "    'cmdline': [part.decode() for part in "
        "Path('/proc/self/cmdline').read_bytes().split(b'\\0') if part],\n"
        "    'environment': dict(os.environ),\n"
        "}\n"
        "print(json.dumps(payload, sort_keys=True), flush=True)\n",
        encoding="utf-8",
    )
    home = tmp_path / "home"
    stable_launcher = home / ".local/bin/tgw-actor"
    stable_launcher.parent.mkdir(parents=True)
    stable_launcher.symlink_to(candidate)
    cache_root = tmp_path / "cache"
    cache_root.mkdir()
    environment = {
        "HOME": str(home),
        "LANG": "C.UTF-8",
        "PATH": "/usr/bin:/bin",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
        "PYTHONSAFEPATH": "1",
        "PYTHONCOERCECLOCALE": "0",
        "TGW_CONTEXT_RUNTIME_ENTRYPOINT": str(candidate),
        "TGW_CONTEXT_STABLE_LAUNCHER": str(stable_launcher),
        "TGW_CONTEXT_SENTINEL": "candidate-only-environment",
        "TMPDIR": str(cache_root),
    }
    helper = (
        "import json, sys\n"
        "from pathlib import Path\n"
        "from tgw import actor_startup\n"
        "actor_startup._protected_stable_launcher = lambda _path: None\n"
        "actor_startup._exec_context_mcp_runtime(\n"
        "    home=Path(sys.argv[1]), actor='fixture',\n"
        "    source_root=Path(sys.argv[2]), environment=json.loads(sys.argv[3]),\n"
        ")\n"
    )
    helper_environment = dict(os.environ)
    helper_environment["PYTHONPATH"] = str(ROOT / "src")
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            helper,
            str(home),
            str(source_root),
            json.dumps(environment, sort_keys=True),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=helper_environment,
    )
    helper_pid = process.pid
    stdout, stderr = process.communicate(timeout=15)

    assert process.returncode == 0, stderr
    payload = json.loads(stdout)
    executable = sys.executable
    runtime_arguments = [
        "--context-mcp-runtime",
        "--context-mcp",
        "--context-mcp-stable-launcher",
        str(stable_launcher),
    ]
    assert payload["pid"] == helper_pid
    assert payload["cmdline"] == [
        executable,
        "-I",
        "-s",
        "-P",
        str(candidate),
        *runtime_arguments,
    ]
    assert payload["argv"] == [str(candidate), *runtime_arguments]
    assert payload["environment"] == environment
    assert "PYTHONPATH" not in payload["environment"]
