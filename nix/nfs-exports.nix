# NFS exports — production host only
# Import in the production host config: ./nix/nfs-exports.nix
{ ... }:
{
  services.nfs.server.exports = ''
    /opt/TGW/ItemCreation/Queue  192.168.60.0/24(rw,sync,no_subtree_check,all_squash,anonuid=1001,anongid=1001)
  '';
}
