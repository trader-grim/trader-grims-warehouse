---
title: TGW Ollama Prompt Templates
markmap:
  colorFreezeLevel: 2
  initialExpandLevel: 3
updated: 2026-06-04
---

# TGW Ollama Prompt Templates

## Overview
- CPU-only inference (AMD Ryzen, 32GB RAM) — keep prompts lean
- One model loaded at a time via Postgres advisory lock (`ollama_lock`)
- Vision model: `qwen2.5vl:7b` — ~18s/item warm, ~10min cold-start
- Text model: `Qwen2.5:latest` (7B) — ~15s/call
- All prompts return JSON only — no prose, no markdown fences

---

## ai_identify worker (`workers/ai_identify.py`)

### Model
`qwen2.5vl:7b` via Ollama `/api/generate` with base64-encoded image

### Photo preprocessing
- Primary image only (alphabetically first image in SKU folder)
- Resized to 512px longest edge, JPEG quality 85 → ~56KB
- Original size logged for monitoring

### System prompt (shared across all variants)
```
You are an eBay listing assistant. You will be shown a photo of an item for sale.
Respond with valid JSON only — no prose, no markdown fences.
```

### User prompt — plain (no hint)
```
Look at this item photo and provide:
- A concise, descriptive eBay-style title (under 80 characters)
- The most likely eBay category name (plain English, e.g. "Board Games", "Action Figures")
- A 1-2 sentence description of what the item appears to be
- Your best guess at condition: "New", "Like New", "Very Good", "Good", "Acceptable"

Respond with JSON:
{
  "title": "...",
  "category": "...",
  "description": "...",
  "condition": "..."
}
```

### User prompt — hinted (ai_hint or existing human title present)
```
Look at this item photo. I already know this item is: {hint}

Using that context together with the photo, provide:
- A concise, descriptive eBay-style title (under 80 characters) that builds on what I told you
- The most likely eBay category name (plain English, e.g. "Thimbles", "Miniature Bottles")
- A 1-2 sentence description covering what is visible (quantity, materials, notable markings)
- Your best guess at condition: "New", "Like New", "Very Good", "Good", "Acceptable"

Respond with JSON:
{
  "title": "...",
  "category": "...",
  "description": "...",
  "condition": "..."
}
```

### User prompt — enriched (PP-LOOKUP-001 product data present) ⚠ template exists, not yet wired
```
Look at this item photo. Barcode lookup identified this product: {product_context}

Using that product data together with the photo:
- Confirm or refine the title to be eBay-ready (under 80 characters, include brand/model)
- The most likely eBay category name (plain English)
- A 1-2 sentence description focusing on condition and any notable visible details
- Condition based on what you see: "New", "Like New", "Very Good", "Good", "Acceptable"

Respond with JSON:
{
  "title": "...",
  "category": "...",
  "description": "...",
  "condition": "..."
}
```

### Hint source priority
1. `ai_hint` field in item JSON (operator-supplied)
2. Existing `title` field if it's not the SKU and `ai_identified` is not yet true
3. (future) `product_lookup.title` + `product_lookup.brand` from PP-LOOKUP-001

### Skip logic
- Skip if `ai_identified: true` AND no `ai_reidentify` flag
- `ai_reidentify: true` forces re-run regardless of `ai_identified`
- Cleared after run: `ai_reidentify` removed, `ai_identified` set to `true`

### Tuning levers
- Change photo resize from 512px → larger for better detail (costs more tokens/time)
- Add brand/model explicitly to hinted prompt for better specifics
- Add "include item number / model number in title if visible" instruction to improve MPN capture

---

## ebay_draft worker (`workers/ebay_draft.py`)

### Model
`Qwen2.5:latest` (7B text model) via Ollama `/api/chat`

### System prompt
```
You are an eBay listing assistant. Given item details and a list of eBay item
specifics (aspects), suggest the best value for each aspect.
For SELECTION_ONLY aspects, you MUST choose from the allowed values listed.
For FREE_TEXT aspects, suggest a concise, accurate value.
If an aspect does not apply, use null.
Respond with valid JSON only — an object mapping aspect name to suggested value.
```

### User prompt (built dynamically per item)
```
Title: {item.title}
Category: {item.ebay_category_name}
Description: {item.description}
Condition: {item.condition}

Aspects to fill:
  Brand (REQUIRED): choose from [Unbranded, Funko, LEGO, ...]
  Character: choose from [Batman, Spider-Man, ...]
  Theme: free text
  Material (REQUIRED): choose from [Plastic, Metal, Wood, ...]
  ...

Respond with JSON: {"Brand": "...", "Theme": "...", ...}
```

- SELECTION_ONLY aspects list up to 30 allowed values; truncates with count if more
- FREE_TEXT aspects shown as "free text"
- REQUIRED aspects flagged explicitly

### Post-processing
- SELECTION_ONLY: AI value validated against allowed list; invalid value → `null`
- REQUIRED aspects left null by AI → backfilled with first allowed value (or "Not Specified")
- Result written to `draft_listing.item_specifics`

### Tuning levers
- Providing brand/MPN from PP-LOOKUP-001 in the prompt context would improve SELECTION_ONLY accuracy
- System prompt could be extended with "prefer specific values over generic ones"
- Temperature is Ollama default (~0.8) — lower temp for more consistent aspect selection

---

## pm_intake worker (`workers/pm_intake.py`)

### Model
`Qwen2.5:latest` (7B text) via Ollama

### Input truncation
- Note truncated to 4000 chars
- Master plan sent as headings-only (not full content) — CPU-only machine, keep lean

### Purpose
Classify what changed in a dropped inbox note → patch Master Plan → archive note.
Prompt built dynamically per note; not a static template.

---

## General tuning notes
- All prompts designed for 7B models on CPU — lean by default
- When GPU arrives (PP-HARDWARE upgrade): can expand prompts, use 14B/32B models
- Photo resize and prompt length are the primary levers for quality vs. speed tradeoff
- Bad output patterns to watch: generic titles ("Vintage Item"), wrong category family, null on all aspects
- `tgw hint <SKU> "text"` is the primary fix for bad identification — cheaper than prompt tuning
