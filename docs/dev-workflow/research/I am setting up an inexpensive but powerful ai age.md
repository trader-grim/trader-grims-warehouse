<img src="https://r2cdn.perplexity.ai/pplx-full-logo-primary-dark%402x.png" style="height:64px;margin-right:32px"/>

# I am setting up an inexpensive but powerful ai agentic coding and marketing analysis platform.  I would like to implement worktrees or similar to automate the handoff of tasks to agents and humans as appropriate.

I currently have the folliwing resources:
Google One 2tb Ai Plus benefits
Perplexity Pro
Aider with Claude API/prompt caching
cursor free tier
Claude Pro (half month left)

The project is an automated ecommerce product listing platform written in python with a postgresql backed state machine deeply integrated into a custom nixos flake and flutter interface for portable clients

The project plan currently exists as a a rather extensive markdown file and a large library of documentation and research.

Which resource is best for project planning and why.
Propose a effective utilization strategy under current circumstances
Propose any improved strategies or strategy improvements

For *project planning* specifically, your strongest “brain” is Claude (Pro + API) wrapped in Aider, with a single canonical planning file (CLAUDE.md/PLAN.md) in your TGW repo; Perplexity and Google AI are best treated as specialized research/marketing copilots around that core, and Cursor as an occasional planner for codebase overviews.[^1_1][^1_2][^1_3][^1_4]

Below is how I’d structure this so it plays nicely with your Nix flake, Postgres state machine, directory-per-task model, and agentic ambitions.

***

## High‑level recommendation

- **Primary planning engine:**
Use Claude (while you still have Pro, then via API) plus Aider as your main *code‑aware* project planner, with a long‑context planning file (`CLAUDE.md` or `PLAN.md`) at your repo root that describes architecture, milestones, and current tasks. This mirrors Anthropic’s own pattern for long‑running, agentic coding projects using a persistent brief and progress files.[^1_3][^1_4][^1_5]
- **Secondary planning \& research:**
Use **Perplexity** (Deep Research + Labs) for marketing analysis, competitor research, and data‑driven experiments, and **Google AI (Gemini + NotebookLM/Docs)** for document organization, summaries, and turning big markdown plans into structured tables and timelines.[^1_2][^1_6][^1_7][^1_8][^1_9][^1_1]
- **Supplemental:**
Use **Cursor (free)** for occasional Plan Mode runs to sanity‑check roadmaps for specific subsystems (e.g., Flutter client, new queue worker group) when its repo‑aware planning is helpful, but don’t make it your source of truth given the free‑tier limits.[^1_10][^1_11][^1_12]

Everything flows through a TGW planning repo plus your `/opt/TGW` task intake directories, which already match your directory‑per‑task mental model.

***

## Which resource is “best” for planning, and why

If you have to pick a single “planning home,” it should be **Claude (+Aider) with a persistent project brief in the repo**:

- Anthropic explicitly recommends a pattern for large, long‑running technical projects: iterate locally with Claude to craft a project brief, save it as `CLAUDE.md` in the repo root, then let agents repeatedly read and update this file as they make progress. That’s exactly what you want for TGW: a file that encodes architecture, style guide, managers/workers, task taxonomy, and success criteria.[^1_3]
- Aider gives Claude full codebase awareness plus git‑native edits and commits, which is better than chat‑only tools for planning *and executing* refactors or new subsystems. Aider already maps your codebase and keeps changes in git, which aligns with your layered filesystem safety and review‑before‑merge rule.[^1_4][^1_13]
- Claude’s long context window (and API long‑context variants) are explicitly optimized for “long agentic coding tasks” and long‑horizon work where the model must remember previous steps, which fits your idea of task handoff via worktrees.[^1_14][^1_5][^1_15]

Perplexity and Google are excellent *adjacent* tools, but neither is as tightly coupled to your codebase and git workflow as Aider+Claude.

***

## Role of each resource in your platform

### Google One 2 TB AI Plus (Gemini + Workspace + NotebookLM)

What it’s good at for you:

- Gemini in Docs/Sheets and Workspace can “create project plans from a simple prompt, and summarize progress and assign tasks” inside documents and spreadsheets, which is ideal for high‑level roadmaps, RACI tables, and progress snapshots derived from your markdown plan.[^1_7][^1_16][^1_1]
- Google AI Plus bundled into the 2 TB Google One plan gives you Gemini 3 Pro access and Workspace integration at essentially no extra cost beyond the storage, so it’s a cheap way to get strong document‑centred AI support.[^1_9][^1_17][^1_16]
- NotebookLM (included at enhanced limits with AI Plus) is particularly useful for creating a notebook over your large TGW docs library and querying it as a personal knowledge base.[^1_7][^1_9]

Limitations for *core* planning:

- It does not operate in your repo, so it’s better as a **PM/document layer** than as the agent actually editing code or respecting your Nix flake and Postgres schema.


### Perplexity Pro (Search, Deep Research, Labs)

What it’s good at:

