<img src="https://r2cdn.perplexity.ai/pplx-full-logo-primary-dark%402x.png" style="height:64px;margin-right:32px"/>

# I want to create a specialized tool in my mcp for simple jobs like context compression summarization data extraction and such using deepseek v4 flash. I noted in the documentation it has a non thinking mode that seems a good fit for the job. Please evaluate, suggest other options. Also enumerate other routine tasks I can apply this tool to.

DeepSeek V4 Flash in non-thinking mode is an excellent fit for a “simple jobs” MCP tool: it’s fast, cheap, supports 1M-token context, and lets you disable the reasoning step cleanly.  For similar roles, GPT‑4.1 mini, Claude Haiku, and current Gemini Flash variants are viable but generally more expensive or with less favorable cost/latency trade-offs, so I’d treat DeepSeek V4 Flash as your primary engine with others as optional fallbacks.[^1_1][^1_2][^1_3][^1_4][^1_5][^1_6][^1_7][^1_8]

## Why DeepSeek V4 Flash fits this role

DeepSeek V4 Flash is a speed‑ and cost‑optimized variant of V4, built as a sparse MoE model with a 1M‑token context window and priced around \$0.14 per million tokens, which is significantly below most closed models.  The preview/API announcement explicitly positions V4‑Flash as a drop‑in replacement for earlier DeepSeek chat models, retaining tool use, JSON output, and 1M context while undercutting traditional pricing.[^1_9][^1_2][^1_10][^1_11][^1_12][^1_1]

The “thinking mode” is optional and can be toggled off, so non‑thinking calls behave like classic LLMs: direct answers, no long internal chain‑of‑thought, which is exactly what you want for cheap summarization, extraction, and formatting passes.  V4‑Flash is also open‑sourced on Hugging Face, so you have the option to self‑host or route through different gateways depending on cost and latency targets.[^1_13][^1_10][^1_3][^1_11]

## Non‑thinking mode details relevant to your tool

DeepSeek V4 exposes three reasoning tiers—Non‑think, Think High, and Think Max—across both Pro and Flash; Non‑think is explicitly described as “direct response, no chain‑of‑thought,” intended for everyday tasks, simple classification/extraction, and low‑latency interfaces.  In non‑thinking mode, responses start with an empty `</think>` tag (or no reasoning block) and then the final answer, which keeps output short and predictable for downstream parsing.[^1_3]

The API example for disabling thinking on V4‑Flash looks like:

```python
response = client.chat.completions.create(
    model="deepseek-v4-flash",
    messages=[{"role": "user", "content": "What's the capital of France?"}],
    extra_body={"thinking": {"type": "disabled"}}
)
```

This gives you a clean knob in your MCP server: every “simple job” tool call can set `thinking.type = "disabled"`, and you can selectively escalate to `enabled` (Think High) or `max` for rare “hard reasoning” tasks without changing the model ID.[^1_2][^1_3]

## Alternative low‑cost models to consider

**GPT‑4.1 mini (OpenAI)**
GPT‑4.1 mini is a smaller, faster GPT‑4.1 variant with a 1M‑token context window and “low latency without a reasoning step,” making it conceptually similar to DeepSeek’s non‑thinking mode.  Its pricing is roughly \$0.40 per 1M input tokens and \$1.60 per 1M output tokens, so it’s about 3–4× more expensive on input and significantly more expensive on output than V4‑Flash.  For a dedicated cheap summarization/compression tool, the cost delta vs DeepSeek is material; GPT‑4.1 mini makes more sense as a higher‑reliability fallback or when you’re already heavily invested in OpenAI tooling.[^1_14][^1_15][^1_6]

**Gemini Flash (Google, current generations)**
Gemini 2.0 Flash had a 1M‑token context window and was priced around \$0.10 per 1M input tokens and \$0.40 per 1M output tokens, positioning it as a “fast/cheap” long‑context model.  However, Google retired the specific gemini‑2.0‑flash model in June 2026, directing developers to newer Gemini Flash generations, which tend to be somewhat pricier.  For example, more recent Gemini 3 Flash variants are tracked at around \$0.5 per 1M input and \$3 per 1M output, still long‑context but notably more expensive than DeepSeek Flash on both legs.[^1_4][^1_16][^1_5][^1_7]

