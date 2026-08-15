from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_review_unit_source_stays_in_the_application_repository():
    flake = (ROOT / "flake.nix").read_text()
    assert "devShells = tgw-flake.devShells;" in flake
    assert "packages.${system}" not in flake
    assert (ROOT / "nix/review-egress.nix").is_file()
    assert "nixosConfigurations.tgw-prod" not in flake
    assert "system.build.toplevel" not in flake


def test_module_declares_only_the_exact_review_service_contract():
    module = (ROOT / "nix/review-egress.nix").read_text()
    for unit in (
        'systemd.services."tgw-review-egress@"',
        'systemd.services."tgw-review-egress-attest@"',
        'systemd.services."tgw-review-egress-namespace@"',
    ):
        assert module.count(unit) == 1


def test_review_module_retains_network_credential_and_lifecycle_semantics():
    module = (ROOT / "nix/review-egress.nix").read_text()
    assert 'NetworkNamespacePath = "/run/netns/tgw-review-%i"' in module
    assert 'LoadCredential = "attestation.pub:' in module
    assert 'LoadCredential = "attestation.key:' in module
    assert 'requires = [ "tgw-review-egress-namespace@%i.service" ]' in module
    assert 'after = [ "tgw-review-egress-namespace@%i.service" ]' in module
    assert 'partOf = [ "tgw-review-egress@%i.service" ]' in module
    assert '!(lib.hasPrefix "/nix/store/" cfg.credentialPath)' in module


def test_provider_targets_derivation_and_preflights_offline_inputs():
    provider = (ROOT / "src/tgw/nixos_reviewed_evaluation.py").read_text()
    assert 'target = ".#packages.x86_64-linux.review-egress-systemd-units"' in provider
    assert 'Path(closure) / "units" / unit' in provider
    assert '"input_closure_manifest_json"' in provider
    assert "offline input closure NAR identity mismatch" in provider
    assert "nixosConfigurations.tgw-prod" not in provider