- Deep Research is designed to autonomously perform multi‑round web research and produce comprehensive reports, which is ideal for **market analysis, pricing strategies, channel research, and marketing experiments** around your eBay store and listing platform.[^1_8][^1_2]
- Perplexity Labs is explicitly aimed at “bringing projects to life” by generating reports, spreadsheets, dashboards, and simple web apps with integrated research and code execution, and it keeps all generated assets (charts, CSVs, code) attached to each Lab.[^1_18][^1_6]
- Users report it being very effective for quick project planning, idea validation, and market research with citations, which lines up with your need for ongoing marketing analysis.[^1_19][^1_8]

Limitations for core planning:

- It’s phenomenal for *content and analysis*, less so as the persistent orchestration brain tied into your git history and directory hierarchy. Treat it as your **marketing and external‑research division**.


### Aider + Claude API (your terminal pair‑programmer)

What it’s good at:

- Aider builds a map of your entire codebase, integrates tightly with git, and auto‑commits changes; this makes it an ideal backend coding agent in an agentic workflow where tools like a “task master” delegate implementation tasks to it.[^1_20][^1_13][^1_4]
- It is explicitly designed for multi‑file refactors, test‑driven fixes, and maintaining context across large projects, and it can be wired to Claude Sonnet or other high‑end models you already have.[^1_14][^1_4]
- People already use Aider in workflows where another tool does task planning/research and then hands actual code edits to Aider as a backend execution agent, which mirrors your idea of a filesystem MCP + queue worker pair delegating implementation tasks.[^1_20]

This is where your `CLAUDE.md` (project brief) and progress/test files live, and where worktrees/branches per task make the most sense.

### Cursor (free tier)

What it’s good at:

- Cursor’s Plan Mode analyzes your repo, generates structured markdown plans (front‑end data models, routes, to‑do lists), and lets you save and share these plans; it is designed to reduce trial‑and‑error by generating a clear roadmap before coding.[^1_10]
- Plan Mode can generate prompt‑to‑plan roadmaps with milestones and dependencies and export them to other tools (Jira, etc.), effectively acting as an AI architect for the codebase.[^1_11]
- Cursor’s agents can run tests, build features in parallel, and understand large codebases, but many of those features shine more on paid tiers.[^1_12][^1_21]

Given you’re on the free tier, Cursor is best used **periodically** to get an alternative high‑level plan for specific subsystems (e.g., Flutter app refactor, adding a new queue‑worker manager), not as the always‑on planner.

### Claude Pro (UI)

What it’s good at *right now*:

- Claude Pro gives you a polished chat UI with long context window and high throughput, and Anthropic’s prompting docs show how to use long‑context effectively by structuring documents and grounding responses in quoted context.[^1_22][^1_5][^1_15]
- Anthropic’s long‑running‑task tutorial recommends spending most of your effort on crafting and iterating a strong project brief locally with Claude before letting agents run independently, which is exactly what you can do in the remaining half‑month.[^1_3]

Use the remaining time to bootstrap the *canonical* planning artifacts you will later feed to Aider/Claude via API.

***

## Concrete utilization strategy under current circumstances

### 1. Establish a single planning repo + files

Create a small **“tgw-planning”** git repo (or use your main repo) and standardize:

- `CLAUDE.md` (or `PLAN.md`): high‑level architecture, TGW style guide essentials, manager/worker taxonomy, NixOS deployment model, and your directory conventions under `/opt/TGW`.[^1_23][^1_24][^1_3]
- `tasks/` or use your existing `/opt/TGW/data/task-intake/` but mirror each task as a small file (`task-XXXX.md` or `task-XXXX.json`) with fields: ID, objective, constraints, inputs/outputs, queue/worker binding, and test oracle.[^1_25][^1_3]
- `progress.md` or `progress.json`: one file that an agent updates with “done / in‑progress / blocked” and pointers to branches/worktrees, following Anthropic’s long‑running task pattern.[^1_3]

This becomes the handoff surface between humans and agents, regardless of which AI you’re using.

### 2. Use Claude Pro *now* to harden the plan

Before Pro expires:

- Feed the entire existing markdown project plan and a sample of your documentation into Claude Pro and have it refactor into:
    - A clean, sectioned `CLAUDE.md` brief.
    - A task schema (JSON/YAML) that matches your directory‑per‑task model and layered filesystem guardrail.
    - A finalized **TGW Style Guide v1.x** section embedded into the brief (filesystem, code hierarchy, managers/workers, logging, backup, notification manager).[^1_23][^1_3]
- Use Anthropic’s prompting tips: put long documents at the top of the prompt, ask Claude to quote the most relevant sentences before summarizing, to ensure accurate use of your large plan doc.[^1_5][^1_15][^1_22]

You’re essentially using Claude Pro’s UI as an editor for your master planning artifacts.

### 3. Move ongoing planning into Aider + Claude API

Once the brief is in place:

- Run Aider in your TGW repo with Claude Sonnet as the model; let Aider read `CLAUDE.md`, your style guide, and `progress.md` at the start of each session, so implementation work stays aligned with the plan.[^1_4][^1_5][^1_3]
- For each new task:
    - Create a branch or git worktree `task/<id>` tied to its task file and queue/worker config.
    - Have Aider implement only within that branch/worktree, running tests and auto‑committing; you later review the diff (`/git diff` or PR) and merge to master, preserving your “layered filesystem safety” rule.[^1_13][^1_20]
