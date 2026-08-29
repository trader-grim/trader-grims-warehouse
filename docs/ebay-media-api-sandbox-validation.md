# eBay Commerce Media API Sandbox validation (Todo 1917 candidate)

This is an operator-run validation procedure. It performs Sandbox writes only;
the implementation Todo does not run it and does not authorize Production.

## Contract pinned by the candidate

- Media API `v1_beta`, image resource added in `1.4.0-beta` (2025-04-17).
- `POST /commerce/media/v1_beta/image/create_image_from_file` with one
  multipart `image` part; successful metadata includes `imageUrl` and
  `expirationDate`, while the image identifier is captured from `Location`.
- `POST /commerce/media/v1_beta/image/create_image_from_url` is a separate
  adapter and is denied unless its HTTPS origin is listed in
  `ebay_media_controlled_https_origins`. Google Drive share URLs are not valid
  TGW-controlled object endpoints.
- Supported inputs: JPG/JPEG, GIF, PNG, BMP, TIFF, AVIF, HEIC, WEBP. Current
  documented examples report 12 MiB and width+height below 15,000 pixels;
  provider errors 190201-190204 remain authoritative.

Primary sources:

- <https://developer.ebay.com/api-docs/commerce/media/static/release-notes.html>
- <https://developer.ebay.com/api-docs/sell/static/inventory/managing-image-media.html>
- <https://developer.ebay.com/develop/get-started/api-deprecation-status>

## Offline candidate evidence

Run from the exact candidate tree:

```sh
/opt/TGW/.venvs/controller/bin/pytest -q -p no:cacheprovider \
  tests/test_ebay_media_upload.py \
  tests/test_ebay_upload_dimension_limit.py \
  tests/test_ebay_upload_provider_effect.py \
  tests/test_ebay_upload_integrity.py \
  tests/test_ebay_upload_xml_escape.py
```

The contract suite must prove preparation precedes reservation and dispatch,
the original remains byte-identical, derived resize is in memory, order is
stable, retries reuse succeeded ledger results, unresolved/ambiguous effects
do not dispatch again, 429/transient/rejection outcomes remain distinct, URL
origins fail closed, response metadata/receipts persist, and the legacy API
name, XML builder, and endpoint are absent.

## Operator-controlled Sandbox procedure

1. Create a dedicated eBay Sandbox seller and user OAuth token with the scopes
   shown by eBay for the Media API. Use a task-local token file and config with
   `ebay_environment: sandbox`; do not replace Production configuration.
2. Copy one valid fixture of every supported format plus fixtures for corrupt,
   unsupported, oversize, and over-dimension media into an isolated temporary
   SKU directory. Record SHA-256 hashes before the run.
3. Invoke `prepare_upload` for each file and verify source hashes still match.
   Record prepared hashes and dimensions. Do not use operational queue rows.
4. Invoke `upload_prepared` once per valid fixture. Assert the host is
   `api.sandbox.ebay.com`, status is 2xx, `Location` yields an image ID, and
   `imageUrl`/`expirationDate` are present in the exact receipt.
5. Call read-only `get_image` with every returned ID and compare its EPS URL and
   expiry to the create response. Preserve redacted receipts (never OAuth
   bearer values) with the exact candidate commit/tree and fixture hashes.
6. Repeat one already-succeeded queue-worker fixture against an isolated effect
   ledger and prove no second POST occurs. Simulate a lost response and prove
   the effect becomes ambiguous/reconciliation-required and cannot blind retry.
7. For the URL path, expose a fixture through a temporary TGW-controlled HTTPS
   object origin, explicitly allowlist that exact origin, validate once, then
   remove the temporary object. Prove HTTP, credentials-in-URL, Drive sharing,
   and non-allowlisted HTTPS URLs fail before dispatch.
8. Exercise Sandbox rate limiting only through a mocked/controlled response;
   do not intentionally exhaust an account quota. Confirm 429 is classified as
   quota, 5xx/timeout as transient or ambiguous, and 4xx media errors as definite
   rejection.

Sandbox success is compatibility evidence, not Production cutover, deployment,
Todo/PP/Plan completion, or operator acceptance. Production requires a separate
exact reviewed candidate and operator-controlled effect before 2026-09-30.
