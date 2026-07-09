Stopping `tgw-worker@ebay_legacy_sync.service` (todo #1248) — its GetMyeBaySelling/GetOrders
runs were re-triggering roughly every 6 minutes instead of the intended 24h interval, spending
most of the ebay_trading Trading-API quota pool (3500/5000, background-halted at 70%) even though
Dave only listed ~6 items today. Root cause of the 6-min cadence not yet found (single worker
process confirmed, no crash loop; not_before rows in queue_jobs look correctly set to +24h).

Effect of stopping: sold-item detection (PP-SOLD-001 poller path, GetOrders → status=sold +
ebay_sale block) and the catch-all active-listing sync for non-Inventory-API listings both pause.
No other mechanism is live right now — the intended replacement (sold-event webhook) is still
blocked on todo #16 (nginx/cloudflared public endpoint; ISS-005 dev_id credential itself IS
resolved, confirmed present in ebay-credentials.json as of this session).

Next session: (1) confirm worker stopped and quota stops climbing, (2) root-cause the 6-min
retrigger before restarting, (3) restart once fixed, or scope #16 endpoint work to move sold-sync
to the webhook path permanently.
