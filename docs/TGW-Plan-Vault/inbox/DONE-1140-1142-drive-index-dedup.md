# DONE — #1140 (drive survey tooling) + #1142 (dedup scan) + plan corrections

**#1140:** Mounted sdi (idle 500G bus-powered USB drive) read-only at
/opt/TGW/mnt/db-home, added to recoll topdirs (db/ subtree auto-excluded
by permissions -- 700 db:users, tgw uid 900 can't read it). Built
scripts/survey_drive.sh (PP-DRIVE-INDEX Phase 0.4) -- cheap read-only
per-drive survey: smartctl identity, file listing, type/size breakdown,
one-page report, auto-cleanup. Live-verified against sdf1 (Ventoy stick).
Reindex of db-home was still running when this session's bandwidth ran
low -- final doc count not yet confirmed, check `/opt/TGW/.recoll` next
session if not already done.

**#1142:** Ran fclones dedup scan against ItemArchive+ItemCatalog (the
already-indexed, already-mounted local data) as a first test. Found only
232 redundant files / 210.8MB -- small, as expected once Dave clarified
the real mechanism.

**Correction from Dave, important standing requirement:** dedup value is
cross-drive consolidation (same file backed up across multiple external
drives over years), not intra-ItemData duplication (each item's own
photos are unique, nothing to dedup there). Dave must personally approve
every data-deletion decision -- this is why the original plan wanted
Hermes running this project (long approval queue, not a single-session
task). Recorded in PP-DRIVE-INDEX-plan.md as a standing requirement for
any future dedup/consolidation tooling: identify + report only, never
delete unattended.
