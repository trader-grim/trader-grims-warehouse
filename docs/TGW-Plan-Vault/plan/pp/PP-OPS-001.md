## PP-OPS-001 — Operational Prerequisites and Unblocking Tasks

Catch-all anchor for one-off setup, infrastructure, and credential tasks that unblock feature
work but don't belong to a specific PP-* project. These are not a project with phases — they are
discrete operator actions required to keep the platform running or to gate a feature todo.

### Scope
- API key / credential provisioning (eBay, Google, OpenRouter, Discogs, etc.)
- Secrets-root file setup and permissions
- System service installs or OS-level configuration
- Hardware or external-account setup steps
- Any `[admin]`-agent or operator-only prerequisite that gates a `[claude]` feature todo

### Policy
Todos linked here have `pp_ref = PP-OPS-001` and `plan_anchor = PP-OPS-001`.  The brief will
extract this short section — not a multi-page design document — so the operator sees only what
they need to execute the task.

---

