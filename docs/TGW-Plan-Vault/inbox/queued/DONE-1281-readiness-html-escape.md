Working todo #1281 (PP-COHESION-001, SECURITY track) in isolated worktree
`/opt/TGW/var/worktrees/1281-readiness-html-escape` on branch
`todo/1281-readiness-html-escape`. Task: HTML-escape item-derived
`f.value` in `readiness_html()` (src/tgw/readiness.py) using `html.escape()`,
leaving `f.label` (always a hardcoded literal) untouched. Adding tests per
packet acceptance criteria, then running full offline pytest suite with
PYTHONPATH pinned to this worktree's src/.
