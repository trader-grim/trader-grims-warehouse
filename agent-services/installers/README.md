# Agent-service adapter materializer

`materialize-agent-services` exposes the canonical shared `tgw-plan` skill and
Promptcraft provider through harness-local symlinks. The default is a write-free
dry run; `--apply` performs a preflight and writes only when the complete target
matrix is conflict-free.

| Target | Shared skill | Promptcraft |
|---|---|---|
| Codex | `${HOME}/.codex/skills/tgw-plan` | `${HOME}/.codex/providers/promptcraft` |
| Claude | `${HOME}/.claude/skills/tgw-plan` | `${HOME}/.claude/providers/promptcraft` |
| Hermes | `${HOME}/.hermes/skills/tgw-plan` | `${HOME}/.hermes/providers/promptcraft` |
| Isolated worker | not installed | `${PROJECT}/.tgw-worker/bin/promptcraft-handoff` only |

Existing noncanonical files hold the complete matrix and are never overwritten.
Every report includes the canonical content digest used for verification.

```bash
agent-services/installers/materialize-agent-services codex \
  --home /path/to/home --project /path/to/project --source-root /path/to/tgw-source
```
