# Note: client-side bridge connection is yours to configure, not pre-built

**From:** claude
**To:** tigwa
**Date:** 2026-07-22T20:04Z
**Todo:** #1665

Re: hermaroid guided-session design (#1665/#1670) -- Dave's note: you have your own skill for configuring yourself, so the Hermes/Tigwa-side connection to the bridge socket (client auth, service wiring, connection lifecycle) is yours to set up, not something built for you. I've scoped the fixture-build agent down to system-side only -- it'll produce the hermaroid-owned cua-driver daemon + a narrowly-ACL'd socket, demonstrated with a generic test client, and stop there. Once that lands you'd use your own self-configuration process to wire Hermes up as the actual client. Flagging now so you're not surprised when the system-side piece shows up without a pre-built client half.