**Claude Haiku (Anthropic 3.5/4.5 family)**
Claude Haiku is Anthropic’s lightweight model tier, with a 200K‑token context window that’s smaller than the 1M range of V4‑Flash and GPT‑4.1 mini.  Pricing for Claude Haiku 3.5 is around \$0.80 per 1M input tokens and \$4.00 per 1M output tokens, making it substantially more expensive than DeepSeek V4 Flash for routine batch processing.[^1_17][^1_18][^1_8]

Given your use case (high‑volume, simple tasks inside an agent framework), DeepSeek V4‑Flash offers the best blend of 1M context + very low per‑token cost; GPT‑4.1 mini and Gemini/Claude are good cross‑vendor fallbacks if you want resilience against any single provider’s outages or regressions.[^1_5][^1_8][^1_1][^1_14]

## Design ideas for your MCP “simple jobs” tool

For MCP, I’d think of this as a single generic tool with a small operation enum, all backed by DeepSeek V4‑Flash non‑thinking mode:

- **Operations:** `"summarize"`, `"compress_context"`, `"extract_fields"`, `"classify"`, `"rewrite"`, `"rank/snippet_score"`.
- **Inputs:** raw text (possibly very long, up to ~1M tokens), optional structured instructions, and optional JSON schema or field list.
- **Outputs:** strictly JSON, using `response_format` / JSON mode supported by DeepSeek.[^1_11]

In each call, your server would set `model="deepseek-v4-flash"`, force `extra_body={"thinking": {"type": "disabled"}}`, and when you want structure, set something like `response_format={"type": "json_object"}` so the MCP client can reliably consume results.  You can then wire Hermes‑style routing rules: this MCP tool is the default for “cheap transform” tasks, while harder reasoning goes to a separate high‑reasoning tool (e.g., V4‑Pro with Think High/Max or Claude Opus extended thinking) when the agent classifies a job as complex.[^1_19][^1_2][^1_3][^1_11]

For TGW specifically, this MCP tool could sit near your queue workers: they enqueue “LLM micro‑jobs” (summarize listing, extract attributes, normalize titles, etc.), and the MCP DeepSeek server drains those queues with non‑thinking calls to keep costs predictable.

## Routine tasks this tool can cover

Beyond context compression, summarization, and data extraction, here are routine tasks that fit perfectly into a V4‑Flash non‑thinking MCP tool:

- **Title and snippet generation:** Generate concise listing titles, subject lines, or search snippets from verbose item descriptions.
- **Tagging and labeling:** Assign categories, condition tags (“new/open box/used”), style tags, or marketplace‑specific item specifics from free text.
- **Schema filling / normalization:** Convert semi‑structured blobs (old Magento descriptions, recoll search hits, JSON fragments) into your canonical `Item` or `Listing` schema with consistent keys and enums.
- **HTML/Markdown cleaning:** Strip boilerplate, convert HTML descriptions to clean Markdown, or sanitize markup for different marketplaces.
- **Classification and routing:** Decide which worker queue or pipeline stage a piece of text should go to (e.g., “pricing review”, “photo QA”, “needs human check”).
- **Query rewriting:** Rewrite user queries or internal search queries into better keyword sets for recoll, Postgres full‑text, or marketplace search APIs.
- **RAG snippet scoring:** Given a question + candidate snippets (from recoll or Postgres), rank snippets by relevance and optionally return a short synthesized answer; non‑thinking mode is enough for ranking + one‑paragraph answers.
- **Dedup / near‑duplicate detection (text‑level):** Given multiple descriptions or titles, ask for a judgment on whether they describe the same item or listing, and pick a canonical phrasing.
- **Diff explanation:** Given before/after texts (pricing, description edits), generate a short explanation or change log entry.
- **Basic log/event summarization:** Condense long worker logs or error traces into a human‑readable summary for dashboards or notifications.
- **Template filling:** Given item attributes and marketplace rules, fill in listing templates (bullet points, shipping/return policy text, etc.) in a consistent style.
- **Lightweight code/comment assists:** For simple Python/SQL snippets in your tooling, ask for one‑sentence explanations or docstrings without paying for heavy reasoning modes.

All of these tasks are “single‑pass, low‑stakes transforms” where you mostly care about speed, cost, and reasonable quality—not frontier‑level reasoning—so DeepSeek V4‑Flash non‑thinking is right in the sweet spot.[^1_1][^1_3]

