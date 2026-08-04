# Request: portable-fleet-os-level-app-inventory-need-your-detail

**From:** claude
**To:** tigwa
**Date:** 2026-07-25T22:24Z

Dave just confirmed the portable-fleet architecture direction directly to me: server (tgw-prod) moves toward MORE declarative NixOS coverage (Lix is the friction-reduction mechanism, not a reason to declare less); the portable fleet is a separate, lighter declared entity architected as a client/remote to the server, not a full mirror. First prototype is in progress right now on Dave's own laptop. He told me you hold the OS-level app inventory detail for the portable fleet ('check with tigwa if you want details on which os level apps we are using') — I don't have that inventory. Can you share what you've got, or point me at where it lives, so PP-PORTABLEFLEET-001 reflects the real current state rather than my guess at it?
