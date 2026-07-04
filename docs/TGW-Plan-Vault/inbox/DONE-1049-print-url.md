# DONE — #1049 get-ebay-token --print-url (CLI half)

The `--print-url` flag was already fully implemented on the Python CLI side
(`src/tgw/api.py`, `src/tgw/apis/ebay/get_access_token.py generate_auth_url()`)
— found on inspection, not built new. No test coverage existed, so added
`tests/test_get_access_token.py` (5 tests, all passing).

Live-verified: `sudo -u tgw tgw get-ebay-token --print-url` generated a real
eBay OAuth URL (`client_id=DaveBuko-Webkulap-PRD-3ddd92b56-f23b0883`) with no
browser/secrets round-trip. Full suite: 1795 passed, 1 skipped.

The other half of #1049 (fish wrapper auto-xdg-open in `nix/tgw/home.nix`) is
a flake change under PP-NIXOS-001's freeze — left untouched, deferred until
Dave lifts the freeze or explicitly approves it.

Committed d8a961c, pushed to catio-nix-0.0.1-alpha.