- Keep `progress.md` updated via Aider/Claude after each meaningful batch of work, so other agents (or future sessions) can resume precisely where you left off.[^1_3]

This gives you the “worktrees or similar” automated handoff: task file → branch/worktree → agent implementation → human review → merge.

### 4. Use Google AI Plus for document‑level planning and coordination

On the documentation/coordination side:

- Store `CLAUDE.md`, style guide exports, and planning snapshots in a Google Drive folder shared across your devices. Gemini in Docs can generate **Gantt‑ish tables, milestone lists, and status summaries** for you with a simple “turn this into a project plan with phases and dates” prompt.[^1_26][^1_1][^1_7]
- Use **NotebookLM** with your TGW documentation and planning files as sources so you can ask, “What are all the open tasks touching NixOS flake configuration?” or “Summarize the backup manager design” without re‑feeding context.[^1_9][^1_7]
- Create a light Google Sheet “backlog dashboard” that pulls task IDs and statuses from exported `progress.md`/`tasks/*.json` as needed; Gemini can help generate formulas and conditional formatting for this.[^1_1][^1_7]

This layer is for *you* and any future collaborators; your agents continue to treat git + `/opt/TGW` as canonical.

### 5. Use Perplexity Pro for marketing \& analytics work

For the *marketing analysis platform* aspect:

- Use **Deep Research** to create structured reports on eBay SEO patterns, category‑specific title structures, pricing strategies, and competitor behavior; export these as markdown/CSV and drop them into your planning repo under `research/`.[^1_2][^1_19][^1_8]
- Use **Labs** to prototype small dashboards or analysis notebooks on top of CSV exports from your TGW Postgres database (e.g., price elasticity experiments, sell‑through curves, photo quality vs. sell‑through); Labs can write and run code and produce charts and simple web dashboards as assets.[^1_6][^1_18]
- Turn key insights into new TGW tasks (e.g., “implement dynamic pricing worker,” “add photo quality scoring heuristic”) and feed those into your `tasks/` + Aider pipeline.

You’re effectively using Perplexity as your marketing analyst that feeds requirements back into the core system.

### 6. Use Cursor selectively

With the free tier:

- At major milestones or when starting a new subsystem, run **Plan Mode** over the repo and ask it to propose a phased implementation plan or refactor roadmap for that subsystem (e.g., “plan the migration of listing creation to a new state machine manager”).[^1_11][^1_12][^1_10]
- Export the generated markdown plan, review it, then either:
    - Integrate the good parts into `CLAUDE.md`, or
    - Convert it into concrete task files under `tasks/`.

This gives you a second opinion/architect, but your main loop stays Aider+Claude.

***

## Strategy improvements / future evolution

### A. Add a thin Python “orchestrator” or MCP server

Medium‑term, formalize what you’re already doing conceptually:

- A small Python service (or MCP server) watches `/opt/TGW/data/task-intake/` and `tasks/`, reads task metadata, and decides whether a task is:
    - **Code:** forward to Aider+Claude (branch/worktree)
    - **Research/marketing:** forward to Perplexity (Deep Research/Labs)
    - **Docs/PM:** forward to Google (Docs/NotebookLM)
- It writes back results into the task file and/or progress file, so humans always see a unified view regardless of which AI did the work.[^1_20][^1_3]

That gets you much closer to the “agentic coding and marketing analysis platform” you’re aiming for.

### B. Normalize your task envelope

Standardize a **task envelope** (JSON/YAML) that every agent understands:

- Fields: `id`, `type` (`code|research|docs|ops`), `repo_path`, `branch`, `inputs`, `outputs`, `acceptance_tests`, `priority`, `status`.
- Use the same schema in:
    - `tasks/*.json` in git
    - A Google Sheet backlog (for human overview)
    - Any Perplexity or Gemini prompts that generate tasks (“produce 10 tasks in this schema”)

This makes it trivial for you to script worktree creation, queue assignment, and agent routing.

### C. Post‑Claude‑Pro optimization

Once your Claude Pro month ends:

- Keep **Claude API** reserved for Aider and long‑context planning updates where repo awareness matters most.[^1_5][^1_14][^1_4]
- Use **Gemini and Perplexity** as cheaper/fixed‑price helpers for:
    - Transforming docs, writing marketing copy, doing competitive research, summarizing task lists.[^1_6][^1_8][^1_1][^1_9]

That optimizes cost while retaining the strengths of each tool.

***

If you’d like, I can next help you draft a concrete `CLAUDE.md` skeleton and a `task.json` schema that match your `/opt/TGW` layout and queue‑worker model so you can drop them straight into your repo and start wiring Aider and Perplexity around them.
<span style="display:none">[^1_27][^1_28][^1_29][^1_30][^1_31][^1_32][^1_33]</span>

<div align="center">⁂</div>

[^1_1]: https://workspace.google.com/solutions/ai/

[^1_2]: https://www.perplexity.ai/hub/blog/introducing-perplexity-deep-research

[^1_3]: https://www.anthropic.com/research/long-running-tasks

