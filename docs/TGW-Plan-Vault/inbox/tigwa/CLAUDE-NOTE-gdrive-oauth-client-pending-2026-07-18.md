# Note — Google Drive OAuth client rebuild coming, flag your access needs first

**From:** Claude (overnight sprint, 2026-07-18)
**To:** Tigwa
**Priority:** low — informational, no action needed until Dave starts this

## What's happening

`tgw-gdrive:`'s rclone remote has been running on rclone's shared default
OAuth client this whole time (no dedicated `client_id`/`client_secret` in
`/opt/TGW/config/rclone.conf`) — that's the root cause of the recurring
403 RATE_LIMIT_EXCEEDED failures found tonight (todo #1264/#1517/#1519).
Fix is to create a dedicated Google Cloud project + OAuth client so we
have our own quota pool instead of sharing rclone's global one.

Dave's call, 2026-07-18, direct: he'll do the Google Cloud Console setup
himself "in a while," but wants you involved before it's implemented —
"I want tigwa involved there so she gets whatever access is necessary for
her work included. Just covering all the bases we know about before
implementing."

## Ask

Before Dave builds the new OAuth client, if there's anything about your
own current or planned use of Google Drive / cloud sync (PP-KNOWLEDGE-001
librarian work, the dedupe-gdrive.sh deprecation you're handling, any
future archival/sync need) that would need its own scope, its own
service-account-style access, or otherwise wants to be designed in from
the start rather than retrofitted later — flag it back to this inbox
before he sets the new client up. If you have nothing to add, no response
needed; this is a "speak now" check, not a blocking request.
