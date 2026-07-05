# IN PROGRESS — #1146 a1131 NFS shares + claude account (server half LIVE)

Dave approved scope + offered Claude an account ("make yourself at home").

DONE (live-verified on tgw-prod, flake commit 5c4ce72 in ~/tgw-flake):
- exportfs confirms: /opt/TGW/data + /opt/TGW/var/log exported READ-ONLY to
  a1131 (192.168.60.101), all_squash→tgw(900). RO is load-bearing: writes go
  through the tgw-api fence only. Secrets NOT exported.
- BONUS: rebuild also deployed the pending tgw-catalog-verify-nightly.timer
  (PP-PHOTOSYNC-001 P7 / todo #1126, now DONE) — next run 02:06 PDT. The unit
  definition (s43's uncommitted backup.nix edit) is now committed too.

PENDING (a1131 went unreachable ~15:10, "no route to host" — needs power-on):
- a1131 rebuild to apply: claude user (uid 1001, wheel, key=db@tgw-prod's
  ed25519) + ro automount at /opt/TGW/mnt/tgw-prod/{data,log} (soft, so an
  offline prod never hangs boot). Config committed + evals clean.
- Deploy path once it's up: rsync ~/tgw-flake to a1131, then
  `sudo nixos-rebuild switch --flake path:<dir>#a1131` there (a1131 has no
  GitHub access, #1082). Then verify: ssh claude@a1131, read a file off the
  data mount, run a pytest from the mount-backed checkout.

## COMPLETE 2026-07-04 evening — all live-verified

- claude@a1131 account live (uid 1001, key-only). Debug trail: NixOS locks
  passwordless users ("!") and sshd refuses locked accounts → hashedPassword
  "*"; wrong pubkey initially (id_*.pub glob lists the GITHUB key first, its
  comment misleadingly says trader-grim@trader-grims-warehouse); NFS sec=sys
  needs client-side tgw group membership (client enforces presented modes
  before server all_squash).
- NFS verified AS claude: ItemData JSON reads OK, logs OK, write refused
  "Read-only file system" (fence holds).
- Wake-on-LAN LIVE-FIRED: suspend → `wakeonlan c8:2a:14:2a:a1:85` → up in
  ~25s. MAC + wake command now in CLAUDE.md. STANDING RULE (Dave): never
  initiate suspend on a1131 (iMac12,1 bug) — Dave's power mgmt sleeps it,
  Claude only wakes it.
- Flake commits (~/tgw-flake): 5c4ce72 + 2 follow-ups. tgw-prod rebuild for
  the permanent wakeonlan package is pending (tool available meanwhile via
  `nix shell nixpkgs#wakeonlan`).
- OPEN QUESTION for Dave: sudo for claude on a1131 — harness requires his
  explicit authorization for a NOPASSWD rule (Option A) vs no sudo (Option B,
  current state, sufficient for checks).

**RESOLVED:** Dave authorized sudo (Option A) same evening — "you have ssh
access to a1131 from tgw, no reason to make that more difficult when you
have your own room." NOPASSWD rule deployed + verified (`sudo -n whoami` →
root as claude). Flake commit records the quote.
