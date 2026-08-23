# Harness adapters

The canonical skill package is `agent-services/skills/tgw-plan` in the admitted TGW
source tree. Adapters expose that exact package; they do not fork its policy.

- Codex interactive: `${CODEX_HOME}/skills/tgw-plan`, normally a symlink.
- Claude interactive: `${HOME}/.claude/skills/tgw-plan`, normally a signed-generation materialization.
- Hermes/Tigwa: `${HERMES_HOME}/skills/tgw-plan`, symlink or thin loader when external
  symlinks are unsupported.
- Isolated workers: receive a compact execution card. Install the full skill only for
  planning-capable treatments.

Run `scripts/check_adapters.py CANONICAL_SKILL ADAPTER...` to verify every installed
adapter resolves to files with the canonical digest. An absent adapter is a failed
check, not permission to use an older local skill.
