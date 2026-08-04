# Result: 1565 clip-secret-exclusion
Status: done
Todo: #1565   PP: PP-CLIP-001

Files touched:
- `src/tgw/clip.py` — added `looks_like_secret()` content heuristic (prefix
  allowlist + Shannon-entropy fallback on token-shaped strings), `_shannon_entropy()`
  helper, `_SECRET_PREFIXES`/`_TOKEN_SHAPE_RE`/`_ENTROPY_THRESHOLD` constants.
- `src/tgw/clipd.py` — `process_change()` gained a `password_hint` parameter and
  now checks `looks_like_secret()`; both checks happen after the dedup-tracking
  update but before `record_clip()` is called, and both return
  `{'ok': True, 'skipped': True, 'reason': ...}`. Added
  `WaylandBackend._has_password_hint()` (queries `wl-paste --list-types`
  [+ `--primary`]) and `X11Backend._has_password_hint()` (queries
  `xclip -o -selection <sel> -t TARGETS`), both wired into their respective
  `run()`/`_run_watcher()` capture paths before persisting.
- `tests/test_clip.py` — table tests for `looks_like_secret()` (one case per
  documented prefix + a synthetic high-entropy fallback, all skipped) and
  safe-content cases (TGW SKU, sentence, URL, low-entropy repeated string,
  empty, embedded-in-prose) that must NOT be flagged; entropy helper unit tests.
- `tests/test_clip.py`/`tests/test_clipd.py` — updated one existing Wayland
  fake (`fake_process_change`) to accept the new `password_hint` kwarg (no
  behavior change to what it asserts).
- `tests/test_clipd.py` — new tests: `process_change` password-hint skip +
  dedup-still-advances + no-hint-persists-normally + secret-pattern skip;
  `WaylandBackend._has_password_hint` (offered/absent/primary-flag/missing-binary)
  + one full `_run_watcher` integration test proving a password-hinted entry in
  a two-entry stream is skipped while the next ordinary entry persists;
  `X11Backend._has_password_hint` (offered/absent/missing-binary).
- `docs/TGW-Plan-Vault/inbox/claude/INPROGRESS-1565-clip-secret-exclusion.md`
  (breadcrumb, written under the worktree path).

Live evidence:
This tgw-prod session has a genuinely live Wayland desktop session reachable
from the worktree (`$WAYLAND_DISPLAY=wayland-1`), so full live verification
was possible, not just direct-call simulation:

1. `wl-copy -t x-kde-passwordManagerHint 'hunter2LiveTest'` set the real
   system clipboard with the password-manager MIME hint present (confirmed
   via `wl-paste --list-types` -> `x-kde-passwordManagerHint`). Calling
   `WaylandBackend()._has_password_hint('clipboard')` against this *live*
   clipboard returned `True`, and `process_change('hunter2LiveTest', ...,
   password_hint=True)` returned `{'ok': True, 'skipped': True, 'reason':
   'password_hint'}` with `clip.list_history()` on the scratch db staying
   `[]` (nothing written).
2. `wl-copy 'ordinary live clip text 12345'` (no hint) -> live
   `_has_password_hint('clipboard')` returned `False` -> `process_change()`
   persisted it normally (`{'ok': True, 'id': 1, ...}`).
3. A real TGW SKU (`tgw202601011200000`) passed through `process_change()`
   persisted normally (`{'ok': True, 'id': 2, 'is_sku': True, ...}`) —
   confirms SKUs are never misclassified as secrets.
4. A GitHub-token-shaped string (`ghp_aB3xQ9zT1kLmN7pR5sV8wY0cD2fH4jK6mZ1`,
   no password hint) was correctly excluded by the content heuristic alone:
   `{'ok': True, 'skipped': True, 'reason': 'secret_pattern'}`.
5. Final scratch-db history contained exactly the ordinary text row and the
   SKU row — the password-hinted content and the secret-shaped content never
   appeared. Cleared the live clipboard afterward (`wl-copy --clear`).

