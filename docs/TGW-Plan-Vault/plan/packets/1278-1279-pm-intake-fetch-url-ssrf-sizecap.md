# Packet: pm_intake.fetch_url() blocks SSRF targets and caps response size
Todo: #1278, #1279   PP: PP-COHESION-001   Track: SECURITY (graduated to concurrent — run alongside #1281/#1283)

**Combined deliberately**: both todos target the same ~15-line function
(`fetch_url()` in `src/tgw/workers/pm_intake.py`) and would conflict as
separate branches. One packet, one branch, both fixes.

## Context budget (ALL the model may load)
This packet + `src/tgw/workers/pm_intake.py` lines 1-260 (`fetch_url()`
and its two helpers `_html_to_text`/`_extract_title`, plus the one caller
at line 487 and the `_URL_FETCH_MAX_CHARS` constant at line 125 — read
these, do not change them) + this todo's existing test file if one
exists. Nothing else. Do not read or touch the rest of pm_intake.py (note
extraction, LLM classification, plan-doc filing — unrelated to this fix).

## Verified live before this packet was written
- `fetch_url()` (line 187) has exactly one caller in this file (line 487),
  inside the note-processing flow: `fetch = fetch_url(url)` where `url` is
  extracted from the text of an inbox note. Whether that note's author is
  trusted or not, the fix here is defense-in-depth — the household network
  has real internal admin surfaces (Syncthing GUIs, NFS exports, this
  repo's own `http_server.py`) a malformed/malicious URL could probe or
  hit accidentally.
- `httpx.Client(follow_redirects=True, timeout=timeout_s)` (line 211)
  makes the request with zero host validation and zero response-size cap.
  `resp.text` (the full decoded body) is fully buffered in memory by
  httpx before any truncation happens — the only size limit anywhere is
  `_URL_FETCH_MAX_CHARS = 32000` (line 125), applied to the *derived text*
  at the caller (`fetch_result.get('text', '')[:_URL_FETCH_MAX_CHARS]`,
  line 263), well after the full raw body is already in memory.
- `pm_intake` the *worker* is currently stopped (CLAUDE.md Current Phase,
  2026-07-12 direction) — this fix applies regardless: the module and this
  code path still exist, are still directly callable, and this is the
  kind of vulnerability class that should not wait for reactivation to
  fix (same reasoning that applied to #1276/#1277).
- httpx's `event_hooks={'request': [...]}` on a `Client` fires for the
  initial request AND for every request `follow_redirects=True` issues
  internally for each redirect hop (confirmed via httpx's documented
  behavior: event hooks operate at the transport-send level, which every
  redirect re-enters) — this is the mechanism that makes redirect-based
  SSRF preventable without disabling redirects outright. Acceptance step 3
  below is what actually proves this holds for our httpx version, not
  just documentation — treat a failure there as a real finding, not
  something to route around by disabling redirects instead (that would be
  an unstated behavior change to a function other code may depend on).

## Spec

### #1278 — SSRF: block requests to private/loopback/link-local/reserved targets
Add near the top of `fetch_url()` (or as a module-level helper used by
it), using only stdlib (`socket`, `ipaddress`):

```python
import ipaddress
import socket
from urllib.parse import urlparse


def _resolve_is_safe(hostname: str) -> bool:
    """False if *hostname* resolves to any private/loopback/link-local/
    reserved/multicast address — blocks SSRF to internal network targets."""
    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror:
        return False  # can't resolve → can't safely proceed
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast):
            return False
    return True
```

In `fetch_url()`, before creating the `httpx.Client`: parse the hostname
from `url` (`urlparse(url).hostname`), call `_resolve_is_safe()`. If it
returns `False` (or the URL has no hostname / isn't http/https), return
`{'ok': False, 'url': url, 'error': 'blocked: url targets a private/internal address'}`
immediately — no request made.

Then wire the SAME check into an `event_hooks={'request': [<hook>]}`
passed to `httpx.Client(...)`, so every redirect hop is re-checked before
it's sent (not just the original URL) — a redirect from a public URL to
`http://169.254.169.254/...` or `http://127.0.0.1:...` must be caught
mid-chain, not just at the entry point. The hook receives an
`httpx.Request`; use `request.url.host` the same way as the pre-flight
check. If a redirect target fails the check, raise inside the hook (any
exception there aborts the request/redirect chain) and catch it in
`fetch_url()`'s existing `except httpx.RequestError` (or add a narrow
except for whatever exception type the raise produces) to return the same
`{'ok': False, 'error': 'blocked: ...'}` shape.

### #1279 — cap response body size before buffering
Replace the buffering `client.get(url, ...)` call with the streaming API
so the response body is read incrementally and can be aborted before a
huge body is fully buffered:

```python
_MAX_RESPONSE_BYTES = 5_000_000  # 5MB — generous for HTML/text pages, well above _URL_FETCH_MAX_CHARS's needs
```

Use `client.stream('GET', url, headers=...)` as a context manager; check
`resp.headers.get('content-length')` first and reject early
(`{'ok': False, 'error': 'response too large (...)'}`) if it's present
and exceeds `_MAX_RESPONSE_BYTES` (a malicious server can lie about this
header, so it's a fast-path optimization, not the real guard). Then
iterate `resp.iter_bytes()`, accumulating into a `bytearray`, and abort
(return the same too-large error) the moment accumulated size exceeds
`_MAX_RESPONSE_BYTES`, without reading further. Decode the accumulated
bytes the same way `resp.text` would (respect the response's detected
encoding — `resp.encoding` is available on a streamed response same as a
buffered one) before passing to the existing `_html_to_text`/`.strip()`
logic. Everything downstream of getting the decoded text stays unchanged.

## Dataset
None — this only changes how `fetch_url()` makes its outbound request; no
ItemData/queue/local storage is touched by this fix itself.

## Out of scope
- Any change to `_URL_FETCH_MAX_CHARS`, note-extraction, LLM
  classification, or anything else in `pm_intake.py` outside `fetch_url()`
  and its immediate helpers.
- Reactivating the `pm_intake` worker — unrelated, not this packet's call.
- IPv6-specific SSRF nuances beyond what `ipaddress.ip_address` already
  classifies (e.g. IPv4-mapped IPv6 addresses) — if the acceptance tests
  reveal a gap there, report it as a finding, don't scope-creep the fix to
  cover it without a fresh spec.

## Acceptance (live)
1. `fetch_url('http://127.0.0.1/')` → `{'ok': False, 'error': 'blocked: ...'}`,
   no request sent (verify via a monkeypatched/mocked transport that no
   network call occurred, not just the return value).
2. `fetch_url('http://169.254.169.254/')` (link-local/cloud-metadata
   range) → same blocked result.
3. **Redirect case** — stand up a local test HTTP server (or use
   `httpx`'s `MockTransport`/`respx` if already a test dependency, else a
   minimal local server bound to `127.0.0.1` on an ephemeral port) that
   responds to a public-looking request with a 302 redirect to
   `http://127.0.0.1:<other-port>/`; confirm `fetch_url()` blocks it
   mid-redirect rather than following it. This is the test that actually
   proves the event-hook mechanism works for this httpx version — do not
   skip it or replace it with a weaker assertion.
4. A real public URL (e.g. an httpbin-style local mock, or a `respx`/mock
   transport returning normal HTML) still returns `{'ok': True, ...}`
   with correctly extracted text — no regression to the common case.
5. A mock response advertising `Content-Length` over 5MB → blocked before
   any body bytes are read (assert the mock's body iterator was never
   fully consumed).
6. A mock response with NO `Content-Length` header that streams more than
   5MB of body bytes → still aborted once the accumulated size crosses
   the cap (proves the fast-path Content-Length check isn't the only
   guard).
7. Run the full offline suite — zero regressions.

## Quota/risk
None — no real outbound network calls in the test suite (use local
mock/test servers or a mock transport); this is a pure defensive-fix
packet for a currently-stopped worker's code path.
