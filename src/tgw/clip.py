"""
tgw.clip — TGW-aware clipboard history store + query CLI (PP-CLIP-001).

This is the durable, headless half of the clipboard manager: a SQLite store, a
SKU classifier, and query functions exposed as `tgw clip {list,last-sku,search,
wipe}`. The X11/XFixes capture daemon, Unix socket, Qtile widget, and rofi menu
are a later phase (they need a live desktop session to verify) and will simply
call record_clip() into this same store.

Why it matters: today both the Qtile SKU widget and the clipboard action only
see the CURRENT clipboard, so a SKU is lost the moment anything else is copied.
A persisted last-sku query lets macro/chord actions work after the clipboard
changes.

DB: ~/.local/share/tgw-clip/history.db
"""
from __future__ import annotations

import math
import re
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional

# Canonical TGW SKU: tgw + 15 digits (18 chars). Matches the pattern used by
# the Qtile widgets and api.py clipboard action.
_SKU_RE = re.compile(r'^tgw(?:\d{15}|\d{17})$')  # 15 = legacy (no ms), 17 = current (with ms)

_RETENTION = 2000  # keep at most this many rows; prune oldest on insert
_RETENTION_DAYS = 14  # prune rows older than this many days on insert

# ---------------------------------------------------------------------------
# Secret-shaped content heuristic (todo #1565/PP-CLIP-001) — best-effort,
# defense-in-depth ONLY. This is not a security boundary: it cannot catch
# every secret shape (multi-word passphrases, secrets embedded in longer
# text, unusual/custom token formats). It exists to keep the *common* case —
# a bare API key or token copied verbatim — out of the persistent history,
# not to guarantee no secret ever lands there.
# ---------------------------------------------------------------------------

# Well-known provider key/token prefixes. Not exhaustive by design (see
# module docstring above) — a short, maintainable allowlist.
_SECRET_PREFIXES = (
    'sk-ant-',      # Anthropic
    'sk-',          # OpenAI and others
    'ghp_',         # GitHub personal access token
    'gho_',         # GitHub OAuth token
    'github_pat_',  # GitHub fine-grained PAT
    'AIza',         # Google API key
    'xoxb-',        # Slack bot token
    'xoxp-',        # Slack user token
    'xoxa-',        # Slack app token
    'xoxs-',        # Slack workspace token
    'AKIA',         # AWS access key id
    'ASIA',         # AWS temporary access key id
    'glpat-',       # GitLab personal access token
)

# A single-line token with no whitespace, 20-200 chars, drawn from a
# base64/hex-ish alphabet — the shape most raw secrets take when copied
# verbatim (as opposed to prose, URLs, or SKUs which use other characters).
_TOKEN_SHAPE_RE = re.compile(r'^[A-Za-z0-9+/=_.-]{20,200}$')

# Shannon entropy threshold (bits/char) above which a token-shaped string is
# treated as secret-like rather than ordinary text/identifiers. 4.0 is a
# starting point per the spec — tune here if it's over/under-triggering.
_ENTROPY_THRESHOLD = 4.0


def _shannon_entropy(s: str) -> float:
    """Shannon entropy in bits/char of string *s*. Empty string -> 0.0."""
    if not s:
        return 0.0
    length = len(s)
    counts = Counter(s)
    return -sum((n / length) * math.log2(n / length) for n in counts.values())


def looks_like_secret(content: str) -> bool:
    """Best-effort heuristic: True if *content* looks like an API key/token/secret.

    Rules (in order):
      1. Never flags a classified TGW SKU (see classify_sku), even though
         SKUs are long alphanumeric strings that would otherwise resemble a
         token.
      2. Multi-line or content containing whitespace is never flagged by
         this heuristic — it only targets a single bare token copied
         verbatim, not a token embedded within prose.
      3. Known provider key/token prefixes (see _SECRET_PREFIXES) are always
         flagged.
      4. Otherwise, a single-line 20-200 char token drawn from a
         base64/hex-ish alphabet is flagged if its Shannon entropy exceeds
         _ENTROPY_THRESHOLD bits/char.

    Defense-in-depth only — see module comment above. Not a security
    boundary Dave should rely on exclusively.
    """
    s = (content or '').strip()
    if not s:
        return False
    if classify_sku(s):
        return False
    if '\n' in s or ' ' in s or '\t' in s:
        return False
    for prefix in _SECRET_PREFIXES:
        if s.startswith(prefix):
            return True
    if _TOKEN_SHAPE_RE.match(s) and _shannon_entropy(s) > _ENTROPY_THRESHOLD:
        return True
    return False


def _default_db_path() -> Path:
    return Path.home() / '.local' / 'share' / 'tgw-clip' / 'history.db'


