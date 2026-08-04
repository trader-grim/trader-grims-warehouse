Status: cleared
Reviewer: Claude (runner-review)
Todo: #1308   PP: PP-COHESION-001
Checked: diff (`git diff f219b4b todo/1308-photo-history-announce`) against
the todo brief's stated bug (main() missing announce_script_run() per
invariant E9), scope (photo_history_recovery.py + new test only), result
manifest completeness. Verified the deviation claim directly: grepped
`announce_script_run` across src/tgw/ — confirmed zero real callers exist
today, only the docstring usage example in logging.py itself. My original
packet briefing wrongly asserted "several other scripts already call it" —
executor caught this, proceeded correctly off the function's own documented
contract instead, and filed todo #1369 (PP-COHESION-001) to audit the rest
of workers/*.py and tools/*.py rather than silently expanding this packet's
scope. That's the right call — noted as a packet-authoring error on my
side, not an executor problem.
Summary: minimal fix — announce_script_run() call added at the top of
main(), before load_config() or any ItemData/queue touch, matching the
function's documented signature exactly. New test asserts call ordering
via monkeypatched call-order tracking. Full suite green (2138 passed, 1
skipped). No triggers fired. Cleared for stitch.
