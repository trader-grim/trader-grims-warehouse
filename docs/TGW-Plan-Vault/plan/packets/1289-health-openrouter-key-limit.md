# Packet: OpenRouter key-limit health check reads the current secrets facility
Todo: #1289   PP: PP-COHESION-001   Track: framework batch (PP-HERMES-EA-001)

## Context budget (ALL the model may load)
This packet + `src/tgw/health.py` (`_openrouter_key_limit()` and
`check_quota()` only) + `src/tgw/apis/secrets.py` (`get_api_key()`,
`get_secret()` — read-only reference, do not modify) + the todo brief
(`tgw todo brief 1289`) + CLAUDE.md's "Secrets from secrets_root" settled
architecture line (already in your context). Nothing else.

## Spec
`_openrouter_key_limit()` at `src/tgw/health.py:617` reads
`Path(cfg.get('secrets_root', ...)) / 'openrouter-credentials.json'`
directly. That per-provider JSON file layout was consolidated 2026-07-09
(todo #1252) into a single facility — `secrets_root/tgw.env` read via
`tgw.apis.secrets.get_api_key(provider)` — and the old JSON files were
moved to `secrets_root/_migrated-to-tgw-env-20260709/`. Since that
migration, `cred_path.exists()` is always `False`, so this check has been
silently dead (always returns `None`, no error, no warning) for over a
month.

Fix: replace the direct file read with
`tgw.apis.secrets.get_api_key('openrouter')`, catching the `RuntimeError`
it raises when unset (per `get_secret()`'s documented behavior) and
returning `None` in that case — preserving the function's existing
contract ("Returns None... never a reason to fail the whole quota
check"). Everything after key retrieval (the `requests.get(...)` call and
its parsing) stays unchanged.

## Dataset
None — this is a read-only health-check function, no data write.

## Out of scope
- `secrets.py` itself — read-only reference, do not modify.
- Any other function in `health.py`.
- The wider "Secrets from secrets_root" migration (#1252) — that's
  already done; this packet only fixes the one reader that was missed.

## Acceptance (live)
1. With a real (or test) `OPENROUTER_API_KEY` set in the environment per
   the current facility, call `_openrouter_key_limit(cfg)` — must
   actually reach the OpenRouter API call (or fail at the network call,
   not at key lookup) instead of silently returning `None` at the
   nonexistent-file check.
2. With `OPENROUTER_API_KEY` unset, call `_openrouter_key_limit(cfg)` —
   must return `None` gracefully (no unhandled exception propagating out
   of this function).
3. Run `tgw health` (or the specific `check_quota()` path) live and
   confirm the OpenRouter key-limit line either populates with real data
   or is absent without crashing the rest of the health check.

## Quota/risk
Adds zero new API calls — this restores an existing, already-budgeted
OpenRouter auth/key check that has been silently not firing; no new
metered usage beyond what the function already intended before it broke.
