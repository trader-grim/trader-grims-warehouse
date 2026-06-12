# TGW Development Workflow

**Status:** v1, 2026-06-10. Defines how AI tools and the operator (Dave) collaborate on TGW
development. Start here; the other docs in this directory go deeper.

| Doc | Covers |
|-----|--------|
| [claude-cli.md](claude-cli.md) | How Claude Code (the CLI) is used today — session ritual, outputs, review gates, prompt templates |
| [next-process.md](next-process.md) | Session handoff, review→commit→deploy loop, and the future Aider/Cline execution tier |
| `research/` | Raw research transcripts (Perplexity) that informed these decisions |

---

## 1. The one-paragraph version

Work is defined in the **todo tracker** (`tgw todo claude` — the canonical task queue; the
master plan is the reference spec, not the queue). **Claude Code** executes tasks in bounded
sessions, producing uncommitted code + tests + doc updates. **Dave reviews every diff** and
controls git history — nothing is committed without his ask. Research and analysis are routed
to cheaper tools (Perplexity, Gemini) so Claude tokens go to code. Once the backlog of small,
well-specified tasks is steady, **Aider** (Claude API + prompt caching) becomes the cheap
execution tier for those, with Claude Code reserved for planning and cross-cutting work.
**Cline** is optional and deferred.

## 2. Tool roster and routing

Route each task to the right tool at design time (PP-MULTIMODEL-001 — the working model since
session 5):

| Tool | Role today | Cost model |
|------|-----------|------------|
| **Claude Code (CLI)** | Primary dev agent: planning, implementation, refactors, debugging, docs. The only tool that writes code right now. | Claude subscription |
| **Perplexity Pro** | External research: eBay APIs, market/SEO analysis, library evaluation. Briefs live in `docs/TGW-Plan-Vault/perplexity/`; run via `tgw perp-run <BRIEF-ID>`. | Flat subscription |
| **Gemini / Antigravity (Google AI Plus)** | Bulk document analysis, data review, large-context one-shots (briefs in `docs/TGW-Plan-Vault/gemini/`); Antigravity adds bounded agentic code tasks + browser-verified UI work. ⚠️ Antigravity CLI **installed 2026-06-10**; Gemini CLI dies 2026-06-18 — 8-day overlap window for side-by-side validation (see next-process.md §3). | Flat subscription |
| **Aider** (future) | Cheap executor of small, well-specified code tasks on Claude API with prompt caching. See [next-process.md](next-process.md). | Pay-as-you-go API, hard budget cap |
| ~~Cline~~ (not planned) | Its niche (agentic IDE + browser-in-the-loop) is covered by Antigravity at zero marginal cost. | — |

Rule of thumb: **research → Perplexity, bulk analysis + bite-sized self-contained tasks →
Gemini/Antigravity, code → Claude Code (later: small/specified → Aider, large/ambiguous →
Claude Code).**

## 3. The loop

```
 ideas / notes ──► Plan-Vault inbox/ + SUGGESTIONS.md
                          │  (processed at session start)
                          ▼
                  Master plan (reference spec)
                          │
                          ▼
              tgw todo claude  ◄── canonical task queue
                          │
                          ▼
            Claude Code session (bounded, 1–3 tasks)
              code + tests + docs, UNCOMMITTED
                          │
                          ▼
              Dave reviews diff  ──► feedback → next session
                          │ approves
                          ▼
              commit (only when Dave asks)
                          │
                          ▼
        deploy: restart tgw-worker@<queue>, tgw health
```

## 4. Principles

1. **The todo tracker is the queue.** Pick work with `tgw todo claude`, update it as you go.
   Don't build task lists in the plan — stale plan tables create operator distraction.
2. **Every diff is human-reviewed.** AI agents never merge or commit on their own. Dave
   controls git history.
3. **Bounded sessions.** One session = one or a few sized tasks, ending in a reviewable state.
   No open-ended "improve things" runs.
4. **Settled architecture is not relitigated.** `CLAUDE.md` § Settled architecture and
   `docs/architecture/overview.md` are constraints, not suggestions.
5. **eBay scopes and live config are operator-only.** Approved OAuth scopes are locked
   (speculative scope edits broke OAuth on 2026-06-05). Config/secrets changes are proposed,
   not applied, unless the task explicitly says otherwise.
6. **Cheap tokens for cheap work.** Don't spend Claude on web research or document summarization;
   that's what the flat-rate subscriptions are for.
