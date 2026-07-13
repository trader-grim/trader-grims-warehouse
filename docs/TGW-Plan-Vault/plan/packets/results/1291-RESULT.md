# Result: 1291 http-server-accept-proposals
Status: done
Todo: #1291   PP: PP-COHESION-001
Files touched: src/tgw/http_server.py, tests/test_http_server.py
Live evidence: Added 3 regression tests (`test_accept_proposals_persists_item_attributes_edit`,
`test_accept_proposals_persists_draft_listing_title_description`,
`test_accept_proposals_item_attributes_absent_before`) that POST
`action=accept_proposals` through the real FastAPI TestClient endpoint and
read back the persisted JSON file from disk. Confirmed via `git stash` that
all 3 tests FAIL against the pre-fix code:
  - item_attributes case: `assert doc["item_attributes"]["Brand"] == "NewBrand"` fails (edit discarded)
  - draft_listing case: fails identically
  - "absent before" case: `KeyError: 'item_attributes'` — this case was
    NOT actually accidentally-correct as the packet's spec section assumed;
    `doc["item_attributes"] = ia` unconditionally aliases `doc.get("item_attributes")`
    to `ia` before the identity check runs, so the check is always False
    regardless of whether the key pre-existed. The fix (explicit
    `ia_touched`/`dl_touched` booleans) handles all three cases correctly.
  All 3 tests PASS against the fixed code. Full offline suite:
  `PYTHONPATH=/opt/TGW/var/worktrees/1291-http-server-accept-proposals/src:$PYTHONPATH python3 -m pytest -q`
  → 2049 passed, 1 skipped (pre-existing skip, unrelated). Verified
  `tgw.http_server.__file__` resolves under the worktree path before testing.
Deviations from spec: The packet's spec text asserted the "item_attributes
absent before" path "was already accidentally correct" pre-fix. Live test
evidence shows this is not true — that path was also broken (`KeyError` on
old code), for the reason above. This does not change the fix (the spec's
proposed replacement code already handles all three cases correctly
regardless), so no code deviation — flagging only because the diagnostic
premise in the packet was slightly off from what live testing showed.
Out-of-scope findings filed: none