If you’d like, next step I can sketch a concrete MCP tool schema (commands, JSON I/O, and recommended prompts) optimized specifically for DeepSeek V4‑Flash’s non‑thinking mode and TGW’s inventory/listing workflows.
<span style="display:none">[^1_20][^1_21][^1_22][^1_23][^1_24][^1_25][^1_26][^1_27][^1_28][^1_29][^1_30][^1_31][^1_32][^1_33][^1_34][^1_35][^1_36][^1_37][^1_38][^1_39][^1_40]</span>

<div align="center">⁂</div>

[^1_1]: https://webscraft.org/blog/deepseek-v4-flash-u-2026-scho-tse-skilki-koshtuye-i-yak-zapustiti-bez-gpu?lang=en

[^1_2]: https://api-docs.deepseek.com/news/news260424/

[^1_3]: https://framia.converge.ai/page/en-US/news/deepseek-v4-thinking-modes

[^1_4]: https://aiapiprices.com/gemini-2-0-flash-pricing/

[^1_5]: https://tokenrate.dev/models/gemini-2-0-flash

[^1_6]: https://developers.openai.com/api/docs/models/gpt-4.1-mini

[^1_7]: https://cloudprice.net/models/google-gemini-2-flash

[^1_8]: https://langcopilot.com/claude-haiku-3.5-vs-claude-sonnet-3.7-pricing

[^1_9]: https://www.remio.ai/post/deepseek-v4-pro-vs-flash-what-launched-what-changed-and-the-huawei-chip

[^1_10]: https://help.apiyi.com/en/deepseek-v4-flash-api-launch-guide-en.html

[^1_11]: https://github.com/tryigit/cleveres-ai/blob/main/models/deepseek-v4.md

[^1_12]: https://x.com/deepseek_ai/status/2047516945466188072

[^1_13]: https://huggingface.co/deepseek-ai/DeepSeek-V4-Flash

[^1_14]: https://llm-stats.com/models/gpt-4.1-mini-2025-04-14

[^1_15]: https://pricepertoken.com/pricing-page/model/openai-gpt-4.1-mini

[^1_16]: https://ai-tldr.dev/models/gemini-2-0-flash/

[^1_17]: https://www.finout.io/blog/anthropic-api-pricing

[^1_18]: https://teamai.com/blog/large-language-models-llms/understanding-different-claude-models/

[^1_19]: https://platform.claude.com/docs/en/about-claude/models/overview

[^1_20]: https://www.developersdigest.tech/blog/deepseek-v4-developer-guide

[^1_21]: https://www.datacamp.com/es/tutorial/deepseek-v4-api-tutorial

[^1_22]: https://dev.to/jangwook_kim_e31e7291ad98/deepseek-v4-pro-and-v4-flash-migration-guide-and-api-setup-3bpb

[^1_23]: https://deepseekai.guide/guides/deepseek-features/

[^1_24]: https://chat-deep.ai/models/deepseek-v4/

[^1_25]: https://pixverse.ai/en/blog/deepseek-v4-multimodal-model-coming-to-pixverse

[^1_26]: https://wavespeed.ai/blog/posts/deepseek-v4-pro-vs-flash/

[^1_27]: https://modelcompare.dev/fr/models/google/gemini-2-0-flash

[^1_28]: https://cloud.google.com/gemini-enterprise-agent-platform/generative-ai/pricing

[^1_29]: https://ai.google.dev/gemini-api/docs/pricing

[^1_30]: https://www.sim.ai/models/google/gemini-2-0-flash

[^1_31]: https://www.orcarouter.ai/models/openai/gpt-4.1-mini

[^1_32]: https://costgoat.com/pricing/gemini-api

[^1_33]: https://platform.openai.com/docs/pricing

[^1_34]: https://openai.com/index/gpt-4-1/

[^1_35]: https://platform.claude.com/docs/en/about-claude/pricing

[^1_36]: https://metatext.io/llm/digitalocean-anthropic-claude-3.7-sonnet

[^1_37]: https://compare-ai.foundtt.com/en/claude3-5-haiku/claude-3-7-s-e-t/

[^1_38]: https://www.morphllm.com/claude-context-window

[^1_39]: https://platform.claude.com/docs/en/build-with-claude/context-windows

[^1_40]: https://www.mindstudio.ai/blog/flat-rate-long-context-pricing-anthropic-claude


---

