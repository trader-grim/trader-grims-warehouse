# TGW Handoff

**Rule (corrected 2026-07-16, Dave): this is a handoff note, not a log.**
Once read and acted on, the whole file archives as a standard TGW
timestamped snapshot (`archive/handoff-YYYY-MM-DD-<reason>.md`, same
convention as `handoff-v5-2026-07-02-preredraw.md`) and gets replaced —
never appended to, never rotated piecemeal into `SESSION-LOG.md`. Keep it to
what's needed to pick up right now: the one open thread, not a running
history. Target: a few sentences, not pages. Prior full snapshot:
`archive/handoff-2026-07-22-nats-syncthing-nix-direction.md`; running
per-session narrative log (unaffected by this rule, still flat-append):
`archive/SESSION-LOG.md`.

---

**Top open thread — finish the NATS dual-authority fix, then resume the
orchestrator/classifier planning week.** Full detail in
`inbox/claude/INPROGRESS-2026-07-22-session-wrap-resume-here.md` — read
that in full before touching Nix, NATS, or Syncthing again.

**Immediately actionable:** `packets/1638-nats-stream-single-authority.md`
(todo #1638) — `nats-stream-init.service` is still failing on tgw-prod;
root cause is `nats_client.py`'s `_ensure_streams()` independently
creating JetStream streams unbounded, uncoordinated with the new
declarative Nix provisioning. Mixed packet, split the flake half
(nix-flake-maintainer) from the `src/tgw/` half (tgw-coder).

**Major standing decision, read before any new Nix work:** Dave, 2026-07-22
— "We are changing unless we find a good reason not to [on Nix]. To what
and when TBD." Full evidence in `TGW-Master-Plan.md`'s `PP-NIXOS-001`
section and memory `project-nix-stability.md`. Not a migration
authorization — just: the default flipped, staying on Nix now needs an
active reason.

**Everything else decided this session, not yet built:** PP-RUNNERCOMMS-001
mailbox redesign (sent to Tigwa for review, check her response first),
PP-LOADTEMP-001 (system-load "weather station," fully shaped, not yet a
packet), PP-POSTGRES-001 (now a real 5-phase plan, Phase 0 = todo #1636
is packet-ready and small/independent). Full detail in the inbox note
above, not repeated here.

**Orchestrator/classifier planning week (started 2026-07-21, still
Dave's stated target: Friday 2026-07-24 for the Max-plan/throughput
jump)** — PP-WORKFLOW-001 (todo #1626), PP-ORCHESTRATOR-001
(specialist-roster pattern, decided), PP-APPROVAL-001, PP-CLASSIFIER-001
(todo #1628) are all decided-not-built, unchanged by tonight's session
except that #1626/#1628 are confirmed still genuinely `tgw-coder`-ready
whenever dispatched. See `project-orchestrator-classifier-cluster-2026-07-21`
memory for the full decision chain if resuming this thread specifically.

**Carried forward, still open, NOT resolved by anything this session:**

1. **Needs Dave's decision — security/process finding, aging:** the
   `nix-flake-maintainer` subagent (todo #1620, `far2l` on a1131)
   committed and pushed directly to `origin/master` without explicit
   confirmation back on 2026-07-21. **PP-FLAKEGATE-001 (built and live as
   of this session) is the structural fix for this class of incident going
   forward** — the original commit itself (`4adb145`) still hasn't had an
   explicit keep-or-revert decision from Dave, separate from the process
   fix now being in place.
2. Apply the `uq_queue_jobs_dedupe_key_pending` backstop index (#1618) to
   the live `state_machine` DB — still not done. Then restart the other
   self-rescheduling workers (`ebay_sync`, `velocity_stats`,
   `ebay_price_reducer`, `ebay_sku_migrate`, `sync_conflict`).
3. **#1619** — document the Postgres arbiter-implication gotcha from
   #1618; decide keep/drop on the throwaway `state_machine_test` DB.
4. **#1614** — ~10% of the 427-item ai_identify batch still shows
   "Unbranded" titles, the SEO-fix regression.

No other standing risk carried forward — check `tgw plan status` / `tgw
health` fresh each session rather than trusting a stale note here.
