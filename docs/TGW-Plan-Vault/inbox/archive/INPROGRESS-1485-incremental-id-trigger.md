# In progress: #1485 PP-INTAKE-004 Phase 1a — backend incremental-ID trigger

Working in worktree `/opt/TGW/var/worktrees/1485-incremental-id-trigger` on
branch `todo/1485-incremental-id-trigger`. Task: `http_server.py`'s photo
append path (`POST /api/items/{sku}/append` op=photo) should, after
appending, check running photo count and — the first time it crosses
`ai_identify.py`'s `_MAX_PHOTOS_CLOUD` threshold — enqueue `ai_identify` if
it hasn't already run for the SKU. Separately, wire session-completion
signal to set `ai_reidentify: true` so a refinement pass runs if more
photos land after the early fire. Backend-only, no Kotlin app work. Reading
`http_server.py` append_item and `workers/ai_identify.py` first to verify
live: actual `_MAX_PHOTOS_CLOUD` value, actual result-field name written by
ai_identify, and actual ai_reidentify read/enqueue mechanism, before writing
code.
