from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "scripts" / "tgw_actor_startup.py"
ACTOR_RUNTIME = Path("/opt/TGW/.venvs/controller/bin/python3")


def test_actor_launcher_uses_the_installed_tgw_runtime():
    """The actor accounts' ambient Python lacks the signing dependencies."""
    first_line = LAUNCHER.read_text(encoding="utf-8").splitlines()[0]

    assert first_line == f"#!{ACTOR_RUNTIME}"
    assert ACTOR_RUNTIME.is_absolute()

