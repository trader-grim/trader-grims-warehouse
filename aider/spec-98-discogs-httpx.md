You are working in the Trader Grim's Warehouse (TGW) repo at
`/opt/TGW/src/trader-grims-warehouse`. Read CLAUDE.md and CONVENTIONS.md before
making any changes; do not deviate from them.

Task: todo #98 — Discogs adapter: migrate from `requests` to `httpx` (PERPLEXITY-005)

---

## Background

`src/tgw/apis/lookup/discogs.py` uses `requests` for HTTP. The codebase is migrating to
`httpx` as the standard sync HTTP library (`httpx` is already used in `pm_intake.py` and
is installed in the venv). Migrate this module to `httpx`, keeping the exact same adapter
surface and behaviour.

The adapter surface that must not change:
```python
def lookup(barcode: str, cfg: Dict[str, Any]) -> Optional[LookupResult]: ...
```

---

## Change 1 — src/tgw/apis/lookup/discogs.py

Replace the `requests` import and call with `httpx`:

```python
# Before
import requests
...
resp = requests.get(url, params=..., headers=..., timeout=...)
resp.raise_for_status()
data = resp.json()
...
except requests.exceptions.RequestException as exc:

# After
import httpx
...
resp = httpx.get(url, params=..., headers=..., timeout=...)
resp.raise_for_status()
data = resp.json()
...
except httpx.HTTPError as exc:
```

Notes:
- `httpx.get()` accepts the same positional/keyword arguments as `requests.get()`.
- `resp.raise_for_status()` and `resp.json()` work identically.
- `httpx.HTTPError` is the base class for all httpx exceptions (covers both network errors
  from `httpx.RequestError` and HTTP status errors from `httpx.HTTPStatusError`). It is the
  correct drop-in for `requests.exceptions.RequestException`.
- Do not change any other logic, field names, or return values.

---

## Change 2 — pyproject.toml

Add `httpx>=0.25` to the base dependencies list (it is already installed but not declared):

```toml
dependencies = [
    ...
    "httpx>=0.25",
    "requests>=2.31",   # keep — other modules still use requests
    ...
]
```

---

## Change 3 — tests/test_lookup.py (update four discogs tests only)

The four discogs tests monkeypatch `discogs_mod.requests.get`. Update them to monkeypatch
`discogs_mod.httpx.get` instead. Touch ONLY the four discogs test functions:
`test_discogs_skips_without_credentials`, `test_discogs_hit`, `test_discogs_miss_returns_none`,
`test_discogs_request_error_returns_none`.

Pattern:
```python
# Before
monkeypatch.setattr(discogs_mod.requests, "get", lambda *a, **k: _Resp(...))

# After
monkeypatch.setattr(discogs_mod.httpx, "get", lambda *a, **k: _Resp(...))
```

For `test_discogs_request_error_returns_none`, replace the raised exception:
```python
# Before
requests.exceptions.RequestException("timeout")

# After
httpx.ConnectError("timeout")   # subclass of httpx.HTTPError; no extra args required
```

Also add `import httpx` near the top of the test file, alongside the existing `import requests`
import (keep the `requests` import — other tests in the same file use it).

---

## Files you may modify

- `src/tgw/apis/lookup/discogs.py`
- `tests/test_lookup.py` (four discogs test functions only)
- `pyproject.toml` (add httpx to base deps — do not change anything else)

---

## Requirements

1. Adapter surface unchanged: `lookup(barcode: str, cfg: Dict[str, Any]) -> Optional[LookupResult]`
2. No `import requests` remains in `discogs.py`
3. All four discogs tests pass: `pytest -q tests/test_lookup.py -k discogs`
4. Full test suite passes: `pytest -q`
5. `pyproject.toml` lists `httpx>=0.25` in base dependencies

---

## Do NOT touch

- Config files, secrets, or eBay OAuth scopes
- Any other lookup adapter (`go_upc.py`, `igdb.py`, `upcitemdb.py`, etc.)
- `src/tgw/apis/lookup/dispatcher.py`
- Any test function in `test_lookup.py` other than the four discogs tests
- The `requests>=2.31` dependency in `pyproject.toml` (other code uses it)
- Any file not listed above

If a requirement is impossible as specified, stop and explain instead of improvising.
