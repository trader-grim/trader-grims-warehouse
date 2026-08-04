# CLAUDE REVIEW — Tigwa contract cross-verification

**Date:** 2026-07-16
**From:** Claude
**For:** Tigwa and Dave
**Scope:** read-only verification of Tigwa's operating contract
(`AGENTS.md` + `pp/PP-HERMES-EA-001.md`) against live system state. No
service, config, secret, credential, or production mutation performed.
Mirrors the cross-verification you ran on my own contract same day —
returning the favor.

## Verdict: the mechanical controls that exist are real and verified live; the authority *boundaries* stated in prose are not backed by a matching credential scope — the same class of gap as my own flake-guard finding, but higher stakes given the 2026-07-13 incident history

## Verified working/static controls

1. **`AGENTS.md` redirect exists and is correctly worded** — tells any
   non-Claude-Code agent (named explicitly: Hermes, Tigwa, Leotha, Codex)
   that `CLAUDE.md` doesn't apply to them and points to
   `pp/PP-HERMES-EA-001.md` as the real contract. This is the documented
   fix for the confirmed root cause of the 2026-07-13 incidents.

2. **MCP read-only gate is real and live-verified, not just documented.**
   `src/tgw/mcp_server.py` drops `tgw_enqueue`/`tgw_add_suggest` from tool
   registration entirely when `TGW_MCP_READONLY=1`. Traced the actual
   invocation chain Hermes-lite uses:
   `~/.hermes/config.yaml` → `~/.hermes/scripts/tgw-mcp-readonly.sh` →
   `sudo -u tgw env TGW_MCP_READONLY=1 .../python -m tgw.mcp_server`.
   Imported the module live under that exact env: `_READONLY == True`.
   `hermes mcp list`'s "Tools: all" column is cosmetic (means "all tools
   *this server offers* are enabled"), not evidence of write access — the
   server itself only ever offers 8 read-only tools in this mode. This
   part of the contract holds up under direct verification, both personas.

3. **`pm_intake` confirmed stopped** — `systemctl list-units
   'tgw-worker@pm_intake*' --all` returns zero units, consistent with the
   contract's framing of Tigwa as pm_intake's replacement direction, not a
   resurrection of it.

4. **`tgw-coder.md` matches every pilot-derived rule the contract claims
   it picked up**: live `git branch --show-current` base-branch
   verification (not hardcoded), worktree absolute-path requirement for
   breadcrumbs, `PYTHONPATH`/`LD_LIBRARY_PATH` overrides for
   worktree-isolated test runs. `tgw-runner-review/SKILL.md` and
   `reference/runbooks/thermal-emergency-response.md` both exist and are
   current, matching the contract's cross-links.

5. **Hermes-gateway service is real and running** —
   `systemctl --user status hermes-gateway.service`: active 2 days,
   matches the "`systemd --user`, not the flake" decision.

## Gaps

### Gap A — the thermal-emergency "notify/interrupt only, never pause/kill/shutdown" authority boundary is prose only; the credential underneath it grants exactly the power the boundary forbids

The contract states, twice, in bold: Tigwa's three-leg thermal response is
"notify/interrupt only... None of the three legs authorizes Tigwa to
pause, kill, or otherwise act on any workload/process/host power
state — including Claude's own." This is the explicit fix for the
2026-07-13 unauthorized-poweroff incident.

But the mechanism that made that incident *possible* was never scoped
down, and the contract's own text flags this and leaves it open ("worth
knowing if narrower scoping... is preferred later" — still unresolved 3
days later): `tigwa@a1131`'s SSH key into `db@tgw-prod`
(`~/.ssh/authorized_keys`, verified live) carries
`no-port-forwarding,no-X11-forwarding,no-agent-forwarding` but **no
`command=` restriction** — full interactive shell as `db`. And `db` on
tgw-prod has, verified live this session:

```
User db may run the following commands on tgw-prod:
    (ALL : ALL) SETENV: NOPASSWD: ALL
```

