# =============================================================================
# CatioNIX users — human accounts and security policy
#
# This is the CatioNIX OS layer: declares the operator account (db) and root.
# It knows nothing about TGW.  The TGW service account (tgw, uid/gid 900) is
# declared in nix/tgw/users.nix — that is the boundary.
#
# db is the primary operator: uid 1000, wheel + networkmanager.
# =============================================================================
{ ... }:
{
  users.users.db = {
    isNormalUser = true;
    uid          = 1000;
    extraGroups  = [ "wheel" "networkmanager" ];
    initialPassword = "tgw";   # change on first login
  };

  users.users.root.initialPassword = "tgw";   # change on first login

  security.sudo.wheelNeedsPassword = false;
}
