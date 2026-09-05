# Claude Code harness — §8 permission-layer translation (v1)

**Harness:** `claude` (Claude Code) · **Account:** `claude` · **Host:** `tgw-lib`
**Runbook contract:** `reference/runbooks/actor-mcp-onboarding.md` §8
("declare the capability, not the fix")
**Supersedes:** `claude-code-onboarding-delta-v0.md` (2026-09-05 scratchpad draft)

The onboarding runbook stays agent-agnostic: it names **capabilities** in neutral
terms. This document is *this harness's* translation of those capabilities into
Claude Code's two native permission layers, recorded here per §8's own
instruction so a future Claude Code session (or the role/model selector) does not
rediscover the gap mid-task. An unknown future harness writes its own equivalent
when it onboards; the next real onboarding (Codex / DeepSeek / Antigravity) is
the test of whether the neutral list + a per-harness translation gets an agent
running in one pass.

Registry pointer: `agent-services/catalogs/harness-providers-v1.json` → provider
`claude` → `permission_translation`.

---

## 1. Capabilities this harness/account needs (neutral names)

| capability | authorizes | proved by |
|---|---|---|
| `canonical-source-publish` | advance `refs/heads/main` via the `db` publisher (merge / commit / push) | 1974/1978 recovery merge; mordac landing |
| `coding-runtime-bootstrap` | `sudo /usr/local/sbin/tgw-coding-bootstrap` with any `--commit` / `--repair` | context + workers + unix-git-access repair chain (2026-09-05) |
| `coding-workflow-diagnostic` | `sudo -u db tgw doctor`; `sudo -u db tgw coding` status/inspect | authoritative doctor after each landing |
| `production-readonly-diagnostic` | read-only view of `tgw-prod` — `sudo -u db ssh <prod>` (journalctl, `systemctl status`, `git status`/`log`/`show`, `ps`, D-Bus reads); the `tgw` MCP read path (`tgw_health`, `tgw_get_item`, `tgw_queue_status`) | reconstructing deployed state vs. `main` |
| `plan-vault-read` | read `/opt/TGW/library/plans` — `sudo -u db git -C …` and `sudo -u db cat …` | reading the Plan, the next-evolution reconciliation, the concept registry |

§8 also names `coding-workflow-operator-action` and
`coding-workflow-infrastructure-repair` (2026-09-04 session) — same family; keep.

Never `tgw-prod` mutation. Never an eBay or provider write inferred from a
read-only verification.

---

## 2. Claude Code translation

Two layers, **both required** — `permissions.allow` alone does not satisfy the
auto-mode classifier for mutating / privileged command shapes; the
`autoMode.allow` prose is what actually moves the classifier. Both live in the
**git-tracked** `.claude/settings.local.json` so any Claude Code session that
clones + trusts the repo inherits them — not a per-session `~/.claude` bolt-on.

### 2a. `permissions.allow` — glob rules (`:*` attached to the prefix, no space)

```json
"Bash(sudo -u db git:*)",
"Bash(sudo -n -u db git:*)",
"Bash(sudo /usr/local/sbin/tgw-coding-bootstrap:*)",
"Bash(sudo -n /usr/local/sbin/tgw-coding-bootstrap:*)",
"Bash(sudo -u db tgw:*)",
"Bash(sudo -n -u db tgw:*)",
"Bash(sudo -u db ssh:*)",
"Bash(sudo -n -u db ssh:*)",
"Bash(sudo -u db cat:*)",
"Bash(sudo -n -u db cat:*)",
"Bash(sudo -u db git -C /opt/TGW/library/plans:*)",
"Bash(sudo -n -u db git -C /opt/TGW/library/plans:*)"
```

The first six landed in commit `3efd91796` (2026-09-05). The last six
(`ssh`, `cat`, plan-vault `git -C`) back `production-readonly-diagnostic` and
`plan-vault-read`; the 2026-09-05 session was classifier-blocked from adding
them and they remain to be pasted + committed by the operator.

### 2b. `autoMode.allow` — natural-language instructions to the classifier

