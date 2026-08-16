import hashlib
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
    runner = repo / "scripts" / "run_candidate_tests.py"
    runner.parent.mkdir()
    runner.write_bytes((ROOT / "scripts" / "run_candidate_tests.py").read_bytes())
    plan = repo / "agent-services" / "catalogs" / "governed-candidate-test-plan-v1.json"
    plan.parent.mkdir(parents=True)
    plan.write_text(json.dumps({
        "schema": "tgw-candidate-test-plan/v1",
        "plan_id": "candidate-package-isolation",
        "version": 1,
        "runner": {
            "path": "scripts/run_candidate_tests.py",
            "sha256": "sha256:" + hashlib.sha256(runner.read_bytes()).hexdigest(),
            "argv_prefix": ["-m", "pytest"],
        },
        "scopes": {
            "focused": {"argv": ["-q", "tests/test_identity.py"]},
            "full": {"argv": ["-q"]},
        },
    }, sort_keys=True))
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
            "--output-artifact", str(tmp_path / "focused-output.json"),
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
    output = json.loads((tmp_path / "focused-output.json").read_text())
    assert receipt["output_artifact_hash"] == output["artifact_hash"]
