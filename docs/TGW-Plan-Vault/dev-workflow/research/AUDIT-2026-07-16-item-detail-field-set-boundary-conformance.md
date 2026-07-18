# AUDIT — Item-detail UI vs. Field-Set Boundary and Delivery Pipeline

**Date:** 2026-07-16  
**Author:** Tigwa  
**Scope:** current item-detail page (`GET /form/items/{sku}`), its item mutation paths, and the Set-A / Set-B boundary model.  
**Authoritative specification:** `reference/TGW-Field-Set-Boundary-and-Delivery-Pipeline.md`  
**Mode:** read-only source/test audit; no target source, config, ItemData, queue, marketplace, or credential changes.

## Verdict

**SUBSTANTIALLY CONFORMANT, WITH ONE MATERIAL HTTP-BOUNDARY GAP AND TWO OPERATOR-UX/SECURITY FOLLOW-UPS.**

The item page is no longer merely an aspiration captured by the diagram. It visibly separates canonical inventory data from the eBay draft, implements the operator-gated reverse Set-B → Set-A flow, preserves provenance, and uses separate forward/reverse pathways. The main remaining contract problem is that the generic item PATCH API can still accept a full set envelope from an authenticated caller.

## Confirmed conformance

| Specification requirement | Current evidence |
|---|---|
| Set A is `item_attributes`; Set B is `draft_listing.item_specifics`; each has an accessor/history model. | `src/tgw/inventory_record.py`; `src/tgw/ebay/draft_specifics.py`; UI reads the two sets separately at `src/tgw/http_server.py:5808-5878`. |
| No ambiguous display blending. | Inventory Record specifics are rendered from Set A unmerged. A different eBay value is a clearly separate secondary line, not a merged source value: `http_server.py:5842-5877`. |
| Set A → Set B crossing uses the named translator. | `tgw.ebay.aspect_translation.translate_inventory_to_ebay_draft()` is imported and used by `src/tgw/workers/ebay_draft.py`. |
| Set B → Set A is read-only first, live-recomputed, operator-selectable, and explicit. | `GET /api/items/{sku}/inventory-diff` and `POST /api/items/{sku}/inventory-diff/apply`: `http_server.py:2372-2411`; panel/UI: `5880-5902`, `6036-6074`. |
| Client values are not trusted on reverse apply. | Server receives keys only, re-diffs live, and applies only current differences: `tgw/ebay/inventory_diff.py:100-166`. |
| Reverse apply preserves provenance and does not modify Set B or forward proposal state. | Named apply function uses Set-A accessor/history; endpoint applies returned Set-A envelope only. HTTP coverage at `tests/test_http_server.py:3693-3796`. |
| eBay `product.aspects` push reads Set B. | `_build_offer_bodies()` obtains aspects from `get_ebay_aspects(item)`: `src/tgw/ebay/sync.py:442-460`. |
| Forward proposals and reverse inventory sync are distinct actions. | Separate `acceptProposals()` vs. `applyInventoryDiff()` flows; test proof at `tests/test_http_server.py:3799-3823`. |

## Test evidence run

Read-only test execution against current source used temporary test data only:

```text
8 passed, 276 deselected
  tests/test_http_server.py -k inventory_diff

31 passed
  tests/test_inventory_record.py
  tests/test_draft_specifics.py
  tests/test_aspect_translation.py
  tests/test_invariant_c12_field_set_accessors.py
```

## Findings

### 1. Material contract gap — generic PATCH can still write full set envelopes

**Severity:** high / boundary hardening required before claiming full enforcement.

The UI’s normal flows use the correct accessors. However, `PatchBody.fields` is untyped (`Dict[str, Any`) at `http_server.py:351-352`. The generic `PATCH /api/items/{sku}` path accepts a fully formed Set-A or Set-B envelope and `_apply_patch()` permits a full envelope to continue as a replace (`http_server.py:974-1031`).

This is weaker than the specification’s explicit prohibition:

```text
No generic PATCH passthrough writes either set.
```

**Required correction:** reject `item_attributes` and `draft_listing.item_specifics` from generic PATCH requests, including full envelopes. Route all such writes through named Set-A/Set-B endpoint operations or a narrowly authenticated accessor-only service boundary. Add negative API tests for both bare and fully enveloped attempts.

### 2. Scope ambiguity — “eBay push reads Set B only” is exact for aspects, not all listing inputs

The eBay aspects payload is correctly Set-B-only. Yet `_build_offer_bodies()` retains legacy fallbacks for title, description, and condition, for example `draft.get('title') or item.get('title', '')` at `src/tgw/ebay/sync.py:462-470`.

This is compliant if the specification’s statement governs the Set-A/Set-B **aspect** boundary. It is not compliant if it is intended to cover every listing field. Clarify the intended scope before changing fallback behavior.

### 3. Reverse-sync UI lacks a truthful load/error state

`loadInventoryDiff()` hides the panel on empty results and silently ignores fetch failure (`http_server.py:6042-6063`). An operator cannot distinguish “there are no differences” from “the comparison failed.”

**Recommended correction:** render a non-blocking failed-to-load state with retry; retain ordinary editing functionality.

### 4. Reverse-diff HTML should escape dynamic values

The diff renderer inserts aspect key, eBay value, and source metadata directly into HTML (`http_server.py:6050-6061`). Those values can originate in local ItemData, marketplace data, or AI output.

**Recommended correction:** use the existing HTML escaping helper or DOM text nodes for every dynamic field before assigning `innerHTML`; add a regression test with markup-shaped values.

### 5. Terminology can obscure the narrow Set-A boundary

The page’s broad “Inventory Record” panel includes top-level canonical catalog fields as well as the separate `item_attributes` Set-A specifics. The current specific-value display is not ambiguous, but the diagram’s Set A is narrower than the page heading.

**Recommended correction:** retain the broad panel, but label the nested Set-A area explicitly as “Set A — Inventory Record specifics” and the draft counterpart as “Set B — eBay category aspects” in operator/help text.

## Promotion recommendation

Do not alter the current workflow based on this audit alone. File the generic-PATCH hardening as the next field-set boundary correction, then review the API-negative tests and browser-side escaping/error state before declaring the diagram’s non-negotiable model fully enforced.
