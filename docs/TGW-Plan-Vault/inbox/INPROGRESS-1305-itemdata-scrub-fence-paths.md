# In progress: todo #1305 — itemdata_scrub.py fence path/read fix

Working in the harness-provided isolated worktree
`/opt/TGW/src/trader-grims-warehouse/.claude/worktrees/agent-adb8e7a854ca5a84f`
on branch `worktree-agent-adb8e7a854ca5a84f` (this sandbox provisions its
own worktree+branch per session rather than the manual `git worktree add`
step in the tgw-coder contract — functionally equivalent isolation, noted
as a deviation from the literal contract steps).

Fixing `src/tgw/workers/itemdata_scrub.py` per packet
`docs/TGW-Plan-Vault/plan/packets/1305-itemdata-scrub-fence-paths.md`
(self-authored — no packet existed yet for #1305, drafted mirroring the
sibling #1312/#1313 fence-bypass packets from the same cohesion batch):
replace hand-built `derive_item_path()` with `config.sku_dir()`/
`sku_json()`, replace the locally duplicated `_is_safe_sku()` check with
the canonical `_safe_segment()` validator (via catching its ValueError),
and replace the raw `json.loads()` read with `resolver.load_item_doc()` +
`find_current_sku()` alias fallback. The recursive/pattern-based
key-deletion write itself is explicitly out of scope (no fence
equivalent exists; documented in-file and in the packet).

Not yet committed as of writing this note.
