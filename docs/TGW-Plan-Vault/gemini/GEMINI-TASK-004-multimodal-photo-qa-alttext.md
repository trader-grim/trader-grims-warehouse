# Gemini Task 004 — Multimodal Photo QA + Alt-Text Pilot

**Date prepared:** 2026-06-08
**Prepared by:** Claude (Opus 4.8), session 19 delegation pass
**Expected output:** `GEMINI-004-result.md` in `docs/TGW-Plan-Vault/inbox/`
**This is the multimodal test** — it exercises your vision capability on real item photos.

> Why you (Gemini): you can read images directly; this is a vision task. Claude is text-only in
> this harness. The deliverable doubles as (a) a data-quality audit and (b) a reusable
> prompt/spec that Claude will wire into the local Ollama vision model on the tgw-api side.

---

## Context

Each TGW item lives at `/opt/TGW/data/ItemData/<SKU>/` and contains:
- `<SKU>.json` — the item record (title, condition, category, ai_hint, draft_listing, etc.)
- one or more `.jpg` photos (filename is an uppercased SKU-derived string, e.g.
  `TGW20141115175901453.jpg`).

The catalog index is the SQLite DB at `/opt/TGW/data/ItemCatalog/tgwcatalog.db`, table `catalog`
(columns `sku, title, location, status, price, image, data`). `data` is the full item JSON.

Two motivations:
1. **Accessibility + SEO** — item photos have no alt-text. Good alt-text helps screen readers
   and external-surface SEO (PP-SEO-001 Phase 5 / PP-DATALEARN-001 alt-text track).
2. **Vision-based catalog QA** — do the photo and the stored title/category actually agree?
   Mismatches (wrong title, wrong category, blurry/empty/placeholder photo) are high-signal data
   defects we currently can't detect with text rules.

## Your task

### Step 1 — pick a sample (you choose, locally)
Select **30–40 items** with at least one photo, spread across different category groups and
statuses. You may read the catalog DB read-only to choose:
```
sqlite3 /opt/TGW/data/ItemCatalog/tgwcatalog.db \
  "SELECT sku,title,status FROM catalog WHERE title!='' AND title NOT LIKE 'tgw%' LIMIT 200;"
```
Then read each chosen item's JSON + its photo(s) from `/opt/TGW/data/ItemData/<SKU>/`.
Prefer a mix: some with rich titles, some with thin/placeholder titles, some `In Stock`, some
`Active`. Record the exact SKUs you used.

### Step 2 — for each sampled item, produce:
- **`alt_text`** — one concise sentence (≤125 chars) describing the photo for a screen reader.
- **`seo_caption`** — a longer (1–2 sentence) descriptive caption suitable for listing-body
  enrichment: brand/model/material/color/notable features *visible in the photo*.
- **`photo_quality`** — one of `good | dark | blurry | cluttered_background | placeholder | empty`.
- **`title_match`** — does the photo agree with the stored `title`? `agree | partial | mismatch`,
  plus a one-line reason.
- **`category_plausible`** — does the photo look consistent with `category_group`/`ebay_category_id`?
  `yes | unsure | no`, with a reason.
- **`suggested_fixes`** — optional: a better title, a missing aspect you can read off the photo
  (e.g. visible UPC/model number), or "re-photograph" if quality is bad.

### Step 3 — aggregate findings
- A table of all sampled SKUs with the per-item verdicts above.
- Summary stats: % good photos, % title mismatches, % category-implausible, top recurring
  defect patterns.
- The **highest-value defects** (mismatches / wrong category on `Active`/high-price items first)
  as a prioritized list — these become catalog-verify follow-ups.

### Step 4 — deliver a reusable spec (this is the durable payoff)
Write a **prompt template + output JSON schema** that a local vision model (we run Ollama on
CPU; small vision models like a quantized LLaVA/Qwen-VL) could use to generate `alt_text` +
`seo_caption` + `photo_quality` per item at scale. Keep the prompt lean — our Ollama box is
CPU-only and slow, so design for short prompts and batch use. Claude will wire this into a
`tgw alt-text <sku>` command that writes the result back through the tgw-api fence.

**Naming convention (operator, 2026-06-08):** alt-text derivative/secondary images use
`<SKU>-alt.jpg` (sidecar to the primary `<SKU>....jpg`). Reflect this in your output schema's
file-path field. (Intent slightly ambiguous — Claude will confirm with Dave before the writer
lands; just assume `<SKU>-alt.jpg` for the spec.)

## Output format (`GEMINI-004-result.md`)
1. **Method** — how you sampled, how many items, model/vision notes.
2. **Per-item table** — SKU | photo_quality | title_match | category_plausible | one-line note.
3. **Proposed alt_text / seo_caption** for each (a second table or per-item block).
4. **Aggregate stats + prioritized defect list.**
5. **Reusable vision prompt template + JSON output schema** (the Step 4 deliverable).
6. **Recommendations** — which catalog-verify rules or pipeline hooks this suggests.

## Constraints
- Read-only on item data — do **not** modify any `<SKU>.json` or the catalog DB. You are
  producing a report + a spec, not writing fields. (The write path is Claude's: it goes through
  tgw-api, per the settled "tgw-api is the fence" architecture.)
- Don't commit to git.
- If a photo won't load or an item is malformed, note it and move on — don't fail the batch.
