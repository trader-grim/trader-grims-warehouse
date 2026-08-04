# Result: 1563 clipboard-agent-delivery-phase0
Status: partial
Todo: #1563   PP: PP-CLIP-001

## Files touched
- `src/tgw/clip.py` — additive `origin`/`label` columns via guarded `ALTER TABLE`
  (`PRAGMA table_info` check, idempotent); `deliver_clip()`; `'deliver'` branch in
  `cmd_clip()`; `list_history()`/`search()`/`get`'s SELECTs updated to include the
  new columns; `_display_line()` helper adds `[AGENT]` tag + label to `list`/`search`
  print formatting.
- `src/tgw/clipd.py` — `launch_rofi_picker()` rewritten: feeds `f"{id}\t{display_text}"`
  per row (with `[AGENT]`/label prefix for agent rows) into rofi, splits on first tab
  after selection, looks up full content by `id` — the old
  `content LIKE '<truncated>%'` reverse lookup is gone entirely.
- `src/tgw/api.py` — `"deliver"` added to the `clip` subparser's `clip_action` choices;
  `--label` and `--requested-by` (default `"claude"`) arguments added; wired through
  to `cmd_clip()`.
- `src/tgw/mcp_server.py` — new `tgw_clip_deliver(content, label='')` tool, same shape
  as `tgw_enqueue`/`tgw_add_suggest`, registered only `if not _READONLY:` (same gate,
  same block pattern).
- `tests/test_clip.py` — `deliver_clip()` origin='agent' test; SKU classification on
  deliver; schema-migration idempotency test built against a real OLD-schema DB
  construction (not just a fresh DB); `list`/`search` surface new columns;
  `cmd_clip('deliver', ...)` ok/error-path tests; `[AGENT]` tag print test.
- `tests/test_clipd.py` — `launch_rofi_picker` tests rewritten for the id-based
  contract (old prefix-match tests replaced, not left stale); new explicit
  prefix-collision regression test (two rows sharing a 150-char prefix, confirm
  each resolves to its own distinct full content by id); `[AGENT]`/label-in-feed test.
- `tests/test_mcp_server.py` — `EXPECTED_TOOLS`/count updated (13→14, `tgw_clip_deliver`
  added); new tests: delegates-to-`deliver_clip`, no-label→None, capitalized-arg
  alias, exception→`{ok:false}`, and READONLY-gating tests (module `importlib.reload`
  with `TGW_MCP_READONLY` unset/`1`, confirming `tgw_clip_deliver` is registered only
  when not readonly — this pattern did not previously exist in the codebase for
  `tgw_enqueue`/`tgw_add_suggest` either; see Deviations).

## Live evidence
1. **Migration safety on real data**: copied the actual production
   `~/.local/share/tgw-clip/history.db` (323 rows, old pre-migration schema —
   verified via `PRAGMA table_info` showing no `origin`/`label` columns) to a
   scratch location. Ran `_connect()` against it (twice, to prove idempotency) —
   post-migration: 323 rows intact, all with `origin='clipboard'`/`label=NULL`
   (correct defaults), 0 rows lost/corrupted.
2. `tgw clip deliver "test content" --label "test"` run against that same real-data
   copy (via both the internal `cmd_clip()` call and the actual `python -m tgw.api
   clip deliver "cli test content" --label "cli-test"` CLI entrypoint) returned
   `{"ok": true, "id": 816/817, "origin": "agent", "label": "test"/"cli-test"}`.
   Row count went 323→324→... with prior rows verifiably unchanged.
3. `tgw clip list` (via `python -m tgw.api clip list`) against that same DB shows:
   `2026-07-19 17:43:11  [AGENT]  cli-test — cli test content` — tagged and labeled
   as specced, distinct from the `[   ]`/`[SKU]` real-clipboard rows below it.
