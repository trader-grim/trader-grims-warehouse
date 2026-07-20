# Review: 1565 clip-secret-exclusion
status: cleared
Reviewer: Claude (main session, post-power-outage resume, 2026-07-19)

## Checked against
- Spec: `tgw todo brief 1565` (no written packet doc for this todo — the
  generated brief is the authoritative spec; task line: "exclude
  password-manager-hinted (x-kde-passwordManagerHint MIME) and API-key/
  secret-shaped content from persistent clip history").
- Result manifest: `docs/TGW-Plan-Vault/plan/packets/results/1565-RESULT.md`.
- Branch diff: `git diff catio-nix-0.0.1-alpha todo/1565-clip-secret-exclusion`.
- Relevant invariants: E12 (branch-per-task — followed, worktree
  `/opt/TGW/var/worktrees/1565-clip-secret-exclusion`); C11 spirit (a
  skip is a finding, not silent) — satisfied without contradicting the
  feature's own goal: `process_change()` returns `{ok:true, skipped:true,
  reason:'password_hint'|'secret_pattern'}` and logs the skip, but the log
  line carries only `selection`, never the flagged content itself — correctly
  avoids the perverse outcome of "logging the secret to prove we didn't
  store the secret."

## Findings
- Both spec items implemented: MIME password-hint check (Wayland via
  `wl-paste --list-types`, X11 via `xclip -o -t TARGETS`, both wired into
  their capture path before `record_clip()`), and `looks_like_secret()`
  content heuristic (prefix allowlist + entropy fallback on token-shaped
  strings), both gating `process_change()` before persistence.
- Both declared deviations are within the spec's own explicit tolerance: the
  brief left X11 approach open ("if substantially more invasive... ship
  Wayland-first and flag the gap") and the implementer judged `xclip -t
  TARGETS` not to meet that bar, shipping X11 fully rather than skipping it —
  reasonable judgment call, not scope creep since it's still the same
  feature (secret exclusion), same file, same function shape as the existing
  `_read_selection_content` pattern. Entropy threshold left at the spec's
  suggested starting point (4.0), with a documented rationale (test corpus
  classified correctly at that value).
- No file touched outside `clip.py`/`clipd.py`/their tests. No config/secrets/
  eBay-scope files touched.
- Live evidence is real observed output: live `x-kde-passwordManagerHint`
  MIME set via `wl-copy` on this session's actual Wayland clipboard,
  `_has_password_hint()` and `process_change()` exercised against it live
  (not mocked), SKU/secret/ordinary-text classification all confirmed against
  live capture, live clipboard cleared afterward. Scratch DB used for
  persistence checks — the real production `history.db` was correctly never
  touched from this unreviewed branch. X11 path is unit-tested only (no live
  X11 session reachable here) — flagged plainly in the manifest, not
  overstated as live-verified.
- Coordination note in the manifest (checked #1563's branch diff before
  starting, confirmed no logical overlap even though both touch
  `clip.py`/`clipd.py`) is accurate — confirmed independently in this review:
  #1563 touches schema/`deliver_clip`/CLI/MCP/rofi-picker; #1565 touches
  `process_change`'s skip logic and the two backends' `_has_password_hint`.
  No line-level conflict expected on merge of both.

## Trigger check
None of the step-3 out-of-control triggers fired. No fix attempts needed.

## Outcome
Cleared for stitch. Manifest status "done" is accurate — no open acceptance
items deferred to Dave, unlike #1563. Stitcher merging both branches should
expect a straightforward textual merge in `clip.py`/`clipd.py` per the
manifest's own coordination note.
