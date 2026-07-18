# INPROGRESS: todo #1522 — padlock base-field clear reverted by unrelated draft save (C14)

Working in worktree `/opt/TGW/var/worktrees/1522-padlock-clear-revert-fix`,
branch `todo/1522-padlock-clear-revert-fix`, off `catio-nix-0.0.1-alpha`.

Fix: `_apply_patch` in `src/tgw/http_server.py` now mirrors any direct
top-level `title`/`description` edit (including a clear) into
`draft_listing[<key>]` immediately, so the draft never holds a stale
value for the padlock auto-sync block to resurrect on the next unrelated
`draft_listing` save. Removed the `xfail` marker from
`test_c14_unlocked_description_clear_reverted_by_unrelated_draft_save` in
`tests/test_http_server.py`.

Next: run full pytest suite, confirm control test + other padlock tests
still pass, write result manifest.
