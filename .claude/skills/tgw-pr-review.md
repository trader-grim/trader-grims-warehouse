# Skill: tgw-pr-review

## Purpose
Review the current branch before merging to main. Checks correctness, lint, invariants, and produces deployment notes.

## Usage
> /tgw-pr-review

## Steps Claude must follow

1. Get the commit list:
   `git log --oneline main..HEAD`

2. Get the full diff:
   `git diff main...HEAD`

3. Run the test suite:
   `pytest -q`

4. Run lint (src/ only — avoids noise from tools/archive):
   `ruff check src/`

5. Check changed files against `docs/TGW-Plan-Vault/reference/invariants.md` — flag any violations of the 29 invariants (A1–E4).

6. Produce a structured report:

   **Commits** — one-line list of what's in the branch

   **Tests** — pass/fail + count delta vs main if known

   **Lint** — clean or list of issues

   **Invariant check** — any violations; "none found" if clean

   **Bugs / concerns** — correctness issues spotted in the diff; be specific (file + line)

   **Deploy notes** — which `tgw-worker@{queue}` units need restarting; any config or migration steps the operator must run manually

   **Verdict** — LGTM / needs changes (with a one-line reason if not LGTM)
