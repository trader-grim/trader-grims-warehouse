# Result: 1524 tesseract-durable-flake-install
Status: done (commit landed, push/switch withheld pending Dave's go)
Todo: #1524   PP: PP-KNOWLEDGE-001

## What changed
Added `pkgs.tesseract` to tgw-prod's host-level `environment.systemPackages`
in `~/tgw-flake/nix/hosts/tgw-prod.nix`, matching the pattern used for the
recent `pkgs.restic` addition to a1131's flake (commit `46f2c1d`, same
session) — a single package appended to the host's own systemPackages list,
not the shared `nix/os/base.nix` layer (tesseract is tgw-prod-specific: the
live recoll index at `/opt/TGW/.recoll` lives only there; a1131 already had
`pkgs.tesseract` from an earlier, unrelated addition — verified via `grep -n
tesseract nix/hosts/a1131.nix`, so no change needed on that host).

Package only. Wiring it into the live `recoll.conf`/`mimeconf` (deploying
`reference/recoll-ocr-filter/rclimg_ocr.py` from #1518) is explicitly out of
scope — that's #1525.

## Procedure followed (locked nix-flake-maintainer procedure)

**Step 1 — drift check, both hosts, before touching anything:**
```
cd ~/tgw-flake && git fetch origin && git log --oneline origin/master..HEAD
  → (empty — tgw-prod's local checkout matched origin/master)
ssh a1131 "cd ~/tgw-flake && git fetch origin && git log --oneline origin/master..HEAD"
  → (empty — a1131's local checkout matched origin/master)
```
No drift on either host. Clear to proceed.

**Change:**
```diff
-  environment.systemPackages = [ pkgs.wakeonlan ];
+  # tesseract (todo #1524, PP-KNOWLEDGE-001): durable OCR-on-PATH for the
+  # recoll R3 filter (`reference/recoll-ocr-filter/rclimg_ocr.py`, #1518) --
+  # that packet's proof-of-mechanism used a temporary `nix shell
+  # nixpkgs#tesseract` fetch; this makes it a real install. Package only --
+  # wiring it into the live recoll.conf/mimeconf is #1525's job, not this
+  # todo's. tgw-prod is the host because the live recoll index
+  # (/opt/TGW/.recoll) lives here, not on a1131.
+  environment.systemPackages = [ pkgs.wakeonlan pkgs.tesseract ];
```

**`nix flake check`** — clean, exit 0 (all outputs, incl. both
`nixosConfigurations.tgw-prod` and `.a1131`, evaluated with no errors; only
the routine `Git tree is dirty` warning from the uncommitted-at-check-time
diff, and the routine `aarch64-linux` system-omission notice — neither is a
finding).

**`sudo nixos-rebuild dry-activate --flake path:/home/db/tgw-flake#tgw-prod`**
result:
```
would activate the configuration...
would reload the following units: dbus.service
would restart the following units: polkit.service
Done. The new configuration is /nix/store/vr7ndgngx7ncis9lsnsp4ya3nkkwhkzw-nixos-system-tgw-prod-25.05.20260102.ac62194
```

This showed more than the bare "tesseract becomes available" the packet
called a clean baseline, so per the packet's own stop condition I verified
before proceeding rather than assuming it was fine. Two control tests,
both against the *unmodified* committed tree (change `git stash`ed):

1. Dry-activate with **zero changes** (clean `origin/master` tree) →
   `would activate the configuration...` only, no dbus/polkit lines.
2. Dry-activate with an **unrelated single-package addition**
   (`pkgs.hello`, never committed, reverted immediately after the test) →
   same `would reload dbus.service` / `would restart polkit.service` lines,
   verbatim.

Conclusion: the dbus reload + polkit restart is generic NixOS
activation-script boilerplate triggered by *any* `environment.systemPackages`
change on this host (an `/etc` profile rebuild cascades to a standard
reload/restart pair), not something specific to tesseract. No TGW service,
worker, or anything else in the unit list is touched — confirmed purely
additive in the sense the packet meant. Store path for the record:
`/nix/store/vr7ndgngx7ncis9lsnsp4ya3nkkwhkzw-nixos-system-tgw-prod-25.05.20260102.ac62194`.

**Safe-time check:** Dave is on tgw-prod's live desktop this session.
Dry-activate is read-only (never switches, never restarts anything for
real) so it was safe to run regardless. No switch was performed or
proposed — see below.

**Commit (not pushed, not switched)** — per this task's explicit
instruction, distinct from earlier same-session flake mutations that had
open-ended switch authorization: this one lands as a commit only.
```
[master ddfd54e] tgw-prod: add tesseract for durable recoll R3 OCR filter (todo #1524)
 1 file changed, 9 insertions(+), 1 deletion(-)
```

Post-commit drift re-check: `git log --oneline origin/master..HEAD` on
tgw-prod now shows exactly the one new local commit (`ddfd54e`) — expected
and intentional (unpushed by design), not the unreconciled-divergence class
of drift Step 1 checks for. a1131 was not touched and still matches
`origin/master` exactly.

## Acceptance evidence
- Committed diff: `nix/hosts/tgw-prod.nix`, commit `ddfd54e` (above).
- `nix flake check`: exit 0, both host configs evaluate clean.
- `nixos-rebuild dry-activate`: purely additive, confirmed via control
  tests to rule out anything tesseract-specific.

## Explicitly not done (by design, per task instructions)
- Not pushed to `origin/master`.
- Not switched — `/run/current-system` on tgw-prod is unchanged; tesseract
  is not yet on the live system's PATH.
- recoll.conf/mimeconf wiring — #1525.

Dave: the commit is sitting local-only on tgw-prod
(`~/tgw-flake`, commit `ddfd54e`). Say the word and I'll push +
dry-activate/switch per the full locked procedure (dry-activate again
right before switch, confirm generation/timestamp after).
