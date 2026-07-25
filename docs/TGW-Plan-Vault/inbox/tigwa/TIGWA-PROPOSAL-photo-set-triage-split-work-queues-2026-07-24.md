# Proposal — photo-set triage, operator-approved splits, listing-photo selection, and item work requirements

**Status:** proposed; no implementation or item/eBay mutation authorized
**Origin:** Dave-directed inspection of `tgw202605032122566`
**Prepared by:** Tigwa, 2026-07-24
**Scope:** photo intake → multi-photo assessment → review/split → per-item identification → eBay photo selection → human work queues.

## Outcome

TGW should recognize when one capture session may contain more than one physical sale item, present an inexpensive visual review artifact, and let the operator either keep it as one item or split it safely. Each resulting item must have only its selected listing photos and may declare explicit follow-up work such as new photos or measurements.

The model proposes. The operator decides. No photo assessment may autonomously create SKUs, move/delete evidence, publish a listing, or overwrite an operator choice.

## Evidence fixture: `tgw202605032122566`

The item is currently an active eBay listing (`327276870280`, offer `266261139018`) at $24.99 with all 16 uploaded images. No mutation was made during this inspection.

A thumbnail montage of the source capture makes three separate physical items clear:

```text
Group 1 — Tuttle's English-Japanese dictionary and black protective case
  7 captured images: 212340, 212340-alt, 212343, 212345, 212352, 212355, 212357

Group 2 — separate coiled metal cable/fitting item
  4 captured images: 212531, 212534, 212538, 212557

Group 3 — separate DANCE pin/button
  4 captured images: 212705, 212712, 212718, 212720
```

The six-photo cloud `ai_identify` cap selected only the first eligible photos. It correctly identified the dictionary but never saw the cable or pin. The current prompt/output contract assumes one item, and the eBay flow uploaded every discovered local JPG—including generated `-alt` derivatives—into one listing.

This fixture demonstrates four related needs:

1. detect a potentially incoherent photo set before a one-item identification/draft;
2. make a human-confirmed split safe and easy;
3. select listing photos independently from raw retained photo evidence;
4. route follow-up needs such as photos or measurements into visible work queues.

## 1. Low-cost photo-set assessment

Build a numbered thumbnail montage from original capture assets plus a sidecar manifest:

```text
tile number → asset ID, filename, capture time, hash
```

Exclude generated/cropped derivative images from the default assessment, while retaining them as evidence. The montage is a derived review artifact, not a listing photo and not a replacement for original images.

Run a bounded low-cost vision assessment only when a trigger is present, such as unusually many photos, significant capture-time discontinuities, visually distinct object clusters, or explicit operator request. The model receives the montage and returns a proposal:

```json
{
  "set_coherence": "coherent_single_item | multiple_items | uncertain",
  "confidence": 0.0,
  "groups": [
    {"tiles": [1, 2], "visible_summary": "…", "confidence": 0.0}
  ],
  "recommended_action": "continue_identification | needs_split_review | needs_photo_review"
}
```

It must use tile IDs, never an untraceable prose-only grouping. Preserve its raw response and assessment metadata as derived evidence.

## 2. Operator review: keep, split, or defer

The operator sees the same montage, can select/move tiles, exclude a tile from a proposed listing, and chooses one explicit action:

```text
[ Keep as one item ]
[ Split into groups ]
[ Needs review ]
```

`Keep as one item` is a normal successful decision. It covers double-sided books, front/back/detail views, an item plus included components, boxed sets, or multiple angles of one sale item. If the model proposed a split but the operator keeps it together, retain an auditable decision such as:

```text
assessment: multiple_items candidate
operator decision: retain one sale item
reason: multiple views/components of same item
```

`Needs review` performs no listing or split action and routes the item to a visible review queue.

## 3. Safe split operation

After an explicit operator `Split into groups` confirmation, retain the original capture and create a split event with source SKU, timestamp, selected tile/asset groups, and actor.

For the fixture, the parent may retain the first physical item and two collision-checked sibling SKUs may be allocated sequentially:

