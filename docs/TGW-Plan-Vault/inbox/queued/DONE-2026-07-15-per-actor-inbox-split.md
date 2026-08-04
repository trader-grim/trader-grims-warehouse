# DONE — per-actor inbox split (todo #1431, PP-HERMES-EA-001)

Dave asked for separate inboxes for Tigwa and himself — the flat shared `inbox/` had
already caused a real incident (Tigwa processing my inbox as her own CLAUDE.md Step 1,
see `pp/PP-HERMES-EA-001.md`'s "CLAUDE.md was leaking into Tigwa's contract").

**Done:**
- Created `inbox/claude/`, `inbox/tigwa/`, `inbox/dave/`.
- Moved the ~74 existing `DONE-*`/`INPROGRESS-*`/`TIGWA-*`/`RESPONSE-*` files (all
  mine or addressed to me) into `inbox/claude/`.
- Left ambiguous root-level drops untouched (`README.md`, `Untitled.base`, two
  research HTML/PDF pairs owned by `tgw:tgw` — not clearly actor-specific).
- `inbox/archive/` and `inbox/queued/` stay shared at the root (already
  cross-actor processed-history / staging areas, not actor-owned).
- Updated `CLAUDE.md` (Step 1, Step 4, Working Rules, key-paths table) to point at
  `inbox/claude/` instead of the shared root.
- Updated `AGENTS.md` to tell Tigwa/Leotha their inbox is `inbox/tigwa/`, and that
  `inbox/claude/` is explicitly not theirs to process.

**Not done / open:** `inbox/dave/` exists but is empty — no content moved there since
nothing in the sweep was clearly Dave-authored-and-addressed-elsewhere. Master plan /
PP-HERMES-EA-001.md itself still references the old flat `inbox/TIGWA-EMERGENCY-...`
path in one historical citation — left as-is (historical record, not a live pointer).
