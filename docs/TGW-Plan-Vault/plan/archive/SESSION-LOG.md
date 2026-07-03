# Session log archive (rolled off handoff.md's 2-session window)

## Session 41 — 2026-07-02 (quota drains, data-preservation bugs, google_direct)

Committed: `a7e7439`, `f511f2d`, `d1cad9a`. Full detail:
`dev-workflow/research/SESSION41-wrapup.md` + `archive/handoff-v5-2026-07-02-preredraw.md`.

- eBay quota drains fixed (QA-telemetry call removed; 25707 fallback capped 24h;
  tree-cache auto-expiry removed; warm-up gated to pre-reset window).
- `google_direct` LLM provider live (free-tier Gemini verified); ai_identify/alt_text/
  ebay_draft/bulk_classify moved to it; OpenRouter auto-fallback.
- ebay_draft aspect-fill now vision-based (up to 10 photos).
- Data-preservation bugs: price reducer never persisted reductions (silent revert on
  re-stage) — fixed; `atomic_write_json` reverted shared-file perms to 0600 — fixed;
  vault permission drift root-caused (stale deployed script + the above).
- `tgw-clipd` crash loop (15,769 restarts) fixed; UTC-as-local timestamp display fixed
  (13 sites).
