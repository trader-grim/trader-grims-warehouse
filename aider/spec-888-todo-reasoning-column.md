# Task 888 — PP-TODO-001: add reasoning level metadata to todos

## Overview

Add a `reasoning` column to `todo_items` so tasks can carry a hint about how much
reasoning/thinking is needed when executing them. Values: `high`, `normal`, `low`.
Default is `normal` (no badge shown). Used to route tasks to the right model tier
or effort level in future.

## Required changes

### 1. DB migration — add `reasoning` column to `todo_items`

In `src/tgw/todo.py`, find where the table is created / ensured and add:

```sql
ALTER TABLE todo_items
    ADD COLUMN IF NOT EXISTS reasoning TEXT NOT NULL DEFAULT 'normal'
    CHECK (reasoning IN ('high', 'normal', 'low'));
```

Apply it at startup (if `ADD COLUMN IF NOT EXISTS` is supported by the PG version,
or use `DO $$ BEGIN ALTER TABLE ... EXCEPTION WHEN duplicate_column THEN NULL; END $$;`
pattern). The migration must be idempotent — safe to run on a DB that already has
the column.

### 2. CLI — expose `--reasoning` on `--add` and `--set-meta`

In the `tgw todo` argument parser:
- Add `--reasoning {high,normal,low}` (default: `normal`) to both `--add` and
  `--set-meta` paths.
- `tgw todo --add "do something" --reasoning high` → inserts with `reasoning='high'`
- `tgw todo --set-meta 888 --reasoning low` → updates `reasoning` on item 888

### 3. Display — `[high]` / `[low]` badge in `tgw todo` listing

In the listing output function:
- When `reasoning == 'high'`, append ` [high]` after the body text.
- When `reasoning == 'low'`, append ` [low]` after the body text.
- When `reasoning == 'normal'`, show nothing (normal is the baseline).

### 4. Brief output — include reasoning when not normal

In `tgw todo brief <id>` output:
- If `reasoning != 'normal'`, add a line `**Reasoning:** high` (or `low`) after the
  task body in the brief header section.

### 5. `suggestions.py` — auto-set reasoning in `classify_batch`

In `src/tgw/suggestions.py`, the `classify_batch` function classifies suggestion
entries. Update the classification output to include a `reasoning` field, and when
writing the resulting todo items, pass the `reasoning` value through to `todo --add`.

The classification prompt should instruct the model to set reasoning based on task
complexity:
- `high` — architectural decisions, multi-file refactors, novel design
- `low` — mechanical edits, renaming, formatting, simple migrations
- `normal` — everything else (default)

### 6. Tests — `tests/test_todo.py`

Add tests for:
- That `todo --add "text" --reasoning high` stores `reasoning='high'`.
- That `todo --set-meta <id> --reasoning low` updates the column.
- That listing output shows `[high]` badge but NOT `[normal]` or `[low]` for normal.
- That `todo brief <id>` includes the Reasoning line when not normal.

## Files to edit
- `src/tgw/todo.py`
- `src/tgw/suggestions.py`
- `tests/test_todo.py`

## Constraints
- Migration must be idempotent (safe on a DB that already has the column).
- `pytest -q` must pass after changes.
- Do not change any config files, secrets, or eBay-related code.
- The CHECK constraint values must be exactly `high`, `normal`, `low` (lowercase).
