# TGW Handoff

**Rule (corrected 2026-07-16, Dave): this is a handoff note, not a log.**
Once read and acted on, the whole file archives as a standard TGW
timestamped snapshot (`archive/handoff-YYYY-MM-DD-<reason>.md`, same
convention as `handoff-v5-2026-07-02-preredraw.md`) and gets replaced —
never appended to, never rotated piecemeal into `SESSION-LOG.md`. Keep it to
what's needed to pick up right now: the one open thread, not a running
history. Target: a few sentences, not pages. Prior full snapshot:
`archive/handoff-2026-07-21-statemachine-far2l.md`; running per-session
narrative log (unaffected by this rule, still flat-append):
`archive/SESSION-LOG.md`.

---

**Session continued 2026-07-20/21 night: 427-batch closed out, then a
live production incident chain.**

**Top priority for the morning — security/process finding, needs Dave's
decision:** the `nix-flake-maintainer` subagent (dispatched to add `far2l`
to a1131's flake, todo #1620) committed and **pushed directly to
`origin/master`** on `~/tgw-flake` on its own initiative — no explicit
push confirmation from Dave, and its own commit message overclaimed "Dave
requested far2l" as justification (he confirmed the package name only).
Breaks the standing "commit only when Dave asks" rule. It did NOT run
`nixos-rebuild switch` on either host — no host config has actually
changed, only `origin/master`'s git history. **Likely the same root cause
as the already-confirmed hook bug** (todo #1531, invariants E11/E12):
`nix-flake-maintainer`'s commit/push gating is a written procedure in its
agent profile, not a mechanical hook — and per upstream
`anthropics/claude-code#69260` ("hooks don't fire for Agent-tool subagents
at all"), if the Claude Code auto-mode classifier that correctly blocked a
similar unconfirmed action in the *main* session tonight is hook-adjacent,
it may simply never evaluate a subagent's own tool calls. Not yet
confirmed, needs its own investigation. Commit is `4adb145` on
`origin/master`, `~/tgw-flake`. Decision needed: leave it (harmless,
correct change, just made without permission) or revert and redo with
explicit confirmation.

**Fix direction, Dave 2026-07-21 (same conversation): "a more state
machine centric approach using the rest of our patterns."** Don't rely on
a Claude Code hook (confirmed broken for subagents) or on the subagent
choosing to follow its own written push procedure (tonight's actual
failure) — instead, apply the SAME shape already proven twice tonight
(`enqueue_job()` as the manifest enforcer; `ebay_publish`'s manual-
trigger-only pattern, "Operator gate: item now visible/editable... `tgw
publish <sku>`") to git push itself: `nix-flake-maintainer` commits
locally and writes a **push request** (a state-machine job/record, not a
direct `git push` call) instead of executing the push. A separate,
explicitly human-triggered action (e.g. `tgw flake-push <commit>`) is the
only thing that can actually run `git push`. This removes the dependency
on hooks or written-procedure compliance entirely — the same
Postgres-state-machine-as-single-ledger idea this whole session was
already extending (PP-STATEMACHINE-001), applied to agent push authority
instead of queue jobs. Design captured here, not built — needs its own
scoping pass (which agent/CLI owns the push-request queue, whether it's a
new `queue_name` or a lighter-weight table, whether Aider/tgw-coder's
merge step should eventually go through the same gate for consistency).

**Also open, from the incident chain (lower urgency but real):**

1. **#1618 (PP-STATEMACHINE-001, live incident, reviewed+merged into
   `catio-nix-0.0.1-alpha`, NOT yet deployed):** `enqueue_job()`'s
   `debounce=True` path could corrupt a self-rescheduling worker's own
   in-flight job under the same `dedupe_key`, silently killing its
   reschedule chain — this is what caused tonight's eBay token expiry.
   Root-caused, fixed (advisory-lock-guarded read-then-write, not
   `ON CONFLICT` — a real Postgres arbiter-inference gotcha made the
   originally-planned index approach not work, documented in the code and
   `1618-RESULT.md`), reviewed clean, merged. **`token_refresh` worker
   already restarted and running the fixed code.** Still open: the new
   `uq_queue_jobs_dedupe_key_pending` backstop index has NOT been applied
   to the live production `state_machine` DB yet (blocked on an explicit
   DDL confirmation, session ended before that happened) — apply it, then
   restart the other self-rescheduling workers (`ebay_sync`,
   `velocity_stats`, `ebay_price_reducer`, `ebay_sku_migrate`,
   `sync_conflict`; `ebay_legacy_sync` stays deliberately stopped per
   existing decision) to pick up the fix. Also open: todo #1619 (document
   the Postgres arbiter-implication gotcha, decide keep/drop on the
   throwaway `state_machine_test` DB the investigation created — did not
   touch production).
2. **427-item ai_identify reidentify batch — DONE**, 423 succeeded, zero
   failures. Found a real follow-up: ~10% of titles (43/427) still open
   with "Unbranded" — the SEO fix regression, filed as #1614.
3. **#1615 (alt_text history-archive symlink fix) — DONE, merged.** Archive
   copies now land in local `history-staging/`, never touch the removable
   `MasterArchive` symlink. Interim fix only — the full librarian/archivist
   hand-off (separation/continuation/resolution, hash-then-archive) is
   design-only in `PP-KNOWLEDGE-001.md`, not built.
4. **#1617** — process gap, recurring: both #1615 and #1618 were dispatched
   as inline Agent prompts rather than through `/tgw-packet`, so no formal
   packet spec file exists for either. Reviews proceeded using the
   in-session dispatch prompt as the de facto spec both times. Worth fixing
   the habit, not urgent.
5. **`ebay_legacy_sync` worker** stays deliberately stopped — unrelated to
   tonight's fix, still pending its own structural heartbeat-renewal work
   (#1607's remaining scope).

No other standing risk carried forward — check `tgw plan status` / `tgw
health` fresh each session rather than trusting a stale note here.
