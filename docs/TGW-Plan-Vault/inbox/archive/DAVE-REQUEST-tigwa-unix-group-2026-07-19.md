# Dave request — provision/use a `tigwa` Unix group on tgw-prod

**From:** Dave, relayed by Tigwa
**To:** Claude
**Date:** 2026-07-19
**Scope:** tgw-prod Unix group membership only

Dave requests that you check whether a Unix group named `tigwa` already exists on tgw-prod.

- If it exists, add Dave's tgw-prod operator account (`db`) to it.
- If it does not exist, create the `tigwa` group, then add `db` to it.

Please keep this narrowly scoped: do not change sudoers, service ownership, file ownership/modes, SSH credentials, Hermes profiles, gateways, secrets, or any other account/group memberships.

Return verified evidence in your response: whether the group pre-existed or was created; the resulting `getent group tigwa` entry; and `id db` showing the effective membership. State any non-default decision or blocker. New login/session group membership may require Dave to begin a new session before it is effective.
