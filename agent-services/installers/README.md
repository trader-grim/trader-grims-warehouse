# Agent-service adapter materializer

`materialize-agent-services` exposes the canonical shared `tgw-plan` skill and
Promptcraft provider through harness-local symlinks. The default is a write-free
dry run; `--apply` performs a preflight and writes only when the complete target
matrix is conflict-free.

| Target | Shared skill | Promptcraft |
|---|---|---|
| Codex | `${HOME}/.codex/skills/tgw-plan` | `${HOME}/.codex/providers/promptcraft` |
| Claude | `${PROJECT}/.claude/skills/tgw-plan` | `${PROJECT}/.claude/providers/promptcraft` |
| Hermes | `${HOME}/.hermes/skills/tgw-plan` | `${HOME}/.hermes/providers/promptcraft` |
| Isolated worker | not installed | `${PROJECT}/.tgw-worker/bin/promptcraft-handoff` only |

An existing noncanonical Claude skill is reported as `HELD_LEGACY` and is never
overwritten. Other conflicts hold the complete matrix. Every report includes the
canonical content digest used for verification.

```bash
agent-services/installers/materialize-agent-services codex \
  --home /path/to/home --project /path/to/project --source-root /path/to/tgw-source
```
