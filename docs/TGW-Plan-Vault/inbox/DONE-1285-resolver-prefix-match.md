Working todo #1285 (PP-COHESION-001, cohesion-audit-2026-07-10 batch) in
isolated worktree `/opt/TGW/var/worktrees/1285-resolver-prefix-match` on
branch `todo/1285-resolver-prefix-match`. Task: `resolver.py`'s
`resolve(sku=q)` old-format prefix-match fast path (`s[:18]==prefix18`) is
dead code for 14-17 char queries because it compares differently-sized
strings, always False, despite the guard explicitly admitting that length
range. First task of a new sequence (SECURITY track #1276-1283 fully closed)
— per cadence rule, needs 2-in-a-row clean before stitching + graduating to
concurrent batching. #1286 queued next.
