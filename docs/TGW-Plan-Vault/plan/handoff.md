# TGW Handoff

**Rule (corrected 2026-07-16, Dave): this is a handoff note, not a log.**
Once read and acted on, the whole file archives as a standard TGW
timestamped snapshot (`archive/handoff-YYYY-MM-DD-<reason>.md`) and gets
replaced — never appended to. Prior full snapshot: `archive/handoff-2026-07-22-
plan-sweep-and-godconsole.md`; running per-session narrative log (unaffected,
still flat-append): `archive/SESSION-LOG.md`.

---

**2026-07-25 session — git cleanup + full inbox/claude backlog processed.**
Picked up an interrupted prior session: working tree had 24 pending
inbox/claude/ files plus uncommitted completed work (07-22 plan
reconciliation, eBay upload dimension-sum fix, http_server dead_letter-
supersede fix). All committed in 5 commits (`25adc38` `87d1c1f` `d43eec0`
`57ea1f1` `243fd1b` `da26c86`). `tgw plan check` — all clear (was 1 warning
at session start, fixed the pre-existing missing_pp_ref backlog too:
#1674/#1678/#1679/#1680/#1681 now tagged).

**Live bug found and fixed same session:** independent review
(`HERMES-INDEPENDENT-REVIEW-2026-07-25-yesterday-fixes.md`) caught a
mixed tz-aware/naive datetime `TypeError` crash in the just-committed
`_superseded_by_success()`/`_after_baseline()` (item detail page). Verified
the repro myself, dispatched the fix through `tgw-coder` per E12 (todo
#1683, branch `todo/1683-tz-normalize-fix`), reviewed the diff, merged,
done. This is the value of the independent-review discipline working as
designed — don't skip it.

**24 inbox/claude files → all processed:**
- Folded into master plan (commit `57ea1f1`): new **PP-PORTABLEFLEET-001**
  (portable fleet buildout program), new **PP-UHHUH-001** (thought-capture
  mode), 2026-07-25 consolidated Nix batch inventory under PP-NIXOS-001,
  workflow-ordering doctrine under PP-WORKFLOW-001, and a correction to
  PP-CATIONIX-001's "#1671 built and verified live" claim (real post-build
  blockers found, not yet closed).
- New todos filed and pp_ref-tagged: **#1684-1691** (nix-request-packetizer
  observation pass, PP-NIXOS-001 boundary review answers, hermaroid-cua-stop
  PID/rollback/evidence fixes, OpenRouter-attribution reply to Tigwa,
  Dave↔Tigwa shared-access packet, workflow-capability audit, alt-text
  full-workflow scope, bulk-listing queue-builder capability matrix).
- All 24 files archived to `inbox/archive/DONE-2026-07-25-*`.

**Still open, carried forward (not this session's to resolve):**
- Todo #1527 — a1131 has no Flutter SDK; needs Dave's device decision.
- Todo #1620 (far2l) — still no explicit keep-or-revert decision from Dave.
- `nix-flake-maintainer` should pick up #1684/#1685/#1688 next (all
  PP-NIXOS-001, all require live host work this session didn't touch).
- Pre-existing `tgw health` failures, not new: `backups` (rclone/snapshot
  staleness, PP-BACKUP-001), `ebay_sync_fallback` (841 consecutive
  fallback runs, todo #1077, eBay Dev Support ticket still the only path).
- Pre-existing test failures, not new: `test_invariant_c12_field_set_accessors.py`
  allowlist stale (already flagged in `tgw plan status`, PP-ADD-005).

No other standing risk carried forward — check `tgw plan status` / `tgw
health` fresh each session rather than trusting a stale note here.
