# TGW Field-Set Boundary and Delivery Pipeline — machine-readable companion

**Companion to:** [TGW-Field-Set-Boundary-and-Delivery-Pipeline.html](TGW-Field-Set-Boundary-and-Delivery-Pipeline.html)  
**Scope:** PP-LISTEDITOR-001; packets #1418 → #1416 → #1417  
**Purpose:** concise, parseable orientation for human and AI coding agents. The packet documents remain the implementation source of truth.

## Non-negotiable model

```yaml
field_sets:
  inventory_record:
    label: "Set A — Inventory Record"
    storage: "item_attributes"
    envelope:
      _set: inventory_record
      version: integer
      updated_at: ISO-8601 timestamp
      fields: "marketplace-agnostic fields/aspects"
    history: "item_attributes_history[]"
    access: "inventory_record accessor module only"

  ebay_draft:
    label: "Set B — eBay Draft"
    storage: "draft_listing.item_specifics"
    envelope:
      _set: ebay_draft
      version: integer
      updated_at: ISO-8601 timestamp
      fields: "eBay category-resolved aspects"
    history: "draft_listing.item_specifics_history[]"
    access: "eBay-draft specifics accessor module only"

boundaries:
  inventory_to_ebay:
    direction: "Set A -> Set B"
    legal_function: "translate_inventory_to_ebay_draft(full_inventory_set, category_id, cfg)"
    result: "full eBay item_specifics set"
  ebay_to_inventory:
    direction: "Set B -> Set A"
    legal_sequence:
      - "diff_ebay_draft_to_inventory(item)"
      - "read-only diff endpoint/UI"
      - "operator selects default-checked subset and explicitly submits"
      - "named Set-A apply function writes selected fields with provenance"
    automatic_promotion: forbidden

prohibitions:
  - "No local per-key merge, prefill fallback, or {**a, **b} spread crosses a set boundary."
  - "No generic PATCH passthrough writes either set."
  - "No ambiguous display value may blend Set A and Set B."
  - "eBay Inventory API push reads Set B only."
```

```mermaid
flowchart LR
    A["Set A: Inventory Record\nitem_attributes envelope\n_set=inventory_record"]
    AHist["item_attributes_history[]\nappend-only provenance"]
    B["Set B: eBay Draft\ndraft_listing.item_specifics envelope\n_set=ebay_draft"]
    BHist["draft_listing.item_specifics_history[]\nappend-only provenance"]
    X["ONLY legal forward crossing\ntranslate_inventory_to_ebay_draft(full Set A, category)"]
    D["Read-only Set B -> Set A diff\ndefault-checked selectable rows"]
    G{"Explicit operator submit?"}
    Apply["Named Set-A apply function\nrecord source/detected_at/applied_at/applied_by"]
    Push["_build_offer_bodies\nreads Set B only"]
    Ebay["eBay Inventory API\nproduct.aspects"]

    A --- AHist
    B --- BHist
    A --> X --> B
    B --> Push --> Ebay
    B --> D --> G
    G -- selected values only --> Apply --> A
    G -- skip/uncheck --> D

    classDef inventory fill:#2e1a5b,stroke:#a78bfa,color:#f8fafc
    classDef draft fill:#064e3b,stroke:#34d399,color:#f8fafc
    classDef gate fill:#78350f,stroke:#fbbf24,color:#f8fafc
    classDef external fill:#1e293b,stroke:#94a3b8,color:#f8fafc
    class A,AHist,Apply inventory
    class B,BHist,Push draft
    class X,D,G gate
    class Ebay external
```

## Packet sequencing

```mermaid
flowchart LR
    P1418["#1418\nSchema foundation\nenvelopes + histories + accessors"]
    Review1418{"Runner review clean\nand Dave sign-off?"}
    P1416["#1416\nForward boundary fix\nSet A -> Set B"]
    Review1416{"Runner review clean?"}
    P1417["#1417\nGated reverse flow\nSet B -> Set A"]
    Review1417{"Runner review clean?"}
    Stitch["Explicit stitch\nmerge + close todo + clean worktree"]
    Escalate["Escalate / do not merge"]

    P1418 --> Review1418
    Review1418 -- yes --> P1416 --> Review1416
    Review1416 -- yes --> P1417 --> Review1417
    Review1417 -- yes --> Stitch
    Review1418 -- ambiguity/no --> Escalate
    Review1416 -- ambiguity/no --> Escalate
    Review1417 -- ambiguity/no --> Escalate

    classDef foundation fill:#2e1a5b,stroke:#a78bfa,color:#f8fafc
    classDef work fill:#064e3b,stroke:#34d399,color:#f8fafc
    classDef gate fill:#78350f,stroke:#fbbf24,color:#f8fafc
    classDef stop fill:#4c0519,stroke:#fb7185,color:#f8fafc
    class P1418 foundation
    class P1416,P1417,Stitch work
    class Review1418,Review1416,Review1417 gate
    class Escalate stop
```

## Implementation check before editing

1. Identify whether the code is operating **within Set A**, **within Set B**, or **crossing a boundary**.
2. Use the named accessor for the relevant set; do not index the raw envelope outside its accessor module.
3. For a cross-set action, find and use the named translation/diff/apply function. Do not recreate partial key logic.
4. Preserve append-only provenance history for every write.
5. For a new write into Set A from Set B, require explicit operator submission; no confidence threshold authorizes automatic promotion.
6. Keep the packet dependency order; reviewer evidence is not an automatic merge authorization.