4. **Rofi picker id-based-lookup fix, against the real-data copy**: `rofi` is NOT
   installed in this live Sway session (`which rofi` → not found; the session's
   actual `$menu` is `wofi --show drun`, confirmed in `~/.config/sway/config`; no
   keybind currently calls `tgw clip pick`/`launch_rofi_picker` at all — filed as
   todo #1564, see Out-of-scope findings). Full end-to-end rofi UI interaction
   could therefore NOT be exercised live — flagging this plainly per
   Prime Directive 4 rather than hand-waving it. What WAS verified live: with
   `subprocess.Popen` swapped for a fake process object simulating a real user's
   rofi selection (the only part rofi itself would have supplied — the selected
   `"id\tdisplay_text"` line), `launch_rofi_picker()` was run directly against the
   real-data DB copy and:
   - resolved the earlier-delivered real row (id 817, "cli test content") correctly
     by id.
   - two freshly-delivered rows sharing an identical 150-char prefix
     (`"Y"*150 + " TAIL-A"` / `"...TAIL-B"`, inserted into the real DB copy via
     `deliver_clip()`) each resolved to their OWN correct, distinct full content
     when selected by their respective ids — this is the direct regression proof
     for the paste-corruption bug, run against real production data, not just
     synthetic unit-test fixtures.
   - the resolved content was piped through the live `wl-copy`/`wl-paste` round
     trip on this session (`echo -n "cli test content" | wl-copy` → `wl-paste`
     returned `cli test content`), proving the "loads onto the live clipboard"
     half of the flow works on this machine's actual Wayland session.
5. `tgw_clip_deliver` registration: `TGW_MCP_READONLY` unset → tool present in
   `mcp._tool_manager._tools`; `TGW_MCP_READONLY=1` → absent. Verified both via
   direct module import (subprocess, not just pytest monkeypatch) and via the new
   pytest coverage.
6. Full test suite: `2596 passed, 1 skipped` (unchanged skip count/reason from
   before this change) under `PYTHONPATH=<worktree>/src` + `LD_LIBRARY_PATH=
   $NIX_LD_LIBRARY_PATH`, confirmed via `tgw.clip.__file__` resolving under the
   worktree path before running.
7. `sudo -u tgw tgw health` run against the live system (installed/shared
   checkout, unrelated to this branch): same failing checks as baseline
   (`backups`, `ebay_sync_fallback` — pre-existing, unrelated to clip). No new
   failures introduced by this change (this change isn't deployed/merged, so this
   only confirms no incidental interference, not a live health check of the new
   code path itself).

## Deviations from spec
- **File-location mapping in the plan doc's table was imprecise, not the actual
  code**: the plan's "Files to change" table lists `src/tgw/clipd.py` as the home
  for `cmd_clip`'s list/search print-formatting change. `cmd_clip()` (and
  `list_history()`/`search()`) actually live in `src/tgw/clip.py` — `clipd.py` has
  no `cmd_clip` function at all (it only has `handle_command()`, a different
  dispatcher for the daemon's Unix socket, plus `launch_rofi_picker()`). Implemented
  the `[AGENT]`/label tag change in `clip.py` where `cmd_clip` actually is;
  `launch_rofi_picker()`'s fix (correctly specced against `clipd.py`) stayed there.
  This is a documentation-location correction, not a scope change — same function,
  same behavior specced, just the file the plan table named was wrong for one of
  the two `clipd.py` line items.
- **`--requested-by` argument accepted but not persisted to schema**: the plan's
  schema section only specs `origin`/`label` as new columns; a `requested_by`
  schema column is explicitly listed as an unresolved Open Question ("could be
  dropped from v0"). Implemented `--requested-by` as a CLI/MCP-level argument
  (accepted, defaults to `"claude"`, matches the New-CLI-verb section's literal
  spec) but did NOT add a schema column for it, since that specific question was
  marked not-yet-asked/undecided — flagging explicitly rather than silently
  deciding either direction.
- **Retention-pruning exemption for agent-delivered rows NOT implemented**: same
  reasoning — the plan's Open Questions section explicitly says this is "leaning
  yes... but not yet asked." `deliver_clip()` runs the exact same retention/TTL
  pruning as `record_clip()` (no exemption), matching "same shape as record_clip()"
  literally. Flagging this rather than silently building the exemption, since an
  undelivered/unseen agent card could in principle age out under the existing
  2000-row/14-day caps — Dave's call, not mine, per the plan doc's own framing.
- **Live worked-example delivery of the real eBay support-ticket text — NOT
  performed.** The packet's acceptance criteria ask for delivering "the actual
  eBay support-ticket text (or an equivalent real prepared artifact)" to Dave's
  live production clip history. I do not have that real artifact in hand this
  session, and writing fabricated placeholder content into Dave's actual
  production `~/.local/share/tgw-clip/history.db` would itself violate the
  feature's own "request-initiated only, never unsolicited" design constraint
  (nothing in this session's actual invocation was Dave asking for a real ticket
  to be delivered right now) — so I did not touch the real production DB at all;
  all live verification above ran against a throwaway copy. This acceptance item
  needs Dave (or a session where he's actually asked for a real artifact
  delivered) to close out live, post-review.
- **Rofi binary genuinely absent from this live Sway session** — see Out-of-scope
  findings below; full rofi-UI live verification (criterion 3 in the packet) could
  not be performed end-to-end for that reason, only the id-lookup logic itself
  (the actual bug fix) against real data, as detailed in Live evidence #4.

## Out-of-scope findings filed
- todo #1564 (PP-CLIP-001): rofi binary not installed on tgw-prod's live Sway
  session and no keybind currently invokes `tgw clip pick`/`launch_rofi_picker`,
  despite PP-CLIP-001 Phase 2 being recorded as DONE with the picker "already
  bound to a keybind." Sway's actual `$menu` is `wofi --show drun`. Either install
  rofi, port `launch_rofi_picker()` to wofi's dmenu mode, or wire the actual
  keybind — as-is the picker (old or newly-fixed) cannot be exercised through a
  real rofi UI on this machine at all.
