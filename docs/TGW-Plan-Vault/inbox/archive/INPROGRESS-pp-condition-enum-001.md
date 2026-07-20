# INPROGRESS: PP-CONDITION-ENUM-001 dispatch

Diagnosed + fixed live: tgw202605051124483 dead-lettered at ebay_stage because
draft_listing.condition_enum held the raw human label "Very Good" instead of a valid
Inventory API enum, bypassing ebay_stage.py's safe _map_condition() fallback. Dave
corrected the condition manually; staging + a stale dead-lettered ebay_publish job
(requeued) both succeeded — https://www.ebay.com/itm/327268460460.

Todo #1561 (investigation) done. Todo #1562 (the generalized fix, per Dave's direction
2026-07-19: one reusable client-side field-flagging function instead of a title-only
one, plus a save-error contract that names the errant field key for any field/any error
source) opened under new PP-CONDITION-ENUM-001, added to master plan. Dispatching to
tgw-coder next as a work packet.

If interrupted before dispatch completes: the packet spec is fully written in the master
plan's PP-CONDITION-ENUM-001 section — pick up by writing the tgw-coder packet from that
section and todo #1562.
