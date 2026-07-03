## PP-RECOVERY-001 — Web UI Regression Audit and Process Recovery

**Opened:** 2026-06-17 (session 33)
**Status:** IN PROGRESS — audit underway

### What happened

On 2026-06-16, a session-end report stated that many completed todos had no git commit
reference. The user interpreted this as extensive code loss and began redeveloping the web UI.
The diagnosis was wrong. No web UI code was actually lost.

**Root causes of the false alarm:**
1. The commit cross-reference grepped for `"todo #NNN"` but many commits used `"#NNN"` or
   described the work without a todo ID — producing false negatives for ~20 commits.
2. Jun 15-16 work (todos #878–#894) was committed to `task/aider-20260616145314` but not yet
   merged to `main`, making it appear absent when comparing against the main branch.
3. The service runs from an editable install — the full PP-EDITOR-001 implementation was live
   regardless of which branch was "current."

### Verified state (2026-06-17 audit)

| Branch | http_server.py lines | Routes |
|--------|----------------------|--------|
| main | 5,832 | 53 |
| task/aider-20260616145314 | 6,360 | 54 |

All `/form/` pages confirmed present: `home`, `items`, `items/{sku}`, `intake`, `bulk`,
`todos`, `suggest`, `offers`, `revisions`, `review`, `pipeline`, `system`, `links`, `/docs`.

**Confirmed NOT implemented** (genuinely missing):
- `#867` — PM chat as modal popup (only the page route exists, no modal/window variant)
- `#868` — `sudo -n` removal from `api.py:295,314,325` (fix was marked done but not applied)

### Recovery actions required

1. **Merge `task/aider-20260616145314` → `main`** before any further development. This branch
   has 20+ commits (Jun 15-16) not yet on main. New work risks diverging from or duplicating it.
2. **Restart `tgw-http.service` and walk every `/form/` route** — compare what renders against
   the WEBUI-AUDIT todos (#998–#1038) in the review queue. This is the ground truth.
3. **Triage `tgw todo review`** — 41 WEBUI-AUDIT todos and 101 pre-800 history todos. For each:
   - Feature confirmed working → `tgw todo --done <review-id>`
   - Feature missing or broken → `tgw todo --delegate <review-id> claude` with a description
4. **Audit any post-Jun-16 redevelopment** for overlap with existing code. Decide which version
   to keep before merging.
5. **Fix `#868`** — remove `sudo -n` pattern from `api.py`.

### Process fixes (implement before next coding session)

- Commit messages must include `todo #NNN` in the subject when closing a todo
- Add `started_at` + `commit_sha` columns to `todo_items` (PP-TODO-001 follow-up)
- Prompt history log now active: `/opt/TGW/var/log/prompt-history.log` (requires Claude
  restart to activate the `UserPromptSubmit` hook in `~/.claude/settings.json`)

### Review queue summary

| Batch | IDs | Count | Prefix |
|-------|-----|-------|--------|
| Pre-800 historical | #897–#997 | 101 | `VERIFY #NNN` |
| Web UI regression | #998–#1038 | 41 | `WEBUI-AUDIT #NNN` |

Triage: `tgw todo review`

---

