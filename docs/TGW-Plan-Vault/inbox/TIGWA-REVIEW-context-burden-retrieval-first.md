# Review request — retrieval-first response to Claude’s context burden

**Artifact:** `reference/TGW-Context-Burden-Retrieval-First-Review-2026-07-15.md`  
**Tracker:** Tigwa #1439 / PP-KNOWLEDGE-001

## Purpose

Review the proposed safe path for reducing repeated Master Plan context loading without creating a second plan authority or losing historical context.

## Evidence

- Confirmed `CLAUDE.md` Step 2 currently cats the full Master Plan.
- Verified current Master Plan size: 1,759 lines / 114,601 bytes; 65 level-2 sections.
- Verified focused Recoll CLI behavior: `recollq` works headlessly; `recoll -q` does not.
- Confirmed no Recoll timer/unit was discovered and the index reports no monitor.
- No source files, startup rules, workers, or index configuration were changed during this review.

## Questions

1. Approve the canonical-plan + deterministic structural-index approach rather than a free-standing summary?
2. What belongs in always-loaded common context: settled architecture/gates only, or a current-state snapshot too?
3. Preferred first interface: `tgw plan brief --pp` or generated file-based context packets?
4. Should ambiguous/no-PP work hard-stop retrieval and require a full-plan read, or merely warn?
5. Is Recoll indexing cadence an immediate approved task or a later infrastructure decision?
