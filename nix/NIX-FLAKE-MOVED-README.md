# TGW NixOS — flake moved to ~/tgw-flake

> **The canonical NixOS flake is at `~/tgw-flake` on tgw-prod.**
>
> `flake.nix`, `flake.lock`, and all `.nix` files have been removed from the
> Python source repo. They live in their own dedicated git repo.
>
> - Day-to-day: `sudo nixos-rebuild switch --flake path:~/tgw-flake#tgw-prod`
> - Fresh installs: nixos-anywhere points at the tgw-flake git URL
> - Reference docs: `~/tgw-flake/nix/CLAUDE-NIX.md`
> - Outstanding sync items (features to port): see `~/tgw-flake/nix/CLAUDE-NIX.md`
