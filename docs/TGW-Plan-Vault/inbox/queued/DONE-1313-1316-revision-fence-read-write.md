# In progress: todo #1313, #1316 — revision.py fence read/write fix

Working in isolated worktree `/opt/TGW/var/worktrees/1313-1316-revision-fence-read-write`
on branch `todo/1313-1316-revision-fence-read-write`, base = `catio-nix-0.0.1-alpha`
(live-verified). Fixing `cmd_revise` and `cmd_revise_apply` in `src/tgw/revision.py`:
both bypass the fence on read (raw `json.loads(path.read_text())` instead of
`resolver.load_item_doc()` + `find_current_sku()` fallback) and on write
(`atomic_write_json()` missing `archive_root=` kwarg, so E5 archive-before-overwrite
never fires). Per packet `docs/TGW-Plan-Vault/plan/packets/1313-1316-revision-fence-read-write.md`.
Not yet committed as of writing this note.
