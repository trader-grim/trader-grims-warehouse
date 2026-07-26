# TGW Handoff

**Rule (corrected 2026-07-16, Dave): this is a handoff note, not a log.**
Once read and acted on, the whole file archives as a standard TGW
timestamped snapshot (`archive/handoff-YYYY-MM-DD-<reason>.md`) and gets
replaced — never appended to. Prior full snapshot: `archive/handoff-2026-07-22-
plan-sweep-and-godconsole.md`; running per-session narrative log (unaffected,
still flat-append): `archive/SESSION-LOG.md`.

---

**2026-07-25/26 session — git cleanup, full inbox backlog, Lix/portable-fleet
direction confirmed, Helicrew cutover.** Started by recovering an interrupted
prior session (24 pending inbox/claude/ files + uncommitted completed work).
`tgw plan check` — all clear.

**What changed:**
- Committed prior session's work + a live bug fix: mixed tz-aware/naive
  datetime crash in `_superseded_by_success()`/`_after_baseline()`, caught by
  independent review, fixed via `tgw-coder` (todo #1683, merged).
- All 24 inbox/claude files processed: 2 new PP sections
  (**PP-PORTABLEFLEET-001**, **PP-UHHUH-001**), Nix batch inventory folded
  into PP-NIXOS-001, workflow-ordering doctrine into PP-WORKFLOW-001,
  PP-CATIONIX-001's "#1671 built and verified live" claim corrected with
  real post-build blockers. 8 todos filed (#1684-1691), pre-existing
  missing_pp_ref backlog fixed (#1674/1678-1681).
- **Dave confirmed the Lix/two-entity architecture direction directly**:
  tgw-prod moves toward MORE declarative Nix coverage (Lix cuts the
  approval-round friction, isn't a reason to declare less); the portable
  fleet is a separate, lighter client/remote entity. First prototype is
  **Helicrew** (Dave's laptop) — Tigwa supplied a verified OS/app inventory
  (Debian 13, no Nix/Lix yet, Tailscale-joined), folded into
  PP-PORTABLEFLEET-001, todo #1693 filed (role/package manifest next step).
  Full detail: master plan `PP-NIXOS-001` + `PP-PORTABLEFLEET-001`; memory
  `project-nix-stability.md`'s top entry.
- **Todo #1692 (dev-shell src-on-PYTHONPATH gate)** — confirmed as a real
  merge-blocking defect independently by Claude and Tigwa, fixed by
  `nix-flake-maintainer` per PP-FLAKEGATE-001/E17 (commit `a692acb` on
  `~/tgw-flake` branch `todo/consolidated-nix-fleet-20260725`, local only,
  verified 2756 passed / zero collection errors). **Push request queued
  (`4cd3a02f-529d-465f-9947-62b119222b87`), waiting on Dave** to run the
  actual `git push` + `tgw flake mark-executed` — exact commands already
  given directly to Dave (corrected once after Tigwa caught a fragile-PATH
  issue in the first version).
- **Helicrew cutover to primary Tigwa/Hermes seat, verified live 2026-07-26
  00:05 UTC** (Tigwa's own checkpoint): Hindsight/dashboard/gateway services
  active under `tigwa`, default gateways disabled on tgw-prod/a1131. Folded
  into PP-HERMES-EA-001. Telegram adapter unconfirmed since cutover — open,
  not urgent (Dave: chase only if still broken).

**Still open, carried forward (not this session's to resolve):**
- Todo #1692's actual push/mark-executed — Dave's action, commands given,
  not yet confirmed done as of session close.
- Once pushed: source-adapter branch (`todo/consolidated-nix-source-20260725`)
  needs its `tgw-flake` lock updated, then a fresh independent merge/test
  review before it can merge into `catio-nix-0.0.1-alpha`.
- Todo #1527 — a1131 has no Flutter SDK; needs Dave's device decision.
- Todo #1620 (far2l) — still no explicit keep-or-revert decision from Dave.
- 8 new todos from the inbox sweep (#1684-1691) plus #1693 — filed, not
  yet worked. `nix-flake-maintainer` should pick up the PP-NIXOS-001 ones
  (#1684/#1685/#1688) next.
- Telegram adapter on Helicrew — open per Tigwa's checkpoint, low urgency.
- Stale breadcrumb `docs/TGW-Plan-Vault/inbox/INPROGRESS-1631-tgw-app-
  dropdown-initialvalue.md` sitting in `inbox/` root (not `inbox/claude/`) —
  todo #1631 is long since merged (`b127f15`); never cleaned up by whatever
  session wrote it. Not touched this session, flagging for next inbox pass.
- Pre-existing `tgw health` failures, not new: `backups` (rclone/snapshot
  staleness, PP-BACKUP-001), `ebay_sync_fallback` (841 consecutive
  fallback runs, todo #1077, eBay Dev Support ticket still the only path).
- Pre-existing test failures, not new: `test_invariant_c12_field_set_accessors.py`
  allowlist stale (already flagged in `tgw plan status`, PP-ADD-005).

No other standing risk carried forward — check `tgw plan status` / `tgw
health` fresh each session rather than trusting a stale note here.
