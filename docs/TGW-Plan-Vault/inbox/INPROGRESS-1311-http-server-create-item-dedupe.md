# In progress: todo #1311 http_server.py create_item_endpoint dedupe

Working in worktree /opt/TGW/var/worktrees/1311-http-server-create-item-dedupe
on branch todo/1311-http-server-create-item-dedupe, off catio-nix-0.0.1-alpha.

Task: make `items.py::create_item()` mkdir the parent dir before writing,
then replace `http_server.py::create_item_endpoint()`'s inline
path-construction/exists-check/mkdir/atomic_write_json with a single call
to `items.create_item()`, translating FileExistsError to HTTPException(409).
Add tests for both. Per packet
docs/TGW-Plan-Vault/plan/packets/1311-http-server-create-item-dedupe.md.

Status at breadcrumb time: about to read items.py and http_server.py.
