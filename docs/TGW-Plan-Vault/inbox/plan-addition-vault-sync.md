# Plan Addition — Obsidian Vault Sync via Syncthing + tgw Conflict Resolution

### For the Opus Planning Session — May 2026

---

## Why This Matters

The plan will live in Obsidian and be edited from multiple devices (laptop, production machine, possibly others). The plan document is the source of truth for project state. Conflicts must be detected, visible, and resolved deliberately — never silently auto-merged or lost.

---

## Decision: Syncthing for Vault Sync

Use Syncthing (already installed and proven) to keep the Obsidian vault in sync across all devices. Do **not** use Obsidian Sync (paid service).

**Rationale:**

- Syncthing is already running reliably on your hardware for photo sync. Zero setup friction, zero subscription cost.
- Markdown plans are plain text. Syncthing handles them perfectly.
- Conflict handling is transparent: when two devices edit the same file, Syncthing creates both versions side-by-side (`.sync-conflict` files). You see the conflict and choose which version wins — critical for a plan document where silent auto-merge is dangerous.
- No cloud service dependency, no vendor lock-in.
- Optional: git-backing the vault provides version history if needed.

**Setup:**

1. Configure Syncthing to sync your Obsidian vault folder (or the `plans/` subdirectory) across all devices.
2. Verify sync is active and working before editing any plan files from multiple devices.
3. Do not enable Obsidian Sync. Ignore the "Sync" menu option in Obsidian — it's for their paid service.

---

## tgw-Specific Conflict Resolution Protocol

When a `.sync-conflict` file appears (two devices edited the same note simultaneously):

1. **Detect:** check Syncthing's recent changes log or Obsidian's file explorer for `.sync-conflict` files.
2. **Examine both versions.** Open the original file and the conflict file side-by-side. Understand what changed on each device.
3. **Decide the canon.** Which version is correct? Did one device have stale state? Did both devices make legitimate but incompatible changes?
4. **Merge by hand.** Edit the original file to be the true state (combining changes from both if needed), then delete the conflict file.
5. **Commit the resolution.** If the vault is git-backed, stage and commit: `git add <file> && git commit -m "Resolved conflict on <file>: [brief reason]"`. This creates an audit trail.
6. **Verify sync.** Check Syncthing to confirm the conflict file is gone and the vault is in sync across all devices.

**Key rule:** No automatic resolution. If two devices disagree, a human reads both versions and decides. This is slow but safe — the plan is not a log file, it's a specification.

---

## Optional: Git Backing for Version History

If you want a fallback version history (restore a plan document to an earlier state, audit who changed what), git-back the vault:

```bash
cd /path/to/obsidian/vault
git init
git add .
git commit -m "Initial vault state"
```

Then, after conflict resolution (step 5 above) or any major plan update:

```bash
git add .
git commit -m "Plan update: [brief summary of changes]"
```

This gives you:
- `git log` to see the history of plan changes.
- `git show <commit>` to see what changed in a specific update.
- `git diff <commit1> <commit2>` to compare plan states.
- `git revert` to undo a change if needed.

Git is optional — Syncthing alone is sufficient for sync. But if the plan is critical to your workflow, git backing is cheap insurance.

---

## Constraints Carried Forward (New)

- All plan documents are edited only in Obsidian, never directly in the filesystem. Syncthing syncs the vault; the files themselves are managed by Obsidian.
- Conflicts must be resolved by hand before proceeding with further plan edits. Never ignore a `.sync-conflict` file.
- If git-backed, commit a summary after resolving conflicts or making major plan changes. The commit message is the audit trail for plan evolution.
- No Obsidian Sync subscription. All sync is via Syncthing.

