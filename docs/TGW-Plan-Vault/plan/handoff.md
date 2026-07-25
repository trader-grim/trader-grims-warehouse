# TGW Handoff

**Rule (corrected 2026-07-16, Dave): this is a handoff note, not a log.**
Once read and acted on, the whole file archives as a standard TGW
timestamped snapshot (`archive/handoff-YYYY-MM-DD-<reason>.md`) and gets
replaced — never appended to. Keep it to what's needed to pick up right
now. Prior full snapshot: `archive/handoff-2026-07-22-plan-sweep-and-
godconsole.md`; running per-session narrative log (unaffected, still
flat-append): `archive/SESSION-LOG.md`.

---

**Top open thread — month-long Max-plan build sprint, "plan until
nothing material left to plan," still in the planning phase.** Full
detail in memory `project-2026-07-22-jetstream-buildout-and-max-plan-
sprint.md` and `TGW-Master-Plan.md`'s top-of-file "Standing context" +
"Doctrine for this whole sweep" sections — read both before resuming.
2026-07-22's session ran a full sweep across ~65 PP sections looking for
stale/contradictory/broken-link/undesigned-gap content; most came back
clean. Fixes made: PP-QUOTA-001 (garbled edit artifact), PP-FLAKEGATE-001
(self-contradicting sentence), PP-HR-001 (dangling citation to a deleted
note, content already preserved inline), PP-NIXOS-001 (`nix/CLAUDE-
NIX.md` confirmed genuinely missing — flagged for nix-flake-maintainer,
not authored blind).

**Immediately actionable next step, morning priority #1 — reconcile
PP-GODCONSOLE-001 (new tonight) against 6 same-night Tigwa notes on the
identical topic.** Dave asked for "my inbox interface, the human facing
one... a console to see all of the inboxes" — designed live tonight:
Part A (personal inbox reader, tgw-http), Part B ("god console" —
all-actor visibility + halt authority, feed-shaped UI, "not too easy to
stop but possible pretty quick" friction bar). Full design in
`TGW-Master-Plan.md`'s new PP-GODCONSOLE-001 section; todo #1661 (open,
correctly — halt mechanism + file-vs-JetStream data source still need
Dave's call). **Before touching #1661 further**: 6 of the 7 files
sitting unread in `inbox/claude/` tonight are Tigwa independently
designing the exact same human-inbox/ntfy/Flutter/KFMAWI territory —
`TIGWA-NOTE-ntfy-human-inbox-connection`, `TIGWA-ADDENDUM-ntfy-flutter-
human-inbox-reconciliation`, `TIGWA-CLARIFICATION-human-in-the-loop-
message-monitoring`, `TIGWA-CLARIFICATION-practical-security-baseline-
human-inbox`, `TIGWA-CLARIFICATION-kfmawi-intentional-unplug-clear`,
`TIGWA-DECISION-kfmawi-outward-communications-surface`. Titles only
skimmed, not cross-read against PP-GODCONSOLE-001 as of session end —
this is a `feedback-design-reconvergence` pattern (memory), process
these first thing, they likely sharpen or partially answer PP-GODCONSOLE-
001's open questions rather than being unrelated. (7th file,
`TIGWA-PROVISIONAL-RESOURCE-CARD-cisco-antares`, is unrelated ferals-
audit business — process separately, lower priority.)

**Continuing the plan-unfolding sweep:** Dave's instruction tonight —
"we will keep searching for gold. Found a lot today." The ~65-PP sweep
found real issues at a decent hit rate; worth another pass once the
GODCONSOLE/Tigwa reconciliation above is done. No specific next-PP target
named — pick up wherever `tgw plan status`/a fresh grep for staleness
markers points, same discipline as tonight (verify live before editing,
don't invent designs owned by Tigwa/Dave).

**Also needs attention, carried forward (not reverified tonight — check
fresh, don't trust blindly):**
- Todo #1658 — live, confirmed-still-active `status`/`#STATUS` write-path
  bug (`items.py` `verifiedupdate()`/`statusupdate()`/`bulk_edit` all
  write the wrong key). Documented, not yet dispatched for a fix.
- Todo #1527 — a1131 has no Flutter SDK/toolchain at all; needs Dave's
  device decision before #1630 (Flutter launch/connect on a1131) can be
  usefully re-scoped.
- Branch `todo/1638-1639-nats-client-fixes` (commit `b790591`) — done,
  tested, live-verified, **not yet reviewed/stitched**. Run
  `/tgw-runner-review` before merging, if not already done.
- `nix-flake-maintainer` todo #1620 (far2l) — still has no explicit
  keep-or-revert decision from Dave on the original unconfirmed push;
  PP-FLAKEGATE-001 (#1625, in progress under `tgw-coder`) is the
  structural fix for future pushes but doesn't retroactively resolve
  this one.
- Tigwa's response (if any) to the Max-plan-sprint context note is still
  unconfirmed — check whether her PP-POSTGRES-001 "pipeline/UI first,
  defer Postgres" sequencing call has been revisited now that she has
  the capacity context.

No other standing risk carried forward — check `tgw plan status` / `tgw
health` fresh each session rather than trusting a stale note here.
