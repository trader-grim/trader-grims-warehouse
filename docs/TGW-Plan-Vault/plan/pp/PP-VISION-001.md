# PP-VISION-001 — vision-matching capability

**Status: PLANNED 2026-07-16.** Elevated from pointer-only stub (since
2026-07-11) per Dave's 2026-07-16 direction. Unlike PP-STORAGE-001 and
PP-WHISPER-001, Dave's own framing here was explicit priority, not a
plan-or-drop choice: **"oh yeah. I want this badly. should have already
been planned."** This doc plans it for real, including a path that starts
*before* the GPU purchase that's been blocking it, so "GPU-gated" stops
meaning "not started."

## What already exists (verified live 2026-07-16)

There is a real precursor already in the codebase, and its own docstring
is honest about its limits: `src/tgw/fingerprint.py` computes a **64-bit
perceptual difference-hash (dhash)** + an **8³-bin RGB color histogram**
per item photo, stores both in a SQLite `fingerprints` table keyed by SKU
(+ `size_class` for candidate narrowing), and `locate_image()`
(`fingerprint.py:192-239`) ranks candidates by a weighted
`0.6*hamming + 0.4*histogram` distance. The module docstring: **"Baseline
precision — a workflow proof, not a final CLIP matcher."** It is wired
only as a manual CLI tool (`tgw locate`, `api.py:2197`) with an on-demand
batch index build (`tgw build-fingerprints`) — explicitly "never runs
inline from a worker" (`fingerprint.py:137-139`). No queue/worker
integration exists.

This matters for planning: the *plumbing pattern* (per-item fingerprint
row, size_class-scoped candidate search, CLI query) is already proven and
reusable. What's missing is the actual matching quality — perceptual hash
is brittle to pose, lighting, and background, and can't distinguish
"same item, different angle" from "different item, similar shape/color"
reliably. That's the deep-embedding upgrade this PP is actually about.

## The two consumers (both already named, neither built)

1. **PP-INVENTORY-001's automated verification leg** — AI-vision-assisted
   check that a photographed item matches its catalog record (drift/
   mismatch detection), complementing the manual sweep tool.
2. **PP-STORAGE-001's findability use case** — "locate this item in
   semi-chaotic storage," now with a real size/weight cue alongside it
   (see `pp/PP-STORAGE-001.md`).

Both consume the same underlying capability: given a photo, return
ranked candidate SKUs by visual similarity. One design, two call sites.

## Hardware reality (verified live 2026-07-16)

`reference/HARDWARE-AI-INFERENCE.md`: the fleet is **100% CPU-only**
today (AMD Ryzen, 32GB RAM, Ollama serialized via Postgres advisory
lock, `qwen2.5vl:7b` at ~18s/item warm). No GPU exists anywhere. That doc
is a shopping guide (RTX 3090 ideal, RTX 3060 12GB minimum) for a
purchase that hasn't happened. This is the actual reason PP-VISION-001
has sat frozen — not lack of design, lack of hardware, and nobody had
written down what the design even needs the GPU *for* specifically until
now.

## Design — phased so Phase 1 doesn't wait on a GPU purchase

### Phase 0 (this doc): model + architecture choice

- **Model: a CLIP-family embedding model** (specific variant TBD at
  Phase 1 kickoff — candidates: OpenCLIP ViT-B/32 as the small/fast
  option, ViT-L/14 as the higher-quality/heavier option) run purely for
  **embedding generation**, not generative inference — this is a
  different workload shape than `qwen2.5vl`'s vision-QA use in
  `ai_identify` (embedding a photo is a single forward pass, far cheaper
  than a VQA generation loop). Per feedback-llm-model-selection: naming
  "a CLIP model" without a pinned variant is not yet a real spec — Phase
  1's first task is benchmarking 1-2 concrete candidates on real TGW
  photos, not picking from a spec sheet.
