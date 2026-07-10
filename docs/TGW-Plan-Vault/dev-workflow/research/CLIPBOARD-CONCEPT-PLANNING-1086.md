# DONE — todo #1086: PP-CLIP-001 conceptual planning pass

Ran the /tgw-plan pass Dave's gate required before any further clipboard
tooling. Output: `docs/ai-plans/clipboard-concept.md`.

**Key finding:** PP-CLIP-001's own Phase 3 ("Unix socket endpoint in
tgw-clipd + lan-mouse hook scripts") and PP-EVENTD-001 (the Go `clip-route`
daemon) describe the SAME cross-machine-sync job twice, with two different
implementations. Recommended resolution: split cleanly by scope —
tgw-clipd stays local-only forever (history, SKU detection, feeds the rofi
picker via its existing CLI contract); all cross-machine/event-routing work
moves entirely to PP-EVENTD-001's `clip-route` (Go), triggered directly by
the lan-mouse hook, never routed through tgw-clipd. This unblocks #1055
(rofi picker) immediately — it only ever depended on the already-stable
`tgw clip` CLI, confirmed to be unaffected regardless of how cross-machine
sync gets built.

**Inbox research checked and found empty:** the "linux universal lan
clipboard manager" Google Search HTML/PDF capture is a client-side-rendered
"AI Mode" SERP with no static result content at all (confirmed via
structural extraction — zero `<h3>`/`<cite>`/result-hrefs). Nothing to
incorporate; flagged in the plan doc for Dave to decide whether to redo
the capture or drop the research step.

Planning only — no source code changed. Left `pp/PP-CLIP-001.md`'s Phase 3
line untouched pending Dave's review/confirmation of the split (the plan
doc's own acceptance criteria gates that edit on his sign-off, not this
pass). Full test suite unaffected (no code touched).
