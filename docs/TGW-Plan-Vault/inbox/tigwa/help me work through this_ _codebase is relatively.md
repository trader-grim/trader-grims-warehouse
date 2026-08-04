<img src="https://r2cdn.perplexity.ai/pplx-full-logo-primary-dark%402x.png" style="height:64px;margin-right:32px"/>

# help me work through this:

codebase is relatively moderate but it is highly integrated. Looking for machine oriented data stores to improve code results in a plan-invariant development processTo achieve a plan-invariant development process—where
your AI agents or systems generate correct, deterministic code
regardless of the specific execution plan or prompt structure—you must
move away from standard vector stores. Because your codebase is moderate
but highly integrated, you need machine-oriented data stores that
capture semantic graph topology, static invariants, and dynamic execution traces. The architecture requires an automated context store. This system decouples code semantic meaning from the underlying storage mechanism. ┌────────────────────────────────────────────────────────┐
│               Plan-Invariant AI Agent                  │
└───────────────────────────┬────────────────────────────┘
│ Queries Machine Context
▼
┌────────────────────────────────────────────────────────┐
│                 UNIFIED CONTEXT STORE                  │
├───────────────────────────┼────────────────────────────┤
│ 1. Heterogeneous Graph    │ 2. Relational/SMT Store    │
│  (Structure/Topology)     │  (Invariants/Guarantees)   │
└───────────────────────────┴────────────────────────────┘
The optimal machine-oriented data stores and graph schemas to implement this approach are detailed below.1. Heterogeneous Graph Databases (The Structural Layer)Highly
integrated codebases suffer when context is chunked blindly. A
machine-oriented graph store preserves the precise network of
relationships (AST, call graphs, and dependency tracking). The Stack: Neo4j, AWS Neptune, or ** FalkorDB** paired with Tree-sitter for code parsing. The Schema: Store code as a Heterogeneous Repository Knowledge Graph. Nodes represent symbols, classes, files, and functions. Edges must capture explicit CALLS, INHERITS, and DEPENDS_ON relationships. Why it enables Plan-Invariance:
Instead of relying on an LLM to "guess" how a change in Module A
ripples into Module B, your agent issues a graph query (e.g., Cypher) to
extract the exact, invariant upstream and downstream dependency bounds. 
2. Relational Databases with Strict Constraints (The Invariant Layer)To
guarantee code correctness without risking AI hallucinations, you need a
data store capable of holding deterministic state metadata and logical
definitions. The Stack: PostgreSQL (leveraging specialized indexing or JSONB dual-model persistence). The Schema:
A versioned schema that stores structural specification metrics,
business logic constraints, type constraints, and system boundaries
(e.g., "Counter X must never be negative"). Why it enables Plan-Invariance: By pairing a relational store of specifications with an automated verifier—such as Satisfiability Modulo Theories (SMT) solvers like Z3—the
engine automatically prunes any AI-generated code paths that violate
state invariants. The plan or prompt doesn't matter; the code is
structurally forced to comply with the database definitions. 
3. Ephemeral Operational Data Stores (The Trace Layer)Static
analysis alone cannot solve deep integration bugs. You need data stores
that log actual runtime data flow to feed back into the AI loop. The Stack: DuckDB or serverless, agent-managed micro-databases. The Schema:
Capture dynamic execution traces, system performance telemetry, and
test coverage matrices indexed directly against Git commit hashes. Why it enables Plan-Invariance:
When a development agent attempts a task, it pulls the execution trace
history of the target functions. This grounds the generative model in
verified operational truth rather than variable natural language prompt
context. 
How They Unify: The Context Store ArchitectureTo make this work seamlessly, wrap these engines into a unified Context Store via an architecture like the Model Context Protocol (MCP). Tools like Airbyte Agents
or custom MCP servers allow you to bridge your relational specification
store and your graph database into a single semantic layer. This
continuously briefs your development loop with deterministic data before
code generation ever begins. What language is your highly integrated codebase written in, and do you currently have an automated testing framework (like PyTest or Jest) that we can hook into to extract dynamic execution traces?Deep multi-metrics learning for mobile app defect prediction ...
Nov 4, 2025 — By
directly extracting code and process metrics from Git repositories,
DMLM eliminates extensive manual feature engineering and ad...
PubMed Central (PMC) (.gov)Contextualized Code Pretraining for Code Generation - arXiv
May 18, 2026 — We
describe the design principles, test case construction process,
automated evaluation protocol, and dataset statistics. * 4.1. D...
arXivSemanticForge: Repository-Level Code Generation through ...
Jun 3, 2026 — Our
approach proceeds in four integrated stages: (1) constructing
heterogeneous repository knowledge graphs that capture both stat...
ResearchGateIt Depends \#103: AI-Driven Data Foundations
Jan 31, 2026 — as
a utility. so with that let's go to the first category uh after uh
foundation. and that is operational data stores. this is whe...4mYouTube·Sanjeev MohanThe Cursor Moment for Data Science: Context at the Core
Sep 17, 2025 — or
one of the other sort of um uh uh coding agent tools that are out there
for for more engineering. and software development purp...8mYouTube·Zerve AIComprehension at AI Speed: Building a Context Store ... - InfoQ
Jul 14, 2026 — AI
ships code at machine speed, but the understanding does not ship with
it. That gap makes AI-assisted codebases harder to mainta...
infoq.comBeyond the Vector Store: Building the Full Data Layer for AI Applications - MachineLearningMastery.com
Mar 24, 2026 — Vector
databases are good for semantic retrieval, while relational databases
are good for structured data.**Vector databases** * *
Machine Learning MasteryDeterministic Planning Patterns for AI Agents by Dan ...
Jun 12, 2026 — where
we don't necessarily have a goal in u in mind. so let's look through
them and look at the two ends of the spectrum. so deter...2mYouTube·Spring I/OInvariants in Programming and LLM Code Generation
Summary.
Invariants are properties that must always hold true during program
execution. They form the foundation of quality gates ...
understandingdata.comBest Context Stores for AI Agents in 2026 - Airbyte
Mar 19, 2026 — TL;DR.
A context store goes beyond vector retrieval. It validates semantic
meaning, resolves entities across systems, and ensures ...
AirbyteBuilding Smarter, Fault-Tolerant Knowledge Systems with Python, LangChain, and Graph Databases
Oct 3, 2025 — 1.
Technology Stack Neo4j : Serves as the primary graph database, storing
entities (like cities and areas) as nodes and relationsh...
Python in Plain EnglishCodebase Analysis Agent: 30s to First Answer
Mar 18, 2026 — Technology
Stack Parsing: Tree-sitter (12 language grammars, syntax-aware
chunking) Embeddings: CodeBERT-based sentence transforme...
ActiveWizardsPrometheus: Unified Knowledge Graphs for Issue Resolution in Multilingual Codebases
Jul 26, 2025 — 2.1.

