{
  description = "TGW Python source development adapter";

  # The canonical NixOS configuration remains in trader-grim/tgw-flake.
  # This adapter exists only so a clean Python-source clone has an explicit,
  # reproducible development-shell entry point instead of an absolute symlink.
  inputs.tgw-flake.url = "git+ssh://git@github.com/trader-grim/tgw-flake.git?ref=todo/consolidated-nix-fleet-20260725";

  outputs = { self, tgw-flake }:
    {
      devShells = tgw-flake.devShells;
    };
}
