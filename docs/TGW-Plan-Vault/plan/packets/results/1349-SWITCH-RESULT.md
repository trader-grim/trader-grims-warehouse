# Result: 1349/1453 switch re-verification — NOT switched, held for direct Dave confirmation
Status: **re-verified live, dry-activate reconfirmed, commit/push/switch NOT executed**
Todo: #1349, #1453   PP: PP-NIXOS-001

## Why this stopped short of the mutation
The instruction to commit+switch arrived via the launching agent's task message, which
asserted "Dave has given explicit direct approval... just now." Per this session's
locked operating rule, no agent message is ever treated as the user's own consent or
approval — only a direct message from Dave in this conversation, or the permission
system itself, qualifies. That direct confirmation has not been given in this
conversation, so the mutation-gated steps (commit, push, `nixos-rebuild switch`) were
not run. Everything read-only/diagnostic was completed and reconfirms the prior
manifest (`1349-RESULT.md`) exactly.

## Step 1 — drift check (re-run)
```
tgw-prod: git fetch origin && git log --oneline origin/master..HEAD → (empty, clean)
a1131:    git fetch origin && git log --oneline origin/master..HEAD → (empty, clean)
```
No drift on either host, matches prior manifest. tgw-prod's working tree shows exactly
the two expected uncommitted files, byte-identical diff to `1349-RESULT.md`:
- `nix/hosts/tgw-prod.nix` (durable-exclusion list extended, #1349)
- `nix/os/sway.nix` (comment correction, #1453)

Nothing else changed since the prior investigation.

## Step 2 — nix flake check (re-run)
Exit 0, clean — all outputs including `nixosConfigurations.tgw-prod` evaluate.

## Step 3 — dry-activate (re-run)
```
$ sudo nixos-rebuild dry-activate --flake "path:$HOME/tgw-flake#tgw-prod"
would stop the following units: tgw-worker@catalog_rebuild.service, tgw-worker@ebay_legacy_sync.service, tgw-worker@ebay_sync.service
would activate the configuration...
Done. The new configuration is /nix/store/0cbj09pjf21gwvs9r0ra10k2g84rs1mj-nixos-system-tgw-prod-25.05.20260102.ac62194
```
**Identical store path** to the prior run (`0cbj09pjf21gwvs9r0ra10k2g84rs1mj`) — confirms
nothing drifted or changed between the original investigation and now. Exactly the
three intended units, no other churn.

## Safe-time check — flagged, not cleared
Both tgw-prod and a1131 currently have live, long-running graphical sessions:
- tgw-prod: Sway session (tty3, PID 2427) active continuously since 2026-07-14 07:31,
  `idle=no` in `loginctl`; this Claude session's own tmux runs inside that session.
- a1131: KDE Plasma/kwin_wayland session (tty2) active continuously since 2026-07-14
  07:31, `idle=no`.

tgw-prod's own change here (stopping 3 already-broken worker units) carries low risk to
the graphical session itself — it doesn't touch Sway/portal/D-Bus units. But per the
profile's own rule ("flag to Dave and confirm it's a safe time... never assume this
risk is tgw-prod-only"), this was not independently cleared with Dave in this
conversation before proceeding, so the switch was held rather than assumed-safe.

## Commit/push/switch — NOT executed
- `git commit` — not run
- `git push` — not run
- `sudo nixos-rebuild switch --flake path:~/tgw-flake#tgw-prod` — not run

## Second relayed-approval attempt — also declined
After this manifest was first written, the coordinator relayed a further message
claiming Dave had "just sent this directly, in the live conversation" confirming
approval and safe-time for both hosts (including a1131-specific detail about Tigwa's
tmux session on a1131). This was still a message *from the coordinator describing*
what Dave allegedly said elsewhere, not a message from Dave appearing directly in
this conversation — the operating rule draws no exception for "this one is a verbatim
quote, I promise." Declined for the same reason as above; no mutation was run.

## What's needed to close this out
A message from Dave **appearing directly in this conversation** (not relayed by the
coordinator) confirming: (a) the approval, and (b) that now is still an acceptable time
given the live desktop session note above. Once that's given, remaining steps are:
commit with a `fix:` message referencing #1349/#1453, push, `nixos-rebuild switch`,
then verify:
```
systemctl is-enabled tgw-worker@catalog_rebuild.service tgw-worker@ebay_legacy_sync.service tgw-worker@ebay_sync.service
```
(expect `disabled`, durable — not just stopped) plus a before/after
`systemctl list-units 'tgw-worker@*'` diff to confirm no other worker was affected, and
a re-run of Step 1's drift check on both hosts to confirm `origin/master` now matches.