1. Graph Schema The knowledge graph represents codebases as
heterogeneous graphs composed of three primary node types: ❶ File...
arXivInvariant-based programming - Grokipedia
Effective
IBP requires languages supporting imperative code with specification
features like quantifiers (∀, ∃), integrated with p...
Grokipedia

This is a substantial architecture question — let me break it down into actionable terms specifically for your TGW stack (Python, PostgreSQL, NATS JetStream, MCP servers already in play).

## The Core Idea: Three Stores, One Context Feed

The goal is that your AI development agents receive *machine-verified, structure-grounded* context before they ever attempt to generate or modify code — so correctness is guaranteed by the data layer, not by prompt quality. Here's how each layer maps to your existing stack:

***

## Layer 1: The Structural Graph (FalkorDB + Tree-sitter)

FalkorDB is the right call here given your Python-heavy codebase. Their [code-graph backend](https://github.com/FalkorDB/code-graph-backend) is a production-ready implementation: it uses Tree-sitter to parse Python (and 10 other languages) into a **Heterogeneous Repository Knowledge Graph** — nodes are functions, classes, files, and modules; edges are `CALLS`, `IMPORTS`, `INHERITS`, `DEFINES`. A 2026 arXiv paper formalizes this approach across 66 languages using Tree-sitter-based knowledge graphs for LLM-assisted code exploration.[^1_1][^1_2][^1_3]

For your highly integrated codebase, the critical benefit is **multi-hop impact analysis via Cypher**. Instead of asking an LLM "what breaks if I change `InventoryItem.status`?", you issue:

```cypher
MATCH (f:Function)-[:CALLS*1..3]->(m:Method {name: "status"})
RETURN f.file, f.name
```

This is invariant — the graph tells you exactly what's downstream, regardless of prompt phrasing.[^1_4]

**Setup for TGW:** Run FalkorDB as a Docker container, index your repo nightly via their CLI, and expose it as an MCP tool. Your Hermes agents can then call `graph_query(cypher)` as a tool before any code generation task.

***

## Layer 2: The Invariant Store (PostgreSQL + Z3)

You already have PostgreSQL. The extension here is modest: add a `code_invariants` table that stores machine-checkable constraints as first-class data:

```sql
CREATE TABLE code_invariants (
    id           SERIAL PRIMARY KEY,
    scope        TEXT,          -- 'function', 'module', 'system'
    symbol       TEXT,          -- e.g., 'queue_worker.process_item'
    constraint   TEXT,          -- human-readable
    smt_expr     TEXT,          -- Z3-compatible SMT-LIB2 string
    severity     TEXT           -- 'hard' (blocks generation) or 'soft' (warns)
);
```

The Z3 SMT solver is a Python library (`pip install z3-solver`). Before your agent commits a code change, a worker parses the diff, extracts affected symbols, pulls their invariants, and runs Z3 to verify the generated code doesn't violate them (e.g., "queue depth must never go negative", "token expiry must be > now + 60s"). **This is the plan-invariance guarantee** — the plan doesn't matter because non-compliant code is structurally rejected before it ever reaches your repo.

For TGW specifically, good candidates for hard invariants include: eBay token refresh windows, inventory counter non-negativity, state machine transition legality (your existing PostgreSQL state machine is already half of this).

***

## Layer 3: The Trace Store (DuckDB)

DuckDB is the right choice for the ephemeral trace layer — it's embedded (no service to manage), columnar (fast for analytics over traces), and supports direct Parquet/JSON ingestion from your test runs. The schema:[^1_5]

```sql
CREATE TABLE execution_traces AS
SELECT
    commit_hash,
    function_fqn,
    test_id,
    duration_ms,
    exception_type,
    coverage_pct,
    recorded_at
FROM read_json('traces/*.jsonl');
```

Hook this into your existing pytest suite with a custom reporter plugin that writes JSONL traces keyed by git commit hash. When an agent is about to modify a function, it queries: *"What's the historical failure rate and last exception type for this function over the last 20 commits?"* This grounds the agent in **operational truth**, not just static structure.

***

## Unification: The MCP Context Server

Since you're already building MCP servers, the natural architecture is a single **`context-store` MCP server** that wraps all three layers and exposes them as tools your Hermes agents call:


| MCP Tool | Backend | What it returns |
| :-- | :-- | :-- |
| `get_impact_graph(symbol)` | FalkorDB | Cypher result: upstream/downstream call chain |
| `get_invariants(symbol)` | PostgreSQL | Hard/soft constraints for a function/module |
| `verify_diff(patch)` | PostgreSQL + Z3 | Pass/fail with violated constraint IDs |
| `get_trace_history(function_fqn)` | DuckDB | Failure rate, last exceptions, coverage trend |

The agent's pre-generation routine becomes a deterministic briefing loop: call all four tools, inject results into context, then generate. The prompt structure becomes irrelevant because the *data* is always the same regardless of how the task was phrased.[^1_6]

***

## Practical Build Order for TGW

Given your moderate-but-integrated codebase, the highest ROI sequence is:

1. **FalkorDB graph first** — index your repo, write 5-10 impact-analysis Cypher queries for your most critical modules (eBay token manager, queue worker, state machine). This alone eliminates the largest class of integration bugs from AI-generated code.
2. **Invariant table second** — start with 10-15 hard constraints captured from your existing business logic; you likely already know them implicitly.
3. **DuckDB traces third** — add the pytest reporter once the first two are in place, so trace data starts accumulating.
4. **MCP wrapper last** — bundle all three into one context server once each layer is independently stable.

The key insight is you're not replacing your existing PostgreSQL-backed state machine — you're *augmenting* it with a specification layer that AI agents can query before touching anything.
<span style="display:none">[^1_10][^1_11][^1_12][^1_13][^1_14][^1_15][^1_7][^1_8][^1_9]</span>

<div align="center">⁂</div>

[^1_1]: https://github.com/FalkorDB/code-graph-backend/blob/main/pyproject.toml

[^1_2]: https://arxiv.org/pdf/2603.27277.pdf

[^1_3]: https://github.leishennb.icu/FalkorDB/code-graph/issues/664

[^1_4]: https://www.falkordb.com/blog/code-graph-is-the-secret/

[^1_5]: https://duckdblab.org/en/post/duckdb-ai-agent-brain/

[^1_6]: https://enterprisedna.co/directories/mcp/davidgut1982-lore-mcp/

[^1_7]: https://github.com/FalkorDB/code-graph/releases

[^1_8]: https://code-graph.falkordb.com/

[^1_9]: https://docs.falkordb.com/genai-tools/code-graph.html

[^1_10]: https://github.com/FalkorDB/code-graph-backend/releases

[^1_11]: https://github.leishennb.icu/FalkorDB/code-graph/issues

[^1_12]: https://github.com/tree-sitter/tree-sitter-graph

[^1_13]: https://github.leishennb.icu/FalkorDB/code-graph/pulls

[^1_14]: https://reference.langchain.com/python/langchain-community/graphs/falkordb_graph/FalkorDBGraph

[^1_15]: https://motherduck.com/learn/motherduck-ai-agent-data-layer/


---

# I have switched to deep research mode as this is important to the success of the development success. Please verify everything

You’re on solid ground: the three-layer, plan-invariant context architecture you outlined is strongly validated by current research and practitioner systems; the main risks are implementation details (indexing, invariant coverage, and latency), not conceptual flaws.[^2_1][^2_2][^2_3][^2_4]

Here’s the full verification write-up with citations:
<span style="display:none">[^2_10][^2_11][^2_12][^2_13][^2_14][^2_15][^2_16][^2_17][^2_18][^2_19][^2_20][^2_21][^2_22][^2_23][^2_24][^2_25][^2_26][^2_27][^2_28][^2_29][^2_30][^2_31][^2_32][^2_33][^2_34][^2_35][^2_36][^2_37][^2_38][^2_39][^2_40][^2_41][^2_42][^2_43][^2_44][^2_45][^2_5][^2_6][^2_7][^2_8][^2_9]</span>

<div align="center">⁂</div>

[^2_1]: https://arxiv.org/html/2504.21030v1

[^2_2]: https://arxiv.org/html/2603.13417

[^2_3]: https://www.linkedin.com/pulse/model-context-protocol-unifying-ai-integration-taner-akdeniz-p7vgf

[^2_4]: https://arxiv.org/html/2511.07584v1

[^2_5]: https://www.inno-press.com/index.php/JAAI/article/view/65

[^2_6]: https://www.computer.org/csdl/proceedings-article/asew/2025/850300a291/2dp6j58tVmg

[^2_7]: https://arxiv.org/abs/2507.19942

[^2_8]: https://arxiv.org/html/2603.27277v1

[^2_9]: https://arxiv.org/pdf/2603.27277.pdf

[^2_10]: https://github.com/tree-sitter/tree-sitter-graph

[^2_11]: https://docs.falkordb.com/genai-tools/code-graph.html

[^2_12]: https://reference.langchain.com/python/langchain-community/graphs/falkordb_graph/FalkorDBGraph

[^2_13]: https://www.falkordb.com/blog/code-graph-is-the-secret/

[^2_14]: https://understandingdata.com/posts/invariants-programming-llm-generation/

[^2_15]: https://arxiv.org/html/2502.18917v2

[^2_16]: https://airbyte.com/agentic-data/context-stores-compared

[^2_17]: https://airbyte.com/agentic-data/context-store

[^2_18]: https://enterprisedna.co/directories/mcp/davidgut1982-lore-mcp/

[^2_19]: https://www.emergentmind.com/topics/multi-level-semantic-structural-code-graphs-sscgs

[^2_20]: https://duckdblab.org/en/post/duckdb-ai-agent-brain/

[^2_21]: https://motherduck.com/learn/motherduck-ai-agent-data-layer/

[^2_22]: https://conare.ai/blog/ai-agent-context-persistence

[^2_23]: https://airbyte.com/blog/airbyte-agents

[^2_24]: https://medium.com/ai-insights-cobet/model-context-protocol-mcp-in-agentic-ai-architecture-and-industrial-applications-7e18c67e2aa7

[^2_25]: https://markaicode.com/model-context-protocol-multi-agent-systems/

[^2_26]: https://airbyte.com/agentic-data/ai-business-tools

[^2_27]: https://airbyte.com/agentic-data/ai-applicant-tracking-systems

[^2_28]: https://airbyte.com/context-store

[^2_29]: https://medium.com/@anand94523/agentic-ai-and-the-model-context-protocol-a-new-era-of-autonomous-agents-5a3a00c333a0

[^2_30]: https://computerfraudsecurity.com/index.php/journal/article/download/817/560/1577

[^2_31]: https://www.youtube.com/watch?v=4AylKDYvHo0

[^2_32]: https://openreview.net/pdf/45f0eed51db8b5f27733d50dcbc981ec98ef7f9d.pdf

[^2_33]: https://arxiv.org/html/2507.19942v1

[^2_34]: https://github.com/EuniAI/Prometheus

[^2_35]: https://arxiv.org/html/2505.14394v1

[^2_36]: https://arxiv.org/pdf/2503.12686.pdf

[^2_37]: https://phemex.com/news/article/delysium-and-ucl-unveil-prometheus-ai-framework-for-multilingual-programming-29267

[^2_38]: https://theory.stanford.edu/~aiken/publications/theses/wei.pdf

[^2_39]: https://github.com/FalkorDB/code-graph/releases

[^2_40]: https://code-graph.falkordb.com/

[^2_41]: https://github.com/FalkorDB/code-graph-backend/releases

[^2_42]: https://github.leishennb.icu/FalkorDB/code-graph/issues

[^2_43]: https://github.com/FalkorDB/code-graph-backend/blob/main/pyproject.toml

[^2_44]: https://github.leishennb.icu/FalkorDB/code-graph/pulls

[^2_45]: https://github.leishennb.icu/FalkorDB/code-graph/issues/664


---

# the full write up did not land.

Got it — I’ll restate the verification inline instead of pointing to the missing report.

Here’s the distilled verification of your architecture, with citations.

***

## 1. Structural graph layer is strongly validated

Recent repository-level code generation systems all converge on “code as a heterogeneous knowledge graph”:

- SemanticForge builds **heterogeneous repository knowledge graphs** that combine static analysis (AST, control/data flow) and dynamic execution traces, then uses them as the primary substrate for repository-level code generation.[^3_1]
- GRACG and related work model the repo as a graph of files, classes, and functions and show that graph-based retrieval outperforms classical file/embedding retrieval at finding the right context for generation.[^3_2]
- Knowledge Graph Based Repository-Level Code Generation (Athale et al.) represents Python repos in Neo4j with nodes for file/class/method/function/attribute and edges like `defines`, `has method`, `used in`, etc., and uses this graph to drive retrieval for LLM code generation, achieving big gains over baseline prompting on EvoCodeBench.[^3_3]

Tree-sitter-based pipelines and tools like `tree-sitter-graph` are used to construct these graphs from ASTs, exactly matching the Tree-sitter → graph DB pipeline you’re proposing.[^3_4][^3_5]

FalkorDB’s Code-Graph is essentially a productionized version of this: a Tree-sitter–powered code graph with nodes for functions, classes, and files, edges for `CALLS`, `IMPORTS`, `INHERITS`, etc., explicitly aimed at GenAI/GraphRAG scenarios.[^3_6][^3_7]

Conclusion: **Using a heterogeneous code graph (FalkorDB + Tree-sitter) as the structural layer is directly in line with current best practice.**

***

## 2. Relational + SMT invariant layer is conceptually correct

There is consensus that invariants must be explicit, machine-checkable artifacts if you want reliable LLM-generated code:

- “Invariants in Programming and LLM Code Generation” positions invariants as the basis for **quality gates**: you define properties that must always hold, and code that violates them is rejected regardless of how it was produced.[^3_8]
- SemanticForge couples its knowledge graph with an **SMT-guided beam search**, using SMT solvers to enforce semantic constraints (types, pre/postconditions, structural rules) on candidate code; this is shown to significantly reduce both logical and schematic hallucinations and boost Pass@1 on a repository-level benchmark.[^3_1]
- Work on class invariant synthesis with LLMs uses the LLM mainly to *suggest* invariants but still relies on formal solvers to check them against code.[^3_9]

On the data side, “context store” discussions explicitly note that simple vector stores cannot represent or enforce relational constraints; they call for **structured, relational storage of business rules and metadata** as part of a true context store. That maps cleanly to your “PostgreSQL catalog of invariants/specs + SMT expressions (Z3)” idea.[^3_10][^3_11]

Conclusion: **A PostgreSQL-backed invariant catalog, with Z3 used as an automated verifier that prunes violating code paths, is exactly the pattern research and context-store vendors are advocating.**

***

## 3. Trace layer (DuckDB) matches current “agent data layer” patterns

Static analysis alone isn’t enough in these systems:

- SemanticForge explicitly includes **dynamic execution traces** in its heterogeneous repo graph and reports that including traces improves handling of logical hallucinations and runtime behavior reasoning.[^3_1]
- Multi-level Semantic-Structural Code Graphs and related work emphasize integrating AST/CFG/DFG with dynamic analysis for advanced code intelligence.[^3_12]

On the storage/tooling side:

- DuckDB and MotherDuck are being pitched as **“agent brains” / agent data layers**: embedded columnar analytics with SQL, good for logs and traces that agents query in their loop.[^3_13][^3_14]
- Articles on agentic data foundations recommend a separate operational/analytical store for telemetry so agents can ask “how has this thing behaved over time?” without touching transactional stores.[^3_15]

Your idea—per-commit execution traces, performance metrics, coverage matrices in DuckDB, keyed by commit hash, queried by agents before editing code—is fully consistent with this.

Conclusion: **DuckDB as the ephemeral trace store feeding runtime truth into the loop is aligned with both research and practitioner guidance.**

***

## 4. Unified context store via MCP is the right integration story

Recent work and industry pieces define “context store” almost exactly how you’re describing:

- Airbyte’s context-store docs describe a **replicated, pre-indexed storage layer** that unifies data from multiple systems, preserves schema and constraints, and exposes a single context API (including MCP) to agents.[^3_11][^3_10]
- Model Context Protocol (MCP) papers and guides describe MCP as a **universal interface** between models, tools, and data, enabling shared context repositories and standardized access for multi-agent systems.[^3_16][^3_17][^3_18]

Patterns they show:

- Wrap multiple backends (databases, knowledge graphs, vector stores) behind MCP servers.
- Agents call a small set of MCP tools like `get_context(query)` or `get_entity(id)` and don’t deal with per-store details.[^3_19][^3_15]

That’s exactly your “Unified Context Store MCP server that fronts FalkorDB + Postgres+Z3 + DuckDB and returns a single coherent briefing blob” idea.

Conclusion: **Using MCP as the unified context interface is directly aligned with how people are building multi-store context layers for agents.**

***

## 5. Big-picture validation

Putting it all together, your design is almost a direct instantiation of what the latest repository-level codegen papers say works:

- Knowledge-graph based repository-level frameworks (SemanticForge, Athale et al., GRACG) all:
    - build a heterogeneous repo-level graph,
    - use it for retrieval and reasoning, and
    - often add verification layers or execution feedback.[^3_2][^3_3][^3_1]
- They report large gains over “prompt-only + file chunks” approaches on EvoCodeBench and similar benchmarks (e.g., SemanticForge improving Pass@1 by ~18 percentage points and significantly reducing hallucinations).[^3_20][^3_1]

Your additions—explicit invariant catalog in Postgres, SMT verification, separate DuckDB trace store, MCP unification—are not at odds with any of this; they are **the logical, system-architected version** of the same core ideas.

***

## 6. Caveats (engineering, not conceptual)

The main “verify everything” caveats are about execution, not whether the pattern is sound:

- **Latency and cost:** SemanticForge and KG-based systems call out that building/updating graphs and running SMT checks is expensive and recommend incremental indexing, scope-limited retrieval (N-hop neighborhoods), and caching.[^3_3][^3_1]
- **Invariant coverage:** Invariant work notes you never capture all invariants at once; they have to be curated and evolved, and missing invariants can still let bad code through.[^3_8][^3_9]
- **Trace completeness:** Traces only cover executed paths; research stresses using them as a complement to static structure and invariants, not a replacement.[^3_12][^3_1]

None of these undermine your architecture; they just shape the implementation plan (phased rollout, careful scoping, feedback loops).

***

If you want, next step I can:

- Sketch a concrete schema for your Postgres invariants table and Z3 integration, and
- Outline the exact MCP tool signatures your Hermes agents should call (e.g., `get_impact_graph`, `get_invariants`, `verify_diff`, `get_trace_history`) so you can start wiring this into TGW.
<span style="display:none">[^3_21][^3_22][^3_23][^3_24][^3_25][^3_26]</span>

<div align="center">⁂</div>

[^3_1]: https://www.inno-press.com/index.php/JAAI/article/view/65

[^3_2]: https://www.computer.org/csdl/proceedings-article/asew/2025/850300a291/2dp6j58tVmg

[^3_3]: https://www.semanticscholar.org/paper/Knowledge-Graph-Based-Repository-Level-Code-Athale-Vaddina/3597d20e64d2fa00402a3b87eab9a7b77a4722fc

[^3_4]: https://github.com/tree-sitter/tree-sitter-graph

[^3_5]: https://arxiv.org/html/2603.27277v1

[^3_6]: https://docs.falkordb.com/genai-tools/code-graph.html

[^3_7]: https://www.falkordb.com/blog/code-graph-is-the-secret/

[^3_8]: https://understandingdata.com/posts/invariants-programming-llm-generation/

[^3_9]: https://arxiv.org/html/2502.18917v2

[^3_10]: https://airbyte.com/agentic-data/context-store

[^3_11]: https://airbyte.com/agentic-data/context-stores-compared

[^3_12]: https://www.emergentmind.com/topics/multi-level-semantic-structural-code-graphs-sscgs

[^3_13]: https://duckdblab.org/en/post/duckdb-ai-agent-brain/

[^3_14]: https://motherduck.com/learn/motherduck-ai-agent-data-layer/

[^3_15]: https://conare.ai/blog/ai-agent-context-persistence

[^3_16]: https://arxiv.org/html/2504.21030v1

[^3_17]: https://medium.com/ai-insights-cobet/model-context-protocol-mcp-in-agentic-ai-architecture-and-industrial-applications-7e18c67e2aa7

[^3_18]: https://www.linkedin.com/pulse/model-context-protocol-unifying-ai-integration-taner-akdeniz-p7vgf

[^3_19]: https://markaicode.com/model-context-protocol-multi-agent-systems/

[^3_20]: https://repositum.tuwien.at/bitstream/20.500.12708/227436/1/Hausberger Benedikt - 2026 - Enhanced Repository-Level Code Comprehension...pdf

[^3_21]: https://ui.adsabs.harvard.edu/abs/2025arXiv250514394A/abstract

[^3_22]: https://arxiv.org/html/2511.07584v1

[^3_23]: https://arxiv.org/html/2505.14394v1

[^3_24]: https://api-inference.huggingface.co/papers/2505.14394

[^3_25]: https://www.sciencedirect.com/science/article/abs/pii/S0950705124007950

[^3_26]: https://www.themoonlight.io/ja/review/knowledge-graph-based-repository-level-code-generation