```text
tgw202605032122566  parent / first retained physical item
tgw202605032122567  available candidate sibling
tgw202605032122568  available candidate sibling
```

The split must:

- preserve source provenance and original raw assets;
- create child JSON through the established item-creation primitive;
- copy only safe shared intake context, for example location and capture provenance;
- attach only the selected original assets to each item;
- strip listing-specific state from children: eBay offer/listing IDs, submitted payloads, image URLs, publication state, stale draft content, prices, and generated derivations;
- perform fresh per-child identification/drafting only after the split is confirmed;
- never end, delete, revise, or publish an eBay listing as an implicit consequence of local splitting.

An active listing must be deliberately ended through the appropriate eBay/operator action before its local record is deleted or reworked. Local deletion alone must not be represented as listing cancellation.

## 4. Explicit listing-photo selection

Retain raw photos as item evidence separately from which images are sent to eBay. Each asset needs listing-selection metadata, for example:

```text
asset ID / source path / hash / capture time
selected_for_ebay: true | false
listing_order: integer | null
exclusion reason: unrelated | duplicate | generated_derivative | poor_quality | operator_choice
```

The eBay publisher must upload only the explicit selected-and-ordered photo set. It must not scan an ItemData directory and upload every JPG. Generated `-alt` and cropped derivatives are false by default unless the operator deliberately selects one.

## 5. Item work requirements and queues

Do not overload one status string. An item can require more than one thing simultaneously. Add explicit requirements, such as:

```text
Needs new photos
Needs measurements
Needs testing
Needs condition/details check
Needs identification/research
Needs photo selection / split review
Needs packaging / weight / dimensions
```

Each requirement records:

```text
kind
state: open | claimed | evidence_added | completed | cancelled
reason
created_by / created_at
assigned work surface or queue
evidence links / completion note
```

Queues are derived from open requirements:

```text
Photos queue          open needs_photos
Measurements queue    open needs_measurements
Identification queue  open needs_identification
Split-review queue    open needs_photo_split_review
```

A new photo or measurement file does not silently complete a requirement. An operator explicitly confirms completion and links the relevant evidence. The existing `needs_photos` dashboard count is insufficient because it detects only missing photos, not existing-but-inadequate photos or any other work requirement.

## Acceptance scenarios

1. The fixture montage yields three proposed groups and routes to split review before draft/publish.
2. Dave can accept a proposed multi-group result as one item; all selected photos stay together and the decision is retained.
3. Dave can create the fixture's two sibling candidates, assign the three photo groups, and verify each child contains no copied eBay/listing state.
4. A double-sided book remains one item after a model split suggestion.
5. An unrelated photo can remain retained evidence but be excluded from eBay.
6. A generated `-alt` image is not automatically uploaded.
7. An item with existing but unsuitable photos can enter `Needs new photos`; an item may also enter `Needs measurements` concurrently.
8. Human completion, not file arrival alone, closes a requirement.
9. Existing published listings and operator-set values are unchanged by assessment, preview, or cancelled split.

## Smallest sequence

1. Implement source-asset manifest and numbered montage generation with offline fixture tests.
2. Add read-only coherence assessment and review UI; stop downstream draft/publish only for explicit unresolved/split-review state.
3. Add operator `keep as one item` decision recording.
4. Add preview-only split candidates, then confirmed split with provenance and eBay-state stripping.
5. Add explicit listing-photo selection and make publishing consume it.
6. Add multi-valued work requirements and derived queues, beginning with photos, measurements, and split review.
7. Run only a bounded synthetic/fixture pilot before any broad migration or automatic enforcement.

## Boundaries

- No automatic split, SKU creation, eBay action, source-photo deletion, or listing publication.
- No model call when a deterministic metadata rule resolves the case; model use is bounded to low-cost visual triage where needed.
- No operator photo selection, work requirement, or review decision is overwritten by a later model/worker pass.
- No bulk reprocessing or changes to existing live listings without separate authorization.
