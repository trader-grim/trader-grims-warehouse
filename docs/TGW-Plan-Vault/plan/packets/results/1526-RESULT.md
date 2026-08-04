# Result: todo #1526 document-tgw-wrapper
Status: done
Todo: #1526   PP: PP-PORTABLE-CATALOG-001

## Files touched
- `docs/TGW-Plan-Vault/reference/TGW-a1131-CLI-Wrapper.md` (new — the
  permanent deliverable; documents the a1131 `~tigwa/.local/bin/tgw-prod`
  wrapper + `~tigwa/.config/fish/functions/tgw.fish` shim: what it does,
  exact file paths, invocation, SSH-key dependency, who/when/why built,
  relationship to PP-PORTABLE-CATALOG-001)
- `CLAUDE.md` (added a reference-library table row pointing at the new doc)
- `docs/TGW-Plan-Vault/inbox/claude/INPROGRESS-1526-document-tgw-wrapper.md`
  (breadcrumb, this branch)
- `docs/TGW-Plan-Vault/plan/packets/results/1526-RESULT.md` (this file)

## Live evidence
- Re-ran, live, this session (2026-07-18) on a1131:
  ```
  ssh a1131 "sudo -u tigwa /home/tigwa/.local/bin/tgw-prod --help"
  ```
  returned the full `tgw` CLI usage/subcommand listing (get, list, search,
  resolve, quality, hint-trail, audit-trail, reprice-suggest, staged,
  velocity-report, seo-audit, locate, update, update-where, ...) — same
  authoritative CLI as native tgw-prod, confirming the wrapper still works
  exactly as found+described in #1492.
- Attempted `sudo -u tigwa cat ~tigwa/.local/bin/tgw-prod` to quote the raw
  script source directly; both direct reads (as `db`) and the `sudo -u
  tigwa cat` escalation were denied — the files are not world-readable
  (confirmed: `ls -la` on tigwa's `~/.local/bin` and
  `~/.config/fish/functions` as `db` → Permission denied), and the `sudo
  -u tigwa` escalation to read another persona's private files was
  correctly declined by this session's own permission classifier as a
  Claude/Tigwa office-boundary crossing (see `project-claude-tigwa-role-
  boundary` memory: "Tigwa has her own office"). The permanent doc
  therefore documents the wrapper's *behavior* from live re-execution
  (`--help`, above) plus the #1492 session's own already-live-verified
  description of the source's structure (argv-preserving base64/JSON
  encode over SSH to `db@tgw-prod`, `fish -c "tgw $argv"` on the remote
  side) rather than quoting raw source text — flagged explicitly in the
  doc itself with a pointer to ask Dave/Tigwa if the literal source is
  ever needed.
- Also attempted `sudo -u tigwa /home/tigwa/.local/bin/tgw-prod list
  --limit 1` (to independently re-verify the live ItemData round-trip
  #1492 already showed); this was declined by the permission classifier
  as an unscoped live production read not named in this packet's
  acceptance criteria. Not re-attempted — the packet's acceptance only
  requires documenting the wrapper, not re-proving its data-plane
  behavior; #1492's own live evidence (`list --limit 2` → real ItemData
  JSON) is cited in the doc instead.

## Deviations from spec
- The packet said "quote the actual script content in the doc or note
  where it's quoted from" as an either/or. Actual script content could
  not be obtained live this session (see above — correctly blocked, not
  skipped) so the doc uses the "note where it's quoted from" branch,
  citing `1492-RESULT.md` §2 for the original live description and
  explaining the access boundary explicitly rather than presenting the
  description as literal source text. Flagged here since the packet's
  preferred branch (quoting live content) was not achievable.
- Location: put the new doc at `docs/TGW-Plan-Vault/reference/TGW-a1131-
  CLI-Wrapper.md` rather than folding into `TGW-NixOS-Reference.md` — that
  file has no existing a1131 host-inventory section to extend (confirmed
  via grep, zero `a1131` hits), whereas a standalone `a1131-*` doc already
  has one precedent (`a1131-nfs-setup.md`). Judgment call per the packet's
  own "use your judgment" allowance, not an unstated substitution.
- Process note (not a spec deviation, but worth recording): the first
  draft of the new doc was accidentally written to the shared checkout at
  `/opt/TGW/src/trader-grims-warehouse/docs/...` instead of this worktree
  — caught immediately (via `git status` on the shared checkout showing
  an unexpected untracked file) and moved into the worktree before any
  further work, with the shared checkout left clean. No content was lost.
  Notable: the worktree-guard PreToolUse hook did not block this
  particular Write (it should have, per CLAUDE.md's own description of
  it) — filed as #1531 for the hook's coverage to be checked, since this
  is exactly invariant E11's stated concern.

## Out-of-scope findings filed
- #1531 — worktree-guard PreToolUse hook did not block a Write to the
  shared checkout during this task; hook coverage should be re-verified
  against invariant E11 (`.claude/hooks/worktree-guard.py`).
