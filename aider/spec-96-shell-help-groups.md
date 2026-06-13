You are working in the Trader Grim's Warehouse (TGW) repo at
`/opt/TGW/src/trader-grims-warehouse`. Read CLAUDE.md and CONVENTIONS.md before
making any changes; do not deviate from them.

Task: todo #96 — PP-SHELL-001 Tier 3: grouped `tgw --help` + `requeue-identify` rename

---

## Background

`tgw --help` currently lists ~65 subcommands in a single flat block, making it hard to
scan. The `requeue` command is ai_identify-only but has a generic name.

---

## Change 1 — Group `tgw --help` subcommands (src/tgw/api.py)

Modify `_build_parser()` so that `tgw --help` shows commands in labeled sections.

argparse does not natively group subcommands. The simplest correct approach is to add
a structured `epilog=` string to `ArgumentParser(...)`. The epilog lists each command
under a section heading; the existing auto-generated subcommand list remains above it.

Suggested groups (use these exact headings):

```
Read / Search
  get, list, search, resolve, locate, quiet-check, reprice-suggest, seo-audit,
  catalog-verify, velocity-report, report, ebay-sweep, dead-letter

Write / Update
  update, update-where, update-title, update-location, update-verified,
  update-status, set-template, set-shipping, enqueue-sku, resolve-legacy,
  mvitems, update-size-class, data-scrub-size-class, data-scrub-pass1

Pipeline
  staged, ready, publish, requeue-identify, sync-conflict

eBay
  ebay-pull, import-sold-csv, setup-ebay-hooks, store-categories, store-category,
  restart-ebay-token, get-ebay-token, ebay-sync-config

Catalog / Build
  build-full, build-search, build-locations, build-full-csv, build-search-csv,
  build-sqlite, build-all, ensure-catalog, build-archive-index, history-index,
  export-catalog, category-groups, alt-text, alt-text-batch

Ops / Admin
  health, todo, brief, suggest, note, btw, plan-render, perp-run,
  whisper-suggest, claude-help, clip, picklist, print-label

(Deprecated aliases are not listed in the epilog — they remain functional but unlisted.)
```

The epilog must use `argparse.RawDescriptionHelpFormatter` (already set) so indentation
and newlines are preserved. Add it as a trailing argument to `ArgumentParser(...)`:
```python
parser = argparse.ArgumentParser(
    ...
    epilog=_HELP_EPILOG,  # module-level constant string
)
```

---

## Change 2 — Rename `requeue` to `requeue-identify` (src/tgw/api.py)

The current `requeue` subcommand only re-queues `ai_identify`. Rename it:

- Primary name: `requeue-identify` (the canonical command)
- Deprecated alias `requeue`: still functional, but prints a deprecation warning to
  stderr before executing. Look at how `titleupdate` → `update-title` is handled for
  the alias pattern; apply the same approach.

The dispatch handler for `requeue` / `requeue-identify` stays the same. Only the
registered name and the alias change.

---

## Files you may modify

- `src/tgw/api.py` — `_build_parser()` and the `requeue`/`requeue-identify` dispatch
- `tests/test_shell_help.py` — new test file

---

## Requirements

1. `tgw --help` output contains at least three of the group headings (e.g. "Read / Search",
   "Write / Update", "Pipeline").
2. `requeue-identify` is registered in the parser and dispatches identically to the current
   `requeue`.
3. `requeue` (deprecated alias) is also registered and produces the same result, but prints
   a deprecation notice to stderr (one line, e.g. "Warning: 'requeue' is deprecated; use
   'requeue-identify'").
4. No other command names, arguments, or behaviour change.
5. All existing tests still pass: `pytest -q`
6. New tests in `tests/test_shell_help.py` pass offline: `pytest -q tests/test_shell_help.py`

Tests to write (at minimum):
- Parse `--help` output and assert it contains "Read / Search" and "Pipeline"
- Assert `requeue-identify` is in `_build_parser().parse_known_args(["requeue-identify",
  "--help"], ...)[0]` — or equivalent check that the subcommand is registered
- Assert `requeue` is also registered (deprecated alias present)
- Assert `requeue` dispatch prints to stderr and does not raise

---

## Do NOT touch

- Config files, secrets, or eBay OAuth scopes
- eBay API code or worker logic
- Any other command's arguments or behaviour
- Any existing test file other than adding `tests/test_shell_help.py`

If a requirement is impossible as specified, stop and explain instead of improvising.
