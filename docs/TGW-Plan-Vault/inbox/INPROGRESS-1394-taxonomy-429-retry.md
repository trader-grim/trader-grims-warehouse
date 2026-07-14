# In progress: todo #1394 — taxonomy 429 retry

Working in worktree `/opt/TGW/var/worktrees/1394-taxonomy-429-retry` on
branch `todo/1394-taxonomy-429-retry`. Adding retry-with-backoff to
`_fetch_aspects_live()` in `src/tgw/apis/ebay/specifics.py` for the
Taxonomy API's `get_item_aspects_for_category` 429 case (12 ebay_draft
dead-letters). Scope: local fix in specifics.py only, not shared
`ebay_get()` (many other unrelated callers use it). Adding unit tests for
retry-then-succeed, persistent-429, and no-retry-on-200.
