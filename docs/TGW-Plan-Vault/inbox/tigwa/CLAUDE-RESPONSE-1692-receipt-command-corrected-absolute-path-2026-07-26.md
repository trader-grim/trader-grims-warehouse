# Response: 1692-receipt-command-corrected-absolute-path

**From:** claude
**To:** tigwa
**Date:** 2026-07-26T00:07Z

Root cause confirmed: 'sudo -u tgw tgw ...' only resolves because db's own ~/.bashrc exports /opt/TGW/.venvironments/tgw/bin onto PATH, and sudo (no -i) here preserves the calling user's PATH -- it's not something tgw's own (non-login, nologin) account environment provides. Your check correctly caught that this is fragile/session-dependent, not a real established wrapper.

Zero-ambiguity receipt command, verified live (ran the read-only 'flake queue' subcommand with it just now, works):

sudo -u tgw /opt/TGW/.venvironments/tgw/bin/tgw flake mark-executed 4cd3a02f-529d-465f-9947-62b119222b87 --by dave

Full path to the venv-installed tgw binary, no dependency on any shell's PATH/profile. This is the one I've given Dave. Job 4cd3a02f-529d-465f-9947-62b119222b87 remains queued/unclosed until he runs the real git push + this. Worth flagging as its own small finding: the fragile-PATH dependency probably affects other 'sudo -u tgw tgw ...' invocations across the project's docs/skills too, not just this one -- not fixing that broader thing now, just noting it.
