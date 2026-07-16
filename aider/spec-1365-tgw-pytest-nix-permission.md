Bug (todo #1365): the `tgw` user cannot run pytest in this checkout because
`nix` at the repo root is a symlink to `/home/db/tgw-flake/nix`, which `tgw`
lacks read access into. pytest fails during its own rootdir/conftest
collection phase with `PermissionError: [Errno 13] Permission denied:
'.../nix'` even though pyproject.toml's `[tool.pytest.ini_options]` already
lists `nix` in `norecursedirs` (line ~89) — norecursedirs only filters test
collection recursion, not pytest's earlier conftest-discovery directory scan
from rootdir, which still tries to stat/scandir every top-level entry
including the unreadable `nix` symlink.

Reproduce first (read-only, do not change anything until you've confirmed
the exact failure):
  sudo -u tgw /opt/TGW/.venvironments/tgw/bin/python -m pytest -q tests/test_items.py --collect-only

Fix: add an `ignore_glob`/`collect_ignore_glob` entry (or equivalent
pytest.ini_options setting — do not touch the `nix` symlink itself, do not
touch anything under nix/, do not touch secrets or config outside
pyproject.toml/conftest.py) so pytest's own startup/conftest-discovery scan
never touches the `nix` path at all, for any invocation. Only edit
pyproject.toml and, if genuinely necessary, a root-level conftest.py.

Acceptance — must pass, run and paste the output:
  sudo -u tgw /opt/TGW/.venvironments/tgw/bin/python -m pytest -q tests/test_items.py --collect-only
  (no PermissionError, collects normally)

  /opt/TGW/.venvironments/tgw/bin/python -m pytest -q tests/test_items.py
  (still passes normally as the db user — the fix must not change behavior
  for any user who CAN read nix/)

Out of scope: do not touch the nix/ directory or its symlink target, do not
touch any file under src/tgw/, do not touch secrets or tgw-api-config.json.
