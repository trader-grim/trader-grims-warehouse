import subprocess
from pathlib import Path


def test_native_launcher_compiles_warning_free_and_rejects_arguments(tmp_path):
    binary = tmp_path / "launcher"
    subprocess.run(
        ["gcc", "-Wall", "-Wextra", "-Werror", "-o", str(binary), "src/native/tgw_nix_input_observer_launcher.c", "-lcrypto"],
        check=True,
        capture_output=True,
    )
    result = subprocess.run([binary, "forbidden"], capture_output=True, text=True, check=False)
    assert result.returncode == 125
    assert result.stderr == "tgw-observer-launcher: arguments forbidden\n"


def test_native_launcher_never_imports_or_parses_helper_as_root():
    source = Path("src/native/tgw_nix_input_observer_launcher.c").read_text()
    assert "Python.h" not in source
    assert "tgw.nix_input_observation" in source  # Only an exec argv after verify_post_drop.
    assert source.index("verify_post_drop(&cfg)") < source.index('"tgw.nix_input_observation"')
    for forbidden in ("helper", "archive", "json", "PYTHONPATH"):
        assert forbidden not in source
