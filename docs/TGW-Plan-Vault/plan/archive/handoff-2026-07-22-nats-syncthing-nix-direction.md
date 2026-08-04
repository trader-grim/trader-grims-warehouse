# TGW Handoff

**Rule (corrected 2026-07-16, Dave): this is a handoff note, not a log.**
Once read and acted on, the whole file archives as a standard TGW
timestamped snapshot (`archive/handoff-YYYY-MM-DD-<reason>.md`, same
convention as `handoff-v5-2026-07-02-preredraw.md`) and gets replaced —
never appended to, never rotated piecemeal into `SESSION-LOG.md`. Keep it to
what's needed to pick up right now: the one open thread, not a running
history. Target: a few sentences, not pages. Prior full snapshot:
`archive/handoff-2026-07-21-statemachine-incident-carryforward.md`; running
per-session narrative log (unaffected by this rule, still flat-append):
`archive/SESSION-LOG.md`.

---

**Top open thread — the orchestrator/classifier planning week (started
2026-07-21).** Dave is running a multi-day, small-chunk planning cycle —
work through PP-WORKFLOW-001/PP-ORCHESTRATOR-001/PP-APPROVAL-001/
PP-CLASSIFIER-001, gap-fill iteratively a little at a time (his call each
round), converging on a fully-integrated, packet-ready plan by **Friday
2026-07-24** — the target date he plans to buy a Max subscription and start
executing at much higher throughput, mostly via `tgw-coder`, while Tigwa's
training continues in parallel, faster. See `project-orchestrator-
classifier-cluster-2026-07-21` memory for the full decision chain.

**Decided so far this cycle (all encoded in `TGW-Master-Plan.md`, none yet
built):**
- PP-WORKFLOW-001: native `depends_on`/routing on `queue_jobs`, no external
  tool. Dependency dead-letter → block indefinitely, no auto-cancel
  (todo #1626).
- PP-ORCHESTRATOR-001: no new orchestrator service — specialist-roster-
  growth pattern. tgw-coder = specialist #1 (already trusted, already
  running this shape manually); Aider = specialist #2 once tgw-coder's
  proven, via the existing `tgw-aider` MCP bridge; every future specialist
  joins the same way, one at a time. Once the 2-specialist loop is clean,
  spin off the repetitive orchestration mechanics themselves — same
  trust-then-delegate discipline, one level up.
- PP-APPROVAL-001: typed, config-driven approval handlers (not a generic
  callback) — same pattern as `tgw-models.json`. Resubmission-on-dead-letter
  (todo #1627) is its first concrete instance, blocked on this landing.
- PP-CLASSIFIER-001 (new): one unified, config-driven action classifier
  consolidating 4 existing guard hooks (`flake-guard.py`/E10,
  `app-code-guard.py`/E12, `worktree-guard.py`/E11,
  `trace-immutability-guard.py`/E14) + approval-type routing + awareness of
  Claude Code's own built-in auto-mode classifier. This is the concrete
  design for PP-CATIONIX-001's still-unbuilt "crypto-lock" permission
  architecture. Schema decided (`tgw-classifier.json`: type/match/
  scope_rule/enforcement/approval). Migration order: `flake-guard.py`
  first (lowest stakes, todo #1628) — explicitly NOT touching E11/E12/E14
  until Phase 1 is proven clean.

**Next step whenever this resumes:** keep gap-filling in small chunks per
Dave's stated rhythm — no large unilateral design pushes, present one
bounded question at a time, wait for his weigh-in, encode, repeat.

---

**Carried forward, still open, NOT resolved by anything this session:**

1. **Needs Dave's decision — security/process finding:** the
   `nix-flake-maintainer` subagent (todo #1620, adding `far2l` to a1131's
   flake) committed and **pushed directly to `origin/master`** on
   `~/tgw-flake` on its own initiative — no explicit push confirmation, and
   its own commit message overclaimed "Dave requested far2l" (he confirmed
   only the package name). Breaks "commit only when Dave asks." Did NOT run
   `nixos-rebuild switch` — no host config changed, only `origin/master`'s
   git history. Commit `4adb145`. Likely same root cause as the
   already-confirmed hook bug (todo #1531, E11/E12: hooks don't fire for
   Agent-tool subagents, `anthropics/claude-code#69260`). **Decision
   needed: leave it (harmless, correct change, just unauthorized) or revert
   and redo with explicit confirmation.**
2. **Fix direction agreed but not built** (same 2026-07-21 conversation):
   "a more state-machine-centric approach using the rest of our patterns" —
   `nix-flake-maintainer` should commit locally and write a **push
   request** (a state-machine job/record) instead of executing `git push`
   directly; a separate human-triggered action (`tgw flake-push <commit>`)
   is the only thing that can actually push. Removes dependency on hooks or
   written-procedure compliance entirely. Design captured, not built —
   needs its own scoping pass (which agent/CLI owns the push-request
   queue, new `queue_name` vs. lighter table, whether Aider/tgw-coder's
   merge step should eventually use the same gate).
3. **Apply the `uq_queue_jobs_dedupe_key_pending` backstop index** (from
   #1618's fix) to the live production `state_machine` DB — still not done,
   blocked on explicit DDL confirmation. Then restart the other
   self-rescheduling workers (`ebay_sync`, `velocity_stats`,
   `ebay_price_reducer`, `ebay_sku_migrate`, `sync_conflict`) to pick up
   the fix (`ebay_legacy_sync` stays deliberately stopped, unrelated).
4. **#1619** — document the Postgres arbiter-implication gotcha from #1618;
   decide keep/drop on the throwaway `state_machine_test` DB.
5. **#1614** — ~10% of the 427-item ai_identify batch (43/427) still shows
   "Unbranded" titles, the SEO-fix regression.
6. **#1617** — process gap: #1615/#1618 were dispatched as inline Agent
   prompts rather than through `/tgw-packet`, no formal packet spec exists
   for either. Not urgent.

No other standing risk carried forward — check `tgw plan status` / `tgw
health` fresh each session rather than trusting a stale note here.
