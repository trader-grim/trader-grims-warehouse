# Work packet — #1602 detective (post-hoc) scan for E11/E12 out-of-process edits

**Todo:** #1602
**Plan:** PP-AGENT-DISCIPLINE-001
**Executor:** `tgw-coder`
**Base:** live-verified `catio-nix-0.0.1-alpha`
**Context budget:** This packet, `docs/TGW-Plan-Vault/reference/invariants.md` sections E11/E12
(read only those two sections, not the whole file), `scripts/check_review_md.py` (as a style/
structure reference — same "mechanical gate, not judgment" shape), and
`.claude/hooks/worktree-guard.py` / `.claude/hooks/app-code-guard.py` (read only, to see what
the now-confirmed-broken preventive hooks were trying to enforce). Do not load the master plan
or unrelated packets.

## Background (why this packet exists — do not re-litigate)

`invariants.md` E11 (2026-07-16, updated 2026-07-20 per todo #1531) documents that
`worktree-guard.py` and `app-code-guard.py` — the `PreToolUse` hooks meant to mechanically
block `Edit`/`Write` calls that (a) land outside a `tgw-coder` worktree, or (b) modify
`src/tgw/`/`tests/` directly instead of through a `todo/<id>-<slug>` branch — **do not fire at
all**, confirmed reproducing twice, matching three open upstream Claude Code bugs
(`anthropics/claude-code#74942`, `#69260`, `#77212`). No local fix exists for the preventive
side. E11's own text names the needed compensating control: *"a periodic scan ... for commits/
edits that landed outside a tgw-coder worktree or outside a todo/<id>-<slug> branch, so
violations are caught and flagged promptly after the fact even though they can no longer be
blocked before the fact."* This packet builds exactly that scan — nothing more.

**E14 (trace-immutability) is explicitly OUT OF SCOPE for this packet.** E14's own documented
mechanism is a "hash-commitment row (packet #1586)" against an `agent_run_transcript_hashes`
table — grepping the live repo (`grep -rln "agent_run_transcript_hashes" --include="*.py" .`)
found **no such table or reader/writer exists yet** — it's a design reference, not built
infrastructure. Building a detective check for E14 would require designing new
hash-commitment infrastructure from scratch, which is a real design decision (what hashes,
where stored, who signs) — not a bounded mechanical fix. Do not build it here. If you find
during this packet that trace-hash infrastructure DOES exist after all, stop and report it
rather than silently building against it.

## Objective

Build a read-only, detective (never preventive, never remediating) git-history scanner that
flags candidate E11/E12 violations: a commit reachable from a given ref that modifies
`src/tgw/` or `tests/` paths and did NOT arrive via the normal `todo/<id>-<slug>` branch +
review + stitch merge-commit pattern.

## Known, load-bearing limitation — state this explicitly in the tool's own output, do not paper over it

**Every commit in this repository is authored as `Dave <dave@mappo.eu.org>`, regardless of
which agent or session actually ran `git commit`** (verified live:
`git log --format='%an <%ae>' | sort -u` returns exactly one identity across the whole
history). This means the scanner **cannot** distinguish "Dave's own direct edit" from "an
agent's out-of-process edit" by git author/committer identity — that signal does not exist in
this repository's git data. The scanner's output is therefore a **candidate list for human
triage**, not a confirmed-violation list, and must say so in its own output text, not just in
a docstring nobody reads at scan time.

## Spec

1. New standalone script: `scripts/scan_out_of_process_edits.py`. Do not modify
   `check_review_md.py` or any hook file — this is a new, separate tool (extend-vs-build-new
   was left open by invariants.md; a new script is simpler to test in isolation and keeps the
   pre-stitch gate script's existing behavior/tests untouched).
2. CLI: `python3 scripts/scan_out_of_process_edits.py [--since <ref>] [--until <ref>]`.
   - `--since` defaults to a sensible bound you choose and document (e.g. the repo's first
     commit, or require the flag — your call, but the default must be named explicitly in
     `--help` output, not silently assumed).
   - `--until` defaults to `HEAD`.
3. Detection logic, walking `git log --since-ref..--until-ref` (first-parent history of the
   target range):
   - For every **non-merge** commit (a commit with exactly one parent) in that range: if its
     changed paths (`git show --name-only`) include anything under `src/tgw/` or `tests/`,
     it is a **candidate** direct-edit-on-integration-branch violation.
   - For every **merge** commit in that range: this is the expected stitch pattern (a task
     branch merged in) and is NOT itself a candidate, regardless of what paths it touches —
     but only if its second parent's branch-name-at-merge-time matches the `todo/<id>-<slug>`
     pattern. If you cannot recover the branch name from git history alone (branch refs may
     already be deleted post-merge), treat any merge commit as non-candidate by default and
     say so explicitly in the tool's output/docstring as a known blind spot — do not try to
     reconstruct deleted branch names heuristically.
4. Output: for each candidate, print commit hash, author date, one-line summary, and the list
   of `src/tgw/`/`tests/` paths it touched. End with an explicit reminder line stating the
   git-author limitation from the section above, so nobody reading the output mistakes
   "candidate" for "confirmed."
5. Exit code: 0 always (this is a reporting tool, not a gate — per this packet's authority
   boundary, it does not fail CI or block anything). If you believe a non-zero exit code for
   "candidates found" would be more useful for future gate integration, note that as a
   documented recommendation in the result manifest, but do not implement it without it being
   in this spec — that's a scope decision for whoever wires this into a gate later, not this
   packet.
6. Never mutate git state (no `git commit`, `checkout`, `merge`, `reset`, `clean`) — read-only
   `git log`/`git show`/`git rev-list` invocations only.
7. Never call out to a live database, eBay API, or credentials — this tool operates on git
   history only.

## TDD sequence

1. Build a throwaway synthetic git repository (in a temp directory, via `git init` + scripted
   commits — NOT the real TGW repo) with: (a) a clean history with only merge commits touching
   `src/tgw/`-equivalent paths (should yield zero candidates), (b) at least one non-merge
   commit directly touching a `src/tgw/`-equivalent path (should yield exactly one candidate),
   (c) a non-merge commit touching only unrelated paths (should yield zero candidates for that
   commit). Write these as regression tests FIRST, run them against a not-yet-built script
   (or an empty stub), observe RED, then implement.
2. Add a test proving the "author is always the same identity" limitation doesn't cause a
   false negative or false positive — i.e., the detector's logic must not depend on author
   identity at all (grep the implementation, or add a test with two different authors in the
   synthetic repo, to prove author is genuinely unused as a detection signal).
3. Run the finished tool read-only against the real repo's live history (`--since` covering at
   least the current sprint's commits) as a live sanity check — this is read-only and safe.
   Record what it found (or didn't) in the result manifest; do not act on any candidate it
   surfaces — reporting only.

## Worktree

```text
/opt/TGW/var/worktrees/1602-detective-outofprocess-scan
todo/1602-detective-outofprocess-scan
```

If either already exists, stop and report the collision rather than reusing or deleting it.

Copy this packet byte-for-byte into the worktree at the same repo-relative path and include it
unchanged in the branch commit.

## Acceptance

```text
tgw-pytest /opt/TGW/var/worktrees/1602-detective-outofprocess-scan tests/test_scan_out_of_process_edits.py -q
tgw-pytest /opt/TGW/var/worktrees/1602-detective-outofprocess-scan -q
```

(If `tgw-pytest` does not exist on this system — as found true during todo #1663 — use the
equivalent `PYTHONPATH=<worktree>/src pytest` invocation per this profile's own contract, and
say so in the manifest, same as #1663 did. Do not treat that substitution as a deviation from
this packet — it is an already-known environment fact, not a new one.)

Run Ruff on every changed/new file. Record exact commands and outputs.

## Deliverable

```text
docs/TGW-Plan-Vault/plan/packets/results/1602-RESULT.md
```

Include RED/GREEN evidence, the synthetic-repo test design, the live read-only sanity-scan
output against the real repo, focused/full-suite/Ruff outcomes, deviations, and — importantly
— a clearly labeled "E14 not built, here's why" note pointing back to this packet's own
Background section, so a future reader doesn't have to re-derive that gap.

## Authority and stop conditions

- No shared-checkout edits.
- No merge, rebase, push, deploy, service action, queue action, Todo closure, eBay/API
  mutation, credential action, backup/sync action, flake edit, or canonical Plan Vault
  acceptance.
- No mutation of any real git branch/worktree outside your own task worktree/branch — this
  tool must never be run with mutating flags against the real repo, even accidentally (it has
  none by design, per spec point 6, but do not add any).
- No wiring this tool into any hook, CI gate, or `PreToolUse` matcher — that is a follow-on
  decision for Dave, not part of this packet.
- No building E14 hash-commitment infrastructure — out of scope, see Background.
- Do not touch #1697, #1705, #1706, or #1663's worktrees/branches.
- Stop if the packet is inconsistent with the live E11/E12 wording, the worktree/branch already
  exists, or acceptance requires production/DB/credential access.
