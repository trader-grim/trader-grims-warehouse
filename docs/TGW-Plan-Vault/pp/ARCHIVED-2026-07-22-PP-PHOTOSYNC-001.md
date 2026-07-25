# PP-PHOTOSYNC-001 — upload integrity + operator lane hardening + fleet photo repair (full detail)

## PP-PHOTOSYNC-001 — upload integrity + operator lane hardening + fleet photo repair
**Opened s43 (2026-07-03) — THE ACTIVE FIX TRACK.** Born from the 3-day EPS quota
exhaustion incident: upload worker masks partial failure as success; s42's retry
policy left a 2,514-SKU immortal backlog (cancelled, Dave-authorized); the operator
quota reserve was unreachable (fixed s43 as invariant C10, live-verified on
tgw202606021133367 — 9→24 live photos). Runs PARALLEL with the forward track
(R1.8 #1122 / PP-BACKUP-001 / #1102) — collision rule in the design doc.
Packets P1–P9 = todos **#1115 #1117 #1118 #1119 #1120 #1121 #1123 #1124 #1125**;
P4 fleet repair (492 photo-short items) ramp **pre-authorized 1→5→ramp by Dave
2026-07-03**. P7–P9 added at Dave's direction same day: truth-audit rules ("test
the function and read the log" as a nightly job — PD4 automated), canary probe
(real buttons on one item, daily), and whole-site audit via the cheapest bulk
source reachable with EXISTING scopes (Feed API report candidate; scopes LOCKED).
**P1 ✅ DONE 2026-07-03** — live-verified against the still-halted real EPS wall;
P4/P8 now unblocked; full suite regression-clean (1444 pass, same 9 pre-existing
failures as baseline).
Full design + packet contracts: `pp/PP-PHOTOSYNC-001.md`.
s42+s43 committed+pushed (`ae9b1e6`); PR to main deferred until P1 verifies.

P10 complete (legacy duplicate check + eBay Motors awareness). P4 paused per P10 findings. See RESEARCH-photosync-p10-legacy-duplicate.md.

P6 investigated: ebay_repush orphan queue diagnosed (2 orphan jobs, no systemd unit). Needs Dave decision: install unit or retire enqueue path. Full detail in `plan/pp/PP-PHOTOSYNC-001.md`.

- #1124 P8 canary probe completed: `scripts/photosync_canary_probe.py` built and live-verified against `tgw201501021970068`. Fixed two bugs (auth header, live-state field shape). 4 new tests, full suite 1814 passed. Daily timer deferred to 2pm.

## Additional packet notes (moved from Active Tracks loose lines, 2026-07-18 hygiene pass)
P1 upload integrity fix complete — see document for spec, implementation, tests, and live verification.

P9 bulk audit: Inventory API getInventoryItems winner (~98 calls), Feed API blocked, GetMyeBaySelling narrower than assumed. Full ranking in dev-workflow/research/RESEARCH-photosync-bulk-audit.md. Follow-up #1127 filed.

P2 ops-digest pending-liability lines shipped (see DONE-photosync-p2-digest-liability.md in dev-workflow/research).

- P9 follow-up #127: `photos_short_on_ebay` re-pointed at live capture index; open question on recurring nightly capture flagged for 2pm triage.
