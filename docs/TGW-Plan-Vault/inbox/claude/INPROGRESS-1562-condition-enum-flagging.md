# In progress: todo #1562 / PP-CONDITION-ENUM-001

Working in worktree `/opt/TGW/var/worktrees/1562-condition-enum-flagging` on branch
`todo/1562-condition-enum-flagging`. Task: generic client-side field-flagging function
(red border), save-error field contract (PATCH validation for condition_enum against
allowed enum set, plus eBay-rejection field extraction into pipeline_error), and wiring
condition select + title field through the shared function. See todo #1562 brief for
full spec. Starting by reading http_server.py sections around
_build_condition_options / loadCatCtx / updateCharCount / draft_listing PATCH handler,
and tgw/ebay/sync.py's _format_ebay_error / ebay_stage.py pipeline_error construction.
