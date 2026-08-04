# Packet: clip.py clipboard-history DB gets restrictive permissions + TTL prune
Todo: #1283   PP: PP-COHESION-001   Track: SECURITY (graduated to concurrent — run alongside #1278/#1279/#1281)

## Context budget (ALL the model may load)
This packet + `src/tgw/clip.py` (the whole file, 196 lines) + this todo's
existing test file if one exists. Nothing else.

## Verified live before this packet was written
- `_connect()` (line 35) creates `~/.local/share/tgw-clip/history.db` via
  `path.parent.mkdir(parents=True, exist_ok=True)` and
  `sqlite3.connect(str(path))` with no explicit permission hardening —
  the file is created at whatever the process umask allows (confirmed
  by the finding as mode 644 in practice: world-readable). Every
  clipboard selection ever copied through `record_clip()` is stored in
  plaintext indefinitely, bounded only by the row-count cap
  `_RETENTION = 2000` (line 28) — no time-based expiry.
- This is a local single-user desktop tool (PP-CLIP-001) — the risk is
  another local user/process on the same machine reading clipboard
  history (passwords, tokens, PII someone copied at some point), not a
  network-facing exposure.
- **Sensitivity filtering (detecting "this looks like a secret, don't
  store it") is explicitly OUT of scope for this packet** — it needs
  product judgment on false-positive risk (a filter that's too aggressive
  could silently drop real SKUs, defeating the tool's actual purpose) that
  belongs to Dave, not a mechanical fix. This packet only does the two
  parts that don't require that judgment call: restrictive permissions,
  and a time-based prune alongside the existing row-count prune.
- TTL default: **14 days**, chosen here (not left for the executor to
  guess, per Prime Directive 3) as a reasonable balance between the
  tool's actual use case (short-lived: "what SKU did I just copy") and
  not being needlessly aggressive. Flag this default in the result
  manifest as something Dave may want tuned; do not silently pick a
  different number if it turns out inconvenient during implementation —
  report back instead.

## Spec

### Restrictive permissions
In `_connect()`, after `path.parent.mkdir(parents=True, exist_ok=True)`,
harden the parent directory to `0700` unconditionally, and the db file to
`0600` unconditionally after `sqlite3.connect()` creates/opens it —
chmod is cheap and idempotent, so applying it on every connect call (not
just first creation) is deliberate: it self-heals an already-existing
644 file from before this fix, not just fresh installs.

```python
def _connect(db_path: Optional[Path] = None) -> sqlite3.Connection:
    path = Path(db_path) if db_path else _default_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    con = sqlite3.connect(str(path))
    path.chmod(0o600)
    ...
```

### TTL-based prune
Add a module constant near `_RETENTION`:
```python
_RETENTION_DAYS = 14
```
In `record_clip()`, alongside the existing row-count prune (lines 78-82),
add a time-based prune using the existing `captured_at` column (already
`datetime('now')`-stamped on insert):
```python
con.execute(
    "DELETE FROM clip_history WHERE captured_at < datetime('now', ?)",
    (f'-{_RETENTION_DAYS} days',),
)
```
Run both prunes (row-count and TTL) every `record_clip()` call, same as
the existing row-count prune already does — no new scheduling mechanism.

## Dataset
None — this only changes filesystem permissions and adds an age-based
delete to an already-ephemeral local cache; no ItemData/queue impact.

## Out of scope
- Sensitivity filtering (see Verified-live above — a separate, harder
  design problem for Dave, not this packet).
- The X11/XFixes capture daemon, Qtile widget, rofi menu (not yet built
  per this file's own docstring) — nothing to touch there.
- Any change to `_RETENTION` (row-count cap) — TTL is additive, not a
  replacement.

## Acceptance (live)
1. Delete any existing test-scoped db file, call `record_clip('test', db_path=<tmp>)`
   — confirm the created file's mode is `0o600` (`stat.S_IMODE(path.stat().st_mode) == 0o600`)
   and the parent directory's mode is `0o700`.
2. Create a db file with mode `0o644` manually (simulating a pre-fix
   file), call `record_clip(...)` against it — confirm it's corrected to
   `0o600` after the call (self-healing, not just fixed on fresh
   creation).
3. Insert a row with `captured_at` manually backdated (via direct SQL)
   to 20 days ago, then call `record_clip('trigger', db_path=<tmp>)` —
   confirm the backdated row is gone afterward (`list_history` no longer
   returns it) while a row from today survives.
4. Confirm the existing row-count retention behavior (2000-row cap) is
   unaffected — existing tests for it (if any) still pass; if none exist,
   this is not new-test-required scope, just don't break it.
5. Run the full offline suite — zero regressions.

## Quota/risk
None — purely local filesystem/SQLite changes, no API calls.
