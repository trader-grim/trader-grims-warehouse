# Result: 1529 runbook-gaps-triage

Status: done
Todo: #1529   PP: PP-RUNBOOK-001

Files touched:
- `docs/TGW-Plan-Vault/reference/TGW-VAULT-RESTORE.md` (fixed syntax bug,
  added naming-reconciliation table, USB-stamp expected-absence guidance)
- `docs/TGW-Plan-Vault/reference/PP-DEPLOY-001-MX-RESTORE-RUNBOOK.md`
  (applicability banner)
- `docs/TGW-Plan-Vault/reference/runbooks/nixos-prod-cutover-runbook.md`
  (applicability banner)
- `docs/TGW-Plan-Vault/reference/runbooks/INDEX.md` (diagnosis-integrity
  ground rule + runbook metadata convention definition)
- `docs/TGW-Plan-Vault/inbox/claude/INPROGRESS-1529-runbook-gaps-triage.md`
  (breadcrumb)
- No code touched — pytest not applicable to this packet.

## Scope note on gap numbering

Todo #1529's body names 5 items for a "#8-13" range and flags there may be
6. Cross-checking the source report
(`docs/TGW-Plan-Vault/reports/TIGWA-REPORT-runbook-gaps-20260713.md`)
against the todo's own wording: the "runbook owner/date/applicability/
last-drill metadata convention" item is actually the report's **gap #7**
("Runbook review/freshness control is missing"), not #8-13. #1380-RESULT.md
claimed gaps #1-7 were closed by the thermal-half work, but live-checking
`thermal-emergency-response.md` (pre-flight, this session) shows it only
has a `Status:` line for itself — no fleet-wide metadata convention was
ever actually built. Rather than silently drop this per the strict
"#8-13" label or silently second-guess #1380's "closed" claim, I triaged
**all 7 gaps (#7-13)** below so nothing is left in a "someone else already
covered it" limbo.

## Disposition table

| Gap | What it says | Action taken |
|---|---|---|
| #7 — runbook owner/date/applicability/last-drill metadata convention | No freshness-control convention exists across runbooks; agents can't judge applicability before acting. | **Fixed directly** — convention defined in `runbooks/INDEX.md`; applied as applicability banners to the 3 files touched in this packet. **Todo #1533 filed** (`--pp PP-RUNBOOK-001`) for retroactive application to the other 10 numbered runbooks (mechanical, busywork tier). |
| #8 — restore command syntax inconsistency | `TGW-VAULT-RESTORE.md` used `tgw enqueue-sku --queue echo <sku>`; Quickstart uses positional `tgw enqueue-sku QUEUE SKU...`. | **Fixed directly** — verified live against `tgw enqueue-sku --help` (positional, no `--queue` flag exists); Quickstart was already correct, VAULT-RESTORE.md corrected to match. |
| #9 — snapshot/vault naming ambiguity | `TGW-VAULT` (USB) vs `TGW-SNAPSHOT-0` (Btrfs history target) vs archive/ItemData disks not clearly distinguished for emergency use. | **Fixed directly** — added a 3-row comparison table to `TGW-VAULT-RESTORE.md` distinguishing all three, verified live (`systemctl status tgw-usb-stamp.service`, `systemctl list-timers` for `tgw-snapshot.timer`, `RequiresMountsFor=/home/snapshot/TGW-SNAPSHOT-0` in the live unit file). |
| #10 — stale pre-NixOS MX material unlabeled | `PP-DEPLOY-001-MX-RESTORE-RUNBOOK.md` and the cutover runbook read like routine current-host operator checklists but describe a completed, one-time, pre-NixOS event. | **Fixed directly** — applicability banners added to both files marking them historical/DR-reference, not routine procedure. |
| #11 — dbukove remote conflict | MX-restore runbook's rclone commands reference remote `dbukove:`; Tigwa's boundary forbids touching it, and reconciliation was unclear. | **Fixed directly, and finding sharpened** — live-verified `sudo -u tgw` rclone config on tgw-prod defines only `[tgw-gdrive]`; the `dbukove:` remote **no longer exists at all** (matches an existing untriaged note on Taskboard #1264 that `bin/dedupe-gdrive.sh` also targets the now-gone `dbukove:`). This is stronger than a policy conflict — every `dbukove:` command in that runbook would fail outright. Folded into the same applicability banner (#10) rather than a separate edit, since both point at the same "this doc predates the current remote" root cause. |
| #12 — USB restore path incompletely drilled | Physical USB restore (`tgw-restore.sh --source usb`) never live-tested; `tgw-usb-stamp.service` observed failed, unclear if that's expected. | **Partially fixed directly, partially filed.** Documented the expected-absence-vs-real-failure distinction in `TGW-VAULT-RESTORE.md` (live-verified: last failure 2026-07-14 was `no partition with label 'TGW-VAULT' found` — an absent-stick case, not a stamp bug). The actual end-to-end drill needs a physical stick and Dave — **todo #1532 filed** (`--pp PP-RUNBOOK-001`). |
| #13 — recovery documentation proves the danger of weak evidence searches | `PP-RECOVERY-001`'s false code-loss conclusion (incomplete grep + branch confusion) should generalize into a standing diagnosis rule, not stay a one-off postmortem. | **Fixed directly** — added a "verify state directly before declaring anything missing" ground rule to `runbooks/INDEX.md`, citing `PP-RECOVERY-001` as the grounding incident, so it's read before diagnosis rather than only found after re-deriving the same mistake. |

Live evidence:
- `sudo -u tgw tgw enqueue-sku --help` → confirmed positional `queue skus`
  syntax, no `--queue` flag (grounds gap #8 fix).
- `systemctl cat tgw-snapshot.service` → `RequiresMountsFor=/home/snapshot/
  TGW-SNAPSHOT-0`, confirming it's a live local Btrfs target distinct from
  the `TGW-VAULT` USB (grounds gap #9 fix).
- `systemctl status tgw-usb-stamp.service` → last run 2026-07-14 07:30:46
  PDT, exit 1, `ERROR: no partition with label 'TGW-VAULT' found` (grounds
  gap #12's expected-absence guidance).
- `sudo -u tgw` rclone config on tgw-prod → only `[tgw-gdrive]` defined, no
  `[dbukove]` (grounds gap #11's sharpened finding).
- `git status --short` in the worktree → 4 files modified, all under
  `docs/TGW-Plan-Vault/reference/`, no code touched.

Deviations from spec: one flagged, not silent — the packet's "#8-13"
framing (5 named items across 6 numbered gaps) turned out to actually be
report gaps #7-13 (7 items) once cross-checked against the source report's
own numbering; #7 (metadata convention) was included per the todo body's
explicit request even though it falls outside the strict #8-13 range, and
because live-checking showed it wasn't actually closed by the earlier
thermal-half work despite #1380-RESULT.md's "gaps #1-7 closed" claim.
Reported here rather than silently either dropping #7 (would violate the
todo's explicit ask) or silently overriding #1380's "closed" claim without
saying so.

Out-of-scope findings filed:
- Todo #1532 (`--pp PP-RUNBOOK-001`) — physical USB restore drill
  (operator/Dave task, needs a real stick).
- Todo #1533 (`--pp PP-RUNBOOK-001`) — retroactive application of the new
  runbook metadata convention to the 10 existing numbered runbooks
  (mechanical, busywork tier).

All 7 triaged gaps (#7-13) now have a disposition; none left silently
untriaged. 17-gap report status after this packet: #1-6 (thermal) done,
#7-13 (this packet) done, #14-17 (eBay-ops) done per #1380 (with #1530
filed for #14's sub-items) — the full 17-gap report is now fully triaged.
