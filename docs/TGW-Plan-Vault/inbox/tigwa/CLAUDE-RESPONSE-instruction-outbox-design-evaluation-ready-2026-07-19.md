# Response: Instruction outbox design evaluation ready

**From:** claude
**To:** tigwa
**Date:** 2026-07-19T04:18Z

Design evaluation for the agent-instruction-outbox concept is done: docs/TGW-Plan-Vault/plan/pp/PP-OUTBOX-001.md. Recommends reusing the existing mailbox as the delivery channel (no new send infra), a thin new instruction_cards Postgres table for pre-send staging only (no pipeline/work authority), and piloting the workflow with a zero-code v0 (a scratch doc + manual Tigwa review/send) before building any UI or table. Five open decisions listed at the end for Dave's call before any build packet. No implementation done — design only, per the concept note's own scope.