[^1_4]: https://aider.chat

[^1_5]: https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/long-context-tips

[^1_6]: https://www.perplexity.ai/hub/blog/introducing-perplexity-labs

[^1_7]: https://knowledge.workspace.google.com/admin/gemini/google-workspace-with-gemini

[^1_8]: https://www.perplexity.ai/hub/getting-started

[^1_9]: https://chromeunboxed.com/premium-ai-becomes-more-affordable-as-google-ai-plus-lands-in-the-us/

[^1_10]: https://www.geeky-gadgets.com/cursor-ai-plan-mode-feature/

[^1_11]: https://www.linkedin.com/posts/ai-tools-news-daily_cursors-plan-mode-just-launched-and-activity-7385955051749466113-byuV

[^1_12]: https://cursor.com

[^1_13]: https://www.reddit.com/r/ChatGPTCoding/comments/1gacxll/aider_code_review/

[^1_14]: https://techcrunch.com/2025/08/12/anthropics-claude-ai-model-can-now-handle-longer-prompts/

[^1_15]: https://www.anthropic.com/news/prompting-long-context

[^1_16]: https://one.google.com/about/plans

[^1_17]: https://9to5google.com/2026/01/27/google-ai-plus-us/

[^1_18]: https://www.infoq.com/news/2025/06/perplexity-labs/

[^1_19]: https://www.reddit.com/r/SideProject/comments/1m63u50/how_perplexity_pro_transformed_my_student/

[^1_20]: https://github.com/eyaltoledano/claude-task-master/issues/813

[^1_21]: https://www.codecademy.com/article/cursor-2-0-new-ai-model-explained

[^1_22]: https://www.anthropic.com/news/claude-2-1-prompting

[^1_23]: https://www.perplexity.ai/search/6edbd396-8dcd-4ac2-a4c4-1632949494ad

[^1_24]: https://www.perplexity.ai/search/4612ac57-ccfc-4c22-a1b6-ae05a3a28495

[^1_25]: https://www.perplexity.ai/search/4289547b-4200-4b1b-8d02-04e14a6adf2d

[^1_26]: https://workspaceupdates.googleblog.com/2025/01/expanding-google-ai-to-more-of-google-workspace.html

[^1_27]: https://www.reddit.com/r/gsuite/comments/17coubt/anyone_do_project_management_with_google/

[^1_28]: https://one.google.com/intl/en_us/about/google-ai-plans/

[^1_29]: https://prismic.io/blog/cursor-ai

[^1_30]: https://github.com/Aider-AI/aider/issues/4226

[^1_31]: https://www.reddit.com/r/GoogleOne/comments/1pd6bqd/your_google_one_premium_2_tb_plan_has_been/

[^1_32]: https://blog.netnerds.net/2024/10/aider-is-awesome/

[^1_33]: https://www.wired.com/story/what-is-google-one/


---

# yes, please generate the sample files

Here are text versions you can drop straight into your repo; you can rename paths/IDs as needed.

***

## 1. `CLAUDE.md` – project brief / repo memory

Put this at the TGW repo root (or in a dedicated `tgw-planning` repo). It’s intentionally short, following current best practices for CLAUDE.md: why, map, rules, workflows.[^2_1][^2_2][^2_3]

