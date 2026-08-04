# Response: EBAY-DS-1077 sync gap confirmed, full draft delivered under new name

**From:** claude
**To:** tigwa
**Date:** 2026-07-22T00:42Z
**Todo:** #1077

Correction accepted -- you were right, I was wrong to guess sync-lag without checking. Root cause not yet found, but here's what's now verified:

1. Your hash (994bac97ba6c81950529eb1c6d92c1b64698bf0815dcb4407a95ad8f71a4d12b, 65 lines, mtime 2026-07-19 12:28:57) is the pre-rewrite version, confirmed as the a1131 replica of /home/db/Sync/ebay-dev-support-orphaned-offer-25707-followup.txt. I edited that path in place on tgw-prod across today's session; no .stversions backup exists for it (checked -- versioning isn't enabled/didn't fire for this file), and its a1131 replica evidently never received any of the edits, not even the 2026-07-20 urgency-paragraph one. Gap is at least 2+ days on this one file; scope beyond it unchecked.

2. Per your requested resolution: did not touch the original path further. Delivered the full rewrite under a distinct, new filename instead -- /home/db/Sync/ebay-dev-support-orphaned-offer-25707-followup-full-review-draft.txt. Verified on tgw-prod directly: SHA-256 b7f96b6274a526fefcd8ffea6fa2ffe2468977c6b83a0103cc6ff29614b92fde, 239 lines, 13780 bytes.

3. Could NOT independently verify that new filename on a1131 -- my SSH to claude@192.168.60.101 failed (publickey rejected) this session, root cause not investigated. So: I'm not claiming this reached a1131 yet. Please check from your end and confirm hash/line-count match before treating it as delivered there.

4. Opened todo #1632 (PP-DATAINTEGRITY-001) to actually diagnose the tgw-prod<->a1131 Syncthing propagation gap -- this is a real, unresolved infra issue independent of the eBay ticket, not closed by delivering a second copy under a new name.

5. Register (EXTERNAL-SUPPORT-TICKET-REGISTER.md, EBAY-DS-1077) updated with both hashes, both paths, the gap, and the open todo -- not just the happy-path version.

Receipt: source found = yes (tgw-prod local edit, verified by direct read+hash both before and after your correction). Final tgw-prod artifact = ...full-review-draft.txt, hash above. Evidence retained = original short-filename path left untouched at its current (rewritten) tgw-prod state, a1131's stale copy not touched or overwritten by me. Still unverified = a1131 state of the new filename, and the actual root cause of the propagation gap (todo #1632, open). Still an unsent Dave-review draft either way.
