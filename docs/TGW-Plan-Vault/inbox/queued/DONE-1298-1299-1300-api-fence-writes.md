Todos #1298+#1299+#1300 (PP-COHESION-001) — DONE, stitched. api.py's 7
direct atomic_write_json call sites (#1298) + migrate-unblock (#1299) now
pass archive_root=cfg.get('archive_root'); migrate-restore (#1300) now
refuses to overwrite an existing live item JSON unless --force is passed,
and its write is also archive_root-protected. Reviewed clean, full suite
green (2150 passed, 1 skipped, 1 pre-existing unrelated flake #1370).
