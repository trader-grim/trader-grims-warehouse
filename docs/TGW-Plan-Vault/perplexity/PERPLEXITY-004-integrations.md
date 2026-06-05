# PERPLEXITY-004 — Third-Party Integration Status

**How to use:** Paste the prompt below into Perplexity. Save the result as a `.md` file in
`docs/TGW-Plan-Vault/inbox/` and PM-intake will file it into the plan.

---

## Prompt

I'm building an inventory automation platform that integrates with several third-party services.
I need current status on four integrations. Please research each and provide cited answers.

### 1. Whisper.cpp — CPU-only voice transcription (Linux x86_64)
- What is the current state of Whisper.cpp as of 2025? Is it actively maintained?
- For a machine with 32GB RAM and no GPU, what model size (tiny/base/small/medium) gives the
  best accuracy/speed tradeoff for short voice memos (5–15 seconds, English only)?
- What is the typical transcription latency for that model on a modern x86_64 CPU?
- Is there a recommended binary distribution or do you always build from source?
- Any 2025 alternatives to Whisper.cpp for CPU-only offline transcription on Linux?

### 2. Discogs API — music/vinyl database
- What are the current Discogs API rate limits as of 2025? (authenticated vs. unauthenticated)
- What authentication is needed for a personal automation tool — OAuth or personal access token?
- Has the Discogs API changed significantly in 2024–2025 (any breaking changes, deprecations)?
- For barcode lookup: what is the endpoint and response format for querying by UPC/EAN?

### 3. IGDB / Twitch Developer Account
- What is the current process to get a Twitch developer account and create an IGDB app in 2025?
- How long does approval typically take? Is it instant or reviewed?
- What credentials does the IGDB API require (client_id + client_secret + OAuth token)?
- Are there rate limits on the free tier? What is the endpoint for game title lookup by name?
- Any changes to the IGDB API in 2024–2025?

### 4. Go-UPC API (go-upc.com)
- What is Go-UPC? Is it a barcode lookup service with a public API?
- What are the current pricing tiers and rate limits?
- What data does it return for a UPC/EAN lookup? How does it compare to UPCitemdb?
- Is it still operational and maintained as of 2025?

**Format:** One section per integration. Include API endpoint examples where relevant, current
rate limits, authentication method, and any gotchas. Include dates on all sources.
