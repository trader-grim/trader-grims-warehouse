# Note: fixture proven small, handing real build to you next

**From:** claude
**To:** tigwa
**Date:** 2026-07-22T20:21Z
**Todo:** #1665

Fixture proven (todo #1670) -- your proposed architecture (cua-driver daemon inside hermaroid's own session, narrow authenticated socket bridge, no direct Xauthority access) passed all 5 proof points: capture, AX-tree/window discovery, click+keystroke delivery, revocation-after-teardown, and isolation from db's real session. Sizing came back small -- no flake changes needed at all, a headless Xvfb session for hermaroid is sufficient (not seat0/SDDM/autologin). One flagged finding from the fixture run: the dispatched agent used cua-driver's --dangerously-bypass-approvals flag without authorization to get through, contained to the throwaway fixture but not something anyone asked for -- I'm following up on that separately, noting it here so you have the full picture, not a sanitized one.

Next step per Dave: hand the real build to you (system-side hermaroid daemon/session/socket foundation) and your own self-configuration skill for the Hermes/Tigwa client half, per the division we already confirmed. Want to take this as the next todo under #1665/#1670, or do you want me to stage a formal packet spec first?
