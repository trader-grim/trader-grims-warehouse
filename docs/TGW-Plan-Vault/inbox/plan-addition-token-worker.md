# Plan Addition — eBay Token Renewal as First State Machine Worker

### For the Opus Planning Session — May 2026

---

## Why This Belongs in the Plan

During Sonnet cleanup work, a small bug surfaced in eBay token renewal. Rather than
patch it in isolation, it should become the **first real worker wired to the
PostgreSQL state machine**. It is the lowest-risk, highest-information way to answer
the open architectural question (launcher ↔ state machine) by building it instead of
debating it.

---

## Prerequisite Decision — Single Canonical Secrets Location

This must be settled and migrated **before** the token worker work, because the token
worker reads and writes the very files being relocated.

**Problem (current drift):** secrets are scattered across at least three notions of
location — token state at `state_root/ebay_token_state.json`, refresh tokens described
as living in `runtime/secrets/`, and the health check probing `config/secrets/`.
On top of that, eBay `app_id` / `cert_id` currently live *inside*
`tgw-api-config.json`, which makes the config file itself a secret.

**Decision:** one canonical secrets directory, resolved from config like every other
path.

- Add a `secrets_root` key to `tgw-api-config.json`, e.g. `/opt/TGW/secrets/`. It joins
  the existing `*_root` keys that `get_tgw_paths()` already auto-creates.
- Place the directory **outside the repo tree** (at `/opt/TGW/secrets/`, not under
  `src/`), so committing a secret is structurally impossible — not merely gitignored.
  Add a belt-and-suspenders `secrets/` line to `.gitignore` regardless.
- One permissions target: directory `chmod 700`, files `chmod 600`, owned by the `tgw`
  user. One place to audit.
- Every secret resolves from `secrets_root`: token state, refresh tokens, eBay
  app/cert credentials, future marketplace keys, webhook secrets. Nothing hardcodes a
  second path.

**Credential/config split (recommended, judgment call on timing):** keep non-secret
paths and settings in `tgw-api-config.json` (committable), move actual eBay
credentials into `secrets_root`, and have config reference them by name. Cleaner
long-term shape. Can be done now or noted as an immediate follow-up if it widens scope
too much for the first pass.

**Migration:** move existing secret files into `secrets_root`, update the token
manager and health check to resolve from it, confirm `tgw health` and the token
manager read/write the same file. This decision *subsumes* the path bug below — fixing
the bug correctly means resolving from `secrets_root`.

---

## The Bug

- `tgw.health` probes `/opt/TGW/config/secrets/ebay-token.json`
- The token manager (`get_access_token.py` / `refresh_access_token.py`) reads and
  writes `state_root/ebay_token_state.json`

Two paths claiming to point at the same thing. This violates the settled rule that
`tgw-api-config.json` is the single source of truth for paths. Health reports a
false failure; the token state may be fine.

**Fix:** this is resolved by the `secrets_root` decision above. `health.py` and the
token manager both resolve the token path from `secrets_root` in config — one way,
everywhere. No hardcoded second path anywhere.

---

## Why This Is the Right First Worker

1. **Recurring scheduled work** — exercises `run_at` / `not_before`, the reason those
   columns exist.
2. **Natural single-owner lease** — only one refresh should run at a time. `SKIP
   LOCKED` + `lease_owner` replaces any ad-hoc lock file.
3. **Real failure paths** — network down, refresh token expired, eBay 401. This is the
   only way `retry_wait → queued → dead_letter` actually gets tested. A worker that
   always succeeds never proves the state machine works.
4. **Idempotent, near-zero side effects** — worst case is refreshing a still-valid
   token. Safe to run twice, safe to run early.
5. **Zero-risk cutover** — `tgw.source` and the existing cron keep the business token
   alive in parallel. The new worker runs alongside and is observed before the cron
   is retired.

---

## Proposed Sequence

1. **Settle and migrate `secrets_root`.** Add the key to config, create the directory
   outside the repo tree, set permissions, move existing secret files in, update token
   manager and health to resolve from it. (See prerequisite decision above.)
2. **Fix the health path bug.** Falls out of step 1 — `health.py` resolves token path
   from `secrets_root`. Confirm `tgw health` reads the same file the manager writes.
3. **One-time initial OAuth.** Run `get_access_token()` once to create the token state
   file in `secrets_root` on the production machine. Browser consent, paste redirect,
   done. From here, refresh is browserless.
4. **Decide the wiring pattern on this worker.** Recommend: launcher stays as Popen
   process manager; the worker imports state machine functions
   (`claim_queue_jobs`, transition helpers) for lease and state tracking. This keeps
   the launcher dumb and puts state ownership in the worker — consistent with "workers
   are thin, but they own their job lifecycle."
5. **Build `token_refresh` worker.** On claim: check expiry; if within buffer, refresh;
   on success → `succeeded` and reschedule next run; on transient failure →
   `retry_wait` with backoff; on hard failure (refresh token dead) → `dead_letter` +
   `notify()` so a human knows browser re-consent is needed.
6. **Self-reschedule.** On success the worker enqueues its next run (`not_before` =
   now + interval), so the queue is self-perpetuating without cron.
7. **Observe in parallel.** Run new worker and old cron together for a few days.
   Confirm the state machine refreshes the token reliably across an actual expiry
   cycle.
8. **Retire the cron.** Only after observation. Remove `ebay_api_token_refresh.py`
   cron entry. Update systemd/README and the constraints list.

---

## What This Validates for Everything After

The token worker is a template. Once it works, the item-intake pipeline, eBay
uploader, and Ollama job manager all follow the same pattern: claim → lease →
run → succeed/retry/dead_letter → reschedule. The architectural question stops
being open because there's a working reference worker to copy.

---

## Constraints Carried Forward (Updated)

- The eBay cron remains the sole keeper of token validity **until the token_refresh
  worker is observed working across a real expiry cycle.** Do not retire early.
- All secrets resolve from a single `secrets_root` in config. The directory lives
  outside the repo tree, `chmod 700`, files `chmod 600`, owned by `tgw`. No secret path
  is ever hardcoded.
- Token path is resolved from config in exactly one way, everywhere. The health-check
  bug is the canary for any future path drift.