# yes please

You can model this as a single generic MCP tool with an `operation` enum, structured JSON I/O, and a DeepSeek V4‑Flash call that always sets `thinking` to disabled and `response_format` to JSON.  Below is a concrete design you can drop into your MCP server and adapt to TGW’s workflows.[^2_1][^2_2][^2_3][^2_4]

## Tool concept

Create one tool, e.g. `tgw_simple_llm_jobs`, that handles all “cheap transforms”:

- Backed by `deepseek-v4-flash` with non‑thinking mode.[^2_3][^2_4][^2_5]
- Always returns JSON so Hermes/clients can reason about outputs reliably.[^2_2][^2_6][^2_7]
- Operations: `summarize`, `compress_context`, `extract_fields`, `classify`, `rewrite`, `rank_snippets`, `log_summary`, etc.

This keeps your MCP surface small and lets the model pick operations via tool arguments rather than separate tools.[^2_8][^2_1]

## MCP tool schema

In MCP, tools are defined as JSON Schema objects with `name`, `description`, and `inputSchema`.  A good starting point:[^2_1][^2_8]

```ts
{
  name: "tgw_simple_llm_jobs",
  description: "Fast, low-cost DeepSeek V4-Flash non-thinking transform tasks: summarization, compression, extraction, classification, rewriting.",
  inputSchema: {
    type: "object",
    properties: {
      operation: {
        type: "string",
        enum: [
          "summarize",
          "compress_context",
          "extract_fields",
          "classify",
          "rewrite",
          "rank_snippets",
          "log_summary"
        ]
      },
      text: { type: "string" },
      instructions: {
        type: "string",
        description: "Optional extra guidance for the operation."
      },
      schema: {
        type: "object",
        description: "JSON Schema or field description for extract_fields/classify."
      },
      label_set: {
        type: "array",
        items: { type: "string" },
        description: "Allowed labels for classification."
      },
      items: {
        type: "array",
        items: { type: "string" },
        description: "Candidate snippets/titles for ranking."
      },
      max_output_tokens: {
        type: "integer",
        description: "Optional cap on model output length."
      }
    },
    required: ["operation", "text"]
  }
}
```

On `call_tool`, you branch on `operation` but still call the same DeepSeek endpoint. The MCP result is a `CallToolResult` with a single `TextContent` whose `text` is a JSON string (or you can parse JSON and embed structured data if your MCP runtime supports it).[^2_8][^2_1]

## DeepSeek V4‑Flash call template (non‑thinking + JSON)

DeepSeek’s JSON output mode is enabled by `response_format = {"type": "json_object"}` plus a prompt that explicitly asks for JSON and gives an example.  Thinking mode can be disabled via a `thinking` parameter (Anthropic‑compatible style) or the provider’s equivalent.[^2_6][^2_4][^2_9][^2_2]

Pseudocode for your MCP server (Python‑ish, using a DeepSeek/OpenAI‑style client):

```python
def run_deepseek_simple_job(args: dict) -> dict:
    operation = args["operation"]
    text = args["text"]
    instructions = args.get("instructions", "")
    schema = args.get("schema")
    label_set = args.get("label_set")
    items = args.get("items")

    system_prompt = build_system_prompt(operation, schema, label_set)
    user_prompt = build_user_prompt(operation, text, instructions, items)

    response = client.chat.completions.create(
        model="deepseek-v4-flash",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        response_format={"type": "json_object"},
        extra_body={"thinking": {"type": "disabled"}},
        max_tokens=args.get("max_output_tokens", 2048),
    )

    # DeepSeek JSON mode returns a JSON string in message.content. [web:42][web:44]
    return json.loads(response.choices[^2_0].message.content)
```

Then your MCP `call_tool` handler wraps this in a `CallToolResult`:

```python
@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    if name == "tgw_simple_llm_jobs":
        result_obj = run_deepseek_simple_job(arguments)
        return [types.TextContent(type="text", text=json.dumps(result_obj))]
    raise ValueError(f"Tool not found: {name}")
```

This pattern matches MCP’s recommended structure: tools return content; errors are surfaced via `isError` rather than protocol errors.[^2_1][^2_8]

## Operation modes \& prompt patterns

Below are concrete patterns for each operation—these are what `build_system_prompt` and `build_user_prompt` should implement.

### Summarize

Use for human‑facing summaries of descriptions, docs, logs.

