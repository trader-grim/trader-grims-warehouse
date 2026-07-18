# In progress: todo #1523 (PP-LISTEDITOR-001)

Working on worktree `/opt/TGW/var/worktrees/1523-revision-apply-empty-aspect-fix`,
branch `todo/1523-revision-apply-empty-aspect-fix`.

Task: mirror `_build_offer_bodies`'s cleared-aspect omission rule
(`if v not in (None, '')`) inside `tgw/revision.py`'s
`_place_delta_in_bodies` aspects/item_specifics branch, so revision/apply's
live-push path (Revisions UI accept-and-push) stops sending
`{Brand: ['']}` to eBay's Inventory API PUT and instead omits the key
entirely — matching invariant C14 / #1462's fix for the other push path.

Regression test `TestLiveApply::test_c14_aspects_delta_clear_omits_key_not_blank_value`
in `tests/test_revision.py` is currently `xfail`; fix should make it pass,
then the xfail marker gets removed.

Status: starting pre-flight reads.
