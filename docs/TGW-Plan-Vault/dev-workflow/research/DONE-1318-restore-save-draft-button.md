# DONE #1318 — restore standalone "Save Draft" button

**Trigger:** Dave asked what happens when "Retry" is pressed on
`tgw202605051936445` and `tgw202605051913468`. Live investigation found:

- `tgw202605051936445`: action line shows "Set Price" (price is null), which
  is a pure client-side scroll+focus — no fetch, no job, no error. Matches
  Dave's report exactly ("button does nothing, no jobs queued, no error").
  Working as designed — not a bug, just needs an operator to type a price.
- `tgw202605051913468`: action line shows "Retry", but `retryPipeline()` only
  scrolls to the error box — it never calls `saveEbayDraft()` or resubmits
  anything. The underlying error: `ebay_stage` dead-lettered 2026-07-07
  because eBay rejected the 83-char draft title (>80 char limit). Traced why
  editing the title didn't help: the `_has_error and not is_active`
  action-line state renders ONLY "Retry" — `saveEbayDraft()` is only ever
  called from inside `listOnEbay()`/`updateItem()`/`relistItem()`, none of
  which render in that state. The only way to reach any of them was to first
  click "Clear error" (untested/non-obvious), reload, then edit-and-save.
  Dave confirmed the diagnosis and directed the fix: put back the standalone
  Save button.

**Root cause:** commit `a7e7439` (session 41, PP-ACTIONCONSOLE-001 one-line
consolidation) deliberately removed a standalone "Save Draft" button on the
assumption that saving always rides along with List-on-eBay/Update-Item. That
assumption doesn't hold for the error state — there was no path left to
persist a draft edit at all while `pipeline_error` is set and the item was
never staged.

**Fix:** `src/tgw/http_server.py` — restored the button
(`<button ... onclick="saveEbayDraft()">Save Draft</button>`) next to
`dl-save-msg`, unconditional on action-line state, so draft edits are always
explicitly savable regardless of what the state-driven action line shows.

**Verification (live):** `tgw-http.service` restarted; rendered
`item_detail_form('tgw202605051913468')` directly against the running config
(no mock) — confirmed `Save Draft` / `onclick="saveEbayDraft()"` / `dl-save-msg`
all present in the actual HTML output post-restart.

**Not done (deferred, not asked for):** did not edit the two items' actual
titles/price or re-trigger `ebay_stage`/`ebay_publish` for them — that's a
real eBay write and wasn't requested; leaving it for the operator to do
through the now-fixed UI. Also did not touch `retryPipeline()`'s scroll-only
behavior or the requirement to manually "Clear error" before a real resubmit
action appears — those are separate, smaller frictions noted but out of
scope for this fix.
