# Session 41 wrap-up (2026-07-02)

**Status:** Extremely long session, huge amount of ground covered. Everything below is
committed and live. Dave is stepping away to consult Fable/Antigravity for a plan
retargeting session — this doc exists so that session has full context without
re-deriving it.

**Dave's framing at end of session:** "I still don't have a working tool... this eBay
inventory is supposed to provide [housing and energy], so to me getting the site
running is quite urgent." He does not want quota-gated blockers used as an excuse to
stop fixing everything else in the pipeline. He is not listing new items until the
whole system (not just eBay) is stable — actively wrangling/coding time, not
production time, until then.

## Session arc (roughly chronological)

1. **eBay/OpenRouter API quota exhaustion investigation** — Dave reported "api token
   limit exhausted" on the first item he opened. Root-caused to THREE stacked issues
   session 39 (2026-07-01) had only partially fixed:
   - `ebay_draft`'s live `getCategorySuggestions` QA-telemetry call (audit finding #3,
     duplicated `ai_identify`'s category call, never actually removed) — **removed**.
   - `ebay_sync`'s 25707 per-SKU fallback (orphaned bad-SKU offer, todo #1077) firing
     every 6h with no circuit breaker — **capped to once/24h**.
   - The aspects-cache warm-up: Dave's actual spec (session 39) was "crawl it at the
     end of every day, then our limit resets" (i.e. once-daily, pre-reset). What
     shipped instead fired on every 6h `ebay_sync` cycle with no time gate — confirmed
     via the session-39 transcript that this was a silent, unflagged substitution, not
     what was asked for. **Fixed: gated to the 2h window before 00:00 PST reset.**
   - The 30-day auto-expiry on the category tree cache was ALSO never something Dave
     asked for (he said "the catalog doesn't change that much, they announce when it
     does" — twice). Combined with the above quota drains, the tree cache never
     survived long enough to actually populate. **Fixed: removed the auto-expiry
     entirely; added `tgw refresh-ebay-taxonomy` for manual invalidation.**
   - **Standing lesson saved to memory** ([[feedback-implement-as-specified]]): don't
     silently substitute a cheaper implementation for what was actually asked —
     flag the deviation and ask, or you'll ship the exact bug the spec was meant to
     prevent. This happened twice in one exchange (the warm-up cadence AND the cache
     TTL) and both were real, confirmed-live quota-exhaustion causes.

2. **Direct Google Gemini routing (`google_direct` provider)** — Dave wants to use his
   own Google API key instead of paying OpenRouter's markup for Gemini calls (already
   confirmed on the free tier). Built `_call_google_direct()` in `apis/llm.py`
   (google-genai SDK, synchronous, auto-falls-back to OpenRouter on any failure).
   Moved `ai_identify`/`alt_text`/`ebay_draft`/`bulk_classify` to it — verified live
   against the actual key: `gemini-2.5-flash`/`gemini-2.5-flash-lite` are free
   (`serviceTier: standard`, real inference, no billing); `gemini-2.0-flash`/
   `-flash-lite` are hard-blocked (429, quota `limit: 0` — Google's docs now flag
   2.0-flash-lite as deprecated, likely why). `google-genai` SDK installed in the prod
   venv. Found and fixed a `CLOUD_PROVIDERS`-unaware bug in `ai_identify.py`/
   `alt_text.py` that a third provider exposed (wrongly ran the Ollama liveness gate
   against `google_direct`).

3. **"Wire in bulk_classify" — vision-based aspect-fill** — Dave's actual complaint:
   "without good starting data all of our guesses will be poor" — `ai_identify` already
   sent multiple photos and asked a rich schema, but `ebay_draft`'s aspect-fill step
   ran a SECOND, separate TEXT-ONLY call with no photos at all, because it runs before
   the real eBay category (and its aspect list) is even known. Result: barcodes/tags
   visible in photos never got used for aspect-filling. Fixed: `ebay_draft`'s
   aspect-fill now sends up to 10 of the item's actual photos through `bulk_classify`
   (free Gemini), with a system prompt that explicitly says to check all photos for
   details visible in only one. Skips the vision call entirely when everything's
   already prefilled (small quota-saving bonus).