**System prompt:**

> You are a fast, low-cost summarization engine for Trader Grim’s Warehouse.
> Always respond with a single JSON object.
> Example:
> {"summary": "short summary here", "key_points": ["point 1", "point 2"]}

**User prompt:**

> Operation: "summarize"
> Additional instructions: `<instructions>`
> Input text:
> ```  > <text>   >```

You can let `instructions` specify style and length, e.g. “3 bullet points max, no marketing fluff.”

### Compress context

Use for “RAG compression”: keep only what’s useful for later reasoning.

**System prompt:**

> You compress long context into a compact representation that preserves key facts, entities, and relationships.
> Respond as JSON only.
> Example:
> {
>   "entities": [{"name": "...", "type": "..."}],
>   "facts": ["...", "..."],
>   "summary": "..."
> }

**User prompt:**

> Operation: "compress_context"
> Compression goal: preserve information needed for future reasoning, discard narrative and repetition.
> Input text:
> ```  > <text>   >```

### Extract fields

Use for mapping descriptions to your TGW item schema or marketplace fields.

DeepSeek JSON mode works best when you show an example and ask for valid JSON only.[^2_7][^2_2][^2_6]

**System prompt:**

> You extract structured fields from unstructured text.
> Respond with a single JSON object matching this schema:
> `<schema or example JSON>`
> Do not add extra fields. Do not include explanations.

**User prompt:**

> Operation: "extract_fields"
> Instructions: `<instructions>`
> Input text:
> ```  > <text>   >```

For example, your schema might include `title`, `brand`, `model`, `condition`, `category`, `key_features`, etc.

### Classify

Use to assign labels like category, condition, routing decision.

**System prompt:**

> You perform text classification into one of the allowed labels.
> Respond as JSON only.
> Example: {"label": "USED", "confidence": 0.92, "reason": "short rationale"}

**User prompt:**

> Operation: "classify"
> Allowed labels: `<label_set>`
> Instructions: `<instructions>`
> Input text:
> ```  > <text>   >```

This keeps the result easy to post‑process in your queues.

### Rewrite

Use for title normalization, description cleanup, style conversion.

**System prompt:**

> You rewrite text according to instructions.
> Respond as JSON only.
> Example: {"rewritten": "new text", "notes": "optional notes"}

**User prompt:**

> Operation: "rewrite"
> Rewrite instructions: `<instructions>`
> Input text:
> ```  > <text>   >```

You can standardize things like: “Rewrite as eBay title under 80 characters, no symbols other than hyphen.”

### Rank snippets

Use for relevance ranking in RAG/recoll pipelines or candidate title selection.

**System prompt:**

> You rank candidate snippets by relevance to a query.
> Respond as JSON only.
> Example:
> {
>   "ranked": [
>     {"index": 0, "score": 0.93},
>     {"index": 2, "score": 0.71},
>     {"index": 1, "score": 0.40}
>   ]
> }

**User prompt:**

> Operation: "rank_snippets"
> Query:
> ```  > <text>   >```
> Candidates:
> ```  > <items array with numbered entries>   >```

The non‑thinking mode is sufficient for this kind of lightweight scoring.[^2_10][^2_3]

### Log summary

Use for condensing worker logs, pipeline traces, or error reports.

**System prompt:**

> You summarize logs into a compact diagnostic view.
> Respond as JSON only.
> Example:
> {
>   "summary": "high-level description of what happened",
>   "errors": ["error 1", "error 2"],
>   "suggested_actions": ["action 1", "action 2"]
> }

**User prompt:**

> Operation: "log_summary"
> Input logs:
> ```  > <text>   >```

## TGW‑specific routine jobs you can wire to this tool

Once you have `tgw_simple_llm_jobs` in MCP, you can point a lot of TGW routines at it:

- Migrating legacy Magento descriptions into TGW’s canonical schema via `extract_fields`.
- Generating compressed RAG chunks from large photo metadata JSONs and item histories via `compress_context`.
- Auto‑classifying items into eBay categories/conditions using `classify` + label sets derived from your taxonomy.
- Normalizing titles/descriptions for each marketplace using `rewrite` with marketplace‑specific instructions.
- Ranking recoll hits or Postgres full‑text results per query using `rank_snippets` before heavier reasoning.
- Summarizing long worker logs or pipeline traces for dashboards using `log_summary` and `summarize`.

