from pathlib import Path


def test_nix_module_has_closed_socket_service_and_rollback():
    source = Path("nix/nix-input-observer-launcher.nix").read_text()
    assert "security.sudo" not in source and "NOPASSWD" not in source
    assert "systemd.sockets.tgw-nix-input-observer" in source
    assert 'SocketMode = "0600"' in source and "MaxConnections = 1" in source
    assert 'StandardInput = "socket"' in source and 'StandardOutput = "socket"' in source
    assert 'environment.etc."tgw/nix-input-observer-launcher.conf"' in source
    assert 'mode = "0400"; user = "root"; group = "root"' in source
    assert 'systemd.services."tgw-nix-input-observer@"' in source
    assert 'Slice = "tgw-nix-input-observer.slice"' in source
    assert "mkIf cfg.enable" in source


def test_launcher_descriptor_has_no_command_or_environment_override():
    source = Path("nix/nix-input-observer-launcher.nix").read_text()
    block = source[source.index("descriptor =") : source.index("in {")]
    for forbidden in ("argv", "environment", "helper", "archive", "command ="):
        assert forbidden not in block


def test_native_launcher_is_the_only_privileged_implementation():
    source = Path("src/native/tgw_nix_input_observer_launcher.c").read_text()
    for required in (
        "argc != 1",
        "O_NOFOLLOW",
        "CLONE_NEWNET",
        "setgroups(0,NULL)",
        "setresgid",
        "setresuid",
        "SYS_capset",
        "PR_CAPBSET_DROP",
        "PR_CAP_AMBIENT_CLEAR_ALL",
        "PR_SET_NO_NEW_PRIVS",
        'fopen("/proc/self/status"',
        '"CapBnd:\\t0000000000000000"',
        "verify_prepared_request(&cfg)",
    ):
        assert required in source
    assert "system(" not in source and "popen(" not in source
    assert 'args[]={python,"-I",observer,NULL}' in source
