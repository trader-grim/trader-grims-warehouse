# DONE — todo #1138: tgw revise --set help text dotted-path claim

Traced the actual behavior: `parse_assignments()` in `revision.py` stores
`--set` keys literally (a key like `"draft_listing.price"` is never
expanded into nested structure), and `_apply_live_revision()`'s
`_SUPPORTED_FIELDS` set only recognizes bare names (`price`, `title`,
`quantity`, etc.). A dotted-path `--set` is accepted at `revise` time
(nothing validates the field name there) but always rejected at `--apply`
time with "unsupported delta field(s)".

Chose to fix the help text rather than build dotted→bare normalization —
that would require mapping every possible dotted path to a bare
equivalent, which is real design work for a todo tagged "minor."

## Live evidence

- `pytest -q` — 2046 passed, 1 skipped (unchanged).
- `ruff check src/tgw/api.py` — clean.
