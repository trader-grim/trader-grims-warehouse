# LLM Providers & Quotas — settled findings

**Read this before changing any LLM provider, model id, or quota assumption.**
These facts have been independently rediscovered at least three times
(s41 migration, s44 "debunking" session, s45 429-storm root-cause). Do not
re-derive them; if reality changes, update THIS file and cite the log evidence.

Last verified: 2026-07-04 (session 45). Owner surfaces: `tgw-models.json`,
`src/tgw/apis/llm.py`, `src/tgw/quota.py`.

## The Google free tier — what is actually true

1. **Published limits are a ceiling, not a grant.** Google's docs advertise
   free-tier 1,000 RPD (gemini-2.5-flash-lite) / 250 RPD (gemini-2.5-flash).
   What a project actually receives is doled out **per project**, varying by
   usage tier, billing state, region, and Google's discretion — especially
   after the December 2025 free-tier cuts (50–80% across the board). More API
   keys on the same project do NOT add quota.
2. **This project's real grant is ~20 requests/day/model.** Ground truth from
   Google's own 429 payload (2,171 incidents, 2026-07-04):
   `quotaId: GenerateRequestsPerDayPerProjectPerModel-FreeTier, quotaValue: 20`.
   Observed daily successful-call counts across 07-02/03/04 (22, ~20, 7+13)
   are consistent with 20/day. Resets midnight Pacific.
3. **It applies to ALL models, not just flash-lite.** A session briefly
   concluded flash was exempt because no flash 429s appeared in
   `quota-incidents.jsonl`. That was a logging artifact, not a fact — see
   gotcha below. Debunked 2026-07-04 (s44 night session).
4. **The authoritative per-project number** is shown live in AI Studio:
   https://ai.dev/rate-limit (log in with the project account). Check there
   before believing either the published table or a stale note.

## Logging gotcha that caused the false "flash-lite only" conclusion

When a `google_direct` call fails and falls back to OpenRouter, the
`ai_usage` row records the ORIGINAL provider (`google_direct`) but, if the
fallback also fails, the FALLBACK's error message (e.g. OpenRouter 402). And
`quota-incidents.jsonl` only gets a `llm_google` entry when the Google
exception is recognizably a 429/RESOURCE_EXHAUSTED **at that layer**.
Consequence: absence of a model in the incident log does NOT mean that model
has Google headroom. To measure real Google capacity, count `ai_usage`
`google_direct` rows recorded BEFORE the day's first 429 incident — after
that timestamp, "successful" google_direct rows were actually served by the
OpenRouter fallback.

## The settled architecture (Dave, 2026-07-04 — do not relitigate)

- **OpenRouter is PRIMARY** for all cloud vision/LLM tasks (`tgw-models.json`;
  google models via their `google/*` OpenRouter ids). It is paid
  (~$0.0002/flash-lite call), metered by the key's daily limit (currently $5),
  and reliable.
- **The Google free tier (~20 calls/day) is the OPERATOR EMERGENCY RESERVE.**
  `call_model()` falls back openrouter→google_direct ONLY for interactive
  callers (C10 operator lane). Background jobs re-raise and transient-requeue;
  they must never drain the reserve. Purpose: the operator can keep
  identifying/drafting through an OpenRouter outage or credit gap.
- **The reverse failover (google_direct→openrouter) is kept intact** and is
  circuit-breaker-gated (`quota.precheck('llm_google')`: post-429 stand-down,
  first call after cooldown is the restoration probe). It is dormant while
  OpenRouter is primary. **When a paid Google API key lands**, flip
  `tgw-models.json` back to `google_direct` — no code change needed.
- `llm_google` has a daily budget of 20 in `quota._DEFAULT_BUDGETS` so
  `tgw health` shows real utilization and background callers halt before
  burning the reserve.

## How to re-verify (instead of re-deriving)

```bash
# What is Google actually granting? (per-model 429s + quoted limit)
sudo -u tgw grep RESOURCE_EXHAUSTED /opt/TGW/var/log/quota-incidents.jsonl | tail -3
# Live per-project grant: https://ai.dev/rate-limit (project account login)
# Real Google successes today = google_direct ai_usage rows BEFORE first 429
```

History: s41 moved tasks TO google_direct when a live probe returned 200s
("free tier verified") — the probe was true but measured availability, not
daily capacity. s45 flipped to OpenRouter-primary after the 20/day grant
made google-primary burn a 429 + ~40s retry latency on every backlog job
(2,171 incidents in one day). Full incident: todo #1144,
`inbox/DONE-1144-llm-provider-flip-operator-reserve.md`.
