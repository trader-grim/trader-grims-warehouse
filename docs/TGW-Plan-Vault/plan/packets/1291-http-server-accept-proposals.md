# Packet: accept_proposals actually persists accepted item_attributes/draft_listing edits
Todo: #1291   PP: PP-COHESION-001   Track: concurrent batch 1 of 3 (PP-HERMES-EA-001)

## Context budget (ALL the model may load)
This packet + `src/tgw/http_server.py` (the `accept_proposals` branch of
`item_action()`, lines ~1436-1458 only, plus its existing test file if
one exists) + the todo brief (`tgw todo brief 1291`). Nothing else — do
NOT load the rest of this file (it is multi-thousand lines).

## Spec
At `src/tgw/http_server.py:1436-1458`:
```python
ia = doc.get("item_attributes") or {}
if "item_specifics" in delta and isinstance(delta["item_specifics"], dict):
    ia.update(delta["item_specifics"])
    doc["item_attributes"] = ia
dl2 = doc.get("draft_listing") or {}
if "title" in delta:
    dl2["title"] = delta["title"]
if "description" in delta:
    dl2["description"] = delta["description"]
proposal_fields: Dict[str, Any] = {"revision_draft": None}
if ia is not doc.get("item_attributes"):
    proposal_fields["item_attributes"] = ia
if dl2 is not doc.get("draft_listing"):
    proposal_fields["draft_listing"] = dl2
```
When `doc.get("item_attributes")` already exists (the common case), `ia`
is the SAME object reference as `doc["item_attributes"]` — `or {}` only
creates a new dict when the key was absent. `ia.update(...)` then mutates
that shared object in place, so `doc["item_attributes"]` reflects the
change immediately too. The later check `ia is not doc.get("item_attributes")`
compares an object to itself — always `False` — so
`proposal_fields["item_attributes"]` is NEVER set, and `_apply_patch()`
never receives the accepted edit. Same bug for `dl2`/`draft_listing`. The
in-memory `doc` looks correct, but nothing is ever written to disk —
accepted operator edits are silently discarded.

Fix: replace the broken identity check with explicit tracking of whether
each dict was actually touched:
```python
ia = doc.get("item_attributes") or {}
ia_touched = False
if "item_specifics" in delta and isinstance(delta["item_specifics"], dict):
    ia.update(delta["item_specifics"])
    doc["item_attributes"] = ia
    ia_touched = True
dl2 = doc.get("draft_listing") or {}
dl_touched = False
if "title" in delta:
    dl2["title"] = delta["title"]
    dl_touched = True
if "description" in delta:
    dl2["description"] = delta["description"]
    dl_touched = True
proposal_fields: Dict[str, Any] = {"revision_draft": None}
if ia_touched:
    proposal_fields["item_attributes"] = ia
if dl_touched:
    proposal_fields["draft_listing"] = dl2
```

## Dataset
This is a real data-integrity fix — accepted operator edits (item
specifics, title, description corrections made via the proposal-review
UI) will now actually persist through `_apply_patch`, where before they
were silently dropped despite the endpoint returning `{"ok": True}`.

## Out of scope
- Any other action branch in `item_action()` (`dismiss_proposals`, etc.).
- `_apply_patch()` itself.
- Anything else in `http_server.py` — this file is large; stay inside
  the one function's one branch.

## Acceptance (live)
1. Construct a `doc` with an existing non-empty `item_attributes` dict
   and a `revision_draft.delta` containing `item_specifics`. Call the
   `accept_proposals` logic (directly, or via the endpoint against a real
   test item) and confirm `proposal_fields` (or the actual patch call
   received) INCLUDES the updated `item_attributes` — not silently
   omitted.
2. Same for `draft_listing`/`title`/`description`.
3. Confirm the case where `item_attributes` was absent before (`doc.get`
   returns `None`, `ia = {}` is a genuinely new object) still works
   correctly too — this was already accidentally correct before (identity
   check happened to be True in that case), don't regress it.
4. If a real test item is available, run the actual endpoint end-to-end
   and confirm the persisted JSON on disk reflects the accepted edit
   after the call — real "on disk" evidence, not just the in-memory dict.

## Quota/risk
None — no new API calls.
