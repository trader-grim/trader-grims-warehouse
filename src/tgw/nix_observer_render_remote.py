"""Fixed no-argument bootstrap used by the privileged render wrapper.

The wrapper pins this file and the audited helper on held descriptors, creates
the isolated network namespace, drops to the configured unprivileged identity,
and only then executes this program.  No request or environment value can
select Python source.
"""

from __future__ import annotations

import hashlib
import os
import re
import sys
from typing import NoReturn

_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")


def _refuse(message: str) -> NoReturn:
    sys.stderr.write(f"tgw-render-remote: {message}\n")
    raise SystemExit(125)


def main() -> int:
    if len(sys.argv) != 1:
        _refuse("arguments forbidden")
    helper_fd_raw = os.environ.get("TGW_RENDER_HELPER_FD", "")
    expected = os.environ.get("TGW_RENDER_HELPER_SHA256", "")
    if not helper_fd_raw.isascii() or not helper_fd_raw.isdecimal() or not _DIGEST.fullmatch(expected):
        _refuse("held helper binding absent")
    helper_fd = int(helper_fd_raw)
    if helper_fd < 3:
        _refuse("held helper descriptor invalid")
    try:
        os.lseek(helper_fd, 0, os.SEEK_SET)
        raw = bytearray()
        while block := os.read(helper_fd, 1024 * 1024):
            raw.extend(block)
            if len(raw) > 2 * 1024 * 1024:
                _refuse("held helper oversized")
        os.lseek(helper_fd, 0, os.SEEK_SET)
    except OSError:
        _refuse("held helper unreadable")
    if "sha256:" + hashlib.sha256(raw).hexdigest() != expected:
        _refuse("held helper identity mismatch")
    namespace: dict[str, object] = {
        "__name__": "tgw.nix_observer_render_helper_held",
        "_BOOTSTRAP_DEFER_MAIN": True,
    }
    try:
        exec(compile(bytes(raw), "<held-nix-observer-render-helper>", "exec"), namespace)
        bootstrap = namespace["BOOTSTRAP"]
        if not isinstance(bootstrap, str):
            _refuse("held helper bootstrap missing")
        exec(compile(bootstrap, "<held-nix-observer-render-bootstrap>", "exec"), {})
    except SystemExit:
        raise
    except BaseException:
        _refuse("held helper bootstrap failed")
    return 70


if __name__ == "__main__":
    raise SystemExit(main())
