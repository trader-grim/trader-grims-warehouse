# INPROGRESS — todo #1183 (part of #1143 cohesion audit): http_server.py subsystem

Continuing the staged full-codebase cohesion+correctness audit. `workers/` and
`apis/ebay/` slices DONE (see RESEARCH-1143-workers-audit.md,
RESEARCH-1143-apis-ebay-audit.md). apis/ebay/ found one real security bug (#1174,
p5 — unsigned webhook forgery, flagged as first remediation priority per Dave).

This session: `src/tgw/http_server.py`, a single 9,211-line FastAPI file (all routes,
HTML rendering, and the eBay webhook receiver live here). Since it's one file, split
into 6 line-range groups aligned to function boundaries (~1500 lines each) rather than
file-groups:

- G1: lines 1-1554 (auth, Pydantic models, list_items, patch/append/ebay_write, bulk_action, item_action)
- G2: lines 1555-2280 (health, queue admin, ebay category endpoints, catalog snapshot, item delete/photo-order/assets)
- G3: lines 2281-3535 (hint trail, intake_landing/intake_form, bulk_form, todos_form, history_form, suggest_form, get_media)
- G4: lines 3535-5991 (get_thumb_noauth, condition options, _render_item_detail_html [huge], item_detail_form)
- G5: lines 5992-7438 (dashboard, activity, pm_chat/pm_action, offers, revisions, drafts, needs_review)
- G6: lines 7438-9211 (pipeline, system_info, restart_worker, system_form, home_form, links_form, docs)

Same design as prior slices: each group's agent reads CLAUDE.md + invariants.md +
_require_auth (lines 258-290, the auth boundary) first, reports
correctness/invariant/cohesion/security findings, then 3-vote adversarial refute
per finding (2-of-3 survival bar).

## Remaining subsystems queue after this

queue/state-machine, scripts/, nix flake.

No result yet — audit in flight via Workflow tool.