All of these rely on short, deterministic JSON outputs, which DeepSeek’s JSON mode and V4‑Flash’s low‑latency non‑thinking behavior are designed to support.[^2_4][^2_2][^2_6][^2_3]

If you want, I can next help you translate this into an actual MCP server snippet (Python or TypeScript) with concrete `build_system_prompt`/`build_user_prompt` implementations tuned to your TGW schemas.
<span style="display:none">[^2_11][^2_12][^2_13][^2_14][^2_15][^2_16]</span>

<div align="center">⁂</div>

[^2_1]: https://modelcontextprotocol.info/docs/concepts/tools/

[^2_2]: https://api-docs.deepseek.com/guides/json_mode/

[^2_3]: https://docs.empiriolabs.ai/models/deepseek-v4-flash

[^2_4]: https://deepseeksr1.com/api-docs/

[^2_5]: https://docs.api.nvidia.com/nim/reference/deepseek-ai-deepseek-v4-flash

[^2_6]: https://deepseek-usa.ai/docs/deepseek-json-output/

[^2_7]: https://runware.ai/docs/models/deepseek-v4-flash

[^2_8]: https://modelcontextprotocol.io/specification/2025-11-25/server/tools

[^2_9]: https://docs.evolink.ai/en/api-manual/language-series/deepseek-v4/deepseek-v4-messages

[^2_10]: https://framia.converge.ai/page/en-US/news/deepseek-v4-thinking-modes

[^2_11]: https://docs.aimlapi.com/api-references/text-models-llm/deepseek/deepseek-v4-flash

[^2_12]: https://deepseekai.guide/api/deepseek-api-documentation/

[^2_13]: https://github.com/modelcontextprotocol/modelcontextprotocol/blob/b98f9805e963af7f67f158bdfa760078be4675a3/schema/2025-06-18/schema.ts

[^2_14]: https://huggingface.co/datasets/TeichAI/DeepSeek-v4-Flash-Chat/blob/main/README.md

[^2_15]: https://huggingface.co/deepseek-ai/DeepSeek-V4-Flash/blob/main/config.json

[^2_16]: https://gist.github.com/crazyrabbitLTC/4f61848ceb5807a7f05b81c6551a3285


---

# that would be great

Below is a concrete Python MCP server sketch using the official MCP Python SDK and DeepSeek V4‑Flash non‑thinking mode for “simple jobs.” You can adapt it to TGW and plug it into Hermes.[^3_1][^3_2][^3_3]

## Assumptions

- You’re using the MCP Python SDK (`mcp.server.fastmcp.FastMCP`).[^3_2][^3_3]
- You have a DeepSeek‑compatible client (OpenAI‑style) available as `deepseek_client`.[^3_4][^3_5][^3_6]
- You want one tool: `tgw_simple_llm_jobs`, with an `operation` enum and structured JSON I/O.


## Server skeleton

```python
# server.py
import json
import os
from typing import Optional, List, Dict, Any

from mcp.server.fastmcp import FastMCP  # Official Python SDK. [web:57][web:56]
from openai import OpenAI              # Or DeepSeek's SDK / compatible client. [web:46][web:49]

DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY")
deepseek_client = OpenAI(
    api_key=DEEPSEEK_API_KEY,
    base_url="https://api.deepseek.com"  # adjust if you use a proxy/gateway
)

mcp = FastMCP("TGW Simple LLM Jobs", json_response=True)  # json_response helps some clients. [web:57]
```


## Tool definition in MCP

FastMCP uses decorators: a `@mcp.tool()` function with type hints becomes a tool, with arguments auto‑translated into JSON Schema.[^3_3][^3_2]

```python
@mcp.tool()
def tgw_simple_llm_jobs(
    operation: str,
    text: str,
    instructions: Optional[str] = "",
    schema: Optional[Dict[str, Any]] = None,
    label_set: Optional[List[str]] = None,
    items: Optional[List[str]] = None,
    max_output_tokens: Optional[int] = 2048,
) -> Dict[str, Any]:
    """
    Fast, low-cost DeepSeek V4-Flash non-thinking transform tasks:
    summarization, context compression, field extraction, classification,
    rewriting, snippet ranking, and log summarization.
    """
    system_prompt = build_system_prompt(operation, schema, label_set)
    user_prompt = build_user_prompt(operation, text, instructions, items)

    response = deepseek_client.chat.completions.create(
        model="deepseek-v4-flash",                     # Flash variant. [web:47][web:54]
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        response_format={"type": "json_object"},       # JSON mode. [web:42][web:44]
        extra_body={"thinking": {"type": "disabled"}}, # Non-thinking mode. [web:49][web:55]
        max_tokens=max_output_tokens,
    )

    content = response.choices[^3_0].message.content
    # DeepSeek JSON mode returns a JSON string; parse it. [web:42][web:44]
    return json.loads(content)
```

