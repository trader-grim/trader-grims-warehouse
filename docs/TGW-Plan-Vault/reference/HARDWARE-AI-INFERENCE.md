# TGW Hardware — AI Inference Reference

*Updated 2026-06-03. Prices are used-market estimates at time of writing.*

## Current Setup

- CPU-only inference (AMD Ryzen, 32GB RAM)
- Ollama serialized via Postgres advisory lock (one model at a time)
- `qwen2.5vl:7b` — vision identification (~18s/item warm, CPU)
- `Qwen2.5:latest` (7B) — text tasks: PM-intake, ebay_draft specifics (~15s/call, CPU)

## Model VRAM Requirements (Q4 quantization, serialized)

| Model | VRAM needed | Notes |
|-------|-------------|-------|
| qwen2.5vl:7b | ~5 GB | Vision model; primary bottleneck |
| Qwen2.5:latest (7B) | ~4–5 GB | Text model |
| Qwen2.5 14B | ~8–9 GB | Would improve draft quality |
| Qwen2.5 32B | ~18–20 GB | Requires 24GB card |
| qwen2.5vl:72B | ~40+ GB | Out of scope for single card |

Since models run serially (Ollama lock), peak VRAM = largest single model, not the sum.

## GPU Recommendations (used gaming rigs)

### Target of opportunity — RTX 3090 (24GB)

The right long-term answer. Now appearing in used rigs as owners upgrade to 4090s.

- 24GB VRAM: runs 7B comfortably, handles 13B/20B, fits quantized 32B
- Matches the "ideal 24GB+" target
- ~$400–500 used
- 350W TDP — confirm PSU before pulling

### Minimum viable — RTX 3060 12GB

Most common card in 2020–2022 gaming rigs. Acceptable floor.

- 12GB VRAM: handles both current models easily, enough headroom for 13B later
- ~$130–160 used
- Drops per-item inference: ~18s → ~3–5s (4–6× speedup)
- 200-item catalog batch: ~1 hour CPU → ~15 minutes GPU

### Also good if encountered

| Card | VRAM | Used price | Notes |
|------|------|-----------|-------|
| RTX 3080 Ti | 12GB | ~$250–320 | Faster than 3060, same VRAM |
| RTX 3080 | 10GB | ~$200–260 | OK but 10GB is tighter for 14B models |
| RTX 4070 Ti | 12GB | ~$450–550 | Better arch, only worth it over 3090 at a deal |
| RTX 4080 | 16GB | ~$600–700 | Good but expensive vs 3090 for this use case |

### 8GB cards — depends on price

8GB cards (RTX 3070, 3070 Ti, 3060 Ti) **work fine for current 7B models**
(qwen2.5vl:7b + Qwen2.5:latest both fit with ~3GB to spare).
The constraint is future headroom, not today's workload.

- **Free or under ~$50: take it.** GPU inference is more efficient per-call than
  CPU even at 8GB. Use as a bridge; swap when a 3090 comes through.
  Resale on RTX 3070 is still $80–120, so worst case it's neutral.
- **$50–100: probably yes** as a bridge if nothing better is available soon.
- **Market rate ($100+): skip.** A 3060 12GB at $130–160 buys real headroom
  for only $30–60 more — the jump is worth it at that price point.

The wall hits when you want to run `qwen2.5:14b` (8–9GB in Q4) — an 8GB card
leaves no margin. Fine indefinitely if the workload stays at 7B.

### Avoid at any price

- **AMD RX 6000/7000 series** — 16GB VRAM is attractive but ROCm support in Ollama
  is less mature than CUDA; only consider if price is exceptional and you can test first

## PCIe Compatibility

Older motherboards with PCIe 3.0 x16 are fine for inference workloads.
Inference is not bandwidth-bound the way training is — a 3090 in a PCIe 3.0
x16 slot loses ~5% vs PCIe 4.0. Completely acceptable.

## Expected Speedups (7B model, warm)

| Hardware | Approx. tokens/sec | Per-item inference |
|----------|-------------------|--------------------|
| CPU (current) | ~5–8 tok/s | ~18s |
| RTX 3060 12GB | ~40–60 tok/s | ~3–5s |
| RTX 3090 24GB | ~80–120 tok/s | ~1–2s |

Throughput numbers are approximate and model-dependent.
At RTX 3090 speeds, the 8,419-item catalog backlog becomes a ~3–4 hour
overnight run rather than a multi-day CPU crawl.

## When You Upgrade

1. Install NVIDIA drivers + CUDA toolkit
2. Ollama detects the GPU automatically on restart
3. Remove the `ollama_lock` serialization if running one model type at a time
   (lock can stay — it just won't have meaningful wait times)
4. Consider pulling larger models: `ollama pull qwen2.5:14b`, `ollama pull qwen2.5vl:72b`
5. Update `VISION_MODEL` / `TEXT_MODEL` constants in the workers if upgrading models

## Deferred: Multi-GPU / LTSP expansion

See master plan "LTSP fat-client worker expansion" — remote nodes as additional
Ollama workers. Each node needs its own GPU; the Postgres advisory lock already
serializes correctly across multiple hosts on the same database.
