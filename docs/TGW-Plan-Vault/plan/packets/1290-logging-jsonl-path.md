# Packet: JSON log path no longer collides with the main log file
Todo: #1290   PP: PP-COHESION-001   Track: framework batch (PP-HERMES-EA-001)

## Context budget (ALL the model may load)
This packet + `src/tgw/logging.py` (`setup_logging()` only, and its
existing test file if one exists) + the todo brief (`tgw todo brief 1290`).
Nothing else.

## Spec
At `src/tgw/logging.py:149`:
```python
json_path = log_root / (filename.replace('.log', '.jsonl') or 'tgw.jsonl')
```
`str.replace()` is a no-op (returns the original string unchanged, still
truthy) when `filename` has no `.log` substring — so when a caller passes
`log_file` without a `.log` extension, the `or 'tgw.jsonl'` fallback never
fires and `json_path` ends up identical to `file_path` (the main
human-readable log file), so both handlers silently write into the same
file.

Fix: derive the JSON filename explicitly —
```python
if filename.endswith('.log'):
    json_filename = filename[:-len('.log')] + '.jsonl'
else:
    json_filename = 'tgw.jsonl'
json_path = log_root / json_filename
```
No other behavior change. `filename` ending in `.log` still produces the
matching `.jsonl` sibling; anything else falls back to `tgw.jsonl` as
originally intended.

## Dataset
None — this is logging infrastructure, not a data write path.

## Out of scope
- Any other part of `setup_logging()` (level, rotation, formatter, etc.).
- Any caller of `setup_logging()` — none should need changes; this only
  fixes what filename gets chosen when `json_file=True`.

## Acceptance (live)
1. Call `setup_logging(component="x", log_file="custom", json_file=True)`
   (no `.log` extension) → resulting JSON handler's path must be
   `tgw.jsonl`, NOT `custom` (must NOT equal the main log file's path).
2. Call `setup_logging(component="x", log_file="custom.log", json_file=True)`
   → JSON handler's path must be `custom.jsonl`.
3. Confirm the two handlers in both cases point at two distinct files on
   disk (not the same inode/path).

## Quota/risk
None.
