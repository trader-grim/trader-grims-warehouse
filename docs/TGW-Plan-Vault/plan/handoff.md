# TGW Handoff

**Rule (corrected 2026-07-16, Dave): this is a handoff note, not a log.**
Once read and acted on, the whole file archives as a standard TGW
timestamped snapshot (`archive/handoff-YYYY-MM-DD-<reason>.md`) and gets
replaced — never appended to. Prior full snapshot: `archive/handoff-2026-07-26-
flakegate-merge-and-0a-0b-evidence.md`; running per-session narrative log
(unaffected, still flat-append): `archive/SESSION-LOG.md`.

---

**2026-07-25/26 session — Workstream 0A/0B evidence packets, cloud-sync
watchdog fix, #1692 flakegate chain closed end to end.**

**What changed:**
- **Workstream 0A (P0 backup/recovery, todos #61/#1264/#1046/#1050)** —
  read-only evidence packet delivered to `inbox/tigwa/Workstream-0A-report.md`.
  Corrected my own initial "hung subprocess" mischaracterization of
  `tgw-cloud-sync` after Dave challenged it directly — raw log showed
  continuous progress, not a stall. Per Dave's direct instruction: removed
  `bin/tgw-cloud-sync`'s 4h watchdog timeout (was killing/restarting the
  first-ever full sync nightly before it could finish) and removed the
  shared flock with `tgw-itemdata-sync` (Dave: `--tpslimit 2` on each side is
  the real concurrency-safety mechanism, not lock serialization). Started
  live; first full sync running unbounded as of session close. Todo #1694
  closed.
- **Workstream 0B (eBay fallback/legacy-worker, todos #1077/#1605/#1607/
  #1248)** — read-only evidence packet delivered to
  `inbox/tigwa/CLAUDE-REPORT-Workstream-0B-ebay-fallback-legacy-worker-2026-07-25.md`.
  Found #1605/#1607's own status text stale relative to live state
  (`ebay_legacy_sync` has been running since 2026-07-21; #1607's structural
  dedupe_key enforcement already built/live under #1608) — refreshed both
  via `tgw todo --note`. #1077's rewritten support-ticket follow-up sent
  2026-07-25 (Dave); register + todo updated, now awaiting eBay's reply.
  **Tigwa's follow-up review (`inbox/claude/TIGWA-REVIEW-0B-lease-race-
  correction-and-remediation-gate-2026-07-25.md`) found my proposed "Packet
  C" insufficient** — live evidence of ~300s recovery-cycle intervals
  contradicts the declared 600s `lease_seconds` mitigation. **A full
  token-fenced heartbeat design (per Tigwa's spec) is still needed and not
  yet written** — see memory `project-workstream-0a-0b-evidence-packets-
  2026-07-25.md` for the full requirement.
- **Todo #1692 (dev-shell src-on-PYTHONPATH gate) — closed end to end**:
  fix pushed to `tgw-flake` (`a692acb`, Dave ran the push + mark-executed —
  the `tgw flake push <job-id>` shortcut I initially suggested doesn't
  actually exist yet, #1625 never merged, corrected this in #1692's status
  text). Source-adapter branch (`todo/consolidated-nix-source-20260725`)
  lock updated to `a692acb` via `nix-flake-maintainer` (todo #1695),
  pushed, independently reviewed by a fresh isolated-worktree agent (no
  blockers, exact 3-file scope confirmed: `flake.nix`/`flake.lock`/
  `pyproject.toml`), then merged into `catio-nix-0.0.1-alpha` (`ff6f1e0`,
  `--no-ff`, Dave's explicit direct go-ahead required twice — an auto-mode
  classifier correctly blocked the first attempt for not having an
  unambiguous merge-specific confirmation) and pushed to origin. Todos
  #1692/#1694/#1695 all marked done this session.

**Still open, carried forward (not this session's to resolve):**
- **Packet C / #1607's real fix** — token-fenced `heartbeat_job()`, per
  Tigwa's detailed spec above. Not started. Needs Dave's approval gate
  before any `src/tgw/queue/` dispatch (shared infra every worker depends
  on).
- `tgw-cloud-sync.service`'s first full sync — still running as of session
  close, multi-day expected. Tigwa's evidence gate (completion marker /
  fresh `rclone-sync-last-success`) not yet reached; no action needed until
  it either completes or shows a genuine new problem.
- #1077 — waiting on eBay Support's reply to the 2026-07-25 follow-up.
  Nothing to do until they respond.
- Secrets-bundle path/execution gap, second USB drive (#61), `sde1`
  snapshot-tree disposition, restore drill (#1050) — all still open per the
  0A report, each needing its own bounded packet + Dave sign-off.
- Todo #1527 — a1131 has no Flutter SDK; needs Dave's device decision.
- Todo #1620 (far2l) — still no explicit keep-or-revert decision from Dave.
- Todos #1684-1691/#1693 (inbox sweep from the prior 2026-07-25/26 session)
  — filed, not yet worked.
- Stale breadcrumb `docs/TGW-Plan-Vault/inbox/INPROGRESS-1631-tgw-app-
  dropdown-initialvalue.md` sitting in `inbox/` root (not `inbox/claude/`) —
  todo #1631 is long since merged (`b127f15`); never cleaned up. Still not
  touched, flagging again for next inbox pass.
- Pre-existing test failures, not new: `test_invariant_c12_field_set_accessors.py`
  allowlist stale (#1622/#1641, PP-ADD-005) — reproduced again this session
  during #1695's verification, still unfixed.

No other standing risk carried forward — check `tgw plan status` / `tgw
health` fresh each session rather than trusting a stale note here.
