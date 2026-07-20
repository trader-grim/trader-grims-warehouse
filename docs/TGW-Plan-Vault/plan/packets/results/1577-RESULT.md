# Result: todo #1577 simple-llm-jobs-label-set-truthiness-fix
Status: done
Todo: #1577   PP: PP-SIMPLEJOBS-001
(follow-up to #1574/#1576, same branch/worktree; see `1576-RESULT.md` for
prior history — this is a new file rather than an edit to that one so #1576's
own evidence trail stays intact)

Files touched:
- `src/tgw/mcp_server.py` — `tgw_simple_llm_jobs`, `classify` operation's
  `label_set` handling changed from truthiness to explicit `is not None` /
  length semantics (Tigwa peer review of #1576, confirmed live by Claude):
  - **Before**: `if operation == 'classify' and label_set:` — an explicit
    `label_set=[]` was indistinguishable from `label_set` not being passed
    (both falsy), so the membership check silently skipped in exactly the
    one case it mattered most: an empty allowed-label domain can never
    yield a valid classification, yet the call still returned `ok: True`.
  - **After**, three distinct states, all before any model call is made
    for the reject case (no point spending a DeepSeek call on a request
    that can't possibly succeed):
    - `label_set is None` → open-ended classification, no membership
      check (unchanged behavior from before #1577).
    - `label_set == []` (explicitly supplied, empty) → new guard added
      immediately after the operation-validity check, before
      `call_model()` is invoked: returns `{'ok': False, 'error':
      'label_set is empty — no valid classification is possible'}`
      (matches the existing error-JSON shape/style in this function; no
      `raw` key since no model response exists yet).
    - non-empty `label_set` → membership check runs exactly as it did
      under #1576, just gated on `label_set is not None and len(label_set)
      > 0` instead of bare truthiness.
  - `schema`'s truthiness for `extract_fields` was explicitly left
    untouched per the packet's instruction (`schema={}` is harmlessly
    equivalent to "no required keys" — not the same failure class as an
    empty `label_set`, which makes the requested operation logically
    impossible).
- `tests/test_mcp_server.py` — 3 new unit tests, one per `label_set` state:
  - `test_simple_llm_jobs_classify_empty_label_set_rejected_before_model_call`
    — `label_set=[]` → `ok: False`, error contains `"empty"`, and asserts
    (via a mock call-counter) that `call_model` was **never invoked** —
    proves the reject happens before spending the DeepSeek call, not just
    that it eventually reports failure.
  - `test_simple_llm_jobs_classify_none_label_set_is_open_ended` —
    `label_set=None` → `ok: True`, any label accepted (unchanged
    open-ended behavior, explicit regression guard against a future
    change conflating `None` with `[]`).
  - `test_simple_llm_jobs_classify_nonempty_label_set_still_validates` —
    non-empty `label_set` still rejects an out-of-set label (regression
    guard that the #1576 membership check itself wasn't weakened by this
    change).
  (test file total: 76 → 79 tests)

Live evidence:
- `python -c "import tgw.mcp_server as m; print(m.__file__)"` with
  `PYTHONPATH=/opt/TGW/var/worktrees/1574-simple-llm-jobs-mcp-tool/src` and
  `LD_LIBRARY_PATH=$NIX_LD_LIBRARY_PATH` resolved to
  `/opt/TGW/var/worktrees/1574-simple-llm-jobs-mcp-tool/src/tgw/mcp_server.py`
  — confirmed testing the worktree's copy, not the shared checkout, before
  running any test.
- `pytest -q tests/test_mcp_server.py -k "label_set or classify"` (same
  PYTHONPATH/LD_LIBRARY_PATH overrides): **7 passed** — all 3 new tests
  plus the 4 pre-existing classify-related tests from #1576, none broken.
- Full suite, same overrides, `pytest -q` from worktree root: **2651
  passed, 1 skipped** (was 2648 passed/1 skipped at #1576's landing — the
  +3 delta is exactly this packet's new tests, nothing else moved).

Deviations from spec: none. Implemented exactly Tigwa's recommendation as
relayed: `is not None` / explicit length checks (not truthiness) for
`label_set`; empty list rejected fail-loud before any model call;
non-empty list keeps the #1576 membership-check logic unchanged; `schema`
line for `extract_fields` left untouched.

Out-of-scope findings filed: none new. Standing blocker carried over
unchanged from #1574/#1576 (not re-flagged as new): `tgw_simple_llm_jobs`
still needs the one-line `simple_llm_jobs` task entry added to the live
`/opt/TGW/config/tgw-models.json` (outside the git repo, outside this
worktree's/agent's edit authority per `worktree-guard`) before the tool
can be invoked for real outside of mocked unit tests.
