# DONE — #1044 PP-DOCLIB-001 design pass

Audited the real scatter: 6 locations with no shared taxonomy
(`dev-workflow/research/` 38 files mixed with DONE-* breadcrumbs,
`gemini/` 7, `perplexity/` 8, `reference/research/` 8 with truncated
browser-export titles, `inbox/archive/` 51, `plan/pp/` 26 canonical
PP-*.md design docs). Also found `docs/ai-plans/` is a second,
overlapping PP-design-doc location (some PP items exist in both places).

Design doc: `docs/ai-plans/pp-doclib-001.md`. Proposes collapsing to four
buckets (source research under `research/<source>/`, PP design docs
staying at `plan/pp/`, session/activity logs, inbox), a `[[wikilink]]`
cross-reference convention (Obsidian renders these natively, already the
vault format), and a thin `tgw docs search/show/recent` CLI that wraps
today's recoll index (#1066) rather than building new search — retrieval
is already solved, only taxonomy + a friendly CLI face were missing.

Planning only, no code/file moves yet (this session's request scope was
the design pass — matches the `/tgw-plan` skill's contract). Four open
questions flagged for Dave: migration timing/ownership, the two
sessions/ directories, docs/ai-plans/ retirement, and whether to build
markmap auto-regeneration now or defer.
