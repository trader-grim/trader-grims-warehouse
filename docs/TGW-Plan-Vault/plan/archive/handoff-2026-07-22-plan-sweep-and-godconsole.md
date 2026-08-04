# TGW Handoff

**Rule (corrected 2026-07-16, Dave): this is a handoff note, not a log.**
Once read and acted on, the whole file archives as a standard TGW
timestamped snapshot (`archive/handoff-YYYY-MM-DD-<reason>.md`) and gets
replaced — never appended to. Keep it to what's needed to pick up right
now. Prior full snapshot: `archive/handoff-2026-07-22-jetstream-buildout-
kickoff.md`; running per-session narrative log (unaffected, still
flat-append): `archive/SESSION-LOG.md`.

---

**Top open thread — month-long Max-plan build sprint, just started.**
Dave, 2026-07-22: "we are planning for a max subscription upgrade to
build this out... Build it all... I think we can easily do it in a month
with a max plan, tigwa, and some api pocket change." Full detail in
memory `project-2026-07-22-jetstream-buildout-and-max-plan-sprint.md`
and `TGW-Master-Plan.md`'s new top-of-file "Standing context" section —
read both before resuming. No month-scale roadmap document exists yet
across all open PPs; work is proceeding packet-by-packet.

**Immediately actionable next step — Packet C of
`docs/ai-plans/jetstream-substrate-buildout.md`:** the JetStream
acceptance-evidence suite (cross-host connect, durable ack, denied
cross-actor access, restart/replay, health check). Blocked on two infra
pieces, both decided this session but not yet built: NATS binds on
tgw-prod's **Tailscale interface** (currently localhost-only, blocks
a1131 entirely), and **one NATS account with per-actor subject
permissions**. Write that packet next, dispatch mixed (flake for the
bind, app-code for account provisioning).

**Also needs attention:**
- Branch `todo/1638-1639-nats-client-fixes` (commit `b790591`) — done,
  tested, live-verified, **not yet reviewed/stitched**. Run
  `/tgw-runner-review` before merging.
- Todo #1641 — pre-existing unrelated test failure (stale line-number
  allowlist in a C12 static test vs `ai_identify.py`), found incidentally,
  needs its own triage.
- Tigwa sent no response yet to the Max-plan-sprint context note
  (`inbox/tigwa/CLAUDE-NOTE-new-context-for-pp-postgres-001-sequencing-
  month-2026-07-22.md`) — check before assuming her PP-POSTGRES-001
  "pipeline/UI first, defer Postgres" sequencing call still stands
  unchanged now that she has the capacity context.

**#1638/#1639 are DONE, both live** — dual-authority NATS stream bug and
`tgw_health`'s NATS-check asyncio crash. Took a 4th live failure on
`nats.nix` to land (exact-sum-to-ceiling reservation rejected by NATS
admission control, fixed with 10% headroom) — recorded in
`TGW-Master-Plan.md`'s PP-AIOPS-001 section as concrete evidence for the
2026-07-22 Nix-direction-change decision (below), not just accumulated
mood.

**Major standing decision, read before any new Nix work:** Dave,
2026-07-22 — "We are changing unless we find a good reason not to [on
Nix]. To what and when TBD." Full evidence in `TGW-Master-Plan.md`'s
`PP-NIXOS-001` section and memory `project-nix-stability.md`. Not a
migration authorization — the default flipped, staying on Nix now needs
an active reason. Also encoded this session: `feedback-verify-directly-
when-possible.md` — when I already have live access to the same host,
run read-only verification myself, don't make Dave paste command output.

**Decided this session, not yet built:** PP-RUNNERCOMMS-001 mailbox
redesign ("basically email" — delivery guarantee, reply trail, versioned
drafts, per-actor compartmentalization; Tigwa's acceptance criteria
recorded in PP-RUNNERCOMMS-001's section), PP-LOADTEMP-001 (two design
gaps — failure-mode default, fence allowlist — both need answers before
packet-ready), PP-POSTGRES-001 Phase 0 (todo #1636, packet-ready,
small/independent, sequencing now uncertain pending Tigwa's response
above).

**Orchestrator/classifier planning week (started 2026-07-21)** —
PP-WORKFLOW-001 (#1626), PP-ORCHESTRATOR-001, PP-APPROVAL-001,
PP-CLASSIFIER-001 (#1628) are decided-not-built, #1626/#1628 confirmed
`tgw-coder`-ready whenever dispatched. See
`project-orchestrator-classifier-cluster-2026-07-21` memory for the full
decision chain.

**Carried forward, still open:**

1. **Needs Dave's decision — aging:** `nix-flake-maintainer` (todo #1620)
   committed+pushed directly to `origin/master` without explicit
   confirmation on 2026-07-21. PP-FLAKEGATE-001 is the structural fix
   going forward; the original commit (`4adb145`) still has no explicit
   keep-or-revert decision.
2. Apply the `uq_queue_jobs_dedupe_key_pending` backstop index (#1618) to
   the live `state_machine` DB — still not done. Then restart the other
   self-rescheduling workers.
3. **#1619** — document the Postgres arbiter-implication gotcha from
   #1618; decide keep/drop on the throwaway `state_machine_test` DB.
4. **#1614** — ~10% of the 427-item ai_identify batch still shows
   "Unbranded" titles, the SEO-fix regression.

No other standing risk carried forward — check `tgw plan status` / `tgw
health` fresh each session rather than trusting a stale note here.