This used a scratch SQLite db (`/tmp/claude-.../scratchpad/clip-live-test/history.db`,
not the daemon's real `~/.local/share/tgw-clip/history.db`) via direct calls to
`WaylandBackend._has_password_hint()` / `clipd.process_change()`, per the
packet's stated fallback ("call `process_change()` directly with realistic
inputs if a live desktop session isn't reachable") — except a live desktop
WAS reachable here (unusual for a worktree — this is tgw-prod's own Sway
session), so the MIME-hint check ran against the real live clipboard/wl-paste,
not a mock. The actual `tgw-clipd` systemd daemon was NOT restarted/exercised
end-to-end (that would touch the real persistent history db and the running
service, out of scope for a worktree change not yet reviewed/merged) — flagging
this distinction explicitly per the project's standing rule against
hand-waving live verification.

Full offline suite: `LD_LIBRARY_PATH=$NIX_LD_LIBRARY_PATH
PYTHONPATH=<worktree>/src:$PYTHONPATH pytest -q tests/test_clip.py
tests/test_clipd.py` -> 86 passed (56 pre-existing + 30 new), confirmed
running against the worktree copy (`tgw.clip.__file__` resolved under
`/opt/TGW/var/worktrees/1565-clip-secret-exclusion/`, not the shared checkout).

Deviations from spec:
- X11 password-manager-hint check implemented via `xclip -o -selection <sel>
  -t TARGETS` (lists offered target names) rather than a native
  Xlib ConvertSelection/SelectionNotify round-trip. The spec explicitly
  allowed this: "if that turns out to be substantially more invasive than
  the Wayland path, it's acceptable to ship Wayland-first and flag the X11
  gap explicitly." I judged the xclip-TARGETS route to be the "reasonably
  direct way" the spec asked to try first — it reuses the exact subprocess
  pattern `_read_selection_content` already uses, no new dependency, no new
  atom-protocol code — so X11 support shipped, not flagged as a gap. Not
  live-verified against a real X11 (non-XWayland) session in this task,
  since the reachable live desktop here is Wayland/Sway; the X11 path is
  covered by mocked unit tests only (`test_x11_has_password_hint_*`).
  Flagging this as a live-verification gap for a genuine X11 session, since
  Prime Directive 4 requires calling this out plainly rather than treating
  mocked coverage as equivalent to live evidence.
- Entropy threshold left at the spec's suggested starting point (4.0
  bits/char) — not tuned further, since the provided test corpus (real
  provider-key shapes vs. sentences/URLs/SKUs/low-entropy strings) all
  classified correctly at that threshold with no false positive/negative
  observed.
- `_SECRET_PREFIXES` allowlist order matters slightly (`sk-ant-` checked
  before `sk-` since `sk-ant-...` also starts with `sk-` — either order
  would still flag it as secret so this doesn't change behavior, just
  documented for clarity).

Out-of-scope findings filed: none. No new operational friction, no adjacent
bugs noticed in this area beyond what's already covered by todo #1563 (which
I did not touch — see below).

Coordination with todo #1563: #1563 (clip-agent-delivery) already has a
complete, committed branch (`todo/1563-clip-agent-delivery`, clean working
tree) that also touches `clipd.py`/`clip.py` — it adds `origin`/`label`
columns, `deliver_clip()`, and rewrites `launch_rofi_picker()`'s id-based
lookup. None of that overlaps the code this task touched
(`process_change()`'s dedup/skip block, `WaylandBackend._run_watcher`,
`X11Backend.run`) — confirmed via `git diff catio-nix-0.0.1-alpha` on
#1563's branch before starting. No merge conflict encountered because this
task's worktree branched independently off the same base
(`catio-nix-0.0.1-alpha`); whoever stitches both branches together should
still expect a straightforward line-level merge in `clip.py`/`clipd.py`
(no logical conflict) but that merge itself is the stitcher's job, not
resolved here.
