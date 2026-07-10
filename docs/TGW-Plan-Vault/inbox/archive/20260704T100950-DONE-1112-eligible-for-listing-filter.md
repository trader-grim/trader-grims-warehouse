# DONE — todo #1112: 'Eligible for listing' inventory filter

Feature was already fully implemented (found while checking the queue):
backend SQL filter (`status_filter=__eligible__` in `http_server.py`'s
`list_items`) and the frontend "Eligible" chip button in the browse page
were both wired, matching Dave's spec exactly (status new/In Stock, NOT
currently on eBay — no Active listing, no PUBLISHED offer; ended listings
qualify since they're relistable). The one real gap was zero test
coverage — added `test_eligible_filter_status_and_ebay_state` in
`tests/test_http_server.py`, covering all 6 branches (never-listed new item,
Active-listed excluded, PUBLISHED-offer excluded, ended-listing included,
sold excluded by status, Staged excluded by status).

Live-verified against the real production catalog: **2,104 items currently
eligible for listing** out of 15,193 new/In Stock items. Full suite: 1776
pass / 1 skipped / 0 fail / 0 errors (was 1775).
