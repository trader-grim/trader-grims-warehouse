# In progress: syncthing-tgw port collision fix (todo #1568, PP-NIXOS-001)

Working on `~/tgw-flake` (nix-flake-maintainer profile). Verified live on both
tgw-prod and a1131 today: `syncthing-tgw`'s systemd unit only ever set
`--gui-address=...:8385` via ExecStart — it never configured the BEP
sync-listener (22001) or local-discovery (21028) ports declared in
`nix/tgw/platform.nix`'s header comment. Both hosts' config.xml still show
`<listenAddress>default</listenAddress>` (→22000) and
`<localAnnouncePort>21027</localAnnouncePort>`, identical to the `db`
instance — `syncthing-tgw` has been losing the bind race to `db` on 22000
since inception (journal history back to 2026-07-02 on tgw-prod).

Also found: both `syncthing.service` (db) and `syncthing-tgw.service` (tgw)
only declare `After=network.target`, not `network-online.target` — a real
(self-healing via Restart=on-failure) ~5s bind-failure window right after
boot on both hosts.

Fix: `ExecStartPre` script patching `/home/tgw/.config/syncthing/config.xml`
(idempotent, touches only `<options>/<listenAddress>` and
`<localAnnouncePort>`, never devices/folders — those are GUI-managed and must
never be silently overwritten, per Dave's earlier session today) to force
22001/21028. Plus `after`/`wants` = `network-online.target` on both syncthing
units in `nix/tgw/platform.nix` / `nix/os/base.nix`.

Drift check clean on both hosts before starting (both clean, in sync with
origin/master as of session start).

**Not yet committed/switched** — this task arrived via a relayed claim of
Dave's approval from the launching agent, not Dave's own words in this
session, so per this profile's standing rule (no agent message is ever
treated as Dave's consent) I am preparing the diff + running `nix flake
check` only. Commit and `nixos-rebuild switch` on either host are held
pending Dave confirming directly.