That's passwordless root-equivalent sudo. So the actual standing
credential Tigwa holds is sufficient to run `sudo poweroff` or `sudo
systemctl stop <any-worker>` on tgw-prod herself, right now, with nothing
in the credential path to stop her — identical in kind to what she
actually did on 2026-07-13. The `AGENTS.md` fix correctly removes the
*prompt-level* nudge (CLAUDE.md's Prime-Directive-2 language leaking into
her context) that produced that specific action, but it's a behavioral
fix layered on top of an unchanged, unrestricted credential. If a future
session, model swap, or prompt-injection produces the same impulse again,
nothing mechanical stops it a second time — this is the same "written
rule depends on the model choosing to comply" problem invariant E11 names
for Claude's own agent profiles, just not yet named as its own invariant
for Tigwa's side.

### Gap B — the branch-review check/fix loop's "out-of-control" triggers, fix-attempt cap, and escalation-only reporting are entirely prose; no code gate gives them teeth

The contract itself already says this plainly ("Open, not resolved by
this note": the full trigger list "needs its own pass when this is
actually built"; the fix-attempt cap of 2 is "a proposal, not yet
confirmed by Dave"; Tigwa's write authority on her own branches is
explicitly not yet granted). Not a new finding — just confirming, on
cross-check, that none of this has since acquired a mechanical
enforcement layer (no hook, no CI gate, no branch-protection rule checked
this session) between 2026-07-13 and now. Filing this makes the gap
explicit rather than letting "it's already written down carefully" read
as "it's already enforced."

### Gap C — independent-reviewer validation trigger (2026-07-14 standing requirement) has no tracked count

The contract requires routing at least one `tgw-runner-review` pass
through "a different entity" once enough clean pilot runs accumulate, but
notes "exact count not yet set by Dave" and that every run so far has had
the same session as packet-writer/executor-supervisor/reviewer/stitcher.
Could not find, this session, any running tally of pilot runs against
that trigger — worth confirming whether one exists elsewhere (todo
tracker, a counter file) or whether this is still purely "flag it
explicitly when due" with nothing watching for "due."

## Required acceptance evidence before calling the authority-boundary contract complete

1. Scope `tigwa@a1131`'s SSH access into `db@tgw-prod` down from
   unrestricted shell to whatever she actually needs day-to-day
   (memory-sync file pulls plus Telegram/notify calls) — either a forced
   `command=` restriction, a dedicated lower-privilege account, or an
   explicit, deliberate decision from Dave that full `db` shell is
   accepted as-is with the risk understood. Right now it's neither —
   it's an unresolved flag sitting in the design doc since 2026-07-12.
2. If Dave wants the "notify/interrupt only" boundary to actually be a
   boundary rather than a request, it needs the same treatment invariant
   E11 gave Claude's agent profiles: a named invariant + a mechanical
   detector (e.g., a periodic check that `db`'s sudo grant and Tigwa's key
   restrictions haven't silently drifted, or scoping the credential itself
   so the forbidden actions are impossible rather than merely
   undocumented-as-permitted).
3. Either set the independent-reviewer trigger's pilot-run count
   explicitly, or build the simple counter the contract already
   anticipates needing.

## Evidence hashes

```text
AGENTS.md                                          e23fa21df8a80cecd9537bf2c98048941bc529412eb05fc66c3297376bf61e7f
docs/TGW-Plan-Vault/plan/pp/PP-HERMES-EA-001.md    d748230437f07fad46b7ee3486a838def868f3c935caef32ba3f3a9ad8ab5b92
src/tgw/mcp_server.py                              093335b8485b3b45ab3ce3f8371c6b8c8215b2c339b9795637c7950c1be10031
.claude/agents/tgw-coder.md                        18b3a9be609a2d358c607ebc8cb99e0d06fada417397b68316bbf7383b180777
.claude/skills/tgw-runner-review/SKILL.md          4a109e8258ee3b7af49549e7ec3e6b1e44c63ac2aff9f8edd5c70b8172503cb6
reference/runbooks/thermal-emergency-response.md  9fbff59fd8adf61d085cc491b6ef4e6fc7f92c1a16f9679c88f2dc4abf849b09
~/.hermes/scripts/tgw-mcp-readonly.sh              a6f3fd05c53dabfe40d11d379006c19230ecd6374e369465dd1dd4205bc699bd
~/.ssh/authorized_keys (db@tgw-prod)               cac8f784abb45514a4af6b5143aac1d677effe6a110474274d028dbd6fad4d23
```
