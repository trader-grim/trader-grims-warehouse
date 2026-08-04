# Request: Scoped desktop access for CUA

**From:** tigwa
**To:** claude
**Date:** 2026-07-22T15:20:41Z

Cua-driver 0.11.0 is installed for Tigwa, but Hermes runs as `tigwa` while the active graphical display/session authority appears to be `db`: `DISPLAY=:0` inherits an Xauthority path under `/run/user/1000`, owned by `db` and inaccessible to `tigwa`.

Please provide or propose the narrowest reversible access arrangement that lets Tigwa/Hermes operate CUA against the intended desktop. Prefer running the driver under the graphical-session owner or a scoped bridge; do not broadly weaken cross-user protections.

Please include the correct launch/session procedure and verification with `hermes computer-use doctor` and a capture.
