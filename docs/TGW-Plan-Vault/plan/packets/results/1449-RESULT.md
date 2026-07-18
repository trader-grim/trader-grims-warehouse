# Result: #1449 flake-guard-edit-write

Status: done (branch not merged — per packet instructions, produced as a
result manifest, merge is Dave's/reviewer's call)
Todo: #1449   PP: PP-AGENT-DISCIPLINE-001
Branch: `todo/1449-flake-guard-edit-write` off `catio-nix-0.0.1-alpha`
Worktree: `/home/db/tgw-worktrees/1449-flake-guard-edit-write`
Commit: `f3e4360`

## Pre-flight verification (invariant C11 — live, not assumed)

Confirmed the gap was real before touching anything, rather than trusting
the todo body as ground truth:

- Located the hook: `.claude/hooks/flake-guard.py` (only one file matching
  `*flake-guard*` in the repo — no separate test file existed for it).
- Located the registration: `.claude/settings.json`, `hooks.PreToolUse[0]`.
  Read live: `"matcher": "Bash"` — confirmed the matcher only ever fires
  for `Bash` tool calls, exactly as the todo described.
- Read the hook body: it reads `tool_input.command` only. A direct
  `Edit`/`Write` tool call's `tool_input` carries `file_path` (+
  `old_string`/`new_string` or `content`), never a `command` key — so even
  if the matcher somehow fired for Edit/Write, the existing regex checks
  (`nixos-rebuild switch/test`, `git commit/push` on a `tgw-flake` command
  string) would silently no-op against an empty string. Confirmed a
  compounding gap, not just a matcher omission: matcher AND body both
  needed the fix.

## What was built

- `.claude/settings.json` — `PreToolUse` matcher changed from `"Bash"` to
  `"Bash|Edit|Write"`, so the hook is now invoked for all three tool types.
- `.claude/hooks/flake-guard.py`:
  - Reads `tool_name` and `tool_input.file_path` in addition to the
    existing `tool_input.command`.
  - New check: if `tool_name` is `Edit` or `Write` and `file_path` contains
    `tgw-flake` as a path segment (regex `(^|/)tgw-flake(/|$)`, so it
    matches `/home/db/tgw-flake/...` or `~/tgw-flake/...` however the
    session's home directory resolves, on either host, and does not
    false-positive on an unrelated path like
    `/home/db/tgw-flakey-notes/readme.md`), emit the same `ask`
    `permissionDecision` the existing Bash-path checks use, pointing at the
    nix-flake-maintainer agent / commit-nix-flake skill procedure.
  - Existing Bash-only behavior (`nixos-rebuild switch/test`, `git
    commit/push` on a `tgw-flake` command string) is untouched — same
    regexes, same messages, still gated only by `cmd`, which stays empty
    for non-Bash tool calls so they can't accidentally trip the old checks.

No test file existed for this hook prior to this change (confirmed by
search), and per the packet's own allowance, a documented manual
verification was used instead of adding a pytest harness for a
hook/config-only change.

## Acceptance evidence (manual verification, hook actually firing)

Ran the updated hook directly with synthetic PreToolUse JSON payloads
piped to stdin, from inside the worktree, matching each case the fix is
meant to cover — see raw output below (exit code and stdout captured
per case).

**Blocks Edit on a `~/tgw-flake` file:**
```
$ echo '{"tool_name":"Edit","tool_input":{"file_path":"/home/db/tgw-flake/hosts/a1131/default.nix","old_string":"a","new_string":"b"}}' | python3 .claude/hooks/flake-guard.py
{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "ask", "permissionDecisionReason": "Edit/Write directly on a ~/tgw-flake file is a gated system-config mutation on tgw-prod/a1131 -- use the nix-flake-maintainer agent (.claude/agents/nix-flake-maintainer.md) or the commit-nix-flake skill procedure before proceeding. See docs/TGW-Plan-Vault/inbox/claude/INCIDENT-2026-07-16-kdeconnect-clipboard-triage-failure.md."}}
```