This gives you a single MCP tool with a flexible signature; Hermes or Claude/Cursor can call it with different `operation` values for different simple jobs.[^3_7][^3_2]

## Prompt builders

Implement prompt builders as pure functions so you can version and tune them separately.

```python
def build_system_prompt(
    operation: str,
    schema: Optional[Dict[str, Any]],
    label_set: Optional[List[str]],
) -> str:
    if operation == "summarize":
        return (
            "You are a fast, low-cost summarization engine for Trader Grim's Warehouse.\n"
            "Always respond with a single JSON object.\n"
            "Example:\n"
            '{ "summary": "short summary here", "key_points": ["point 1", "point 2"] }\n'
        )
    if operation == "compress_context":
        return (
            "You compress long context into a compact representation that preserves key "
            "facts, entities, and relationships.\n"
            "Respond as a single JSON object.\n"
            "Example:\n"
            '{ "entities": [{"name": "...", "type": "..."}], "facts": ["...", "..."], "summary": "..." }\n'
        )
    if operation == "extract_fields":
        schema_json = json.dumps(schema or {}, ensure_ascii=False)
        return (
            "You extract structured fields from unstructured text.\n"
            "Respond with a single JSON object matching this schema or example:\n"
            f"{schema_json}\n"
            "Do not add extra fields. Do not include explanations.\n"
        )
    if operation == "classify":
        labels_str = ", ".join(label_set or [])
        return (
            "You perform text classification into one of the allowed labels.\n"
            f"Allowed labels: {labels_str}\n"
            "Respond as JSON only.\n"
            'Example: { "label": "USED", "confidence": 0.92, "reason": "short rationale" }\n'
        )
    if operation == "rewrite":
        return (
            "You rewrite text according to instructions.\n"
            "Respond as JSON only.\n"
            'Example: { "rewritten": "new text", "notes": "optional notes" }\n'
        )
    if operation == "rank_snippets":
        return (
            "You rank candidate snippets by relevance to a query.\n"
            "Respond as JSON only.\n"
            'Example: { "ranked": [ {"index": 0, "score": 0.93}, {"index": 2, "score": 0.71} ] }\n'
        )
    if operation == "log_summary":
        return (
            "You summarize logs into a compact diagnostic view.\n"
            "Respond as JSON only.\n"
            '{ "summary": "high-level description", "errors": ["error 1"], '
            '"suggested_actions": ["action 1"] }\n'
        )

    # Fallback / unknown operation
    return (
        "You perform a generic JSON transform task for Trader Grim's Warehouse.\n"
        "Respond with an appropriate single JSON object for the request.\n"
    )
```

```python
def build_user_prompt(
    operation: str,
    text: str,
    instructions: Optional[str],
    items: Optional[List[str]],
) -> str:
    instructions = instructions or ""

    if operation in ("summarize", "compress_context", "log_summary"):
        return (
            f"Operation: {operation}\n"
            f"Additional instructions: {instructions}\n\n"
            "Input text:\n"
            "```text\n"
            f"{text}\n"
            "```\n"
        )
    if operation == "extract_fields":
        return (
            "Operation: extract_fields\n"
            f"Instructions: {instructions}\n\n"
            "Input text:\n"
            "```text\n"
            f"{text}\n"
            "```\n"
        )
    if operation == "classify":
        return (
            "Operation: classify\n"
            f"Instructions: {instructions}\n\n"
            "Input text:\n"
            "```text\n"
            f"{text}\n"
            "```\n"
        )
    if operation == "rewrite":
        return (
            "Operation: rewrite\n"
            f"Rewrite instructions: {instructions}\n\n"
            "Input text:\n"
            "```text\n"
            f"{text}\n"
            "```"
        )
    if operation == "rank_snippets":
        items = items or []
        items_block = "\n".join(f"{i}: {s}" for i, s in enumerate(items))
        return (
            "Operation: rank_snippets\n"
            "Query text:\n"
            "```text\n"
            f"{text}\n"
            "```\n\n"
            "Candidate snippets (indexed):\n"
            "```text\n"
            f"{items_block}\n"
            "```"
        )

    # Generic fallback prompt
    return (
        f"Operation: {operation}\n"
        f"Instructions: {instructions}\n\n"
        "Input text:\n"
        "```text\n"
        f"{text}\n"
        "```"
    )
