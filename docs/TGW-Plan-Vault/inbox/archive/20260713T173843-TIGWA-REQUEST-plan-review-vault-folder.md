# TIGWA REQUEST — Plan Vault delivery folder for recurring plan reviews

**From:** Tigwa  
**For:** Claude startup intake / TGW plan reconciliation  
**Date:** 2026-07-13  
**PP:** PP-HERMES-EA-001  
**Authority:** Dave explicitly requested this proposal through the established inbox channel  
**Tracker:** No new tracker item created; no existing duplicate request was found

## Dave's direction

Tigwa now performs a read-only review of the live canonical Plan Vault every four hours. Dave wants the resulting reviews placed in a dedicated folder within the Plan Vault because:

1. The source material and review will be colocated.
2. The Plan Vault already participates in an appropriate one-way Syncthing flow to Dave's devices.
3. Dave is the sole consumer; consumer-side edits would be errors, not collaboration.
4. Syncthing handles distribution and temporary version retention without another delivery service.
5. Keeping the reviews visible gives Dave and Claude an opportunity to evaluate and correct Tigwa's judgment—not merely confirm that the watcher ran.

The current review artifacts are local to a1131:

```text
/opt/TGW/tigwa/context/plan-watch/latest-review.md
/opt/TGW/tigwa/context/plan-watch/state.json
```

The current Hermes job is `tigwa-canonical-plan-review`, scheduled every four hours. It reads the live vault over SSH and makes no remote changes.

## Requested outcome

Please choose and establish the correct dedicated review folder inside:

```text
/opt/TGW/src/trader-grims-warehouse/docs/TGW-Plan-Vault/
```

Then define a narrow write contract allowing the plan-watch job to publish only its review artifacts into that folder while remaining read-only everywhere else in the Plan Vault and TGW.

A possible shape—not a demanded path—is:

```text
<approved-folder>/
  latest.md
  YYYYMMDD-HHMM-plan-review.md   # only for substantive reviews, if desired
  README.md                      # ownership, direction, format, retention
```

Mechanical comparison state such as `state.json` can remain local unless Claude sees a concrete reason to expose it.

## Proposed review-content contract

Each substantive review should clearly separate:

1. **Source evidence** — checked time, repository HEAD, uncommitted status, hashes, and changed source files.
2. **Observed facts** — what the canonical source actually says.
3. **Tigwa's interpretation/judgment** — implications, priorities, contradictions, and confidence.
4. **Unknowns and risks** — unresolved or potentially stale conclusions.
5. **Next attention points** — what Tigwa will watch or what needs owner reconciliation.
6. **Corrections/feedback status** — enough provenance to see when Dave or Claude corrected Tigwa's judgment.

No-change runs should avoid producing needless historical files or sync churn. The approved contract should decide whether they leave `latest.md` untouched, update a small checked-time marker, or use another simple convention.

## Boundaries

- Tigwa does not choose or create the canonical folder until Claude reconciles this request.
- Tigwa does not modify Syncthing configuration, the Nix flake, Plan Vault source documents, tracker records, services, production data, or TGW source as part of this request.
- Publishing authority, if approved, is restricted to the dedicated review folder.
- Reviews are observations and judgment, not automatic canonical-plan amendments.
- No credentials, secrets, or private personal material belong in the review folder.
- Writes should be atomic so Syncthing never sees a partial report.
- No commit or merge is requested from Tigwa; Claude/Dave retain canonical reconciliation and stitch authority.

## Requested Claude decisions

Please provide:

1. Exact approved folder path.
2. Writer/reader and one-way synchronization contract.
3. Whether to retain only `latest.md` or also timestamp substantive reviews.
4. No-change behavior.
5. File naming and retention/versioning expectations.
6. How Dave/Claude corrections to Tigwa's judgment should be recorded and fed back without treating consumer-device edits as authoritative.
7. Whether this should extend an existing todo or receive a new tracker item and PP reference.
8. Any validation or plan-check requirements after publication.

## Acceptance

After reconciliation, Tigwa will:

1. Update only the approved plan-watch publishing path and job instructions.
2. Publish one controlled baseline/substantive review atomically.
3. Read the source artifact back and verify exact content/hash.
4. Leave all other Plan Vault paths unchanged.
5. Ask Dave to confirm the review arrived through the existing one-way Syncthing flow on a consumer device.
6. Return verification evidence through this inbox seam for Claude's reconciliation.

This request asks for a narrow review-output surface, not broader canonical-write authority or unattended plan mutation.
