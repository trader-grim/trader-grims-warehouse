# In progress: todo #1565 — tgw-clipd secret exclusion

Working in worktree `/opt/TGW/var/worktrees/1565-clip-secret-exclusion` on
branch `todo/1565-clip-secret-exclusion`. Task: exclude password-manager-hinted
(x-kde-passwordManagerHint) and API-key/secret-shaped content from
`tgw-clipd`'s persistent clip history. Adding a MIME-type check in the
Wayland backend and a content-based entropy/prefix heuristic in
`process_change()`. Coordinating around todo #1563 (clip delivery, also
touching clipd.py/clip.py) — checking git state before editing those files.
