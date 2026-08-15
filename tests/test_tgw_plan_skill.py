import importlib.util
import json
import subprocess
import sys
from pathlib import Path


def load_script(skill: str, script: str):
    root = Path(__file__).parents[1]
    path = root / "agent-services" / "skills" / skill / "scripts" / script
    spec = importlib.util.spec_from_file_location(f"skill_{skill}_{path.stem}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_adapter_digest_is_content_bound(tmp_path: Path):
    module = load_script("tgw-plan", "check_adapters.py")
    canonical = tmp_path / "canonical"
    adapter = tmp_path / "adapter"
    for root in (canonical, adapter):
        (root / "references").mkdir(parents=True)
        (root / "SKILL.md").write_text("skill\n")
        (root / "references" / "plan-v2.md").write_text("reference\n")

    assert module.digest(canonical) == module.digest(adapter)
    (adapter / "SKILL.md").write_text("different\n")
    assert module.digest(canonical) != module.digest(adapter)


def test_plan_binding_separates_approved_ref_from_evidence_head(tmp_path: Path):
    root = tmp_path / "plans"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "fixture@example.invalid"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Fixture"], cwd=root, check=True)
    for relative in (
        "plan/SPEC-plan-capability-graph-v2.md",
        "plan/PLAN-governed-execution-platform-build.md",
    ):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("approved\n")
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "approved"], cwd=root, check=True)
    approved = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, check=True, text=True,
        stdout=subprocess.PIPE,
    ).stdout.strip()
    subprocess.run(["git", "update-ref", "refs/tgw/approved/test", approved], cwd=root, check=True)
    (root / "receipt").write_text("later evidence\n")
    subprocess.run(["git", "add", "receipt"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "evidence"], cwd=root, check=True)

    script = Path(__file__).parents[1] / "agent-services/skills/tgw-plan/scripts/verify_plan_root.py"
    result = subprocess.run(
        [sys.executable, str(script), str(root), "refs/tgw/approved/test"],
        check=True, text=True, stdout=subprocess.PIPE,
    )
    binding = json.loads(result.stdout)
    assert binding["approved_commit"] == approved
    assert binding["head_commit"] != approved
    assert binding["clean"] is True