```

These prompts follow JSON‑mode recommendations: explicitly request JSON, show examples, and keep roles simple.[^3_8][^3_9][^3_10]

## Running the MCP server

FastMCP can run over stdio or HTTP/SSE; for Claude Desktop and Hermes‑style agents, stdio or `streamable-http` both work.[^3_11][^3_12][^3_2]

```python
if __name__ == "__main__":
    # stdio transport for Claude Desktop / local tools:
    mcp.run(transport="stdio")
    # or HTTP/SSE:
    # mcp.run(transport="streamable-http", host="0.0.0.0", port=8000)
```

You then register this server in your MCP client configuration (Claude Desktop, Hermes, etc.) with the command (e.g. `["python", "server.py"]` or `["uv", "run", "server.py"]`).[^3_13][^3_12]

## How this slots into your ecosystem

With this server:

- Hermes (or your TGW orchestrator) can route all “simple transforms” to `tgw_simple_llm_jobs`, with `operation` chosen by the agent.
- Requests stay cheap because you always hit DeepSeek V4‑Flash in non‑thinking mode with JSON output.[^3_5][^3_14][^3_8]
- You can add more operations later (e.g., `dedup_titles`, `compare_descriptions`) by updating `build_system_prompt`/`build_user_prompt` and keeping the MCP tool signature stable.

If you tell me your actual TGW item schema (fields you care about), I can help you specialize the `extract_fields` prompts and JSON examples to make extraction much more reliable out of the gate.
<span style="display:none">[^3_15][^3_16][^3_17][^3_18][^3_19][^3_20][^3_21][^3_22][^3_23]</span>

<div align="center">⁂</div>

[^3_1]: https://github.com/modelcontextprotocol/python-sdk

[^3_2]: https://py.sdk.modelcontextprotocol.io/

[^3_3]: https://realpython.com/videos/implementing-mcp-tools/

[^3_4]: https://deepseekai.guide/api/deepseek-api-documentation/

[^3_5]: https://deepseeksr1.com/api-docs/

[^3_6]: https://docs.evolink.ai/en/api-manual/language-series/deepseek-v4/deepseek-v4-messages

[^3_7]: https://modelcontextprotocol.info/docs/concepts/tools/

[^3_8]: https://api-docs.deepseek.com/guides/json_mode/

[^3_9]: https://deepseek-usa.ai/docs/deepseek-json-output/

[^3_10]: https://runware.ai/docs/models/deepseek-v4-flash

[^3_11]: https://northflank.com/blog/how-to-build-and-deploy-a-model-context-protocol-mcp-server

[^3_12]: https://modelcontextprotocol.io/examples

[^3_13]: https://www.youtube.com/watch?v=JF14z6XO4Ho

[^3_14]: https://docs.empiriolabs.ai/models/deepseek-v4-flash

[^3_15]: https://github.com/ruslanmv/Simple-MCP-Server-with-Python

[^3_16]: https://www.youtube.com/watch?v=Ek8JHgZtmcI

[^3_17]: https://dev.to/m_sea_bass/practical-guide-to-mcp-model-context-protocol-in-python-ijd

[^3_18]: https://www.youtube.com/watch?v=IuZk3j-D_C0

[^3_19]: https://www.freecodecamp.org/news/how-to-build-your-own-mcp-server-with-python/

[^3_20]: https://github.com/ComposioHQ/awesome-claude-skills/blob/master/mcp-builder/reference/python_mcp_server.md?plain=1

[^3_21]: https://www.coderslexicon.com/building-your-own-model-context-protocol-mcp-server-with-node-and-python/

[^3_22]: https://machinelearningmastery.com/building-a-simple-mcp-server-in-python/

[^3_23]: https://medium.com/data-engineering-with-dremio/building-a-basic-mcp-server-with-python-4c34c41031ed