4. **Live incident: `tgw202605051933258` (vintage bottle) price drift** — Dave was
   about to use this item for an unrelated live-fire test when he noticed its price
   had drifted. This turned into the biggest structural finding of the session:

   **`ebay_price_reducer` never persisted `draft_listing.price`** after a reduction —
   only mutated in memory, silently dropped every run because `fence_patch_item`'s
   payload never included `draft_listing`. `ebay_stage.py` reads `draft_listing.price`
   FIRST, so the next time `ebay_stage` ran for ANY reason, it pushed the stale
   pre-reduction price back live, silently reverting a markdown eBay had already
   accepted. **Fixed**, plus reordered so the critical local write happens before the
   `ebay_offer` deep-merge call that was ALSO crashing intermittently
   (`KeyError('api_key')`, confirmed 3 separate times across two days in different
   code paths — root cause of that specific crash not fully pinned down, but the
   ordering fix means it can no longer discard already-accepted eBay writes).

   Dave's response: **"we do not throw away or overwrite data... now I want you to
   look at the log and find our existing problems we already should know about."**
   This became a full audit.

5. **Data-preservation audit** — reviewed all 20 `fence_patch_item()` call sites in the
   codebase for the same "mutated in memory, never persisted" bug class. Found one
   more, real: `ebay_draft.py`'s taxonomy-retry fallback (when `ai_identify` fails to
   resolve a category and `ebay_draft` retries) resolved `ebay_category_id`/
   `ebay_category_name` but never persisted them — every re-draft burned a fresh live
   Taxonomy call re-resolving the SAME category. **Fixed.** Other 18 sites clean.

6. **`docs/TGW-Plan-Vault` permission bug + "why does this keep breaking"** —
   `pm_intake` had been failing to write `TGW-Master-Plan.md`/`FILING-LOG.md` for
   ~19-24h (60 `PermissionError`s), because those files land at `db:tgw 0644` (no
   group-write) whenever `db` (Dave, Hermes, or me) touches them. Traced through
   several layers:
   - The permissions-hardening script's fix for this (0660/2770 shared-write policy)
     was already committed to the repo on 2026-06-29 (`e02455e`) — **just never
     deployed** to `/opt/TGW/bin/`, the copy that actually runs. Deployed it.
   - Even after deploying + running it, files kept reverting to 0600/644 within
     minutes. Root cause: `atomic_write_json()` (`items.py` + `catalog.py`, the
     codebase's canonical write pattern) uses `tempfile.NamedTemporaryFile`, which
     creates its temp file at **0600 regardless of the parent directory's ACL or
     umask** — an ACL can only constrain a requested mode DOWNWARD, never grant what
     the creator explicitly excluded. **Fixed**: both now `chmod()` the temp file to
     the target's existing mode (or 0660 default) before the rename.
   - Extended default ACLs (`setfacl -d -m g:tgw:rwx`) to `src/bin/config/var/backups`
     (secrets deliberately excluded) as the primary continuous-enforcement mechanism.
   - `tgw-permissions-reset.sh --check` now also audits the OPPOSITE drift direction
     (too closed, not just too open) and logs every run to `permissions-audit.log` so
     recurring drift is visible over time instead of only when someone happens to
     look.
   - **Standing lesson saved to memory** ([[feedback-act-dont-just-notice]]): investigate
     and act on an active problem immediately, don't report the symptom and wait to be
     told to keep digging. This came up because a runaway `ugrep -r` (mine, left
     running unsupervised in the background) drove NVMe temp to 86°C, correctly
     tripping `tgw-thermal-watchdog` into repeatedly stopping every `tgw-worker@*`
     service for ~9 minutes — but I reported "workers are stopped" three separate
     times before actually checking `journalctl -u tgw-thermal-watchdog`, which had
     the full answer immediately available. **The watchdog protects against pipeline
     workers misbehaving; it has no scope over ad-hoc/agent-spawned processes** — a
     design requirement now logged in [[project-nix-stability]] for the Catio/NixOS
     stabilization work.

7. **Live UI bugs found while checking "can Dave actually use the site right now"**:
   - **Timestamp display bug**: Postgres's session timezone is GMT, so every
     `queue_jobs` timestamp comes back UTC. `http_server.py` truncated the raw ISO
     string (`str(ts)[:16]`) at 13 different display sites, which strips the `+00:00`
     offset entirely — displaying a bare value that looks like local time but is
     actually UTC. Dave read a dead-lettered job's timestamp as "7 hours in the
     future." **Fixed**: new `_local_ts()` helper converts to `America/Los_Angeles`
     before formatting, applied at all 13 sites.
   - **"Retry does nothing"**: NOT a bug — confirmed the Taxonomy API is still live
     429'd (tested directly), so retrying an `ebay_draft` job needing a fresh aspects
     lookup correctly requeues but fails again within seconds on the same wall. Won't
     clear until 00:00 PST reset.
   - Also found while checking: `ebay_legacy_sync` hit a DIFFERENT quota wall —
     Trading API `GetMyeBaySelling` "exceeded usage limit," a third exhausted pool
     separate from Taxonomy and Sell Inventory. Reset schedule for this one not yet
     confirmed — worth checking eBay dev docs.
   - **`tgw-clipd` crash loop**: found via a broader log sweep Dave asked for
     ("existing problems we already should know about," generalized beyond the
     fence_patch_item bug class). Restart counter was at 15,769+ and climbing every
     ~3-4 seconds. Trivial root cause: `--verbose`/`--backend` only defined on the
     `daemon` argparse subparser, but the systemd unit invokes `tgw-clipd` bare (no
     subcommand) — the real invocation path crashed on `args.verbose` before doing
     anything else. **Fixed.**

## Commits this session (chronological)

1. `a7e7439` — eBay quota drains + data-preservation bugs (the big one: sections 1-6
   above, minus the clipd/timestamp fixes which came after)
2. `f511f2d` — `tgw-clipd` crash loop fix
3. `d1cad9a` — dead-letter/pipeline timestamp UTC-as-local display bug

## Open items / next steps

- **Gated until 00:00 PST tonight**: 12 `ebay_draft` + `ebay_sync` fallback jobs
  (Taxonomy 429). `ebay_legacy_sync`'s 8 Trading-API-limit dead-letters — reset
  schedule unconfirmed.
- **Ready to bulk-requeue now, not gated by anything**: **3,134** `ebay_draft`
  dead-letters from the old OpenRouter-402 pile (the account had run out of
  OpenRouter credit before `google_direct` existed). That whole code path is now on
  `google_direct` with validated fallback — these should mostly just work if
  requeued. Awaiting Dave's go-ahead (bulk operation, flagging per
  [[feedback-bulk-scripts-require-approval]]).
