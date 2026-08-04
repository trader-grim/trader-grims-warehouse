# INPROGRESS: todo #1563 — clipboard-agent-delivery Phase 0

Dispatched to tgw-coder. Full plan at docs/ai-plans/clipboard-agent-delivery.md. Scope:
`tgw_clip_deliver` MCP tool (same pattern as tgw_enqueue/tgw_add_suggest, READONLY-gated
so Tigwa's training-mode restriction covers it automatically), origin/label columns on
clip_history (additive), and a real bugfix in launch_rofi_picker()'s content-prefix
matching that Dave confirmed is the likely cause of a recurring paste-corruption symptom
he already hits.

If interrupted before tgw-coder returns: check for a result manifest under
docs/TGW-Plan-Vault/plan/packets/results/1563-*, otherwise the packet needs
re-dispatching from the plan doc.
