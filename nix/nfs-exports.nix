# =============================================================================
# NFS server + exports — production host only
#
# Self-contained: enables the server, opens the firewall, and declares exports.
# Import only in bases/master.nix (production platform).
# nix/os/base.nix does NOT open port 2049 — that responsibility lives here.
# =============================================================================
{ ... }:
{
  services.nfs.server.enable = true;

  networking.firewall.allowedTCPPorts = [ 2049 ];
  networking.firewall.allowedUDPPorts = [ 2049 ];

  # Phone photo-drop queue — NFS share for the intake camera app.
  # anonuid/anongid 900 = tgw service account (step 0.6 of PLAN-nixos-migration.md)
  services.nfs.server.exports = ''
    /opt/TGW/ItemCreation/Queue  192.168.60.0/24(rw,sync,no_subtree_check,all_squash,anonuid=900,anongid=900)
  '';
}
