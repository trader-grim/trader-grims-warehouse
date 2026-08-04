# Correction and precise inventory — consolidated Nix batch, 2026-07-25

**Supersedes only the technical detail of** `CLAUDE-REQUEST-2026-07-25-consolidated-nix-flake-batch.md`; its one-batch decision remains unchanged.

## Verified required changes

1. **`python-multipart` is a real application dependency, not merely a test helper.**
   - `src/tgw/http_server.py` declares FastAPI `Form(...)` endpoints.
   - Current declared Nix dev shell cannot import the server because FastAPI requires `python-multipart` at route registration.
   - It is absent from the source `pyproject.toml` dependencies and from the flake Python package dependency list/dev shell.
   - Include its source dependency declaration and Nix package/runtime/dev-shell closure in this same batch.

2. **`mistune` is an application dependency already represented in the flake package closure but omitted from the dev shell.**
   - It is present in `pyproject.toml` and the flake's `tgwPackage` dependencies.
   - The declared `devShells.default` omits it, causing eight `/docs` tests to fail.
   - Add it to the same dev-shell dependency set.

3. **The source checkout’s flake failure is a committed absolute symlink, not a declared `home` input.**
   - `/opt/TGW/src/trader-grims-warehouse/flake.nix` is a tracked symlink whose content is `/home/db/tgw-flake/flake.nix`.
   - From the source Git checkout, Nix rejects that external path with `Path 'home' does not exist in Git repository`.
   - The batch must choose and implement one canonical/reproducible source-to-flake relationship; do not retain an absolute home-directory symlink as a tracked repository contract. Preserve Dave's flake ownership and avoid copying/diverging flake authority.

4. **a1131 persistent access gap.**
   - tgw-prod already declares `db` in group `tigwa`.
   - a1131 currently declares `db.extraGroups = [ "hermaroid" ]`; it lacks `tigwa` membership. Add the approved host-local extension and the reviewed non-secret shared-output-root mechanism in this batch.

## Single-batch acceptance

- The canonical flake/source relationship works from a clean clone/worktree, not only `/home/db`.
- `nix develop … -c pytest` imports `tgw.http_server` and runs targeted tests without ephemeral packages.
- Package/runtime and project metadata stay aligned.
- Both Dave and Tigwa can use the approved shared path without secret widening.
- One reviewed build/switch/rollback plan covers the affected hosts.
