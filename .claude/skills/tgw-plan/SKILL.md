---
name: tgw-plan
description: Claude planning pass for a TGW feature or epic. Use when the user says /tgw-plan or asks to plan a feature. Produces a mini design doc written to docs/ai-plans/{feature-slug}.md. Planning only — no source code changes.
---

# TGW Plan

Claude planning pass for a feature or epic. Produces a mini design doc at `docs/ai-plans/{feature-slug}.md`. No source code changes.

## Usage

Invoke with a feature slug and brief description:
> /tgw-plan {feature-slug} — {one-line description}

## Steps

1. Read `CLAUDE.md` and note all settled-architecture constraints:
   - tgw-api fence: all ItemData reads/writes go through tgw-api
   - Workers are thin: no direct path construction in workers
   - Output contract: every API call returns `{ok, ...}`
   - Secrets from `secrets_root`: no hardcoded paths in `src/`
   - Catalog rebuild is always a job: never call `build_all_catalogs()` inline
   - SKU format: `tgwYYYYMMDDHHMMSSmmm`

2. Gather context: scan relevant existing source files, tests, and any linked section in `docs/TGW-Plan-Vault/`.

3. Write the design doc to `docs/ai-plans/{feature-slug}.md` using the template below.

4. Do not create or modify any source code — planning only.

## Output template

```markdown
# {feature-slug}: {one-line description}

**Status:** Draft — {date}
**PP ref:** {pp-ref or "none"}

## Problem / motivation

{why this is needed}

## Constraints (from settled architecture)

- {list any settled-arch constraints that apply to this feature}

## Proposed approach

{the design — what changes, how components interact}

## Files to change

| File | Change |
|------|--------|
| `src/tgw/...` | ... |

## Acceptance criteria

- [ ] {test or observable behaviour that proves it works}

## Open questions

- {anything that needs Dave's input before coding starts}
```
