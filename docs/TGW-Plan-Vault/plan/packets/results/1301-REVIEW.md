Status: cleared
Reviewer: Claude (runner-review)
Todo: #1301   PP: PP-COHESION-001
Checked: diff (`git diff f219b4b todo/1301-resolver-silent-except`) against
the todo brief's stated bug (silent bare-except discarding unreadable items
in resolve()'s JSON-loading selector loop), scope (resolver.py + new test
only), result manifest completeness (status/files/live-evidence/deviations
all present).
Summary: minimal fix — bare `except Exception: continue` now logs a
warning naming the skipped SKU before continuing, using the project's
existing `logging.getLogger(__name__)` convention (verified against
health.py/promo.py/scrub.py). Exception type deliberately left broad per
the packet's own guidance. New regression test uses caplog to assert both
the valid item still resolves and the corrupt one is logged. Full suite
green (2138 passed, 1 skipped). Executor flagged two similarly-shaped
bare-except sites nearby (_build_sku_old_index, _location_skus_from_itemdata)
as out-of-scope rather than fixing them — reasonable call, left for PP
owner to confirm whether a separate todo already covers them (worth a
quick check before closing out the resolver.py sweep in this PP). No
triggers fired. Cleared for stitch.
