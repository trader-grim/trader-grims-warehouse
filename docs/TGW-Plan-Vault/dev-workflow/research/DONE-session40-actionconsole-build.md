# DONE — session 40: PP-ACTIONCONSOLE-001 build (todo #1085) — LANDED, pending operator review

Final state: build complete and live on tgw-http. Late addition per Dave's review:
Inventory Record separated to the top (standalone, own redesign effort later); the
designed draft/live workflow sits below in its own bordered "eBay Listing" block
(action line + Editor/Live tabs). All 4 state pages verified 200; ruff clean. Also
fixed a latent `(dl or {{}})` f-string set-literal crash — items without drafts 500'd.
NEXT: Dave tests → iterate per ship-and-adjust; then revision-apply live-fire
(todo #1084, one low-stakes price-only delta); ops surface for the relocated
troubleshooting buttons still needs a home. Clipboard: new todo #1086 conceptual
planning pass GATES #1055 rofi picker (Dave's direction, recorded in master plan).

Building the state-driven item detail redesign per the settled design in master plan
PP-ACTIONCONSOLE-001. Scope: one action line (state-morphing primary button, Approve
toggle, End Listing, Reset Draft dual-semantics, Archive/Delete always), Editor/Live
tabs (Live tab = graduated eBay Live Data dropdown content + View-on-eBay link; sold →
Sold Listing tab front + Relist), remove pipeline breadcrumb, merge pricing history to
one left-column display + dedupe comps, contextual repair buttons on jobs trail
(dead-letter Retry, zero-clutter guarantee). Phase 2 revision apply landed earlier
this session (_APPLY_ENABLED=True, live PUT path, 74 tests pass, tgw-http restarted).
File: src/tgw/http_server.py item detail (~lines 4400-5350).
