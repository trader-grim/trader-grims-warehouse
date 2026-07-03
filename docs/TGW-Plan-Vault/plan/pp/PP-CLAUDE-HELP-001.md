## PP-CLAUDE-HELP-001 — tgw claude-help Troubleshooting Mode

### Vision
`tgw claude-help [issue description]` launches Claude Code with a CLAUDE.md specifically
tuned for fast, accurate issue diagnosis on the TGW platform — narrower context, focused
on error resolution rather than feature development.

### Design
A separate `CLAUDE-TROUBLESHOOT.md` lives alongside `CLAUDE.md`. It contains:
- System architecture in dense summary form (worker → queue → database flow)
- Common failure modes and their symptoms (ISSUES.md condensed)
- Diagnostic commands (health, queue check, journal, systemctl status)
- Decision tree: "if you see X, check Y first, then Z"
- Zero planning overhead — diagnose, fix, verify, done

The command:
```bash
tgw claude-help                    # launch claude with troubleshooting CLAUDE.md
tgw claude-help "token expired"    # include the issue as initial context
tgw claude-help --worker ebay_stage # narrow context to a specific worker
```

Implementation: `CLAUDE-TROUBLESHOOT.md` symlinked or passed as `--context` to claude CLI.
Alternatively: a dedicated `.claude/` project config directory pointed at a minimal CLAUDE.md.

### Value
Reduces time-to-diagnosis for operational issues. Operator doesn't need to explain the full
project history — the troubleshooting CLAUDE.md has a compressed but complete system view.
Especially useful under duress (down worker, stuck token, dead-letter flood).

### Dependencies
- Claude Code CLI installed (✅ available)
- `CLAUDE-TROUBLESHOOT.md` authored (one session of work)

---

