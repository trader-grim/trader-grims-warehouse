# TIGWA AMENDMENT — Use Gemini 3.1 Flash-Lite as SHCS audit workhorse

**From:** Tigwa
**To:** Claude
**Re:** `TIGWA-PLAN-2026-07-22-sellerhub-audit-execution.md`

Dave surfaced the better model routing. Amend the execution plan:

- **Primary batch model:** stable `gemini-3.1-flash-lite`, direct Gemini API, exact version recorded at canary time.
- **Use it for:** repeated bounded screenshot/document/code-evidence extraction; schema-constrained candidate row generation; classification; normalization; dedupe; missing-evidence detection; and batch-level summaries.
- **Why:** Google documents it as a stable, low-latency/cost-effective multimodal model for high-volume lightweight/data-extraction workflows. It supports text/image/video/audio/PDF input, a 1,048,576-token input limit, structured outputs, thinking, caching, file search, and Batch/Flex inference.
- **Escalation only:** `gemini-2.5-pro` for a small, explicitly costed set of hard cross-surface contradiction, sequencing, or enhancement-synthesis reviews after the evidence batches exist.

This does not relax the evidence boundary. Flash-Lite output is candidate/derived data; it cannot establish `full-parity`, close a gap, or promote a task without raw account evidence, traced TGW evidence, and Tigwa/Dave review.
