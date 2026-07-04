# DONE — todo #1102: test-suite repair

Full suite: **1,761 passed / 1 skipped / 0 failed / 0 errors** (was 1,513
passed / 12 failed / 236 errors at session start tonight).

Root causes found and fixed:
1. **`test_http_server.py` (236 errors → 0):** every fixture set
   `http_server._web_key`, an attribute that no longer exists — the s42/43
   session replaced the old "embed a per-session key in page HTML" auth
   model with a proper cookie-login wall (`_web_password` + `_session_guard`
   middleware + `tgw_session` cookie). Renamed the monkeypatch target, added
   a `_login(client)` helper that injects a valid session directly (fast,
   no real login POST needed), and applied it to the 58 tests that assert on
   authenticated `/form/*` page content. The 4 tests that specifically
   asserted "a web key is embedded in the HTML" were rewritten — that
   pattern was deliberately removed (httponly cookies are strictly more
   secure), so the tests now assert the real API key never leaks into
   rendered HTML instead. One unrelated field-drift bug fixed along the way
   (`/api/items` now returns `ebay_listing_status`, test's expected-column
   set hadn't caught up).
2. **`test_fence.py` (18 errors → 0):** same `_web_key` → `_web_password`
   rename, one line.
3. **`test_ebay_publish_price_drift.py` (2 fail → 0):** the s42 ordering
   guard (`state_machine.active_jobs_for_sku`, ebay_publish.py:148) was
   never mocked in this test file, so both tests fell through to a real
   Postgres connection attempt and failed on peer-auth. Mocked it.
4. **`test_config_hygiene.py` + `test_freeship.py` (7 fail → 0):** neither
   set `secrets_root` in their test config JSON, so `load_config()` fell
   back to the real `/opt/TGW/secrets/tgw-api-key.json` — unreadable
   (correctly, chmod 600 tgw-owned) by any non-tgw test runner. Pointed
   `secrets_root` at a tmp_path subdir in both files.

No production code changed for this packet except the two genuinely-missing
test mocks/fixtures above — this was pure test-suite drift repair.
