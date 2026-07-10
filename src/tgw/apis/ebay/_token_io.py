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
