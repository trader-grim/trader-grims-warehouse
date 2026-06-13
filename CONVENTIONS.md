# TGW Conventions (Aider / agent session guide)

Read **CLAUDE.md** first — this file adds the non-negotiables that apply to every agent session.

---

## Settled architecture — do not relitigate

- **tgw-api is the fence.** All ItemData reads and writes must go through it. Never construct
  `ItemData/<SKU>/<SKU>.json` paths directly in worker or CLI code.
- **One folder per SKU.** `ItemData/<SKU>/<SKU>.json` + media files alongside it. SKU format:
  `tgwYYYYMMDDHHMMSSmmm`.
- **PostgreSQL is the work ledger.** Workers inherit from `QueueWorker`; enqueue via
  `state_machine.enqueue_job()`. Never write worker state to flat files or ad-hoc DB tables.
- **Workers are thin.** Call tgw-api; never contain business logic or build ItemData paths.
- **Catalog rebuild is always a job.** Never call `build_all_catalogs()` inline — always
  enqueue it as a `catalog_rebuild` job via the state machine.
- **Secrets from `secrets_root`.** Use `cfg['secrets_root']` to locate secrets; never
  hardcode `/opt/TGW/secrets/` or any absolute path in `src/`.

---

## Output contract

Every `tgw` command function must return a plain `dict` with at minimum:

```python
{"ok": True, ...}   # on success
{"ok": False, "error": "reason"}  # on failure
```

The CLI dispatcher serialises this to JSON and exits. No exceptions should escape the top-level
command handler. Match this shape in every new command; check it in tests.

---

## Hard prohibitions

Never do any of the following, regardless of what the task asks:

- Modify `tgw-api-config.json`, any file under `/opt/TGW/secrets/`, or any `.env` file
- Add, change, or remove eBay OAuth scopes in any config, code, or comment
- Modify the eBay token refresh logic or token storage paths
- Call `build_all_catalogs()` inline — always enqueue as a job
- Construct ItemData file paths in worker or CLI code — call tgw-api instead
- Add any speculative feature, refactor, or cleanup beyond the specified task

---

## Test constraints

- Every new behavior needs a test; tests must pass offline (`pytest -q`).
- Mock all external I/O: HTTP calls, database access, filesystem writes outside `tmp_path`.
- Do not make live API calls or connect to real eBay, Discogs, or any external service.
- If you cannot write a passing offline test for a requirement, stop and explain why.

---

## When to stop

If a requirement is impossible as specified, or completing it would require touching anything in
the prohibitions list, **stop and explain** rather than improvising, approximating, or working
around the constraint.
