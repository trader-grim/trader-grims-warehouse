"""Shared atomic-write helper for the eBay OAuth token file.

Deliberately dependency-free (stdlib only) — get_access_token.py and
refresh_access_token.py are the OAuth recovery path and must keep working
even if something else in the tgw package is broken.

audit#1143 #1162+#1177: both save_token_state() functions used to carry
identical tmp+rename+chmod(0o600) logic inline; a fix to one was likely to
be missed in the other. Factored out here instead of reusing
items.atomic_write_text() — that helper preserves/defaults the target's
existing file mode (0o660, group-writable), which is wrong for a secret:
this file must always be 0600, never group-writable.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any, Mapping

TOKEN_ENVIRONMENT_KEY = "_tgw_ebay_environment"
_TOKEN_ENVIRONMENTS = frozenset({"production", "sandbox"})


def _closed_token_environment(environment: Any) -> str:
    if not isinstance(environment, str) or environment not in _TOKEN_ENVIRONMENTS:
        raise ValueError(
            "token environment must be exactly 'production' or 'sandbox'"
        )
    return environment


def validate_token_environment(
    state: Mapping[str, Any],
    expected_environment: str,
    *,
    allow_legacy_production: bool = True,
) -> None:
    """Reject token state that is not bound to the selected environment.

    Token files created before environment isolation have no marker and are
    production state by definition.  That one compatibility case remains
    readable; sandbox token state is never accepted without an explicit stamp.
    """
    expected = _closed_token_environment(expected_environment)
    marker = state.get(TOKEN_ENVIRONMENT_KEY)
    if marker is None:
        if allow_legacy_production and expected == "production":
            return
        raise ValueError(
            f"{expected} eBay token state has no environment marker"
        )
    if marker != expected:
        raise ValueError(
            f"eBay token environment {marker!r} does not match "
            f"selected environment {expected!r}"
        )


def stamp_token_environment(
    state: Mapping[str, Any],
    environment: str,
) -> dict[str, Any]:
    """Copy token state and persist its closed environment marker."""
    selected = _closed_token_environment(environment)
    marker = state.get(TOKEN_ENVIRONMENT_KEY)
    if marker is not None and marker != selected:
        raise ValueError(
            f"eBay token environment {marker!r} does not match "
            f"selected environment {selected!r}"
        )
    persisted = dict(state)
    persisted[TOKEN_ENVIRONMENT_KEY] = selected
    return persisted


def atomic_write_token_json(path: Path, text: str) -> None:
    """Write the token file atomically via tmp+rename, always chmod 0600."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        'w', encoding='utf-8', delete=False, dir=path.parent
    ) as tmp:
        tmp.write(text)
        tmp_path = Path(tmp.name)
    tmp_path.chmod(0o600)
    tmp_path.replace(path)