def _connect(db_path: Optional[Path] = None) -> sqlite3.Connection:
    path = Path(db_path) if db_path else _default_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    con = sqlite3.connect(str(path))
    path.chmod(0o600)
    con.row_factory = sqlite3.Row
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS clip_history (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            content     TEXT NOT NULL,
            selection   TEXT NOT NULL DEFAULT 'clipboard',
            is_sku      INTEGER NOT NULL DEFAULT 0,
            sku         TEXT,
            captured_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    con.execute('CREATE INDEX IF NOT EXISTS idx_sku ON clip_history(sku)')
    con.execute('CREATE INDEX IF NOT EXISTS idx_captured ON clip_history(captured_at)')
    con.commit()
    return con


def classify_sku(content: str) -> str:
    """Return the SKU if content is exactly a TGW SKU, else ''."""
    s = (content or '').strip()
    return s if _SKU_RE.match(s) else ''


def record_clip(content: str, selection: str = 'clipboard',
                db_path: Optional[Path] = None) -> Dict[str, Any]:
    """Record a clipboard event. Classifies SKUs. Returns the stored row summary."""
    content = content or ''
    sku = classify_sku(content)
    con = _connect(db_path)
    try:
        cur = con.execute(
            'INSERT INTO clip_history (content, selection, is_sku, sku) '
            'VALUES (?, ?, ?, ?)',
            (content, selection, 1 if sku else 0, sku or None),
        )
        rowid = cur.lastrowid
        # Retention: prune oldest beyond the cap.
        con.execute(
            'DELETE FROM clip_history WHERE id NOT IN '
            '(SELECT id FROM clip_history ORDER BY id DESC LIMIT ?)',
            (_RETENTION,),
        )
        # Retention: prune rows older than the TTL.
        con.execute(
            "DELETE FROM clip_history WHERE captured_at < datetime('now', ?)",
            (f'-{_RETENTION_DAYS} days',),
        )
        con.commit()
        return {'ok': True, 'id': rowid, 'is_sku': bool(sku), 'sku': sku or None}
    finally:
        con.close()


def list_history(limit: int = 20, sku_only: bool = False,
                 db_path: Optional[Path] = None) -> List[Dict[str, Any]]:
    con = _connect(db_path)
    try:
        sql = 'SELECT id, content, selection, is_sku, sku, captured_at FROM clip_history'
        if sku_only:
            sql += ' WHERE is_sku = 1'
        sql += ' ORDER BY id DESC LIMIT ?'
        return [dict(r) for r in con.execute(sql, (limit,)).fetchall()]
    finally:
        con.close()


def last_sku(db_path: Optional[Path] = None) -> Optional[str]:
    """Most recently captured SKU, regardless of later non-SKU clips."""
    con = _connect(db_path)
    try:
        row = con.execute(
            'SELECT sku FROM clip_history WHERE is_sku = 1 ORDER BY id DESC LIMIT 1'
        ).fetchone()
        return row['sku'] if row else None
    finally:
        con.close()


def search(pattern: str, limit: int = 20,
           db_path: Optional[Path] = None) -> List[Dict[str, Any]]:
    con = _connect(db_path)
    try:
        rows = con.execute(
            'SELECT id, content, selection, is_sku, sku, captured_at '
            'FROM clip_history WHERE content LIKE ? ORDER BY id DESC LIMIT ?',
            (f'%{pattern}%', limit),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        con.close()


def wipe_nonsku(db_path: Optional[Path] = None) -> int:
    """Delete all non-SKU history rows (keeps SKU rows). Returns count deleted."""
    con = _connect(db_path)
    try:
        cur = con.execute('DELETE FROM clip_history WHERE is_sku = 0')
        con.commit()
        return cur.rowcount
    finally:
        con.close()


def cmd_clip(action: str, *, pattern: str = '', limit: int = 20,
             sku_only: bool = False, clip_id: Optional[int] = None,
             copy: bool = False, db_path: Optional[Path] = None) -> Dict[str, Any]:
    """CLI handler for `tgw clip {list,last-sku,search,wipe,get}`."""
    if action == 'last-sku':
        sku = last_sku(db_path)
        if sku:
            print(sku)
        return {'ok': True, 'sku': sku}

    if action == 'list':
        rows = list_history(limit=limit, sku_only=sku_only, db_path=db_path)
        for r in rows:
            tag = 'SKU' if r['is_sku'] else '   '
            print(f'{r["captured_at"]}  [{tag}]  {r["content"][:80]}')
        return {'ok': True, 'count': len(rows), 'rows': rows}

    if action == 'search':
        rows = search(pattern, limit=limit, db_path=db_path)
        for r in rows:
            print(f'{r["captured_at"]}  {r["content"][:80]}')
        return {'ok': True, 'count': len(rows), 'rows': rows}

    if action == 'wipe':
        n = wipe_nonsku(db_path)
        print(f'wiped {n} non-SKU clip(s)')
        return {'ok': True, 'wiped': n}

    if action == 'get':
        if clip_id is None:
            return {'ok': False, 'error': 'get requires --id <ID>'}
        con = _connect(db_path)
        try:
            row = con.execute(
                'SELECT id, content, selection, is_sku, sku, captured_at FROM clip_history WHERE id = ?',
                (clip_id,),
            ).fetchone()
        finally:
            con.close()
        if row is None:
            return {'ok': False, 'error': f'no clip entry with id {clip_id}'}
        content = row['content']
        print(content)
        if copy:
            import subprocess as _sp
            # Try wl-copy first (Wayland), fall back to xclip
            for cmd in (['wl-copy'], ['xclip', '-selection', 'clipboard']):
                try:
                    r = _sp.run(cmd, input=content, text=True, timeout=3)
                    if r.returncode == 0:
                        break
                except (FileNotFoundError, _sp.TimeoutExpired):
                    pass
        return {'ok': True, 'id': row['id'], 'content': content,
                'is_sku': bool(row['is_sku']), 'captured_at': row['captured_at']}

    return {'ok': False, 'error': f'unknown clip action: {action!r}'}
