# DONE — #1055 PP-CLIP-001 Phase 2 rofi picker

Built `bin/tgw-clip-picker`: reads `tgw.clip.list_history()` directly,
SKU entries pinned to the top and tagged `[SKU]`, piped into `rofi -dmenu
-format i` (dmenu fallback if rofi absent), resolves the selected row back
to its clip id, calls `tgw clip get --id N --copy`. Matches the design
doc's exact spec (`docs/ai-plans/clipboard-concept.md` / `pp/PP-CLIP-001.md`
Phase 2 section) — no revision needed, #1086's gate confirmed cleared.

**Real bug found + fixed during live verification (Prime Directive 4):**
`tgw clip get --id N` crashed with `PermissionError` for the operator's own
user (`db`) — `load_config()` unconditionally called
`(secrets_root/"tgw-api-key.json").exists()`, and `/opt/TGW/secrets` is
`700 tgw:tgw`, so even a `.exists()` stat from a non-tgw user raises
`PermissionError` (not "file missing", an outright traversal denial). This
broke the *entire* premise of the nix wrapper's `clip` special-case ("run
as current user, not tgw") for every existing `tgw clip` subcommand, not
just this new picker. Fixed in `src/tgw/config.py` (wrap the `.exists()`
check in the same try/except the key-read already had — treat
permission-denied as "key absent", consistent with the function's existing
lenient-defaults philosophy). Added `tests/test_config_secrets_permission.py`
(2 tests). Live-verified: `tgw clip get --id 465` now returns `{"ok": true,
...}` as `db` user (previously crashed).

**Not fully live-verified:** the rofi/dmenu UI itself — this session runs
headless on tgw-prod (no display, no rofi/dmenu binary installed). The
Python row-listing + id-resolution logic was verified against real
production clip history (5+ real rows, correct SKU-pinning and id mapping).
Needs one operator pass on an interactive desktop session (a1131 or db's
own machine) to bind a keybind and confirm the rofi window itself.

Also noticed, not fixed: running pytest as the `tgw` user fails on
collection (`nix/` symlinks into `/home/db/tgw-flake`, which is
unreadable to `tgw` per the 700 `/home/db` policy) — pre-existing, unrelated
to this packet; running as `db` works fine (1795 passed). Worth a
`conftest.py` fix later (`collect_ignore` doesn't prevent the scandir
PermissionError since it fires during directory enumeration, before the
ignore filter applies).
