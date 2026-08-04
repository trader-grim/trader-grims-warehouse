# DONE — admin-file/pm_intake topology update (todo #1435/#1436, PP-HERMES-EA-001)

Tigwa filed #1435 (spec) and Dave via Tigwa filed #1436 (mirrored request) moments
after I finished the inbox directory split (todo #1431) — both asking for
`tgw admin-file`/`pm_intake.py` to actually understand the new per-actor topology
instead of only scanning the flat root.

**Implemented:** `scan_and_enqueue`/`cmd_admin_file` now discover root + `dave/` +
`tigwa/` only, never `claude/`/`queued/`/`archive/`/`review/`. Owner-qualified
queue paths prevent same-filename collisions across owners. Job payloads carry
owner/source_path/sha256/intake_ts. `--dry-run` added. 7 new tests, 46/46 total pass.
Live-verified with a real non-mutating `sudo -u tgw tgw admin-file --dry-run` against
production (correctly found and gated all 14 real notes in `inbox/tigwa/`).

Full detail + test evidence: `docs/TGW-Plan-Vault/inbox/tigwa/
RESPONSE-1435-1436-admin-file-topology-implemented.md` (review artifact, addressed
to Tigwa/Dave per the requests' instruction).

Todos #1435/#1436 left open (not marked done) — both requests said to keep them
open until review/linking completes.

**Still separately open:** todo #1434 (Tigwa's own librarian/PM-intake workflow
ownership) is hers, not mine — nothing to do there from this session.