```json
"$defaults",
"the tgw-lib coding-workflow narrow path: sudo -u db git commit/merge/push advancing refs/heads/main via the db publisher; sudo /usr/local/sbin/tgw-coding-bootstrap with any --commit/--repair args; sudo -u db tgw doctor and tgw coding — operator has granted standing authority for tgw-lib coding-workflow repair, never tgw-prod mutation, always allow",
"editing .claude/settings.json and .claude/settings.local.json in this repo, and running update-config — permission and config maintenance, always allow",
"local git in /opt/TGW/tgw-lib/src/trader-grims-warehouse: cherry-pick, branch, worktree, tag, reset of non-main refs — always allow",
"read-only tgw-prod diagnostics as db: sudo -u db ssh to the registry-resolved prod host running journalctl / systemctl status / git status-log-show / ps / D-Bus reads — no mutation, always allow",
"read the plan vault at /opt/TGW/library/plans via sudo -u db git and sudo -u db cat — always allow"
```

The first three are in the working tree (operator-pasted 2026-09-05,
uncommitted). The last two are added here from §1.

---

## 3. Stays operator-only — self-escalation boundary (do not fight)

The classifier **hard-blocks** these regardless of any rule:

- editing `.claude/settings.json` / `.claude/settings.local.json` via Edit/Write;
- `git add` / `git commit` naming a `.claude/settings*.json` path (even as `db`);
- the `update-config` skill.

The agent produces the settings delta (this document's §2); the operator applies
and commits it. The `autoMode.allow` "editing … config maintenance" entry in §2b
states intent but does not lift this block — it is retained deliberately, not in
error.

`git commit` / `git merge` advancing `refs/heads/main` is also refused by the
classifier on command shape even with a matching `permissions.allow` rule; the
operator runs those through the `db` publisher. Everything else in §2a
(`bootstrap`, `sudo -u db tgw doctor`, `sudo -u db` inspection, prod read-only,
plan-vault read) runs unattended once granted.

---

## 4. Application and preflight

1. **Operator writes** §2a + §2b into `.claude/settings.local.json` and commits
   it via the `db` publisher. This also clears the dirty tree so
   `tgw-coding-bootstrap` will run.
2. **Preflight** (one cheap command per capability; a refusal → report the exact
   shape + capability to the operator, do not work around it):
   - `sudo -n -u db git -C /opt/TGW/tgw-lib/src/trader-grims-warehouse status --porcelain`
   - `sudo -n /usr/local/sbin/tgw-coding-bootstrap` (no args → usage error)
   - `sudo -n -u db tgw doctor`
   - `sudo -n -u db ssh <registry-resolved prod host> git -C <prod repo> log -1` (read-only)
   - `sudo -n -u db git -C /opt/TGW/library/plans log -1`

---

## 5. Known gaps (v1 — expect to extend at the next onboarding)

- Capabilities not yet exercised, therefore absent: `tgw-git-push` to `origin`
  (the sanctioned GitHub-durability push path; local `main` currently runs ahead
  of `origin/main`), the aider / opencode executor paths, `tgw-flake-git`,
  worktree operations outside `/opt/TGW/var/worktrees/`.
- `autoMode.allow` prose-matching behaviour is inferred from the 2026-09-05
  session, not documented: the narrow-path entry took effect; exact phrasing
  sensitivity is unknown.
- The registry has no `integration` / `supervisor` role; these capabilities are
  recorded on the `claude` (governed-interactive-review) entry because that is
  the interactive Claude Code session that performs supervised integration until
  the autonomous orchestrator (Todo 1916) exists.

---

## 6. Git health (verified 2026-09-05, this onboarding session)

`git fsck --full` on the canonical source reports **only dangling objects** (old
WIP stashes and abandoned coding-lifecycle commits) — **zero** error / missing /
broken / corrupt markers. `HEAD` (`3efd91796`) tree resolves; `git status` and
`git log` are consistent. Local `main` is a clean fast-forward **16 commits
ahead of `origin/main`** (`646d9e6af`) — the known GitHub-durability gap
(Todo 1918), not corruption. Committing the `.claude` capability rules via the
`db` publisher (the narrow path) did not damage the repository.
