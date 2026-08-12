# Promptcraft

Promptcraft is the recovered local-only intent-translation capability originally owned by `PP-CLIP-001`. Its implementation translates rough sender intent into a receiver-native communication contract, with provider-neutral harness compilation and deterministic quality gates available through both CLI and local stdio MCP. It is canonically housed as a distinct TGW agent-service provider; the archived Capability Lab is lineage evidence, not a competing live Promptcraft system.

The intended PP-CLIP seam is explicit user selection: selected clipboard entry or named snippet → Promptcraft compiler/linter → prompt plus receipt. There is no ambient clipboard surveillance, no new clipboard database, and no cross-machine transport. Cross-machine clipboard/event transport remains `PP-EVENTD-001` authority.

It exists because a failed prompt/harness combination does not establish a model or product limitation. Promptcraft holds intent and dialect separately: the intent contract preserves meaning, authority, evidence, outcome, and acceptance; the dialect contract selects the receiver's harness profile, native tools, context strategy, known traps, and completion semantics. One intent may therefore produce different prompts for Hermes, Antigravity, Codex, or a person without silently becoming a different task.

## What it does

- compiles structured task briefs into harness-native prompts;
- detects requirements that cannot be performed with declared tools;
- blocks prompt/instruction artifacts mixed into factual evidence;
- warns against whole-corpus context flooding;
- detects missing output, citation, authority, and completion contracts;
- compares two prompts under an otherwise matched configuration;
- emits a SHA-256 prompt receipt;
- exposes deterministic MCP tools and a user-controlled prompt-crafting workflow.

Promptcraft does not invoke a model, use the network, read arbitrary files through MCP, mutate TGW, or grant authority. The connected host model performs the qualitative drafting; Promptcraft supplies the compiler, profiles, and non-negotiable gate.

## Execution-card handoff

`bin/promptcraft-handoff` is the mechanical adapter between a compact
`tgw-execution-card/v1` and a launcher. It verifies the immutable card, renders
only receiver-native strategy plus exact resource pointers and preserved authority,
exclusions, and acceptance, then emits `tgw-promptcraft-receipt/v1` inside a
hash-bound `tgw-launcher-handoff/v1`. The launcher verifies that object and its
lease before accepting a minimal invocation. Editing or manually transcribing any
card, instruction, resource hash, profile, or receipt fails closed.

```bash
./agent-services/providers/promptcraft/bin/promptcraft-handoff craft \
  --receiver-identity receiver-run-8 < card.json > handoff.json
./agent-services/providers/promptcraft/bin/promptcraft-handoff verify \
  < handoff.json > invocation.json
```

The card's `selected_provider` is a qualified role-provider identity, not a fixed
product assignment. The receiver profile controls presentation only.

## MCP tools and approval workflows

### Intent translation and approval — primary use

`approve_draft` is the author-side intent translator and communication gate—not a glorified spell checker. It first recovers the intended outcome, then translates it into the receiver's native communication contract while preserving Dave's meaning, authority, directness, voice, and decision rights. A human may need natural phrasing and shared context; an agent or harness may need native tools, boundaries, deliverables, and completion semantics; a controller may need a schema and authority contract. Spelling and grammar correction are incidental to accurate transmission. Consequential ambiguity is surfaced rather than guessed, and the result is never sent or executed automatically.

Inputs:

- `draft` (required)
- `audience`, `receiver_contract`, `purpose`, `context`, and `tone` (optional)

Output contract:

- `Approval status`
- `Intent understood`
- `Approved version`
- `Translation choices`
- `Meaning changes`
- `Clarification needed`

The user remains the send gate. In Hermes, reload MCP after updating Promptcraft, then invoke the `approve_draft` prompt workflow before issuing consequential or unclear prompts.

### Harness tools

- `lint_prompt`
- `craft_prompt`
- `compare_prompts`
- `get_harness_profile`

### Harness workflow

`craft_prompt` treats its structured brief as a canonical intent contract and translates it through the selected harness profile. Version 2 receipts make matched translation auditable:

- `intent_sha256` stays identical when meaning, authority, evidence, outcome, and acceptance stay identical;
- `dialect_sha256` changes when the receiver profile, native tools, strategy, or known traps change;
- `prompt_sha256` identifies the exact rendered prompt that was issued.

A matched evaluation must hold `intent_sha256` constant. Otherwise it is comparing different tasks, not different harness dialects.

Supported profiles:

- `antigravity-managed`
- `codex`
- `claude-code`
- `chatgpt-work`
- `hermes`
- `generic`

## CLI

```bash
./agent-services/providers/promptcraft/bin/promptcraft profile antigravity-managed

./agent-services/providers/promptcraft/bin/promptcraft lint prompts/example.md \
  --harness antigravity-managed \
  --source sources/plan.md \
  --tool code_execution

./agent-services/providers/promptcraft/bin/promptcraft compile agent-services/providers/promptcraft/promptcraft/examples/antigravity-tgw-v3-brief.json

./agent-services/providers/promptcraft/bin/promptcraft compare old.md new.md \
  --harness antigravity-managed \
  --source sources/plan.md \
  --tool code_execution
```

Use `--strict` with `lint` to make WARN return nonzero. BLOCK always returns status 2.

## Hermes MCP registration

```bash
hermes mcp add promptcraft \
  --command /opt/TGW/tgw-lib/src/trader-grims-warehouse/agent-services/providers/promptcraft/bin/promptcraft-mcp

hermes mcp test promptcraft
```

After a Hermes restart or `/reload-mcp`, the tools are registered as:

```text
mcp_promptcraft_lint_prompt
mcp_promptcraft_craft_prompt
mcp_promptcraft_compare_prompts
mcp_promptcraft_get_harness_profile
```

Hermes also exposes MCP prompt utilities when the server prompt capability is discovered.

## Quality findings

| Code | Gate | Meaning |
|---|---|---|
| `PC001` | BLOCK | required deterministic compute is unavailable |
| `PC002` | BLOCK | instruction-like artifact is mixed into evidence |
| `PC003` | WARN | prompt forces context-flooding corpus reads |
| `PC004` | WARN | output contract is not mechanically recognizable |
| `PC005` | BLOCK | consequential effect lacks an authority prohibition/gate |
| `PC006` | WARN | final deliverable is not protected from preliminary-only output |
| `PC007` | WARN | global concision conflicts with exhaustive coverage |
| `PC008` | WARN | source-grounded task lacks exact anchors/citations |

## Evaluation rule

Before a harness fixture runs:

1. freeze a structured task brief;
2. compile for the target native harness;
3. resolve every BLOCK and WARN finding;
4. freeze the prompt SHA-256 receipt;
5. verify the factual source packet contains no competing instructions;
6. run with native differentiating capabilities enabled;
7. score the output using the frozen acceptance contract;
8. attribute failure only to the complete prompt/harness configuration actually tested.

A prompt change is a configuration change. It invalidates strict matched-fixture comparisons unless recorded explicitly.
