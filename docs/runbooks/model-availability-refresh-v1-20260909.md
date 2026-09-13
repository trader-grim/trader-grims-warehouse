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
  `models_free` / `models_paid_via_zen` / `models_go` — the last added
  2026-09-12 for the quota-priced OpenCode Go tier, which rides the same
  `opencode` CLI/binary as zen and is therefore a model list on the existing
  `opencode` entry, not a new executor) are checked this way — `manual`, the
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

## The second half: live dispatch-outcome holds (Todo 1956 slice)

Freshness (above) keeps the catalogue from going stale; **health** is tracked
live by the dispatch itself — `tgw.model_observations` (LEAF-11-8 / Todo
1956). There is no prober daemon: every session attempt in
`tgw.development.harness_session._dispatch_chain` writes one observation, and
`tgw.model_selector.select_executor` reads them back on the next turn:

- `unavailable` — the attempt raised `SessionUnavailable` (no credential /
  quota / auth / rate-limit). Holds the executor out of selection for **60
  min**.
- `error` — the attempt ran but failed for a non-availability reason
  (`SessionError` or no parseable report). Holds for **15 min**.
- `available` — the attempt produced a report. Clears any hold for that
  executor immediately.

A held executor is skipped exactly like one the availability file marks
unavailable, and the hold reason travels in `Selection.reason` (and in an
all-held ABSTAIN alongside the file's own reasons). An explicit operator pin
(`$TGW_IMPLEMENT_EXECUTOR` / `$TGW_REVIEW_EXECUTOR`) still overrides a hold,
with the reason noting it. Recording never raises into a dispatch, and the
selector only reads — `harness_session` is the sole writer.

The observations file lives at `/opt/TGW/var/log/model-observations.jsonl`
(durable, not `/tmp`; override with `$TGW_MODEL_OBSERVATIONS`, recording
gated by `$TGW_MODEL_OBSERVATIONS_ENABLED` / the `OBSERVATIONS_ENABLED` flag,
default ON). It is append-only JSON-lines (`tgw-model-observation/v1`) and
self-bounding: past 2000 lines it is rewritten to the last 1000.

To clear a stuck hold: delete the file, or append an `available` observation
for the executor (a successful dispatch does this on its own). Check what the
selector currently sees with:

```
python3 -c "from tgw import model_observations as m; print(m.recent_status('opencode'))"
```

## Manual catch-ups the daily job cannot do (2026-09-12 note)

The live sources under `tools/model-currency/model_currency/sources/`
(`groq.py`, `models_dev.py`, `openrouter.py`) structurally cannot discover
Nous, OpenCode Go, or Antigravity — config changes for those providers are a
manual/one-time catch-up (Todo 2013 did the 2026-09-12 round: DeepSeek
v4-Pro retirement + v4.1-Flash reordering, the `models_go` list, the Nous $20
ceiling TODO, the confirmed `groq/*` alternate, Antigravity explicitly
deferred for lack of a headless/CLI path), not something this job maintains.
Adding real source modules for those providers so future changes get caught
automatically is follow-on work. `cost_policy` / `research` / `note` prose —
including the new quota-vs-metered cost dimension — is never touched by a run;
only `available`, `model` hints, and `updated` move.

## The live OpenCode Go tier (Todo 2014, 2026-09-13 note)

OpenCode Go is now a second, live credential, not just a catalogue entry. The
operator completed `opencode auth login -p opencode-go` interactively, which
registered a distinct `opencode-go` entry in `~/.local/share/opencode/auth.json`
(separate from the pre-existing, still-exhausted OpenCode Zen entry), and
`opencode run --format json -m opencode-go/deepseek-v4.1-flash` returned a
correct response with real cost tracked — genuinely usable, with what the
operator describes as a decent amount of usage on a 5-hour rolling basis
(quota-priced: $12/5hr, $30/week, $60/month limits, not per-token).

Three consequences, all wired by Todo 2014:

- **Registration is interactive and operator-side only.** Dropping an API key
  into an env var is NOT enough — confirmed this session: the key alone routes
  through the exhausted zen endpoint regardless of which env var holds it.
  Only the registered `opencode-go` auth entry (copied by
  `harness_session._run_opencode` into each session's fresh HOME alongside the
  `OPENCODE_GO_API_KEY` slot) reaches Go-tier billing. Never attempt to
  auto-register `opencode auth login` from code.
- **The model id picks the billing, not the executor.** There is deliberately
  no separate `opencode-go` executor entry: `model_selector` prefer lists name
  executors (`opencode`/`claude`/`manual`, validated against the
  coding-executor catalogue), and `_run_opencode` passes the model id through
  verbatim to `opencode run -m`. The implementation role's `model.opencode` /
  `model.opencode_go` hints therefore carry the `opencode-go/...` id
  (`opencode-go/deepseek-v4.1-flash`, ahead of the free zen fallback
  `opencode_zen_free` and ahead of the `claude` escalation) — an
  `opencode/...`-prefixed id for the same model would bill through zen
  instead, so the prefix is load-bearing.
- **The daily job must not migrate the Go tier away.** The live sources still
  cannot discover `opencode-go/*` ids, so `_is_live` in
  `tgw.model_availability_refresh` counts an opencode-family hint as live when
  its bare id is on the committed `executors.opencode.models_go` slice
  (operator-verified, not source-verified). A Go-prefixed hint that is NEITHER
  live NOR on that slice still migrates to the best live opencode candidate —
  no freeze-forever. `prefer` order and `available` flags are untouched, as
  before.