- **93 `ebay_draft` dead-letters, "model returned non-JSON"**: old (2016-2018 vintage
  SKUs), pre-session-41 code path (the old text-only aspect-fill, now replaced by the
  vision-based one). The error message in the DB is truncated to 200 chars
  (`raw[:200]` in `ebay_draft.py`), so it's not yet confirmed whether the underlying
  model output was actually truncated (token-limit cutoff) or just the stored error
  message. Not urgent — these will hit an entirely different code path if/when
  requeued. Worth a look if they recur under the new pipeline.
- **`tgw202605051933258`** (the vintage bottle): Dave said he'd fix its price himself
  once he understood the reducer bug. Draft/offer price sync bug is fixed; the
  specific item's live price may still need a manual correction — check with Dave.
- Tomorrow morning per Dave's plan (stated before this incident cascade started):
  spot-check one item through the new vision aspect-fill once Taxonomy resolves
  again, then bulk-queue the 2026-vintage-SKU backlog that's neither listed nor sold
  and review results as a batch.
- Todo #1077 (orphaned bad-SKU offer, root cause of the `ebay_sync` per-SKU fallback)
  reassigned to `admin` — needs Dave to actually contact eBay support, not something
  Claude can execute.

## Related memory files (for cross-session/cross-tool context)

[[feedback-implement-as-specified]] · [[feedback-act-dont-just-notice]] ·
[[project-google-direct-migration]] · [[project-nix-stability]] ·
[[feedback-api-quota-flagging]] · [[project-api-data-reuse-audit]] ·
[[project-clip-001]] · [[feedback-bulk-scripts-require-approval]]
