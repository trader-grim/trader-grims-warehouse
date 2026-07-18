# PP-OPERATOR-QUEUES-001 — saved review-lens queues, browse-page chips (full review detail)

## PP-OPERATOR-QUEUES-001 — saved review-lens queues, browse-page chips

**Todo #1466, reviewed + closed 2026-07-16.** Tigwa built this same-day from a
3-sentence prompt: `src/tgw/operator_queues.py` (new), `config.py`/`http_server.py`
diffs (4 endpoints + browse chip strip), `tests/test_operator_queues.py`.
Two independent reviews filed by Claude, both read-only (no source/config/data
mutated):

- **Code review — APPROVE-WITH-NITS.** No SQL injection surface (traced full
  clause/params assembly; column allowlist matches real schema; JSON path
  validated against `^[A-Za-z_][A-Za-z0-9_]*$`), AI-draft gate real (drafts
  invisible to `GET /api/operator-queues` until activated), durable (survives
  a catalog-SQLite rebuild by construction, atomic `mkstemp`+`fsync`+`replace`
  writes). 3 low-severity nits, none blocking: (1) `source` field is
  self-declared not authenticated — matches existing app-wide convention
  (invariant C10), not a new gap; (2) `contains`/LIKE filter doesn't escape
  literal `%`/`_` — cosmetic, not an injection risk (parameterized binding);
  (3) a config-fallback branch in `_operator_queue_store()` is dead code in
  the normal `load_config()` path.
- **UI review — SHIP-INTERNAL-SLICE, not operator-complete.** Chip
  placement/interaction mirrors the existing status-chip convention well.
  Gap: a queue chip and a status chip are visually identical (same `.chip`
  class) — an operator can't tell "saved review lens" from "status filter"
  apart from reading the label, which matters because a queue can silently
  return empty when its underlying condition changes. AI-drafted queues are
  real in the backend but completely invisible in this UI (no
  discover/create/edit/activate surface — matches the packet's own stated
  scope, not an oversight). Recommended next slice: visual distinction from
  status chips, a "N pending review" badge for drafts, fix the silent
  fetch-failure pattern (shared with `loadInventoryDiff()`, same bug class
  Tigwa's field-set-boundary audit flagged separately same day).

Full review docs (superseded by this summary, not separately retained):
`inbox/claude/CLAUDE-REVIEW-OPERATOR-QUEUES-001-2026-07-16.md`,
`inbox/claude/CLAUDE-UI-REVIEW-OPERATOR-QUEUES-001-2026-07-16.md`.

