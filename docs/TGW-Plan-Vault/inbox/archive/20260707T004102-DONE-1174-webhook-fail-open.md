# DONE todo #1174 — eBay webhook signature fail-open security fix

## What shipped

`src/tgw/apis/ebay/notifications.py::verify_notification_signature` was the
sole gate on the public unauthenticated `/webhooks/ebay/notification` endpoint
(`http_server.py:9162`) and failed OPEN (returned True) in three branches:
no SOAP header, no/empty signature element, and any XML parse exception.
An attacker who knew any listing_id could POST an unsigned forged
FixedPriceTransaction notification and have it accepted, corrupting
inventory (mark sold / decrement qty) with zero auth.

Flipped all three branches to fail CLOSED (return False, log a warning).
Verified live that dev_id/app_id/cert_id are all configured in
`/opt/TGW/secrets/ebay-credentials.json` — so every genuine eBay
notification carries a verifiable signature and there is no legitimate
unsigned/no-header case being broken by this change. Updated the module
docstring and the function docstring to state the fail-closed contract.

## Tests changed

`tests/test_sold_recon.py`: the two tests that asserted the vulnerable
fail-open behavior as intentional (`test_verify_signature_accepts_when_no_header`,
`test_verify_signature_accepts_when_no_signature`) were flipped to assert
rejection. Added `test_verify_signature_rejects_on_unparseable_body` for the
parse-exception path (previously untested). All 21 tests in the file pass.

## Live evidence

- `python -m pytest -q tests/test_sold_recon.py` → 21 passed.
- Full offline suite: `python -m pytest -q` → 1836 passed, 1 skipped, 9 failed.
  All 9 failures reproduce identically on `git stash` (pre-existing, unrelated
  to this change): 7 in `tests/test_model_routing.py` (openrouter vs
  google_direct — stale test expectations from the s45 provider-flip back to
  OpenRouter primary) and 2 in `tests/test_invariants_pricing.py`
  (`ebay_price.py:124` NoneType). Confirmed out of scope for #1174; not
  touched.

## Deviations

None. Implemented exactly as specified in the packet brief — fail closed on
all three unverifiable cases, no config/secrets/scope changes.

## Out-of-scope finds (not fixed, flagging for separate todos)

- `tests/test_model_routing.py` (7 failures) and
  `tests/test_invariants_pricing.py` (2 failures) are pre-existing broken
  tests on this branch, unrelated to notifications.py. Worth a follow-up
  todo to either fix the tests (openrouter is the current provider per
  memory `project-google-direct-migration.md`) or the `ebay_price.py:124`
  NoneType bug — should not stay red on `pytest -q`.