- **Storage: extend the existing `fingerprints` SQLite table** with an
  `embedding BLOB` column (a serialized float vector) alongside the
  existing dhash/histogram columns, rather than standing up new
  infrastructure — reuses the proven per-SKU-row/size_class-index
  pattern. If the vector count/query pattern outgrows SQLite's cosine-
  similarity-via-Python-loop feasibility at ~55k+ items, revisit
  `pgvector` on the PP-KNOWLEDGE-001 core-spine Postgres instance — not
  needed to start.
- **Query: cosine similarity** between query-photo embedding and stored
  embeddings, size_class-scoped exactly like `locate_image()` already
  does — same narrowing, better distance metric.

### Phase 1 — CPU feasibility pilot (no GPU purchase required to start)

Small-scale validation before spending money: embed a sample of ~200-500
existing item photos across a few size_classes on CPU, measure (a) actual
throughput (seconds/item, to project full-catalog batch time), (b)
match quality on a hand-picked set of known-similar and known-different
item pairs, compared side-by-side against the current dhash/histogram
baseline. This answers the real open question — is CPU-only embedding
generation viable for batch indexing (even if slow), with a GPU only
needed later for interactive/real-time queries — or is CPU infeasible
even for batch, making the GPU purchase a hard prerequisite rather than
an optimization. **This phase is the concrete evidence that should drive
the GPU purchase decision**, replacing "someday" with a measured
throughput number HARDWARE-AI-INFERENCE.md's shopping guide can be
weighed against.

### Phase 2 — full-catalog batch index (GPU, once acquired)

Batch-embed the full ~55k-item catalog (background job, thermal-aware,
matches the existing `build_fingerprint_index` "on-demand batch build,
never inline from a worker" precedent — same operational shape, not a
new pattern). Dry-run/sample-verify discipline per invariant E5 for any
bulk write, even though this is a derived/recomputable index, not raw
data (Prime Directive 1: derived is recomputable, but recomputing a
55k-item embedding pass is expensive enough to still warrant a sample
check before committing to a full run).

### Phase 3 — wire into consumers

- `tgw locate` gets an `--embedding` mode alongside the existing
  perceptual-hash mode (keep both — perceptual hash is free/instant and
  a reasonable first-pass filter even after embeddings exist).
- PP-INVENTORY-001's verification leg: new item photos get embedded at
  intake time (extends `ai_identify` or a new lightweight queue,
  implementer's call at build time) and compared against the item's own
  stored reference embedding — a low-similarity score on a *re-photograph
  of the same SKU* (e.g. during a location audit) is the drift signal
  PP-INVENTORY-001 needs.
- PP-STORAGE-001's findability flow: `tgw locate` output gets the
  size/weight envelope from `size_class_ranges` printed alongside each
  candidate.

## Out of scope (this planning pass)

- Actually running Phase 1 — this doc plans it; execution is a real todo
  once filed.
- The GPU purchase itself — Phase 1's throughput/quality numbers are
  meant to inform that decision, not preempt it.
- Any change to `ai_identify`'s existing `qwen2.5vl` VQA usage — that's a
  different workload (generative vision-language, not embedding
  similarity) and stays as-is.

## Next step

File a todo for Phase 1 (CPU feasibility pilot) — fully delegatable per
the planner rubric once a concrete model variant is picked at kickoff.
This is the piece that can start immediately, before any hardware
decision, and is what actually answers "should have been planned" with a
plan that doesn't just wait.

## Cross-links
- `src/tgw/fingerprint.py` — the existing perceptual-hash precursor this
  extends, not replaces.
- `reference/HARDWARE-AI-INFERENCE.md` — GPU shopping guide; Phase 1's
  output should get folded back into this doc once it exists.
- `pp/PP-STORAGE-001.md` — the findability consumer.
- TGW-Master-Plan.md's PP-INVENTORY-001 section — the verification-leg
  consumer.
- `reference/LLM-Providers-Quotas.md` / feedback-llm-model-selection —
  every LLM/vision-model task needs a pinned model, not "an embedding
  model" — Phase 1 exists partly to make that pin evidence-based.