```markdown
# Trader Grims Warehouse – CLAUDE Project Brief

## Purpose (WHY)

Trader Grims Warehouse (TGW) is an automated e‑commerce product listing platform for my eBay resale business.
Its primary jobs are:
- Model inventory, listings, and workflows in a PostgreSQL‑backed state machine.
- Automate product listing creation, updates, and archival based on live data and business rules.
- Provide portable client interfaces (Flutter) for item intake, review, and operational control.
- Integrate tightly with NixOS flakes for reproducible deployment and disaster recovery.

TGW is a long‑lived, production system with ~20k items and growing. Reliability, reproducibility, and safe automation are more important than maximum feature velocity.

---

## Repo map (WHAT & WHERE)

High‑level layout (paths may be mounted under `/opt/TGW` in production):

- `src/trader_grims_warehouse/`
  - `api/` – `tgw-api.py` entrypoint and API managers (queue, backup, logging, notification, etc.).
  - `queue_workers/` – narrow, single‑purpose workers bound to queues via config.
  - `managers/` – coordination logic (queue pool manager, backup manager, notification manager, etc.).
  - `models/` – domain models and Postgres schema mapping.
  - `services/` – eBay API integration, pricing engine, photo pipeline, etc.
  - `flutter_client/` – Flutter app interface (portable clients).
- `config/`
  - `tgw-api-config.json` – canonical paths (itemdata_root, catalog_root, config_root, runtime_root, etc.).
  - `queues/` – queue definitions and worker bindings.
  - `env/` – environment‑specific overrides (dev/stage/prod).
- `nix/`
  - `flake.nix`, `flake.lock` – NixOS/flake configuration for TGW services and dependencies.
  - `modules/` – reusable Nix modules for Postgres, queue workers, backups, monitoring, etc.
- `runtime/`
  - `state/` – runtime state for the Postgres‑backed state machine.
  - `logs/` – structured logs; no long‑term history here.
- `data/`
  - `ItemData/` – current item source data (photos, metadata).
  - `ItemCatalog/` – canonical catalog.
  - `ItemArchive/` – archived/historical items.
  - `task-intake/` – directory‑per‑task folders for agentic work and human review.
- `docs/`
  - `style-guide/` – TGW style guide (filesystem, code hierarchy, manager/worker rules).
  - `runbooks/` – operational runbooks (backup/restore, deploy, migration).
  - `ADR/` – architecture decision records.
- `planning/`
  - `CLAUDE.md` (this file).
  - `tasks/` – normalized task envelopes (`task-*.json`/`task-*.md`).
  - `progress.md` – high‑level project status and current focus.

If something is not documented here, ask before inventing new top‑level directories.

---

## Rules (HOW – coding & infra)

When editing or generating code:

- **Single source of truth for paths**  
  - Always read paths from `tgw-api-config.json` or config modules, never hardcode `/opt/TGW/...` in code.
  - Respect the separation between `apps`, `config`, `data`, `runtime`, `logs`, and `history` as documented in the style guide.

- **Managers vs workers**  
  - `tgw-api.py` is the only public Python control surface for TGW.
  - Managers coordinate work; workers execute narrow tasks.
  - If a process needs staffing, it gets:
    - A queue definition under `config/queues/`.
    - A worker implementation under `queue_workers/`.
    - Optional manager wiring in `managers/` and NixOS service configuration.

- **Safe changes & layering**
  - All edits happen on feature branches or worktrees (`task/<id>`), never directly on `master`.
  - Aider/agents may commit to the task branch; a human reviews diffs before merge.
  - No direct edits under `data/` or `ItemCatalog/` by agents unless explicitly requested.

- **Coding conventions**
  - Python 3 with type hints where reasonable.
  - Prefer standard libraries and established dependencies (documented in `docs/style-guide/` and `CONVENTIONS.md`).
  - Write small, composable functions with clear docstrings.
  - Keep eBay API logic isolated in dedicated service modules.

When editing Nix or infra:

- Do not change `flake.lock` unless explicitly asked.
- NixOS modules should be additive and composable; avoid hard‑coding machine‑specific paths.

---

## Workflows (HOW – work gets done)

**High‑level loops:**

1. **Task intake**
   - New work items are created as:
     - A folder under `data/task-intake/` with `task.json` and `task.md`, or
     - A file under `planning/tasks/` (same schema).
   - Each task has a unique `task_id`, type (`code|research|docs|ops`), status, and acceptance tests.

2. **Branch / worktree creation**
   - For `code` tasks, a branch or worktree `task/<task_id>` is created from `master`.
   - Aider is run against this branch with `CLAUDE.md`, `CONVENTIONS.md`, and relevant source files added.

3. **Agentic implementation**
   - Agents (via Aider) implement the task on the branch only.
   - They update `task.md` with what changed and how, and, if appropriate, append notes to `planning/progress.md`.

4. **Human review & merge**
   - A human reviews the git diff for `task/<task_id>`.
   - If accepted, changes are merged to `master`; the task status becomes `done`.
   - If rejected, the task is updated with feedback (`blocked` / `needs‑changes`) and the agent may try again.

5. **Deployment & ops**
   - NixOS flakes define services and system units for managers and workers.
   - Backup and restore procedures follow documented runbooks in `docs/runbooks/`.

**When in doubt:**

- Ask before:
  - Creating new top‑level directories.
  - Changing schema migrations.
  - Modifying NixOS service definitions.

Focus on incremental, reversible changes that keep the system running for a live eBay business.
```


***

## 2. `task.json` – normalized task envelope

Use this in each task directory under `data/task-intake/` and/or `planning/tasks/`. It’s designed so your orchestrator, Aider, Perplexity, and Google Docs can all speak the same language.[^2_4][^2_5]

