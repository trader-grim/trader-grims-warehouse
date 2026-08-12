from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_flake_exports_dedicated_closed_review_unit_derivation():
    flake = (ROOT / "flake.nix").read_text()
    assert "packages.${system}.review-egress-systemd-units" in flake
    assert "checks.${system}.review-egress-systemd-units" in flake
    assert "./nix/review-egress.nix" in flake
    assert 'credentialPath = "/run/credentials/tgw-review-auth.json"' in flake
    assert "nixosConfigurations.tgw-prod" not in flake
    assert "system.build.toplevel" not in flake
    assert "activation = false" in flake
    assert "inputIdentities.nixpkgs" in flake
    assert "nixpkgs.outPath" in flake


def test_derivation_emits_only_the_exact_review_unit_contract():
    flake = (ROOT / "flake.nix").read_text()
    expected = {
        "tgw-review-egress@.service",
        "tgw-review-egress-attest@.service",
        "tgw-review-egress-namespace@.service",
    }
    declared = {line.strip().strip('"') for line in flake.splitlines() if line.strip().startswith('"tgw-review-egress') and line.strip().endswith('.service"')}
    assert declared == expected
    assert "$out/units/${unit.name}" in flake
    assert "$out/verifier-metadata.json" in flake


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
