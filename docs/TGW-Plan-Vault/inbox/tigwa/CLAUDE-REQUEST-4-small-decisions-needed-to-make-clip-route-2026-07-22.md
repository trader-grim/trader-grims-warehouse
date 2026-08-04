# Request: 4 small decisions needed to make clip-route dispatch-ready

**From:** claude
**To:** tigwa
**Date:** 2026-07-22T07:07Z

PP-EVENTD-001/clip-route is fully designed (reference/PP-EVENTD-001-design.md, complete 2026-06-29) and essentially dispatch-ready -- 4 small open decisions are the only thing left before Phase 1 can be packeted. Requesting these for Dave's call: 1) Google Drive account for the git-annex data plane -- same account as ItemData photo sync, or a separate vault account? 2) Go module path -- github.com/DaveBuko/clip-route (public-style path) or kept internal to the tgw repo (e.g. under src/tgw or a subpath of the existing module)? 3) Unix socket path -- confirm /run/user/<uid>/clip-route.sock (XDG standard) is fine, or a different location? 4) Annex key scope in events -- include the annex_key in every clipboard event sent to all subscribers, or only include it when the payload is actually annex-backed (large payload)? Once these are answered we'll write the Phase 1 packet (Go clip-route binary: Unix socket IPC, Postgres ingest, KDE+Android HTTP delivery) plus fold in two known small bugs found in the same doc: CurrentLocation symlink regression (dropped during the PP-CONTEXT-001 rebuild, Dave wants it back) and tgwset_selected() still calling a nonexistent tgw tgwset subcommand.
