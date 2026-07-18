# In progress: #1147 PP-KNOWLEDGE-001 R2 — tgw search --full-text

Working in worktree `/opt/TGW/var/worktrees/1147-search-full-text` on branch
`todo/1147-search-full-text`, off `catio-nix-0.0.1-alpha` (verified live
branch, not `main`). Building the R2 packet: `tgw search --full-text`
CLI subcommand (recollq-backed), a web UI search bar in http_server.py,
and an MCP tool `tgw_search_full` in mcp_server.py + EXPECTED_TOOLS update
in tests/test_mcp_server.py.

Pre-flight note: the todo/spec references "the six hours-to-seconds queries
from FUTURE-IDEAS PP-SEARCH-001" as acceptance cases — searched
FUTURE-IDEAS.md, TGW-Master-Plan.md, PP-DRIVE-INDEX-plan.md,
recoll-annex-jetstream.md; no literal enumerated list of six queries exists
in any live doc (PP-SEARCH-001 content was folded into PP-KNOWLEDGE-001's
master-plan section without preserving a discrete list, if one ever
existed as such — likely lived only in Dave's original transcript).
Proceeding using representative real recovery/audit-style queries (the kind
named nearby: "49 missing item JSONs", SKU lookups, serial/label lookups)
as the acceptance evidence instead, flagged as a deviation in the result
manifest rather than blocking the whole packet on a missing artifact.

recollq confirmed live: `/run/current-system/sw/bin/recollq` works against
`/opt/TGW/.recoll` (Recoll 1.43.2 + Xapian), 26343+ hits on a smoke test.
