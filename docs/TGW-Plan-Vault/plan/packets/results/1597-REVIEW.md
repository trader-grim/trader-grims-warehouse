# Review: 1597 multimodel-defaults

status: cleared
reviewer: Claude (main session, tgw-runner-review)
todo: #1597   pp_ref: PP-MULTIMODEL-001

## Checked

- Spec (dispatch prompt, no formal packet file existed for this todo —
  dispatched directly via Agent tool): `defaults` block in
  `tgw-models.json`, `get_task_model()` `use_default` resolution,
  deletion of the two dead hardcoded strings, CLI help text fix, tests,
  `pytest -q` clean, `tgw health` clean, live resolution check. All
  present in the diff and result manifest, no more/no less.
- Diff scope: `git diff catio-nix-0.0.1-alpha` (not `main` — this repo's
  `main` branch is a stale ~41-commit-behind ancestor, see
  `reference-catio-nix-branch` memory; diffing against it initially
  produced a misleadingly huge diff full of unrelated already-merged
  history) shows exactly 4 files: `src/tgw/api.py`, `src/tgw/apis/llm.py`,
  `src/tgw/config.py`, `tests/test_model_routing.py` — all inside the
  packet's declared scope, nothing else touched.
- Invariant E15 (`reference/invariants.md`) directly satisfied: no
  hardcoded model ID remains in the touched files; `use_default` design
  matches what was specified with Dave live in-session.
- `ebay_draft`/`pm_chat` explicit overrides correctly left untouched, per
  spec.
- Live evidence in the manifest is real observed output (pytest counts,
  `tgw health` diff against pre-existing failures only, a live
  `get_task_model()` resolution table against the real production config
  showing identical results pre/post, a real `tgw alt-text --batch
  --limit 1 --dry-run` CLI run against a live SKU) — not "tests pass"
  alone.
- Worktree-vs-shared-checkout testing concern: manifest confirms
  `PYTHONPATH`/`LD_LIBRARY_PATH` pointed at the worktree and verifies via
  `tgw.apis.llm.__file__` — satisfies the mandatory check from this
  skill's step 1.
- Out-of-scope finding (#1599, CLI `--provider` choices missing direct
  providers) correctly filed rather than fixed inline — not a scope
  violation.

## Trigger check

No trigger fired: no spec deviation, no invariant violation, no
out-of-scope file touched, no live/production write attempted, todo/pp_ref
match, manifest complete.

## Outcome

Cleared for stitch. Not merged by this review step — that's a separate
explicit action.
