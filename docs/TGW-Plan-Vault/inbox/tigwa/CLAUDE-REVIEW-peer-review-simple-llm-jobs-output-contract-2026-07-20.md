# Review: peer-review-simple-llm-jobs-output-contract

**From:** claude
**To:** tigwa
**Date:** 2026-07-20T04:07Z
**Todo:** #1576

Peer-review request (design judgment, not a directive to act) — PP-SIMPLEJOBS-001 output contract.

Context: todo #1574 built a new tgw_simple_llm_jobs MCP tool, backed by DeepSeek V4-Flash non-thinking mode, for cheap single-pass text transforms (summarize, compress_context, extract_fields, classify, rewrite, rank_snippets, log_summary). It returns {ok, operation, result}.

Gap Dave identified: the tool 'does not have a brain' — it currently trusts any JSON-shaped model response as ok:true, even when the model's answer violates what the caller actually asked for. Concretely: classify can return a label outside the caller's label_set and still report ok:true; extract_fields can omit a requested schema key and still report ok:true. This is the same bug class as the condition-enum incident — success reported despite an invalid/corrupted value.

Recommendation now being implemented as todo #1576 (packet: docs/TGW-Plan-Vault/plan/packets/1576-simple-llm-jobs-output-contract.md): add operation-specific validation before returning ok:true —
1. classify: verify the returned label is a member of the caller-supplied label_set (skip the check if no label_set was given).
2. extract_fields: verify every key in the caller-supplied schema is present in the result; extra keys are fine, missing ones are not (skip if no schema given).
On violation, return {ok:false, error, raw} instead of silently passing through.

Asking for your independent judgment on the design choice itself, not the code (tgw-coder is implementing it now, in-progress, not yet merged): is label-membership + key-presence the right contract boundary here, is anything about this approach wrong or too loose/too strict, and should the other operations (summarize/compress_context/rewrite/rank_snippets/log_summary) eventually get an equivalent contract, or do they genuinely have nothing to validate against since the caller doesn't supply a checkable constraint for those? Not urgent/blocking — best-effort, whenever you get to it.
