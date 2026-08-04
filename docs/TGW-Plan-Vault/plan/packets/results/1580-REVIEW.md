# Review: todo #1580 — agent-trace-phase1

Status: cleared
Reviewer: Claude (main session)
Branch: `todo/1580-agent-trace-phase1` @ `f0907f4` (1 commit ahead of `catio-nix-0.0.1-alpha`, 0 behind)

Checked against `docs/TGW-Plan-Vault/plan/packets/1580-agent-trace-phase1.md`'s Spec/
Out-of-scope/Acceptance sections and the 1580-RESULT.md manifest.

- Spec item 1 (`agent_runs` table, in-code self-apply DDL): confirmed — `_AGENT_RUNS_DDL`
  + `_ensure_agent_runs_table()` mirrors `_ensure_ai_usage_table()`'s pattern exactly,
  all columns/types/CHECK values match the packet verbatim. `schema.sql` copy added with
  the explicit drift-warning comment the packet required (and retroactively applied to
  the pre-existing `ai_usage` block too — a reasonable, in-scope documentation addition,
  not scope creep).
- Spec item 2 (`start_agent_run`/`end_agent_run`): confirmed, signatures match.
- Spec item 3 (`tgw trace start`/`end` CLI): confirmed — flags match packet exactly,
  `start` prints only the run_id to stdout as specified, `--host`/`--git-branch`
  best-effort auto-detect via `_detect_host()`/`_detect_git_branch()`, both verified
  never-raise (bare `except Exception: return None`).
- Spec item 4 (`archive_transcript()`): confirmed — atomic temp-file-in-same-dir +
  `os.replace()` (E5), raises `FileNotFoundError` on missing source (not a silent
  no-op), directory auto-created, per-run_id files don't clobber each other.
- Spec item 5 (unit tests): 26 new tests in `tests/test_agent_trace.py`, independently
  re-run from the worktree — 26/26 pass. Full offline suite independently re-run —
  2686 passed, 1 skipped, matching the manifest's claimed numbers exactly.
- Scope: all 6 touched files (`state_machine.py`, `schema.sql`, `logging.py`, `api.py`,
  `test_agent_trace.py`, the INPROGRESS breadcrumb) are within the packet's declared
  area. Phase 2/3/4 work (Obsidian render, `/form/runs` UI, hooks/skill) correctly not
  touched.
- Deviations (both flagged in the manifest, not silently present): `get_agent_run()`
  added as a minimal read-back helper — reasonable, needed by the round-trip tests and
  by Phase 2/3 regardless. `end_agent_run()` raising `ValueError` on an unknown
  `run_id` — a real live-reproduced silent-no-op bug (UPDATE matching zero rows
  reporting success) caught during acceptance testing and fixed inline, explicitly
  C14-class reasoning cited correctly. Both are additive, not substitutions of
  anything the packet asked for.
- `git_branch` empty in the live acceptance run: correctly diagnosed as a `db`-owned
  worktree vs `tgw`-user git-ownership-guard artifact of the acceptance environment,
  not a code defect — `_detect_git_branch()`'s never-raise contract is exactly what
  made this fail soft instead of crashing the command. No action needed.
- Invariants: E5 (atomic write, `archive_transcript`) confirmed satisfied. Prime
  Directive 1 / Data Charter raw/derived split correctly reflected in code comments
  and the permanent-retention (no TTL) behavior.

No trigger fired. Cleared for stitch.
