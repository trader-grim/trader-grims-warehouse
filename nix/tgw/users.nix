# =============================================================================
# TGW users — service account
#
# THIS IS THE ONLY FILE THAT MAY DECLARE THE TGW SERVICE ACCOUNT.
# Human accounts live in nix/os/users.nix.
#
# uid/gid 900: system uid below 1000, verified free.  The live MX user is
# currently uid 1001; the usermod/chown migration to 900 is operator-gated
# as step 0.6 in PLAN-nixos-migration.md.  Setting it here ensures the flake
# and the live system converge on the same value at cutover.
# =============================================================================
{ ... }:
{
  # Tell the tgw NixOS module (nix/tgw.nix) what uid to expect.
  # Must match the declaration below.
  services.tgw.uid = 900;

  users.users.tgw = {
    isSystemUser = true;
    uid          = 900;
    group        = "tgw";
    home         = "/opt/TGW";
    createHome   = false;   # systemd.tmpfiles.rules (in tgw.nix) owns the tree
    description  = "Trader Grim's Warehouse service account";
    extraGroups  = [ "video" ];   # camera access for intake workflows
  };

  users.groups.tgw = {
    gid = 900;
  };
}
