# Model-availability freshness loop — v1 (2026-09-09)

Bounded slice of the "updater/research factory" (LEAF-11-8 / Todo 1956). This
is the freshness half only: it keeps `config/model-availability.json` current
against the live model catalogue. It does not add trait-based task profiles,
wire more roles into the selector, do benchmark scoring, or feed
dispatch-outcome data back in — those are separate, later tasks.

## What it does

`tgw model-availability refresh` (equivalently `python -m
tgw.model_availability_refresh`) rewrites the availability file **in place**:

- `executors.<name>.available` flips to `false` (with a dated
  `"no live model as of <date>"` reason) when the live catalogue no longer
  reaches that executor's provider path, and back to `true` when it does.
  Only executors that actually name a provider path (`models` /
  `models_free` / `models_paid_via_zen`) are checked this way — `manual`, the
  human fallback, has no model family to confirm against a live catalogue and
  is left alone.
- `roles.<role>.model` hints for roles with `consumed_by_selector: true`
  (today: `implementation`, `review`) are recomputed every run to the best
  live candidate for each provider slot.
- Every other role's `model` hint is left as written **unless** the exact
  model id it names has fallen out of the live catalogue, in which case it is
  swapped for the best live candidate *for that slot's own executor family*
  (never a catalogue-wide best pick from an unrelated family) and the swap is
  recorded in the receipt ("minimal auto-migration"). For a closed-list
  executor such as `claude` (`executors.claude.models` is the exact id set
  the claude-code CLI accepts), candidates are restricted to that allowlist —
  a live model merely named "claude" elsewhere in the catalogue does not
  qualify.
- `roles[].prefer` order, and every `_comment` / `research` / `note` /
  `cost_policy` / `role_chart_source` string, are never touched.
- Top-level `updated` is bumped to today's date (UTC) on every run, even a
  quiet one, so the timestamp tells you the job ran.

The live catalogue comes from `tools/model-currency` (standalone, no `tgw.*`
imports) through `tgw.model_currency_adapter.live_coding_models()` — the same
adapter `tgw.model_selector` was written to expect ("when the live
availability prober lands it writes the same file on a schedule and nothing
here changes"). `tgw.model_selector` itself is unmodified by this job; it
still only reads `executors` and `roles.<role>.prefer`.

## The `freshness: frozen` opt-out

Add `"freshness": "frozen"` to any executor or role object in
`config/model-availability.json` to pin it. A frozen object is left
byte-identical by the refresh — no `available` flip, no `model` recompute or
migration, nothing. Use this for a deliberate manual override you don't want
the daily job to touch. Remove the key to hand the object back to the job.

## The daily timer

`tgw-model-availability-refresh.timer` (`OnCalendar=daily`,
`RandomizedDelaySec=1800`, `Persistent=true`) runs
`tgw-model-availability-refresh.service`, a oneshot that calls `python -m
tgw.model_availability_refresh` as `tgw-harness`. `Persistent=true` means a
missed run (host down at the scheduled time) fires on the next boot instead
of waiting a full day.

Run it by hand:

```
tgw model-availability refresh            # writes, appends a receipt
tgw model-availability refresh --dry-run  # prints the receipt/diff, writes nothing
```

## Receipts

Every run — dry or real — returns a `tgw-model-availability-refresh/v1`
receipt (`sources_live`, `sources_failed`, `changed`, `written`, ...). A real
(non-dry-run) call also appends that receipt as one JSON line to
`/opt/TGW/var/log/model-availability-refresh-receipts.jsonl` — durable
storage, not `/tmp`, so the history of what changed and when survives a
reboot.

## Offline behaviour

If every live source is unreachable (`tools/model-currency` falls back or
comes back empty), the job makes **no** `available` / `model` changes — it
only bumps `updated` and appends a receipt whose `sources_failed` explains
why. It exits 0 either way; a cold network is not a failure for a daily
timer.
