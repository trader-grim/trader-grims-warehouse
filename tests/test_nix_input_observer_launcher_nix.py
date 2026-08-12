from pathlib import Path


def test_nix_module_has_one_exact_no_argument_sudo_rule_and_rollback():
    source = Path("nix/nix-input-observer-launcher.nix").read_text()
    assert 'sudoRule = "codex ALL=(root) NOPASSWD: ${command}"' in source
    assert "security.sudo.extraConfig = sudoRule" in source
    assert 'environment.etc."tgw/nix-input-observer-launcher.json"' in source
    assert 'mode = "0400"; user = "root"; group = "root"' in source
    assert "mkIf cfg.enable" in source
    assert "systemd.services" not in source
    assert "NOPASSWD: ALL" not in source


def test_launcher_descriptor_has_no_command_or_environment_override():
    source = Path("nix/nix-input-observer-launcher.nix").read_text()
    block = source[source.index("descriptor =") : source.index("in {")]
    for forbidden in ("argv", "environment", "helper", "archive", "command ="):
        assert forbidden not in block
