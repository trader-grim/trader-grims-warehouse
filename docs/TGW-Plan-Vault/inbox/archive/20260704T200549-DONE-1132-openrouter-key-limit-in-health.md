# DONE — #1132 OpenRouter key-limit visibility in tgw health

The requeue half of this todo was resolved earlier today (Dave raised
the key limit to $5/day, backlog requeued). This closes the other half:
added `_openrouter_key_limit()` to `src/tgw/health.py`, wired into
`check_quota()` — live `auth/key` lookup (5s timeout, swallows any
network/credential error, never fails the check itself), surfaces
`$X.XX of $Y remaining (daily|weekly)` directly in `tgw health`'s quota
line, and flags `warn=True` when the key is below 10% remaining even if
no 429s have fired yet (catches the exact silent-exhaustion pattern from
today's incident before it becomes a 402 pile-up).

Live-verified: `tgw health` now shows `openrouter key: $4.92 of $5
remaining (daily)` in its real output. 5 new tests
(`tests/test_health_openrouter_key_limit.py`), full suite 1802 passed
(up from 1795).
