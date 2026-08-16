import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def test_candidate_test_runner_precedes_an_ambient_editable_package(tmp_path):
    repo = tmp_path / "candidate-repository"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "Test")
    package = repo / "src" / "tgw"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("IDENTITY = 'base'\n")
    tests = repo / "tests"
    tests.mkdir()
    (tests / "test_identity.py").write_text(
        "from tgw import IDENTITY\n\n\ndef test_candidate_package_wins():\n    assert IDENTITY == 'candidate'\n"
    )
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "base")
    (package / "__init__.py").write_text("IDENTITY = 'candidate'\n")
    _git(repo, "commit", "-am", "candidate")

    ambient = tmp_path / "ambient"
    (ambient / "tgw").mkdir(parents=True)
    (ambient / "tgw" / "__init__.py").write_text("IDENTITY = 'ambient'\n")
    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "run_candidate_tests.py"),
            "--repo", str(repo),
            "--candidate", "HEAD",
            "--scope", "focused",
            "--",
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "tests/test_identity.py",
        ],
        text=True,
        capture_output=True,
        check=False,
        env={
            **os.environ,
            "PYTHONPATH": os.pathsep.join((str(ROOT / "src"), str(ambient))),
        },
    )

    assert completed.returncode == 0, completed.stderr
    receipt = json.loads(completed.stdout)
    assert receipt["status"] == "PASS"
    assert receipt["scope"] == "focused"
