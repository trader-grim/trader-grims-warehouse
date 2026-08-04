# Proposal — preserve identification evidence through eBay drafting, taxonomy assignment, and price research

**Status:** proposed; no implementation authorized
**Origin:** Dave-directed investigation of `tgw202605032128138`
**Prepared by:** Tigwa, 2026-07-24
**Scope:** `ai_identify` → `ebay_draft` → category-group / Store-category assignment → `ebay_price` search preparation.

## Outcome

A good identification should remain useful throughout the draft. TGW should turn known, source-traced item facts plus the full eBay taxonomy into an editable, usually-correct eBay draft:

- no generic fallback brand inserted ahead of a known real brand;
- a selected eBay category assigns the appropriate TGW category group and eBay Store category from taxonomy context;
- price research opens with concise high-ranking search terms, not an empty field or a copied prose title;
- operator edits always win and are never overwritten by reruns.

This is a bounded data-flow and defaulting improvement. It does not authorize listing publication, category policy changes without Dave review, AI-model/provider changes, or any bulk re-draft.

## Exact evidence: `tgw202605032128138`

The 2026-07-21 hinted `ai_identify` record, using `google_direct/gemini-3.1-flash-lite`, correctly extracted:

```text
brand: Silva
model: Type 7 ML
title: Silva System Type 7 ML Baseplate Compass
```

The persisted item still has top-level `brand: Silva` and `model: Type 7 ML`.

However, the subsequent `ebay_draft` pass only prefilled `Type` from the Inventory Record. It did not pass the known top-level brand/model into the aspect-pre-fill layer. Its aspect classifier therefore supplied `Brand: Unbranded`. The draft worker log records the deterministic downstream damage:

```text
'Silva System Type 7 ML Baseplate Compass'
→ 'Unbranded Silva System Type 7 ML Baseplate Compass'
```

The title enhancer trusted the eBay-draft Brand aspect and did not prefer canonical item brand or reject generic fallback values. The same missing handoff also produced a false `no_model` quality signal despite a known model.

The item has eBay category `52482`. eBay identifies it as `Hiking Compasses & GPS`; it belongs in Sporting Goods. The item currently has no `category_group`, so existing Store-category assignment correctly returns nothing. The information needed to decide this already exists in TGW: eBay taxonomy tree/cache, selected category, category-group configuration, and the live eBay Store-category source.

`search_terms` is blank because the field is currently an operator-set high-priority pricing-query override. The pricer can fall back to the draft title, but the blank UI forces an operator to copy/paste/edit before Sold, Active, Terapeak, or targeted comp research. For this workflow, it should instead begin with concise high-ranking buyer/comps terms.

## Proposed behavior

### 1. Preserve known item facts into the eBay Draft

Use one validated prefill chain for every eBay aspect:

```text
product lookup (if authoritative)
→ canonical item fields (brand, model/model_number, UPC, MPN, etc.)
→ Inventory Record fields translated to the selected eBay category
→ vision aspect suggestions for remaining unknowns
→ required-aspect fallback only when still empty
```

Only apply a value to an eBay aspect if the target category defines that aspect and its allowed-value rule accepts it. Preserve field provenance and do not promote an uncertain value to human-verified truth merely because it is used as a draft proposal.

For the compass fixture, `Brand: Silva` and applicable model data are prefilled before the aspect model is asked to fill remaining values. No extra model call is required.

### 2. Prevent generic-brand title poisoning

Title enhancement must prefer known non-generic identity sources in this order:

```text
product_lookup.brand → item.brand → validated eBay Draft Brand
```

Never inject the generic values `Unbranded`, `Does Not Apply`, `N/A`, `Unknown`, or `Other` into a title. Do not change a title already containing a distinct known brand merely because a later aspect pass disagrees.

### 3. Resolve category group and Store category from the full taxonomy

Preserve or recover the selected eBay category's full ancestry, not only its leaf ID/name. Produce a reviewable crosswalk from:

```text
full eBay taxonomy path + leaf ID
→ TGW category group
→ eBay Store-category ID/name
```

Resolution order:

1. exact configured leaf-ID mapping;
2. configured taxonomy-ancestor/path mapping;
3. a narrow, reviewable taxonomy-label rule;
4. cheap model classification only for the genuinely unresolved residue, constrained to existing TGW groups;
5. `Other` only when still unresolved, marked for review.

For `52482 / Hiking Compasses & GPS`, the resulting group and Store category are **Sporting Goods**. This is deterministically inferable from taxonomy context; it is not a generic fallback and does not need a model call.

Reconcile the complete category-tree/cache, existing TGW category groups, and live `GetStore` categories into a proposed crosswalk. Use the reconciliation to identify category groups lacking an appropriate Store category and propose additions/changes that match the TGW groups. Do not silently create or alter Store categories.

### 4. Prefill editable high-ranking price-research terms

When the operator has not supplied a manual override, generate a compact query from known identity fields:

```text
brand + exact model + product type + highest-value buyer synonym
```

For the fixture, an appropriate starting value is:

```text
Silva Type 7 ML baseplate compass
```

This is not an SEO listing title. It is a concise high-ranking buyer/comps query, intended for eBay Sold, Active, Terapeak, and Browse pricing. Exclude generic seller prose and low-value filler. Store it with source `generated:seo_terms`; preserve any operator edit forever across re-drafts. The existing model work may refine a deterministic candidate only when necessary; the first usable implementation must not add a model request.

The UI should state the distinction plainly:

```text
SEO / price-research terms (editable)
```

rather than implying an empty operator-only field is a missing draft field.

## Acceptance fixture

Use `tgw202605032128138` with its preserved identification evidence. A test/replay must prove:

1. eBay Draft Brand is `Silva`, and a simulated aspect result `Brand: Unbranded` cannot replace it.
2. Draft title remains `Silva System Type 7 ML Baseplate Compass`; it never begins with `Unbranded`.
3. Model evidence is visible to quality logic; `no_model` is not emitted solely because a known `Type 7 ML` was lost at the field-set boundary.
4. Category `52482 / Hiking Compasses & GPS` resolves to the Sporting Goods group and its configured Store category using taxonomy data.
5. The draft opens with generated editable terms equivalent to `Silva Type 7 ML baseplate compass`.
6. A later operator change to Store category or terms is retained after re-draft.
7. A non-mapped category becomes a visible `Other`/needs-review case rather than a fabricated specialized group.
8. Existing operator-set values and existing published/listing state are unchanged in regression fixtures.

## Smallest implementation sequence

1. Add canonical field-to-eBay-aspect prefill and title generic-brand guard, with the fixture tests.
2. Add taxonomy-lineage persistence/recovery plus a read-only crosswalk report; review the report before changing category-group or Store-category policy.
3. Apply the approved Sporting Goods mapping for `52482` and any reviewed mapping batch.
4. Add deterministic high-ranking price-research-term prefill and the editable/source-preserving UI behavior.
5. Run a bounded test batch; report exact counts for direct mapping, taxonomy mapping, unresolved/Other, and operator overrides preserved. Do not bulk mutate drafts without separate authorization.

## Non-goals / boundaries

- No new eBay Store category is created automatically.
- No AI model call is required merely to classify a taxonomy case already resolved by the tree.
- No user-entered Store category or research term is overwritten.
- No automatic publishing, repricing, or bulk draft rewriting is authorized.
- The unrelated `Type 7 NL` versus `Type 7 ML` alt-text discrepancy remains a separately preserved model-evidence conflict; this proposal prevents it from overriding known structured identification, but does not silently adjudicate it.