**Blocks Write on a `~/tgw-flake` file (different host's home path):**
```
$ echo '{"tool_name":"Write","tool_input":{"file_path":"/root/tgw-flake/flake.nix","content":"x"}}' | python3 .claude/hooks/flake-guard.py
{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "ask", "permissionDecisionReason": "Edit/Write directly on a ~/tgw-flake file is a gated system-config mutation on tgw-prod/a1131 -- use the nix-flake-maintainer agent (.claude/agents/nix-flake-maintainer.md) or the commit-nix-flake skill procedure before proceeding. See docs/TGW-Plan-Vault/inbox/claude/INCIDENT-2026-07-16-kdeconnect-clipboard-triage-failure.md."}}
```

**Edit on an unrelated file still passes (no block, exit 0, no output):**
```
$ echo '{"tool_name":"Edit","tool_input":{"file_path":"/opt/TGW/src/trader-grims-warehouse/src/tgw/items.py","old_string":"a","new_string":"b"}}' | python3 .claude/hooks/flake-guard.py
(exit=0, no output)
```

**Existing Bash `nixos-rebuild switch` gate unchanged:**
```
$ echo '{"tool_name":"Bash","tool_input":{"command":"sudo nixos-rebuild switch --flake ~/tgw-flake#a1131"}}' | python3 .claude/hooks/flake-guard.py
{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "ask", "permissionDecisionReason": "nixos-rebuild switch/test is a gated system mutation on tgw-prod/a1131 -- ..."}}
```

**Existing Bash `git commit` on `~/tgw-flake` gate unchanged:**
```
$ echo '{"tool_name":"Bash","tool_input":{"command":"cd ~/tgw-flake && git commit -m x"}}' | python3 .claude/hooks/flake-guard.py
{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "ask", "permissionDecisionReason": "git commit/push on ~/tgw-flake is a shared-infra history change across tgw-prod and a1131 -- ..."}}
```

**False-positive guard — a path that merely contains the substring
`tgw-flake` as a prefix of a different directory name must NOT trigger**
(this is why the fix uses a path-segment regex rather than a bare
substring check like the pre-existing Bash command-string checks use):
```
$ echo '{"tool_name":"Edit","tool_input":{"file_path":"/home/db/tgw-flakey-notes/readme.md","old_string":"a","new_string":"b"}}' | python3 .claude/hooks/flake-guard.py
(exit=0, no output)
```

Also confirmed: `python3 -m ast` parse of `flake-guard.py` and
`json.load()` of `settings.json` both succeed (no syntax break introduced).

**Not verified in this pass** (documented gap, not silently skipped): a
real live end-to-end firing of the hook through the actual Claude Code
PreToolUse mechanism (as opposed to invoking the script directly with a
synthetic payload) — the harness's own hooks-settings-watcher caveat means
a `/hooks` reload or session restart is needed for `.claude/settings.json`
changes to take effect in a running session, and this branch's settings.json
change hasn't been merged/reloaded into the live session yet. This is the
same open item already tracked for the SessionStart hook (see CLAUDE.md's
"Live-fire not yet confirmed" note) — flagging rather than claiming full
live confirmation.

## Diff summary

```
 .claude/hooks/flake-guard.py | 21 ++++++++++++++++++++-
 .claude/settings.json        |  2 +-
 2 files changed, 21 insertions(+), 2 deletions(-)
```

Full diff is in commit `f3e4360` on branch `todo/1449-flake-guard-edit-write`.

## Deviations from spec

None. The spec asked for the matcher extension plus path-based gating on
Edit/Write for `~/tgw-flake` paths on either host; delivered exactly that.
Chose a path-segment regex (`(^|/)tgw-flake(/|$)`) rather than the plain
substring test the existing Bash checks use, to avoid a false-positive on
an unrelated directory name that happens to start with `tgw-flake` — this
is a strictly tighter match than "contains the substring," not a
loosening, so it doesn't change what the packet asked for.

## Out-of-scope findings filed

- The still-open "live-fire not yet confirmed" gap noted above is not new
  — it's the same pre-existing settings-watcher caveat already tracked in
  CLAUDE.md/memory (`reference-hooks-settings-watcher-caveat`) for the
  SessionStart hook. No new todo filed; flagging here is sufficient since
  the existing tracked item already covers "verify hook config changes
  actually take live effect after a `/hooks` reload."
- No other TGW application code was touched. `src/tgw/` untouched, per
  packet scope.

## Next step (not taken here — reviewer's call)

Branch is ready for review/merge into `catio-nix-0.0.1-alpha` by
Dave/reviewer. Once merged, a `/hooks` reload or fresh session start is
still needed to confirm live-fire per the caveat noted above.
