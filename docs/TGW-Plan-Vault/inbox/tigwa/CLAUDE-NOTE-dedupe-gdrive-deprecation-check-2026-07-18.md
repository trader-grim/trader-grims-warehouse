# Note — check use of bin/dedupe-gdrive.sh, then close/deprecate it yourself

**From:** Claude (session 2026-07-18, overnight sprint)
**To:** Tigwa
**Priority:** low — quick check, not urgent

## What we found

`bin/dedupe-gdrive.sh` (Google Drive folder dedupe via rclone) targets a
remote named `dbukove:` that no longer exists in
`/opt/TGW/config/rclone.conf` (only `tgw-gdrive:` is defined now). It's
currently broken/stale — found while investigating a separate cloud-sync
403 issue tonight (todo #1264/#1517/#1519, all closed).

Dave's call (2026-07-18, direct): "dedupe is done and will be handled by
librarian going forward, we can deprecate that script if tigwa is not
using it. quick check note and let her close it out and deprecate it so
she can archive and document our deprecations incase we wonder why or
want to resurrect."

## Ask

1. Quick check: is anything in your librarian/knowledgebase workflow
   (PP-KNOWLEDGE-001) currently invoking `bin/dedupe-gdrive.sh`, or
   depending on it running? (Best guess is no — the remote's been broken
   for a while and nothing appears to reference it outside your own
   `PP-KNOWLEDGE-001` planning docs — but confirm rather than assume.)
2. If not in use: deprecate the script per your own stewardship pattern —
   archive it, document why (superseded by the librarian/dedupe workflow
   under PP-KNOWLEDGE-001), and note it somewhere findable in case anyone
   later wonders why it's gone or wants to resurrect it. This is
   explicitly your filing-authority call to make (per the 2026-07-16
   filing-authority reinforcement), not something I should do for you.
3. If it turns out something *does* still depend on it, flag that back
   and we'll figure out the remote-name fix together instead of
   deprecating.

No action needed from me on this — over to you. (Separately, and not your
concern: `bin/tgw-secrets-backup` had the *same* stale `dbukove:` remote
reference, but that one's a live, in-use monthly backup timer, so I'm
fixing that one directly rather than deprecating it.)
