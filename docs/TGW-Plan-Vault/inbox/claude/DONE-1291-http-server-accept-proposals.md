# In progress: todo #1291 http_server.py accept_proposals patch bug

Working in worktree `/opt/TGW/var/worktrees/1291-http-server-accept-proposals`
on branch `todo/1291-http-server-accept-proposals`. Fixing the
`accept_proposals` branch of `item_action()` in `src/tgw/http_server.py`
(~lines 1436-1458): the identity check `ia is not doc.get("item_attributes")`
is always False because `ia.update()` mutates the dict in place before the
comparison, so accepted item_attributes/draft_listing edits never reach
`proposal_fields` and are silently dropped by `_apply_patch()`. Replacing
with explicit `ia_touched`/`dl_touched` boolean tracking per the packet spec.
Scope: this one branch only, per packet Out-of-scope list. Next: verify live
against actual file content, apply fix, run pytest with PYTHONPATH override,
write result manifest.
