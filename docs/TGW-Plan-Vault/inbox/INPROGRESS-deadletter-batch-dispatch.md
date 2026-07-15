# In progress: PP-DEADLETTER-001 8-wide packet dispatch (2026-07-14)

Dave asked to experiment with a bigger concurrent tgw-coder batch (previous
cadence was 2-3 at a time) specifically to surface more rules/invariants
per round. Wrote 8 packets and dispatched all 8 as parallel tgw-coder
agents in one round:

- #1393 — ebay_draft 95 non-JSON aspect-fill (extract_json fence-stripping bug + possible token-budget config flag)
- #1394 — ebay_draft 12 taxonomy-429 (retry-with-backoff, same shape as today's Gemini fix)
- #1395 — ebay_stage 17 non-leaf-category ('99' Everything-Else fallback is the lead)
- #1397 — ebay_sync 9 offer-endpoint 400 (unhandled eBay error ID, not the known 25707)
- #1398 — ebay_upload 10 dimension-limit (pre-flight Pillow downscale, no resize logic exists today)
- #1399 — ebay_upload 3 XML-parse-error (likely unescaped photo_path.stem in the Trading API XML payload)
- #1400 — single-SKU cascade (tgw202605051933258): KeyError('api_key') + malformed ImageLinks message, folded #1396 in
- #1403 — ebay_draft truncated-image OSError, RESCOPED by Dave to log+notify+defer only (not repair; leg1 #1154 already detects this class)
- #1404 — ebay_publish Brand-missing, single item, verify isolated vs systemic

All packets under docs/TGW-Plan-Vault/plan/packets/1393...1404-*.md.
Dispatching via Agent tool (subagent_type=tgw-coder), results land as
result manifests at plan/packets/results/<id>-RESULT.md per the standard
branch-per-task contract. Review + stitch after they land — same
PP-COHESION-001 discipline as #1367/#1383 earlier today.
