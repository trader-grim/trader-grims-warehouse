# Review request — supervised PM-intake / librarian workflow v1

**From:** Tigwa
**To:** Claude
**Linked todos:** #1434, #1479
**Programs:** PP-HERMES-EA-001, PP-KNOWLEDGE-001, PP-ANNEX-001
**Status:** design/sinkhole review only; no implementation authorization

Dave directed Tigwa to give the currently inactive, practically useless `pm_intake` process a complete supervised-librarian redesign.

Canonical staged proposal:
`dev-workflow/research/PROPOSAL-pm-intake-librarian-workflow-v1-2026-07-22.md`

Please independently review the actual proposal and current worker/topology evidence. Focus on:

1. authority leakage: can an agent/model accidentally file, mutate, create tasks, or imply Dave acceptance?
2. provenance/recovery: raw versus derived records, receipts, supersession, and rollback/rebuild gaps;
3. whether PostgreSQL/state-machine is used correctly instead of creating a competing opaque file queue;
4. model-spend control and the risk of turning retained historical artifacts into an automatic model workload;
5. whether the small first pilot and synthetic fixture are sufficient to expose failure modes before a real promotion;
6. missing constraints or a smaller safe first implementation packet.

Return concrete gaps, contrary evidence, and revised acceptance criteria—not generic approval. Do not activate `pm_intake`, run `admin-file`, replay dead letters, move/archive files, modify plans/tasks, change config/services, or spend provider funds as part of this review.
