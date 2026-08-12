import importlib.util
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
