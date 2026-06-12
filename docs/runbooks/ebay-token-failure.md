# Runbook: eBay OAuth token failure

**Failure mode:** the eBay user access token is expired and `token_refresh` cannot renew it.
Two severities:

- **A — access token lapsed, refresh token still good.** Transient; `token_refresh`
  self-heals. eBay-writing jobs requeue on `token is expired` with a 900 s backoff.
- **B — refresh token dead (HTTP 400 `invalid_grant`).** Requires operator browser
  re-consent. This is ISS-009; it happened on 2026-06-05 after a scope change.

Blast radius: `ebay_upload`, `ebay_price`, `ebay_stage`, `ebay_publish`, `ebay_sync`,
`ebay_legacy_sync`, `ebay_price_reducer`, `ebay_sku_migrate` all degrade. Intake,
ai_identify, drafting (offline mode), and catalog work continue.

## Symptoms

- `tgw health` shows dead_letter jobs in `token_refresh`, or the optional eBay token check
  fails.
- `journalctl -u tgw-worker@token_refresh.service` shows HTTP 400 / `invalid_grant` from
  `POST /identity/v1/oauth2/token`.
- Many queues accumulate `retry_wait` jobs whose `error_detail` contains `token is expired`
  — they loop every ~15 min with warning-level notifications
  (`/opt/TGW/var/log/notifications.jsonl`).
- Nothing new appears in `tgw staged`; photo uploads and price lookups stop.

## Likely root causes

1. **Refresh token invalidated by a scope change** — requesting different scopes during
   any OAuth flow kills the existing refresh token. Scopes are LOCKED:
   `sell.inventory`, `sell.account`, `sell.marketing`. Never add speculatively.
2. **Refresh token expired naturally** (eBay refresh tokens last ~18 months).
3. **`secrets_root/ebay-token.json` corrupted or wrong permissions** (must be `tgw`-owned,
   chmod 600).
4. **eBay identity endpoint outage** (rare; transient — severity A behavior).
5. Historical note: a double-buffer bug delayed refresh until the last 5 min of token life
   — fixed 2026-06-06 (worker passes `force=True`). If refreshes look "late", check that
   fix hasn't regressed.

## Diagnosis

```bash
# 1. Worker state and recent errors
systemctl status tgw-worker@token_refresh.service
journalctl -u tgw-worker@token_refresh.service --since "-6 hours"

# 2. Dead letters for token_refresh (error_detail tells A vs B)
sudo -u tgw tgw dead-letter --queue token_refresh

# 3. Token file: expiry is epoch seconds in the 'expiry' field
sudo -u tgw python3 -c "
import json, time
t = json.load(open('/opt/TGW/secrets/ebay-token.json'))
exp = t.get('expiry', 0)
print('expires:', time.ctime(exp), '| remaining (min):', int((exp - time.time())/60))"

# 4. Permissions sanity
sudo -u tgw ls -l /opt/TGW/secrets/ebay-token.json   # expect -rw------- tgw tgw
```

**Decision:** error text shows `invalid_grant` / HTTP 400 from the token endpoint →
**severity B** (re-consent). Network/5xx errors or an alive worker that simply hasn't
fired yet → **severity A** (wait or nudge).

## Recovery

### Severity A — nudge the refresh

```bash
# Clears token_refresh dead letters and enqueues a fresh refresh immediately
sudo -u tgw tgw restart-ebay-token
journalctl -u tgw-worker@token_refresh.service -f   # watch it succeed
```

### Severity B — operator browser re-consent

1. Run the OAuth consent flow (browser opens; sign in as the seller account):

   ```bash
   sudo -u tgw python3 /opt/TGW/src/trader-grims-warehouse/src/tgw/apis/ebay/get_access_token.py
   ```

   Paste the **full redirect URL** at the script's `→` input() prompt — *inside the Python
   prompt, not at bash*. If you already have the authorization code, use the `--code` flag
   to skip the browser. The script writes a fresh `secrets/ebay-token.json`.

2. **Do not touch the scope list in the script/config.** Same three scopes, exactly.

3. Restart the token machinery and clear the backlog:

   ```bash
   sudo -u tgw tgw restart-ebay-token
   # Requeue eBay jobs that dead-lettered on token errors (classifier-driven, safe)
   sudo -u tgw tgw dead-letter --requeue-transient
   ```

## Rollback

- There is nothing to roll back in TGW itself — recovery only writes a new
  `ebay-token.json`. If the new token file is malformed, re-run the consent flow; the
  `token_refresh` worker is the **only** writer of this file, so no other state diverges.
- If you accidentally ran the consent flow with wrong scopes: the old refresh token is now
  dead regardless — re-run the flow with the locked scope set. Update
  `feedback-ebay-config` expectations: there is no path back to the previous token.
- Backlog jobs requeued by mistake are harmless: every handler is idempotent and will skip
  or fail back to dead_letter.

## Verification

```bash
# 1. Token fresh and long-lived (~2h access token typical)
sudo -u tgw python3 -c "
import json, time; t=json.load(open('/opt/TGW/secrets/ebay-token.json'))
print('remaining (min):', int((t['expiry']-time.time())/60))"

# 2. token_refresh queue clean and self-scheduled
psql -U tgw state_machine -c "
  SELECT state, count(*) FROM queue_jobs
  WHERE queue_name='token_refresh' GROUP BY state;"
# expect: one 'queued' (the next self-schedule), zero dead_letter

# 3. eBay-writing queues draining
psql -U tgw state_machine -c "
  SELECT queue_name, state, count(*) FROM queue_jobs
  WHERE queue_name LIKE 'ebay%' GROUP BY 1,2 ORDER BY 1,2;"
# retry_wait counts should fall over the next 15-30 min

# 4. End-to-end probe: a live eBay read (dry-run = no writes)
sudo -u tgw tgw ebay-pull --dry-run --no-sold

# 5. Notifications quiet
tail -20 /opt/TGW/var/log/notifications.jsonl
```
