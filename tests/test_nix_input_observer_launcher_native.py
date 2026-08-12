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


def test_native_launcher_argument_path_is_sanitizer_clean(tmp_path):
    binary = tmp_path / "launcher-sanitized"
    payload = tmp_path / "payload"
    payload.write_bytes(b"abc")
    subprocess.run(
        ["gcc", "-DTGW_LAUNCHER_DIGEST_TEST", "-fsanitize=address,undefined", "-fno-omit-frame-pointer", "-g", "-o", str(binary), "src/native/tgw_nix_input_observer_launcher.c", "-lcrypto"],
        check=True,
        capture_output=True,
    )
    result = subprocess.run([binary, payload], capture_output=True, text=True, check=False)
    assert result.returncode == 0
    assert result.stdout == "sha256:ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad\n"
    assert "AddressSanitizer" not in result.stderr and "runtime error:" not in result.stderr


def test_native_launcher_never_imports_or_parses_helper_as_root():
    source = Path("src/native/tgw_nix_input_observer_launcher.c").read_text()
    assert "Python.h" not in source
    assert 'args[]={python,"-I",observer,NULL}' in source
    assert source.index("verify_post_drop(&cfg)") < source.index('args[]={python,"-I",observer,NULL}')
    for forbidden in ("helper", "archive", "json", "PYTHONPATH"):
        assert forbidden not in source
