# Review: packet #1671 staged for your review before dispatch

**From:** claude
**To:** tigwa
**Date:** 2026-07-22T20:26Z
**Todo:** #1671

Formal build packet staged per your request: docs/TGW-Plan-Vault/plan/packets/1671-hermaroid-cua-bridge-build.md (todo #1671). Covers all 7 of your required items -- session lifecycle scripts (start/stop/crash-cleanup), daemon/socket ownership+ACL spec, client-config documentation (not implementation -- that stays yours), the 5 proof results reproduced against the real persistent scripts with retained command/log evidence, isolation verification, an explicit no-bypass requirement (standard permission mode only, no --dangerously-* flag without separate named authorization), and rollback/no-flake-change confirmation.

One clarification on the bypass item: the agent's own follow-up (already relayed) confirmed the --dangerously-bypass-approvals command was blocked by the permission classifier before it ever reached a1131 -- it never executed. Every one of the 5 fixture proofs ran under default standard permission mode from the start, not a replacement after the fact. I've preserved that raw distinction in the packet rather than treating it as resolved -- I'm not asking you to take my word for it, the packet requires the real build's own run to demonstrate standard-mode-only again independently.

Please review the packet before I dispatch. Not authorizing implementation until you've had a look.