```json
{
  "task_id": "TGW-0001",
  "title": "Implement queue worker and manager for dynamic eBay pricing",
  "type": "code",  // one of: "code", "research", "docs", "ops"
  "created_at": "2026-06-09T13:20:00-07:00",
  "created_by": "human",
  "status": "todo",  // "todo", "in-progress", "blocked", "done", "abandoned"
  "priority": "high",  // "low", "medium", "high", "urgent"
  "labels": [
    "pricing",
    "ebay",
    "queue-worker",
    "state-machine"
  ],

  "description": "Design and implement a dynamic pricing worker and manager. The worker will adjust listing prices based on rules (sell-through rate, inventory age, competitor prices). The manager will coordinate runs, enforce safety limits, and expose a control surface via tgw-api.",

  "inputs": {
    "codebase_paths": [
      "src/trader_grims_warehouse/managers/pricing_manager.py",
      "src/trader_grims_warehouse/queue_workers/pricing_worker.py",
      "src/trader_grims_warehouse/services/ebay/",
      "config/queues/pricing.json",
      "docs/runbooks/pricing.md"
    ],
    "data_sources": [
      "postgres://tgw_state_machine",
      "eBay API (orders, listings, completed items)"
    ],
    "constraints": [
      "No direct writes to ItemCatalog without going through the state machine.",
      "All price changes must be logged with before/after values and justification.",
      "Support dry-run mode first."
    ]
  },

  "outputs": {
    "artifacts": [
      "src/trader_grims_warehouse/managers/pricing_manager.py",
      "src/trader_grims_warehouse/queue_workers/pricing_worker.py",
      "config/queues/pricing.json",
      "docs/runbooks/pricing.md",
      "tests/test_pricing_worker.py"
    ],
    "acceptance_tests": [
      "pytest -q tests/test_pricing_worker.py",
      "Manual review: run worker in dry-run mode and verify logs only.",
      "Manual review: run against a small set of test SKUs in staging."
    ],
    "success_criteria": [
      "Worker processes a batch of items and computes new prices without errors.",
      "No production listings are changed when dry-run is enabled.",
      "Pricing manager can be called via tgw-api with start/stop/status operations."
    ]
  },

  "orchestration": {
    "repo": "git@github.com:you/trader_grims_warehouse.git",
    "branch": "task/TGW-0001-pricing-worker",
    "worktree": "/opt/TGW/worktrees/TGW-0001-pricing-worker",
    "queue_binding": "pricing-queue",
    "nix_profile": "tgw-dev",
    "agent_routing": {
      "code": "aider+claude",
      "research": "perplexity",
      "docs": "google-gemini"
    }
  },

  "notes": [
    "Consider starting with a simple rule-based engine; ML pricing can be added later.",
    "Coordinate with the backup manager to snapshot relevant tables before first production run."
  ]
}
```

You can of course trim fields you don’t want; the important thing is that `task_id`, `type`, `status`, and the I/O/branch metadata are stable.

***

## 3. `progress.md` – lightweight status tracker

This is a simple central log that humans and agents append to. It’s meant to be readable at a glance and safe for Aider to edit.[^2_6][^2_7][^2_1]

```markdown
# TGW Project Progress

This file tracks the current focus and major task statuses for Trader Grims Warehouse.
Agents may append entries but should not delete history.

---

## Current focus

- Hardening the Postgres-backed state machine and queue/worker pattern.
- Nix flake integration for core services (tgw-api, queue pool manager, backup manager).
- Laying groundwork for dynamic pricing and richer marketing analytics.

---

## Task summary

- Total open tasks: 17
- In progress: 5
- Blocked: 2
- Done (last 30 days): 9

(Counts are approximate; the source of truth is the `planning/tasks/` directory and/or `data/task-intake/`.)

---

## Recent changes

### 2026-06-09

- `TGW-0001` – Implement queue worker and manager for dynamic eBay pricing  
  - Status: `in-progress`  
  - Branch: `task/TGW-0001-pricing-worker`  
  - Notes: Skeleton `pricing_manager.py` and `pricing_worker.py` created. Queue definition `config/queues/pricing.json` stubbed. Next step: implement dry-run logic and tests.

- `TGW-0002` – Formalize backup manager and NixOS services  
  - Status: `todo`  
  - Notes: Backup manager design documented in `docs/runbooks/backup-manager.md`. Needs module in `nix/modules/backup-manager.nix` and service wiring.

### 2026-06-05

- `TGW-0099` – Normalize logging format and rotate logs out of history  
  - Status: `done`  
  - Branch: `task/TGW-0099-logging-refactor` (merged)  
  - Notes: Structured JSON logging added; historical logs moved into `data/history/`. Runbook updated.

---

## Guidelines for updates

When you complete or meaningfully advance a task:

1. Add a dated entry under the appropriate heading.
2. Include:
   - Task ID (`TGW-XXXX`)
   - Short description
   - Status (`todo`, `in-progress`, `blocked`, `done`)
   - Branch/worktree name
   - 1–3 bullet notes on what changed or what is needed next

Do not remove old sections; append new entries so we preserve a lightweight activity log.
```


***

If you want, next step I can:

- Adapt these to your exact `/opt/TGW` paths and current manager/worker names, and
- Sketch a tiny Python “orchestrator” that reads `task.json`, creates the git worktree, and launches Aider with the right files (`CLAUDE.md`, `CONVENTIONS.md`, task files) pre‑loaded.
<span style="display:none">[^2_10][^2_11][^2_12][^2_13][^2_14][^2_15][^2_16][^2_8][^2_9]</span>

<div align="center">⁂</div>

[^2_1]: https://www.facebook.com/groups/881330171044518/posts/982266307617570/

[^2_2]: https://www.humanlayer.dev/blog/writing-a-good-claude-md

[^2_3]: https://artificialcorner.com/p/claude-md

[^2_4]: https://blogs.ischool.berkeley.edu/i290ma-f12/files/2011/09/Work-Breakdown-Structure-Guide.doc

[^2_5]: https://mcpbr.org/aider-polyglot

[^2_6]: https://www.larksuite.com/en_us/blog/project-brief

[^2_7]: https://www.anthropic.com/research/long-running-tasks

[^2_8]: https://github.com/Aider-AI/aider/issues/959

[^2_9]: https://github.com/ChrisWiles/claude-code-showcase

[^2_10]: https://aider.chat/docs/usage.html

