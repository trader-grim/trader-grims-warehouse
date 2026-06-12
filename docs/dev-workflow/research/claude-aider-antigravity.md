# Claude / Aider / Antigravity — research note

**Status:** RECONSTRUCTED 2026-06-10. The original export saved as an empty file (0 bytes);
no copy existed on disk, in Syncthing versions, or in any clipboard store. This replacement
was rebuilt by Claude from live web sources (cited below) and is *more current* than the
research files around it. The Claude/Aider portion is omitted — it confirmed decisions
already recorded in `../README.md` and `../next-process.md`.

---

## 1. What Google Antigravity is (as of June 2026)

Google's agent-first development platform, shipped as four products:

| Product | What it is |
|---------|-----------|
| **Antigravity IDE** | VS Code-fork agentic IDE. Signature feature: agent-driven Chrome with screenshots/recordings ("artifacts") for browser-in-the-loop verification. |
| **Antigravity 2.0 desktop** | Standalone agent-manager desktop app (multiple async agents, server-side orchestration). |
| **Antigravity CLI** | Go-based terminal agent, **successor to Gemini CLI**. Carries over Agent Skills, Hooks, Subagents, Extensions (now "plugins"). Adds async background workflows. Install: `curl -fsSL https://antigravity.google/cli/install.sh \| bash` |
| **Python SDK** | Programmatic agent access. |

Pricing/access: included with Google AI plans (Pro $20/mo; new mid-tier Ultra $100/mo; top
Ultra reduced $250→$200). **Dave's Google AI Plus (Google One bundle) covers it** — same
quota pool as Gemini. Limits are now a single combined rate limit across Flash/Pro models,
drawn down per API pricing; Google has tripled limits twice in 2026 but heavy users still
report hitting weekly caps in a couple of work sessions.

## 2. ⚠️ Forced migration — deadline June 18, 2026

**Gemini CLI stops serving free and AI Pro/Ultra users on 2026-06-18** (enterprise licensees
keep it). TGW's Track 2 delegation uses Gemini CLI, so this is not optional: install
Antigravity CLI and re-verify the Track 2 workflow before the 18th. Skills/hooks/subagents
carry over conceptually, but note **headless mode was a Gemini CLI feature with no confirmed
Antigravity equivalent** — verify scripted/non-interactive use on the TGW host before relying
on it.

## 3. Evaluation for TGW

**a. Antigravity replaces Cline in the future-tools plan.** Cline's deferred niche was
"agentic IDE + browser-in-the-loop, watch every step." Antigravity does exactly that — and at
**$0 marginal cost** under the existing Google subscription, vs Cline burning metered Claude
API tokens. Browser verification is also a *better* fit for TGW's actual browser needs (eBay
Seller Hub checks, Flutter web debugging) than Cline's generic browser tool. Recommendation:
drop Cline to "not planned"; Antigravity takes the third-tool slot.

**b. It widens the Gemini track from analysis to supervised execution.** Gemini already
generated the Flutter scaffold (GEMINI-TASK-003). Antigravity gives that same quota an
agentic harness. Constraints stay as already learned (session 19/20 plan notes): compute
caps with ~5 hr refresh — **keep jobs bite-sized**; route only self-contained items (Round-5
"any agent" items), never eBay-invariant or hot-path code; same branch-per-task + human-merge
rules as every other agent.

**c. What it does *not* change.** Claude Code remains the primary dev agent (architecture,
cross-cutting work, anything touching settled invariants); Aider remains the planned cheap
executor for XS/S specified tasks. Antigravity is the *free, bounded, browser-capable* lane,
not a replacement for either.

**d. Risks.** Product churn is high (plans and limits restructured repeatedly within ~6
months); weekly caps make it unreliable for sustained work; headless/scripting support
unconfirmed; desktop products assume a GUI session (fine on Dave's desktop, not on the
headless host). Treat it as a delegation lane, not infrastructure.

## 4. Actions

1. **Operator, before 2026-06-18:** install Antigravity CLI, sign in with the Google AI Plus
   account, confirm Track 2 briefs still run. (Deadline-driven — todo filed.)
2. Trial: route 1–2 bite-sized self-contained Round-5 items through Antigravity; compare
   review burden vs Claude Code, same as the planned Aider trial.
3. Workflow docs updated: `../README.md` roster + `../next-process.md` § 3.

## Sources

- [Transitioning Gemini CLI to Antigravity CLI — Google Developers Blog](https://developers.googleblog.com/an-important-update-transitioning-gemini-cli-to-antigravity-cli/)
- [Changes to Antigravity plans — antigravity.google](https://antigravity.google/blog/changes-to-antigravity-plans)
- [Google has tripled Gemini usage limits for Antigravity, twice — 9to5Google](https://9to5google.com/2026/05/21/google-has-tripled-gemini-usage-limits-for-antigravity-twice/)
- [Google Antigravity 2.0: Agent-First Dev Platform — apidog](https://apidog.com/blog/google-antigravity-2/)
- [Antigravity vs Gemini CLI — Augment Code](https://www.augmentcode.com/tools/google-antigravity-vs-gemini-cli)
