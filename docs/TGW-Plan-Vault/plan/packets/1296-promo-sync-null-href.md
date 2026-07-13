# Packet: cmd_promo_sync survives promotionHref: null
Todo: #1296   PP: PP-COHESION-001   Track: framework batch (PP-HERMES-EA-001), second run of new sequence (cadence rule)

## Context budget (ALL the model may load)
This packet + `src/tgw/promo.py` (`cmd_promo_sync()` only, lines ~729-800,
and its existing test file if one exists) + the todo brief
(`tgw todo brief 1296`). Nothing else.

## Spec
At `src/tgw/promo.py:773`:
```python
promo_id = promo_summary.get("promotionId") or promo_summary.get("promotionHref", "").split("/")[-1]
```
`dict.get(key, default)` only returns `default` when `key` is ABSENT —
not when the key is present with value `None`. If eBay returns a
promotion summary with `promotionId` absent/None AND `promotionHref`
explicitly present but `null`, `promo_summary.get("promotionHref", "")`
returns `None` (the key exists), and `.split("/")` on `None` raises
`AttributeError`.

Fix — wrap the `promotionHref` lookup so a `None` value is coerced to
`""` before `.split()`:
```python
promo_id = promo_summary.get("promotionId") or (promo_summary.get("promotionHref") or "").split("/")[-1]
```
When both `promotionId` and `promotionHref` are absent/None, this now
correctly produces `promo_id = ""`, which the very next line
(`if not promo_id: continue`) already handles gracefully — that fallback
already exists and is correct; it just wasn't reachable before because
the line above it crashed first.

## Dataset
None — this only prevents a crash during a sync loop; no new field is
written that wasn't already intended.

## Out of scope
- Any other part of `cmd_promo_sync()` (discount parsing, item matching,
  fence writes below this block).
- Any other function in `promo.py`.

## Acceptance (live)
1. Construct a promo_summary dict `{"promotionId": None, "promotionHref": None, ...}`
   (or omit `promotionId` entirely) and confirm `cmd_promo_sync`'s loop
   does NOT raise `AttributeError` — it should skip this entry via the
   existing `if not promo_id: continue`.
2. Confirm the normal case still works: `{"promotionId": "abc123", ...}`
   → `promo_id == "abc123"`.
3. Confirm the href-fallback case still works:
   `{"promotionId": None, "promotionHref": "https://api.ebay.com/.../PROMO-456"}`
   → `promo_id == "PROMO-456"`.
4. If a live/sandbox eBay call is easy to exercise safely, running
   `cmd_promo_sync` against real API data is a bonus, but the three
   constructed cases above are sufficient live evidence for this pure
   parsing fix.

## Quota/risk
None — no new API calls.
