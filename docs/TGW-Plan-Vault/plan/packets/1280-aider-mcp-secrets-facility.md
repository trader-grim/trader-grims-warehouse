# Packet: aider_mcp_server.py reads keys via the single secrets facility
Todo: #1280   PP: PP-COHESION-001   Track: SECURITY batch, concurrent

## Context budget (ALL the model may load)
This packet + `src/tgw/aider_mcp_server.py` (`_load_api_keys()` only,
lines ~57-70) + `src/tgw/apis/secrets.py` (`get_api_key()` signature only,
read-only reference) + the todo brief (`tgw todo brief 1280`). Nothing
else.

## Verified live before this packet was written
Same class of bug already fixed once this session (todo #1289,
`health.py`'s `_openrouter_key_limit()`): the 2026-07-09 secrets migration
(#1252) moved per-provider `<name>-credentials.json` files to
`secrets_root/_migrated-to-tgw-env-20260709/`. `_load_api_keys()` here
still reads the old paths directly
(`_SECRETS_ROOT / 'anthropic-credentials.json'`,
`_SECRETS_ROOT / 'openrouter-credentials.json'`) — both now nonexistent,
caught by a bare `except Exception: pass`, so both keys silently fail to
load every time this module is imported.

## Spec
```python
def _load_api_keys() -> dict[str, str]:
    keys = {}
    for env_name, filename in [
        ('ANTHROPIC_API_KEY', 'anthropic-credentials.json'),
        ('OPENROUTER_API_KEY', 'openrouter-credentials.json'),
    ]:
        p = _SECRETS_ROOT / filename
        try:
            val = json.loads(p.read_text())['api_key']
            if val:
                keys[env_name] = val
        except Exception:
            pass
    return keys
```
Fix — read via the single secrets facility instead of the dead file path:
```python
def _load_api_keys() -> dict[str, str]:
    from tgw.apis.secrets import get_api_key
    keys = {}
    for env_name, provider in [
        ('ANTHROPIC_API_KEY', 'anthropic'),
        ('OPENROUTER_API_KEY', 'openrouter'),
    ]:
        try:
            val = get_api_key(provider)
            if val:
                keys[env_name] = val
        except Exception:
            pass
    return keys
```
Preserve the existing "silently skip if unavailable" behavior (this
function's contract is best-effort key loading, not a hard requirement) —
just fix WHERE it looks. `_SECRETS_ROOT` and the `json` import may become
unused after this change in this function specifically; check whether
`_SECRETS_ROOT` is used elsewhere in the file before removing it (do not
remove if still referenced elsewhere).

## Dataset
None — this restores correct key-loading behavior for `aider_mcp_server`;
no data write.

## Out of scope
- `secrets.py` itself — read-only reference, do not modify.
- Any other function in `aider_mcp_server.py`.
- The audit-log/timeout constants near this function.

## Acceptance (live)
1. With real keys available via the current facility
   (`secrets_root/tgw.env`), call `_load_api_keys()` — confirm both
   `ANTHROPIC_API_KEY` and `OPENROUTER_API_KEY` are now actually
   populated (previously always empty due to the dead file path).
2. With a provider's key unset, confirm that provider is simply absent
   from the returned dict (no exception propagates) — matches the
   original best-effort contract.
3. Run the full offline suite — confirm zero regressions.

## Quota/risk
None — no new API calls, this only fixes key *loading*, not usage.
