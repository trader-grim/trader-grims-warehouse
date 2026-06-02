# Operations — Vault Sync (Syncthing)

The Obsidian vault is synced across devices via Syncthing. No Obsidian Sync subscription.

## Setup (already live)
- Syncthing syncs the vault folder across all devices (laptop, production machine, others).
- The `.stfolder` marker file in the vault root is a Syncthing artefact — ignore it.
- Do not enable Obsidian Sync. The "Sync" menu option in Obsidian is the paid service.

## Conflict resolution protocol

When two devices edit the same file simultaneously, Syncthing creates a `.sync-conflict` file alongside the original. This is intentional — never ignore it.

1. **Detect**: check Syncthing's recent changes log or the file explorer for `.sync-conflict` files.
2. **Examine both**: open the original and the conflict file side-by-side.
3. **Decide the canon**: which version is correct? Did one device have stale state? Did both make legitimate but incompatible changes?
4. **Merge by hand**: edit the original to be the true state (combine both if needed), then delete the conflict file.
5. **Verify sync**: confirm Syncthing shows no remaining conflicts and all devices are in sync.

**Rule**: no automatic resolution. If two devices disagree, a human reads both and decides.
The plan is a specification, not a log — silent auto-merge is not acceptable.

## Optional git backing

If you want a fallback version history for the vault:

```bash
cd /path/to/obsidian/vault
git init
git add .
git commit -m "Initial vault state"
```

After conflict resolution or any major plan update:

```bash
git add .
git commit -m "Plan update: <brief summary>"
```

This gives `git log`, `git diff`, and `git revert` for plan history.
Git backing is optional — Syncthing alone is sufficient for sync.
