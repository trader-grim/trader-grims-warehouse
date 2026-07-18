# Result: 1458 aider-task-slug-gate
Status: done
Todo: #1458   PP: PP-HERMES-EA-001

Files touched:
- src/tgw/aider_mcp_server.py
- tests/test_aider_mcp_server.py
- docs/TGW-Plan-Vault/inbox/claude/INPROGRESS-1458-aider-task-slug-gate.md (breadcrumb, worktree-local)
- docs/TGW-Plan-Vault/plan/packets/results/1458-RESULT.md (this file)

## Part 0 — live MCP discovery re-check

Ran `claude mcp list` as `db` (2026-07-17/18, this session):

```
tgw: sudo -u tgw /opt/TGW/.venvironments/tgw/bin/python -m tgw.mcp_server - ✔ Connected
tgw-aider: sudo -u tgw /opt/TGW/.venvironments/tgw/bin/python -m tgw.aider_mcp_server - ✔ Connected
```

No Anthropic 529 outage was in effect at check time — discovery is confirmed
live and working under the correct identity (`db`, not the `tgw` worker
subprocess identity that caused the earlier false-negative). This closes the
"remains unverified" item from the 2026-07-16 note in PP-HERMES-EA-001.

## Part 1 — task_slug='' fallthrough gap (the real bug)

Fixed via option (a) from the packet (preferred): `task_slug` is now a hard
requirement. `aider_run_task` rejects an empty, missing, or whitespace-only
`task_slug` outright with a clear `{ok: False, error: "task_slug is
required..."}` before any file resolution or subprocess work happens. There
is no more shared-checkout / `_REPO_ROOT` fallback path in `aider_run_task` —
every dispatch now goes through `_ensure_worktree()`, matching tgw-coder's
mandatory worktree-isolation contract.

Checked existing callers/tests first (per packet instruction): no caller in
`src/` invoked `aider_run_task` at all (it's an MCP tool, invoked by
Claude/Tigwa sessions, not by other TGW code), and the only place an empty
`task_slug` was exercised was the test suite's own default-arg calls — those
were exploiting the gap, not a legitimate use case. No `allow_shared_checkout`
escape hatch was added since no dependency on the old behavior was found.

`_ensure_worktree`'s PYTHONPATH/LD_LIBRARY_PATH env overrides (the
tgw-coder-documented worktree gotchas, todo #1374) are now applied
unconditionally in `aider_run_task` rather than being gated behind
`if task_slug:` (dead code now that task_slug is always present).

## Part 2 — preflight seam

Added `_build_preflight_context(work_dir)` in `src/tgw/aider_mcp_server.py`:
gathers the same class of summary Claude's `SessionStart` hook produces —
`docs/TGW-Plan-Vault/inbox/claude/*.md` file count + names (capped at 10
shown), and `tgw plan check`'s output — into a short Markdown block. It is
best-effort: any read/subprocess failure degrades to a short "(skipped)" /
"(could not run)" note rather than blocking the task, since this is context,
not a gate.

`aider_run_task` now prepends this block to the task prompt before writing
the Aider `--message-file`, so every Aider dispatch starts with the same
class of Plan Vault awareness a Claude session gets at `SessionStart`,
closing the awareness gap named in the packet.

## Live evidence

1. **task_slug='' now rejected** — `tests/test_aider_mcp_server.py::
   test_run_task_empty_string_task_slug_rejected`,
   `test_run_task_missing_task_slug_rejected`,
   `test_run_task_whitespace_task_slug_rejected` all assert
   `{ok: False, error: "task_slug is required..."}`. Verified passing:
   ```
   $ LD_LIBRARY_PATH=$NIX_LD_LIBRARY_PATH \
     PYTHONPATH=/opt/TGW/var/worktrees/1458-aider-task-slug-gate/src:$PYTHONPATH \
     /opt/TGW/.venvironments/tgw/bin/python -m pytest tests/test_aider_mcp_server.py -q
   27 passed in 1.29s
   ```
   Confirmed testing the worktree's own copy, not the shared checkout —
   `python -c "import tgw.aider_mcp_server as m; print(m.__file__)"` resolved
   to `/opt/TGW/var/worktrees/1458-aider-task-slug-gate/src/tgw/
   aider_mcp_server.py` before the run.

2. **Preflight context is actually wired into the Aider dispatch** —
   `test_run_task_injects_preflight_context` captures the real
   `--message-file` contents passed to the (mocked) `aider` subprocess and
   asserts the preflight block's text is present ahead of the task prompt
   text. `test_build_preflight_context_real` exercises the unpatched
   `_build_preflight_context()` against a temp `work_dir` and confirms it
   degrades gracefully (no raise) when `tgw` isn't resolvable there, while
   still returning the expected section headers
   (`Plan Vault preflight` / `inbox/claude` / `tgw plan check`).

3. **Full suite green, no regressions:**
   ```
   $ LD_LIBRARY_PATH=$NIX_LD_LIBRARY_PATH \
     PYTHONPATH=/opt/TGW/var/worktrees/1458-aider-task-slug-gate/src:$PYTHONPATH \
     /opt/TGW/.venvironments/tgw/bin/python -m pytest -q
   2520 passed, 1 skipped in 180.78s (0:03:00)
   ```

## Deviations from spec

- Chose option (a) (task_slug required, no shared-checkout escape hatch) over
  option (b) (approval-gated shared-checkout flag) — packet named (a) as the
  preferred default absent evidence of a legitimate shared-checkout
  dependency, and none was found. Flagging explicitly per contract even
  though it's the packet's own stated preference, since it forecloses a
  future "trivial one-off, no todo" use case the original docstring
  mentioned as intentional. If that use case turns out to matter in
  practice, a `allow_shared_checkout` flag (auto_commits defaulted False)
  can be added later — not built now since nothing currently depends on it.
- Preflight context is prepended to the *same* Aider `--message-file` prompt
  rather than delivered via a separate side-channel — simplest wiring that
  satisfies "closes the awareness gap," not spec'd more precisely than that
  in the packet.
- No packet file existed at `docs/TGW-Plan-Vault/plan/packets/<id>-*.md` for
  this todo — worked from the todo brief's own "Linked plan section" +
  "Constraints" content instead, which was self-contained and specced
  (cadence/acceptance/out-of-scope all present).

## Out-of-scope findings filed

None — no new adjacent issues found during this task beyond what the packet
already named.