[^2_11]: https://plane.so/blog/how-to-write-a-project-brief-template-and-examples

[^2_12]: https://asana.com/resources/project-brief

[^2_13]: https://www.reddit.com/r/ClaudeAI/comments/1r66oo0/how_i_structure_claude_code_projects_claudemd/

[^2_14]: https://aider.chat/docs/usage/conventions.html

[^2_15]: https://www.youtube.com/watch?v=SGFbtlRk_4s

[^2_16]: https://aider.chat/2024/09/26/architect.html


---

# Please generate the orchestrator

Here’s a minimal but usable Python orchestrator that:

- Reads a `task.json`.
- Creates a git branch + worktree for it.
- Launches Aider pointed at the task’s files plus `CLAUDE.md` (and optionally `CONVENTIONS.md`) using `--message` so it does a first pass and exits.[^3_1][^3_2][^3_3][^3_4][^3_5][^3_6]

You can drop this into your repo as `tgw_task_orchestrator.py` and adjust paths/model to taste.

***

## `tgw_task_orchestrator.py`

```python
#!/usr/bin/env python3
"""
TGW Task Orchestrator

- Reads a task.json envelope.
- Creates a git branch + worktree for the task.
- Launches aider with CLAUDE.md, CONVENTIONS.md, and task files loaded.

Usage:
  python tgw_task_orchestrator.py init path/to/task.json
  python tgw_task_orchestrator.py run  path/to/task.json
"""

import json
import subprocess
import sys
from pathlib import Path
from textwrap import dedent

# --- CONFIG -----------------------------------------------------------------

# Root of the TGW git repo (will be auto-detected if None)
REPO_ROOT = None  # e.g. Path("/opt/TGW/src/trader_grims_warehouse").resolve()

# Where to put worktrees (relative to REPO_ROOT if not absolute)
WORKTREES_DIR = Path("worktrees")

# Default base branch to branch from
BASE_BRANCH = "master"

# Aider command & model configuration
AIDER_CMD = "aider"  # assumes in PATH
AIDER_MODEL = "sonnet"  # Claude 3.7 Sonnet; change to match your config
AIDER_EXTRA_ARGS = [
    "--yes",            # auto-confirm
    "--auto-commits",   # let aider commit changes
]

# Files to always add to aider sessions (relative to repo root)
DEFAULT_AIDER_FILES = [
    "CLAUDE.md",
    "CONVENTIONS.md",
]


# --- UTILITIES --------------------------------------------------------------

def run(cmd, cwd=None):
    print(f"+ {' '.join(cmd)} (cwd={cwd})")
    subprocess.run(cmd, check=True, cwd=cwd)


def detect_repo_root() -> Path:
    if REPO_ROOT is not None:
        return Path(REPO_ROOT).resolve()
    # ask git where the top-level repo root is
    out = subprocess.check_output(
        ["git", "rev-parse", "--show-toplevel"],
        text=True
    ).strip()
    return Path(out).resolve()


def load_task(task_json_path: Path) -> dict:
    with task_json_path.open("r", encoding="utf-8") as f:
        task = json.load(f)
    # basic sanity
    for key in ("task_id", "title", "type", "status"):
        if key not in task:
            raise ValueError(f"task.json missing required field: {key}")
    return task


def slugify(text: str) -> str:
    return "".join(
        c.lower() if c.isalnum() else "-"
        for c in text
    ).strip("-")


# --- WORKTREE / BRANCH ------------------------------------------------------

def ensure_worktree(task: dict, repo_root: Path) -> Path:
    """
    Create a git branch + worktree for this task if not already present.

    Returns the worktree path.
    """
    task_id = task["task_id"]
    title_slug = slugify(task["title"])[:40]
    branch_name = f"task/{task_id}-{title_slug}"

    # Allow override from task.orchestration
    orch = task.get("orchestration", {})
    branch_name = orch.get("branch", branch_name)

    worktrees_dir = (
        Path(orch["worktree"]).parent
        if "worktree" in orch
        else repo_root / WORKTREES_DIR
    )
    worktrees_dir.mkdir(parents=True, exist_ok=True)

    worktree_path = (
        Path(orch["worktree"])
        if "worktree" in orch
        else worktrees_dir / branch_name.replace("/", "_")
    )

    if worktree_path.exists():
        print(f"Worktree already exists at {worktree_path}")
        return worktree_path

    # Create branch + worktree from BASE_BRANCH
    run(
        [
            "git", "worktree", "add",
            "-b", branch_name,
            str(worktree_path),
            BASE_BRANCH,
        ],
        cwd=repo_root,
    )

    return worktree_path


# --- AIDER LAUNCH -----------------------------------------------------------

def build_aider_message(task: dict) -> str:
    """
    Build a short initial instruction for aider based on the task envelope.
    """
    description = task.get("description", "").strip()
    task_id = task["task_id"]
    title = task["title"]

    msg = dedent(f"""
    You are working inside the Trader Grims Warehouse (TGW) repo.

    Task ID: {task_id}
    Title: {title}
    Type: {task.get("type")}

    Description:
    {description}

    Follow the TGW style guide and CLAUDE.md.
    - Only modify files relevant to this task.
    - Work on this branch/worktree only.
    - Update any task notes file if present (e.g. task.md).
    - Ensure tests/acceptance checks described in task.json can pass, or
      explain why they cannot.

    First, briefly restate your understanding of the task and propose
    a small set of concrete steps. Then start implementing step 1.
    """).strip()

    return msg


def collect_task_files(task: dict, repo_root: Path, worktree_path: Path) -> list[str]:
    """
    Collect files to pass to aider:
    - DEFAULT_AIDER_FILES from repo root
    - Any files listed in task["inputs"]["codebase_paths"] or similar
    """
    rel_paths: list[str] = []

    # Always-include files (CLAUDE.md, CONVENTIONS.md, etc.)
    for p in DEFAULT_AIDER_FILES:
        if (repo_root / p).exists():
            rel_paths.append(p)

    # Task-specific codebase_paths (if present)
    inputs = task.get("inputs", {})
    code_paths = inputs.get("codebase_paths", [])
    for p in code_paths:
        if (worktree_path / p).exists():
            rel_paths.append(p)

    # Task metadata files (task.json, task.md) if they live under repo
    # Try to locate relative path from repo root if orchestration.repo matches
    # (We assume you will run this orchestrator from repo root or below.)
    # You can adjust this if task files live outside the repo.
    return sorted(set(rel_paths))


def run_aider_for_task(task: dict, repo_root: Path, worktree_path: Path):
    """
    Launch aider in single-message mode for this task.
    """
    message = build_aider_message(task)
    task_message_file = worktree_path / ".tgw_task_message.txt"
    task_message_file.write_text(message, encoding="utf-8")

    files = collect_task_files(task, repo_root, worktree_path)
    if not files:
        print("Warning: no files collected for aider; it will still see repo map.")

    cmd = [
        AIDER_CMD,
        "--model", AIDER_MODEL,
        "--message-file", str(task_message_file),
        *AIDER_EXTRA_ARGS,
        *files,
    ]

    run(cmd, cwd=worktree_path)


# --- CLI --------------------------------------------------------------------

def main(argv: list[str]) -> int:
    if len(argv) < 3 or argv[^3_1] not in {"init", "run"}:
        print(
            "Usage:\n"
            "  python tgw_task_orchestrator.py init path/to/task.json\n"
            "  python tgw_task_orchestrator.py run  path/to/task.json\n"
        )
        return 1

    mode = argv[^3_1]
    task_json_path = Path(argv[^3_2]).resolve()
    task = load_task(task_json_path)

    repo_root = detect_repo_root()
    print(f"Repo root: {repo_root}")

    if mode == "init":
        worktree_path = ensure_worktree(task, repo_root)
        print(f"Initialized worktree for task at: {worktree_path}")
        return 0

    if mode == "run":
        worktree_path = ensure_worktree(task, repo_root)
        run_aider_for_task(task, repo_root, worktree_path)
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
```


