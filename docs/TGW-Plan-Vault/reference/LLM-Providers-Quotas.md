# LLM Providers & Quotas — settled findings

**Read this before changing any LLM provider, model id, or quota assumption.**
These facts have been independently rediscovered at least three times
(s41 migration, s44 "debunking" session, s45 429-storm root-cause). Do not
re-derive them; if reality changes, update THIS file and cite the log evidence.

Last verified: 2026-07-09 (audit#1143 code-review follow-up, #1252/#1253).
Owner surfaces: `tgw-models.json`, `src/tgw/apis/llm.py`, `src/tgw/quota.py`,
`src/tgw/apis/secrets.py`.

## SUPERSEDES the s45 "OpenRouter is PRIMARY" architecture below

**Dave, 2026-07-08: paid direct-API keys installed for Google, DeepSeek, and
Anthropic — all three flipped to direct-primary, OpenRouter demoted to
fallback-only.** This is the live, current routing — confirmed 2026-07-09 by
reading `/opt/TGW/config/tgw-models.json` directly (its own `_comment` field
carries this decision) and by a live `get_task_model()` call returning
`google_direct`/`deepseek_direct`/`anthropic_direct` for all 7 tasks. The
"OpenRouter is PRIMARY" section below (s45, 2026-07-04) is now HISTORY, not
current state — kept for the Google free-tier post-mortem facts, which are
still true background, not for the provider-priority conclusion.

**Caveat on the Google free-tier facts below:** the "~20 requests/day/model"
measurement was taken under the OLD free-tier account state. Whether that
specific Google API key's account now has paid billing enabled (as opposed
to just being routed as if it does) has not been independently re-verified
against a live 429 — if `tgw health`'s `llm_google` spend starts hitting 300
and 429s return with the old `FreeTier` quotaId, the paid-key assumption is
wrong and the budget must drop back down (see `quota.py _DEFAULT_BUDGETS`
comment — same file, same the-facts-live-here discipline as this doc).

**Where the keys live now:** `secrets_root/tgw.env` (todo #1252) — one
`KEY=value` per provider (`GOOGLE_API_KEY`, `DEEPSEEK_API_KEY`,
`ANTHROPIC_API_KEY`, `OPENROUTER_API_KEY`), sourced into the process
environment by `tgw.config.load_config()`. Every direct-call function reads
its key via `tgw.apis.secrets.get_api_key(provider)` — see
`TGW-Config-Reference.md`'s Secrets Reference section.

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

## HISTORY — the s45 architecture (Dave, 2026-07-04), superseded 2026-07-08

Kept for the free-tier facts and incident history above; the provider
priority below is no longer current — see the SUPERSEDES note at the top.

- ~~OpenRouter is PRIMARY for all cloud vision/LLM tasks~~ — superseded;
  direct providers (`google_direct`/`deepseek_direct`/`anthropic_direct`) are
  primary as of 2026-07-08, OpenRouter is the fallback.
- **The Google free tier (~20 calls/day) is the OPERATOR EMERGENCY RESERVE.**
  `call_model()` falls back openrouter→google_direct ONLY for interactive
  callers (C10 operator lane). Background jobs re-raise and transient-requeue;
  they must never drain the reserve. Purpose: the operator can keep
  identifying/drafting through an OpenRouter outage or credit gap. This
  reserve mechanic is still intact in code as the fallback direction; it's
  just not the primary path anymore.
- ~~llm_google has a daily budget of 20~~ — now 300 (`quota._DEFAULT_BUDGETS`,
  2026-07-08 paid-key decision); see the caveat at the top of this doc.

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
