# Research consideration: Perplexity guided and governed research integration

**Status:** retained research / future consideration — no implementation authorized
**Owner:** Dave, with Tigwa as librarian/design steward
**Captured:** 2026-07-20
**Related programs:** proposed PP-EVIDENCE-001; PP-AGENTTRACE-001; future research/tooling work

## Why retain this

Perplexity could add value beyond ordinary web search through frontier-model research with current-source retrieval and citations. The opportunity has two distinct interaction modes that must not be collapsed into one automation design.

## Dave’s clarification: guided research is a first-class mode

Dave explicitly wants the option to conduct guided research: he can observe intermediate work, interject, correct the framing, and steer investigation toward TGW’s own way of looking at a question. This is related to, but different from, unattended/background research automation.

Guided research is not a failure to automate. It is an operator-directed deliberation surface. It should preserve:

- visible working context and source trail;
- interruption and redirection without discarding the prior thread;
- Dave’s ability to refine the research lens, exclusions, or decision question;
- an explicit transition from exploratory discussion to a named retained evidence artifact;
- clear separation between research output, design recommendation, and implementation authority.

Do not treat a browser/UI-guided session as an unsafe substitute for an API credential, nor treat an API job as a substitute for Dave’s active steering.

## Candidate integration lanes

### Lane A — guided Perplexity Research capture

Use Perplexity Pro’s interactive research experience when Dave wants to steer, interrupt, or compare research perspectives. Build only a later, narrow capture/finalization path: Dave explicitly selects a report/thread/output to retain; Tigwa records the source URL or supplied export/text, acquisition provenance, retrieval date, citations available, and its relationship to a TGW decision.

The automation target is capture, provenance, and review—not autonomous control of Dave’s browser session. Do not export cookies, reuse a personal browser profile, or attempt unattended browser-driven research with the Pro session.

### Lane B — official Perplexity MCP, narrow read-only proof of value

Perplexity documents an official stdio MCP server with `perplexity_search`, `perplexity_ask`, `perplexity_research`, and `perplexity_reason`. A later proof of value could expose only the read-only tools through a dedicated API key, explicit cost cap, and a new-session Hermes MCP configuration.

Best fit: agent-assisted interactive research where Tigwa can request bounded source discovery or a cited analysis while retaining the result through the normal evidence workflow.

### Lane C — governed TGW research gateway

A future server-side `tgw research` capability could wrap Perplexity’s API with named request types, source/domain constraints, cost/timeout caps, and durable evidence packets. Every retained output should record the question, requester, prompt/request shape, model/mode, usage/cost, retrieval time, citations, raw output, hash, and review status.

This is the strongest fit for evidence/rebuild discipline, but it requires a separately reviewed design and must not become arbitrary agent web access or unbounded model spend.

### Lane D — structured source discovery

Perplexity’s Search API can return ranked structured results, snippets, dates, and URLs with filters. A future low-cost mode could use it only to discover candidate sources; Tigwa reads selected primary sources and makes source-grounded synthesis separately.

### Lane E — asynchronous Deep Research jobs

Perplexity documents `sonar-deep-research` and an async API flow. This is suitable only for named, high-value questions with a Dave-visible budget, explicit decision owner, preserved citations, and a review gate. It should not become an invisible background task that writes conclusions into plans.

## Known product facts (first-party sources)

- Perplexity Pro and API usage are distinct. Perplexity states that API credits are pay-as-you-go and an API subscription is not required to buy/use them: https://www.perplexity.ai/help-center/en/articles/10354847-api-payment-and-billing.html
- Official MCP documentation describes a stdio MCP server and the four research/search/reasoning tools above: https://docs.perplexity.ai/docs/getting-started/integrations/mcp-server
- Perplexity documents `sonar-deep-research` as exhaustive research across hundreds of sources, with citations and an async request/poll flow: https://docs.perplexity.ai/docs/sonar/models/sonar-deep-research
- The Search API is documented as structured, real-time ranked results with domain/language/region filtering; Perplexity distinguishes it from Sonar’s generated, cited answer: https://docs.perplexity.ai/docs/search/quickstart

## Guardrails for any future pilot

1. Start with a dedicated API key, minimal read-only tool allowlist, explicit spend cap, and revocation procedure.
2. Preserve guided research as a Dave-operated mode; never automate a logged-in Pro browser session or export/reuse browser cookies.
3. Preserve raw external result plus citation/provenance packet before any TGW synthesis; generated claims are not canonical facts by default.
4. Use named research questions and decision owners; no automatic plan changes, source mutations, or task dispatch.
5. Separate source discovery, deep research, guided deliberation, retention, and implementation authorization in the UI/workflow.
6. Pilot with a small curated source set and a disposable/dedicated profile when any browser-backed component is later evaluated.

## Suggested next action

When Dave chooses to revisit, create a narrow pilot proposal comparing Lane A guided-capture against Lane B official MCP plus Lane D source discovery. Specify credential custody, spend limit, artifact schema, citations/raw-output retention, user steering controls, and success criteria before enabling any key or MCP server.
