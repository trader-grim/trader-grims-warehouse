# INPROGRESS — session 40: PP-ACTIONCONSOLE-001 build (todo #1085) — LANDED, pending operator review

Building the state-driven item detail redesign per the settled design in master plan
PP-ACTIONCONSOLE-001. Scope: one action line (state-morphing primary button, Approve
toggle, End Listing, Reset Draft dual-semantics, Archive/Delete always), Editor/Live
tabs (Live tab = graduated eBay Live Data dropdown content + View-on-eBay link; sold →
Sold Listing tab front + Relist), remove pipeline breadcrumb, merge pricing history to
one left-column display + dedupe comps, contextual repair buttons on jobs trail
(dead-letter Retry, zero-clutter guarantee). Phase 2 revision apply landed earlier
this session (_APPLY_ENABLED=True, live PUT path, 74 tests pass, tgw-http restarted).
File: src/tgw/http_server.py item detail (~lines 4400-5350).
