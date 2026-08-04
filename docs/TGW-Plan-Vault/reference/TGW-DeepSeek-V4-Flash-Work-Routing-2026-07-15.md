# DeepSeek V4 Flash work-routing note

**Status:** source-grounded operating guidance; no provider, Aider, Hermes, or flake configuration has been changed.  
**Owner:** Tigwa  
**Related PP:** PP-KNOWLEDGE-001 / PP-HERMES-EA-001  
**Official source:** https://api-docs.deepseek.com/guides/thinking_mode (read 2026-07-15)  
**Preserved input:** `inbox/tigwa/Deepseek-v4-flash-aider-config-tweaks.txt` — SHA-256 `f7b47ff89b77c2ada884e62bb91299d30e464123868827e6af959ef51b9afb1c`

## Verified API behavior

- Thinking is enabled by default.
- In OpenAI-format requests, toggle it with `extra_body: {"thinking": {"type": "enabled" | "disabled"}}`.
- Thinking-mode effort controls are `reasoning_effort: "high" | "max"`; the documented normal default is `high`. The service may set `max` automatically for some complex agent requests.
- In thinking mode, `temperature`, `top_p`, `presence_penalty`, and `frequency_penalty` have no effect even if accepted for compatibility.
- Thinking output is returned as `reasoning_content` alongside final `content`; multi-round/tool-call continuation rules must be implemented by the client according to the official API guide, not improvised by a task prompt.
- The API is stateless: each multi-round request must supply the needed prior conversation history. Context-burden reduction therefore comes from bounded source packets and deliberate history selection, not an assumption that the provider remembers a prior turn.
- Flash supports tool calls and JSON output in both modes. The official OpenAI-format base URL is `https://api.deepseek.com`.
- The official model page lists a 1M context length and a 384K maximum output for Flash. These are service limits, not a reason to indiscriminately send a whole repository or Master Plan.

## TGW routing policy

Use model work only after deterministic source selection and preservation. Do not use a model to decide whether a source artifact is knowledge.

| Work shape | DeepSeek mode | Required boundary |
|---|---|---|
| Manifests, source metadata extraction, checksum/provenance labeling, simple structured transformation, first-pass routing | thinking **disabled** | deterministic schema; source path/hash retained; no plan/shelf mutation |
| Bounded source synthesis, PP/task briefing, code/test failure triage, cross-document relationship candidates | thinking **enabled**, `high` | exact source packet first; show uncertainty and retain citations/links |
| Cross-subsystem design, non-obvious debugging, safety/production decisions, invariant or API-contract analysis | thinking **enabled**, `max` only when justified | independent review and tests/verification; not a substitute for source evidence or Dave approval |

For the planned `tgw plan brief` path, a model should receive the exact source packet, not the whole Master Plan by default. It must escalate to a full-plan read when broad planning, reconciliation, audit, ambiguity, or Dave’s direction requires it.

## Corrections to the preserved research capture

The capture is a useful lead, but not a configuration authority:

- Its proposed `OPENAI_API_BASE=https://deepseek.com` is inconsistent with the official OpenAI-format base URL `https://api.deepseek.com`. Its Aider model identifiers/configuration syntax were not verified from first-party Aider integration documentation in this review; do not apply them.
- `reasoning_effort: non-thinking` and `low` are not documented controls on the official Thinking Mode page. Disable thinking explicitly for non-thinking work.
- Do not tune sampling parameters while thinking: documented behavior says they have no effect.
- Do not assume cache duration, model limits, or an architect/editor split without current first-party integration documentation and a safe local test.

## Operating objective

Use V4 Flash for inexpensive, bounded work after the knowledge system supplies a small exact packet. Reserve deep thinking for work where it reduces rework more than it consumes context/time. Preserve model inputs/outputs that become planning evidence, but never record secrets, API keys, credentials, or raw chain-of-thought as canonical knowledge.