***

### How this fits your workflow

```
- `init` creates the `task/<id>-<slug>` branch and worktree from `master` using `git worktree add -b …`, following standard git worktree usage.[^3_3][^3_7][^3_6][^3_1]
```

- `run` ensures the worktree exists, builds a task‑specific message from `task.json`, gathers `CLAUDE.md`/`CONVENTIONS.md` plus the `inputs.codebase_paths` files, and calls Aider in one‑shot `--message-file` mode so it does an initial planning+implementation pass and exits.[^3_8][^3_2][^3_4][^3_9][^3_5]
- You can wire your future MCP/orchestrator to call this script from your queue system, and still manually drop into the worktree to continue a session interactively if needed.

If you want, I can also add:

- A `--dry-run` flag that skips `aider` and just prints the commands.
- A mode that updates `progress.md` after each run by appending a simple entry for the task.
<span style="display:none">[^3_10][^3_11][^3_12][^3_13][^3_14][^3_15]</span>

<div align="center">⁂</div>

[^3_1]: https://dev.to/yankee/practical-guide-to-git-worktree-58o0

[^3_2]: https://opendeep.wiki/Aider-AI/aider/using-aider-files-repos

[^3_3]: https://stackoverflow.com/questions/49802226/git-worktree-add-from-specific-commit-hash

[^3_4]: https://aider.chat/docs/scripting.html

[^3_5]: https://opendeep.wiki/Aider-AI/aider/using-aider-cli-arguments

[^3_6]: https://git-scm.com/docs/git-worktree

[^3_7]: https://www.theserverside.com/blog/Coffee-Talk-Java-News-Stories-and-Opinions/Use-this-git-worktree-add-example-and-never-switch-branches-again

[^3_8]: https://aider.chat/docs/usage.html

[^3_9]: https://github.com/aider-ai/aider

[^3_10]: https://github.com/Aider-AI/aider/issues/660

[^3_11]: https://aider.chat/docs/

[^3_12]: https://stackoverflow.com/questions/79517581/configure-aider-with-custom-models

[^3_13]: https://aider.chat/docs/config/options.html

[^3_14]: https://aider.chat/docs/usage/tips.html

[^3_15]: https://www.gitkraken.com/learn/git/git-worktree

