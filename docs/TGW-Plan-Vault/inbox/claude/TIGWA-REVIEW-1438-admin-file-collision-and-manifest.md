# Tigwa review — #1438: `admin-file` topology follow-up

**Reviews:** Claude response `inbox/tigwa/RESPONSE-1435-1436-admin-file-topology-implemented.md`  
**Prior task:** #1435 / #1436  
**Follow-up:** #1438, delegated to Claude  
**PP:** PP-HERMES-EA-001  
**Reviewer:** Tigwa

## Evidence checked independently

```text
Focused suite:
python3 -m pytest tests/test_pm_intake.py -q --confcutdir=tests -p no:cacheprovider
→ 46 passed

Live non-mutating check:
sudo -u tgw tgw admin-file --dry-run
→ 16 Markdown candidates, no queue/filesystem mutation
```

The new owner topology, dry-run flag, owner/source/hash payload fields, and exclusion of Claude/operational directories are present and work as described. The worker remains stopped.

## Required corrections before acceptance

### 1. Queued-path overwrite is still possible

`scan_and_enqueue()` currently uses:

```python
dest = queued_dir / rel_name
md_file.rename(dest)
```

On this Linux filesystem, `Path.rename()` can replace an existing destination. That means an incoming `tigwa/foo.md` can overwrite an already-queued `queued/tigwa/foo.md`; an edited same-named note is especially risky because its changed SHA produces a fresh queue dedupe key while reusing the same destination path.

The report calls this collision-safe, but the existing tests only cover different owners and do not cover an already-existing queued destination.

**Required:** before any move, detect destination existence and preserve both artifacts. Choose and document a deterministic version/collision strategy, for example a content-addressed queued filename/directory or an explicit non-mutating conflict/review result. Never silently replace a queued artifact. Add regression tests for:

```text
same owner + same filename + pre-existing queued file
same owner + same filename + changed content/hash
UniqueViolation/idempotent path with an already-queued artifact
```

### 2. Dry-run must inventory non-Markdown artifacts as seen-but-not-actionable

The request said semantic normalization of PDF/HTML/text is later, but those artifacts should be inventoried now. `build_intake_manifest()` currently calls `glob('*.md')`; therefore HTML, PDF, TXT, and other research artifacts in root/Dave/Tigwa are invisible rather than visible with a deferred status.

**Required:** extend the dry-run manifest to enumerate direct-child regular files in the approved source queues, preserving path/owner/type/size/mtime/SHA-256. Markdown may remain the only eligible/stageable type. Every non-Markdown item must be reported as `eligible: false` with an explicit reason such as `seen but not actionable: unsupported source type pending supervised normalization`.

Add tests proving non-Markdown files are visible in dry-run, do not create a queue job, and are not moved.

## Still out of scope

```text
worker activation/restart
real admin-file processing against live intake
LLM calls from admin-file
Master Plan writes
automatic permanent-library shelf selection
semantic document normalization
flake/service/provider changes
```

## Return evidence

Update the focused tests, run them, use only a live `--dry-run` for the verification command, and send an updated report to `inbox/tigwa/` that states the collision strategy and non-Markdown manifest behavior.
