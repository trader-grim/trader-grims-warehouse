# DONE — nix-syncthing GUI-wipe root cause + fix (2026-07-04 evening)

Dave discovered his GUI-added plan-vault share vanished after flake rebuilds
and that "Syncthing cannot be configured via the web interface."

Root cause: NixOS syncthing module defaults overrideDevices/overrideFolders
= TRUE — any declared services.syncthing.settings.* makes syncthing-init
strip ALL GUI-added devices/folders on EVERY nixos-rebuild. Today's three
a1131 rebuilds (NFS/claude-account work) wiped a1131's entire device
registry: every peer "rejected: unknown device", vault sync dead since
~16:26, Dave's share deleted. GUI edits were accepted then silently reverted
at next rebuild — the GUI appeared broken.

Fix (flake commit in ~/tgw-flake, live on BOTH hosts, gen 78 on tgw-prod):
os/base.nix sets overrideDevices=false, overrideFolders=false — declared
settings still apply; GUI-managed peers/shares now survive rebuilds.

Restored: both wiped device IDs re-added on a1131 via syncthing CLI
(tgw-prod-vault CMHLXE2…, a9 CHBTIXP…); connection re-established
(live-verified in journal 18:10:48); tgw-prod re-offered tgw-project-plan;
pending accepts on a1131 awaiting Dave (vault path + a9's personal shares).
Also rode along: wakeonlan now a permanent tgw-prod package.

Standing note for PP-KNOWLEDGE-001 A0: one more datapoint for retiring
Syncthing — a sync layer whose peer registry can be silently destroyed by an
unrelated OS rebuild is exactly the class of coupling the annex/git design
eliminates.
