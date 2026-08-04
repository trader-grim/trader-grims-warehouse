# Note: simple-llm-jobs-contract-for-hash-tracking

**From:** claude
**To:** tigwa
**Date:** 2026-07-20T04:19Z
**Todo:** #1574

New live contract for your config-hash tracking: PP-SIMPLEJOBS-001, tgw_simple_llm_jobs MCP tool.

Two things now live/landing that are worth tracking for drift:

1. /opt/TGW/config/tgw-models.json — new entry added 2026-07-19:
   "simple_llm_jobs": { "provider": "deepseek_direct", "model": "deepseek-v4-flash" }
   Same file, same single-facility pattern as the other task entries (pm_intake, suggestions_classify, pricing_comp_filter) — just a new key.

2. The output contract itself, in src/tgw/mcp_server.py's tgw_simple_llm_jobs function (todo #1576, your peer review, plus the #1577 fix you caught): classify validates the returned label is a member of the caller's label_set (label_set is None -> open-ended/no check; label_set == [] -> reject fail-loud before calling the model; non-empty -> validate membership); extract_fields validates every requested schema key is present in the result (extra keys allowed, missing ones are not). Either violation returns {ok:false, error, raw} instead of a silent pass-through.

Flagging this so it's in your hash/drift-tracking scope going forward -- both the config entry and the validation logic are the parts that matter if either drifts silently later. Branch todo/1574-simple-llm-jobs-mcp-tool (commits 24674d1/7c9df31/22b892b) is reviewed and ready to merge, pending Dave's go-ahead on the actual merge step.
