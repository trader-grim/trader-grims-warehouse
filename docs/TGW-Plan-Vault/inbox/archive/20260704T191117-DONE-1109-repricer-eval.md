# IN PROGRESS — #1109 PP-REPRICER-001 eval packet

Comparing the two recovered Google pricing options from PP-PRICING-001's
design. Option (b) — SerpApi google_shopping — is blocked: no key
provisioned yet (todo #1110). Proceeding with option (a) only: Gemini +
Google Search grounding, using the existing `google_direct` key
(`llm_google` quota pool), zero SerpApi/Bing dependency.

No production wiring — this is a standalone eval script per the todo's
own framing ("report signal quality before any wiring"). Does not touch
`apis/llm.py`'s production `_call_google_direct()` path.

Ground truth: no programmatic "Dave's real prices" dataset exists for
specific items, so scoring against live `BrowseCompsProvider` comps
(existing quota-free eBay Browse API path, already used in production
`ebay_price` worker) as the baseline signal instead — flagging this
substitution since the todo said "Dave's real prices," and asking Dave to
spot-check a handful once the report is out.

**DONE — live-verified result.** Ran `scripts/eval_repricer_gemini_grounding.py`
against 10 real, sold TGW items (fixed seed, reproducible sample —
niche/vintage long-tail items, not cherry-picked electronics). All 10
got both a Gemini+Search-grounding estimate and live Browse comps
(zero errors, zero 429s — confirms gemini-2.5-flash has separate/healthier
quota headroom than the flash-lite bucket that's been 429ing all day).

**Result: Gemini+Search grounding underperforms the existing free Browse
comps signal.** Scored against TGW's own historical sold price:
- Gemini estimate: mean abs error **45.3%**, median **43.5%**
- Existing `BrowseCompsProvider` median: mean abs error **30.4%**, median **33.4%**

Gemini's web search kept finding *plausible but wrong* comps (vintage/generic
matches instead of the exact item — e.g. "$4-$10 lapel pins" vs the real
$25.87 sale, or a completely different mug type at $56 vs a $23.55 real
sale). Browse comps, drawing on eBay's own live active-listing pool, stayed
closer across the board.

**Recommendation:** do NOT wire Gemini+Search-grounding as a
`MarketDataProvider` replacement or blend partner — it doesn't beat what
we already have for free. It might still have narrow value as a fallback
*only* when Browse comps return zero samples (all 10 test items had
`browse_count >= 3`, so this eval didn't actually test that edge case —
worth a follow-up with genuinely-zero-comp items if that path still
seems worth exploring). Option (b), SerpApi, remains untested — still
blocked on #1110's key provisioning.

Raw results: `/opt/TGW/var/log/repricer-eval-1109.json`.
