# TGW remediation commit workflow — v1 (2026-08-15)

Use this procedure for production defects and follow-up patches. It keeps a quick
repair traceable without mixing source, Plan, flake, or live-state authority.

1. Capture the failing item, request, log, service generation, and exact source commit.
2. Select the smallest Plan/PP/Todo execution root that actually governs the change.
3. Create a dedicated application worktree and branch from canonical application
   source. Never edit an immutable release or the production flake to patch Python.
4. Add a deterministic regression test before or with the fix. Preserve unrelated
   worktree changes and item evidence.
5. Commit the remediation as one coherent source commit. Follow-up review corrections
   become successor commits; do not amend a reviewed/deployed candidate.
6. Run focused tests, static checks, then the full suite in a bounded disk-backed
   scratch directory.
7. Record independent review separately from implementation. Current production
   release policy requires human approval; non-production profiles may use another
   separately admitted reviewer when their Plan policy permits it.
8. Merge the reviewed commit into canonical `main` with expected-parent checks,
   publish through `tgw-source-git`, and verify remote readback.
9. Install the exact commit as a new immutable generation and verify the affected
   operator workflow end to end. Do not call the patch complete merely because tests
   or deployment commands returned zero.
10. If acceptance fails, retain the failed generation/evidence, roll back if needed,
    and create a new remediation commit. Never hot-edit the selected generation.

For a flake-only correction, use an isolated branch in `/home/db/tgw-flake` and the
flake maintenance procedure. For Plan changes, commit only in
`/opt/TGW/library/plans`. Cross-repository changes keep separate commits and explicit
bindings; they are never combined into one Git history.
