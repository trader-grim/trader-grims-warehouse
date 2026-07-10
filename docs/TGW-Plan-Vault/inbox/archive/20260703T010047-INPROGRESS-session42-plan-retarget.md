# INPROGRESS — Session 42: plan retarget + R0 quota independence (2026-07-02)

Retarget doc approved by Dave ("yes. Then run it.") — R0 executed same day.
Full status in `plan/RETARGET-2026-07-02.md` (see "R0 execution status" section).

DONE this session (all live, workers + tgw-http restarted, tests green):
- Bulk aspects: `tgw warm-ebay-aspects` — all 15,105 categories cached via the
  untouched 100/day bulk pool; UI aspect lookups need ZERO live Taxonomy calls;
  operator testing UNBLOCKED today (verified live against 429'd pool).
- `tgw.quota` budget layer at every metered choke point: counters, background
  70% halt + 30-min post-429 stand-down, 429s = logged incidents with caller
  identity (var/log/quota-incidents.jsonl), new `quota` health check. Caught
  181 real 429s (ebay_draft/ebay_upload churn) within minutes of deploy.
- Worker 429/quota errors now TRANSIENT-requeue, never dead-letter.
- `tgw ops-digest` — morning one-screen: health flags, quota, dead-letter
  deltas, restart flags, stale inbox.
- 6 naive datetime sites fixed; invariant E6 (UTC storage) added.
- FOUND: test suite rot — 236 errors/11 fails pre-existing (todo #1102).

NOT yet committed to git (Dave controls commits). Open next: R2.2 digest on web
UI home, R2.3 push-on-red, R3 plan diet, R1 live-fires (listeditor price delta,
action console operator test), 3,239 ebay_draft dead-letter bulk requeue
(awaiting Dave's go), test-suite repair #1102.

## Addendum (afternoon): PRIME DIRECTIVES + raw eBay capture

Dave's core frustration addressed structurally: standing requirements were being lost
across sessions because they lived as conversation/plan prose. Changes:
- CLAUDE.md now opens with 5 PRIME DIRECTIVES (preserve all data; act on alarms;
  implement as specified; live-verified = done; encode new directives immediately).
- Raw eBay response capture at the client fence (capture_response(), all three doors:
  REST/Trading/EPS) → data/eBayCapture/YYYY-MM-DD.jsonl.gz. Invariant E7. Live-verified.
- Memory: feedback-prime-directives.md.
- BLOCKED (needs Dave): thermal PreToolUse hook (.claude/settings.json + hooks/
  thermal-gate.sh) — harness denied agent self-modification; Dave must create it or
  grant permission. Script content is in the session transcript.

## Addendum 2: data-first plan redraw EXECUTED (Dave: "Redraw our plan how you just specified")

- Master plan redrawn to 264 lines, organized around the data axiom (dataset assets
  table first, verbs as tracks that grow/refine/act on it). Old 6,060-line plan split
  byte-exact: PP designs → plan/pp/ (22 files), other sections → plan/archive/sections/,
  full pre-redraw copy → plan/archive/TGW-Master-Plan-2026-07-02-preredraw.md.
- handoff.md rewritten as rolling doc (105 lines, last-2-sessions + risks, hard cap
  150); old handoff archived.
- tgw plan check: all clear before and after (all 11 open-todo pp_refs kept as
  headings; zero plan_anchors were in use).
- Data Charter: reference/TGW-Data-Charter.md; CLAUDE.md Prime Directive 1 updated
  with the axiom; todos #1103 (dataset-growth digest lines) #1104 (enforce E5) seeded.
