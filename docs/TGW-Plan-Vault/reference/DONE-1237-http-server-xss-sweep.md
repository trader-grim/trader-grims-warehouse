# DONE — todo #1237: http_server.py unescaped/unsafe output sweep

audit#1143 mitigation track, first packet (Dave: syncthing findings stay
deferred per s47 replan; start the http-server track at #1237).

## Fixed (all 5 sites in one pass, src/tgw/http_server.py)

1. `/login` `+` `/login-error` (`login_get`/`login_post`) — `next` param was
   interpolated unescaped into `<input type="hidden" name="next" value="{next}">`
   via `str.format` (reflected XSS). Now escaped with `html.escape` on both
   the GET render and the POST failure re-render.
2. `login_post` open-redirect guard — `next.startswith("/")` accepted
   protocol-relative `//evil.com` URLs. Replaced with a new
   `_safe_next_path()` helper that also rejects `//` and `/\` prefixes,
   falling back to `/form/home`.
3. `/form/intake/{sku}` unknown-SKU 404 page — raw `sku` path segment
   interpolated into `<h2>SKU not found: {sku}</h2>` (reflected XSS, cookie
   exposure risk). Now escaped.
4. `intake_form` success template — `weight_oz`/`barcode`/`ai_hint` (stored
   item fields, attacker-influenceable via intake) rendered unescaped into
   `value="..."` attrs (stored attribute-injection). Now escaped at the
   `doc.get(...)` call site.
5. `/docs/{path}` vault markdown viewer — `mistune.create_markdown(escape=False, ...)`
   let raw HTML/script in any vault `.md` file execute verbatim on an
   unauthenticated route. Flipped to `escape=True`.

## Evidence

- 7 new regression tests added to `tests/test_http_server.py` (one per
  fix + the safe-redirect-still-works case), all green:
  `test_login_get_escapes_next_param`,
  `test_login_post_escapes_next_on_failure`,
  `test_login_post_rejects_protocol_relative_redirect`,
  `test_login_post_allows_safe_relative_redirect`,
  `test_intake_form_404_escapes_unknown_sku`,
  `test_intake_form_escapes_stored_fields`,
  `test_docs_page_escapes_raw_html_in_markdown`.
- Full `tests/test_http_server.py`: 244/244 pass (was 237; no regressions).
- Full repo suite: 1835 passed / 9 failed — the 9 failures are in
  `test_model_routing.py` / `test_invariants_pricing.py`, files this change
  never touched (confirmed via `git diff --stat`); pre-existing, unrelated.
- `tgw-http.service` restarted clean (journalctl shows normal
  startup/shutdown, no errors).
- **Live verification on the running service**: `/login?next="><script>...`
  confirms the response now contains `&lt;script&gt;` and no literal
  `<script>` tag (curl, prod, post-restart). `/docs/reference/runbooks/INDEX.md`
  still renders 200 after the `escape=True` flip (no regression to a real
  doc). The open-redirect and stored-field fixes were **not** additionally
  live-curl-tested against prod — that would have required either the real
  web-login password (I stopped short of hunting for it in
  config/secrets per the session's own guardrail) or writing a throwaway
  file into the real synced Plan Vault (declined — pollutes a live synced
  dataset). Both are instead covered end-to-end by the TestClient regression
  tests above, which exercise the exact same code path.

## Deviations from spec

None — all 5 sites fixed exactly as the audit finding specified (consistent
`h()`/`html.escape` use, a proper redirect-target allowlist, `mistune
escape=True`).
