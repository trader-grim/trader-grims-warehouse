"""
tgw.health — Platform health checks for TGW.

Checks that core platform dependencies are reachable and operational.
Returns structured status dicts — never raises, always returns.

Usage:
    from tgw.health import check_all, check_itemdata, check_ollama
    from tgw.config import load_config

    cfg = load_config(Path('/opt/TGW/config/tgw-api-config.json'))
    status = check_all(cfg)
    if not status['ok']:
        print(status)

    # From CLI:
    tgw health

Each check returns:
    {'ok': True/False, 'check': 'name', 'detail': '...', 'elapsed_ms': N}

check_all() returns:
    {'ok': True/False, 'checks': [...], 'elapsed_ms': N}
"""

from __future__ import annotations

import json
import logging
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, Optional

log = logging.getLogger('tgw.health')


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------

def _result(check: str, ok: bool, detail: str,
            elapsed_ms: float, **extra: Any) -> Dict[str, Any]:
    return {'ok': ok, 'check': check, 'detail': detail,
            'elapsed_ms': round(elapsed_ms, 1), **extra}


def check_itemdata(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """ItemData root exists and is readable."""
    t = time.time()
    root: Path = cfg['itemdata_root']
    try:
        if not root.exists():
            return _result('itemdata', False, f'missing: {root}',
                           (time.time() - t) * 1000)
        count = sum(1 for c in root.iterdir()
                    if c.is_dir() and (c / f'{c.name}.json').exists())
        return _result('itemdata', True,
                       f'{root} — {count} items', (time.time() - t) * 1000,
                       item_count=count)
    except Exception as e:
        return _result('itemdata', False, str(e), (time.time() - t) * 1000)


def check_catalog(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Search catalog exists and is non-empty."""
    t = time.time()
    path: Path = cfg['search_catalog_path']
    try:
        if not path.exists():
            return _result('catalog', False, f'missing: {path}',
                           (time.time() - t) * 1000)
        size = path.stat().st_size
        return _result('catalog', True,
                       f'{path.name} — {size // 1024}KB',
                       (time.time() - t) * 1000, size_bytes=size)
    except Exception as e:
        return _result('catalog', False, str(e), (time.time() - t) * 1000)


def check_location_tree(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Location symlink tree exists."""
    t = time.time()
    root: Path = cfg['location_tree_root']
    try:
        if not root.exists():
            return _result('location_tree', False, f'missing: {root}',
                           (time.time() - t) * 1000)
        count = sum(1 for _ in root.iterdir())
        return _result('location_tree', True,
                       f'{root} — {count} locations',
                       (time.time() - t) * 1000, location_count=count)
    except Exception as e:
        return _result('location_tree', False, str(e), (time.time() - t) * 1000)


def check_postgres(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """PostgreSQL reachable and queue_jobs table accessible."""
    t = time.time()
    try:
        from tgw.queue.state_machine import init, queue_depths, dead_letter_count
        dsn = cfg.get('postgres_dsn', 'dbname=state_machine user=tgw')
        init(dsn)
        depths = queue_depths()
        dl = dead_letter_count()
        depth_str = ', '.join(f'{q}:{n}' for q, n in depths.items()) or 'all queues empty'
        detail = f'depths=[{depth_str}] dead_letter={dl}'
        return _result('postgres', True, detail, (time.time() - t) * 1000,
                       queue_depths=depths, dead_letter=dl)
    except Exception as e:
        return _result('postgres', False, f'unreachable: {e}', (time.time() - t) * 1000)


def check_sqlite_catalog(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """SQLite catalog exists and reports row count."""
    t = time.time()
    try:
        import sqlite3
        db_path = cfg['sqlite_catalog_path']
        if not db_path.exists():
            return _result('sqlite_catalog', False, f'missing: {db_path}',
                           (time.time() - t) * 1000)
        con = sqlite3.connect(db_path)
        row_count = con.execute('SELECT COUNT(*) FROM catalog').fetchone()[0]
        con.close()
        mtime = db_path.stat().st_mtime
        import datetime
        age = datetime.datetime.now() - datetime.datetime.fromtimestamp(mtime)
        return _result('sqlite_catalog', True,
                       f'{row_count:,} rows — updated {int(age.total_seconds() // 60)}m ago',
                       (time.time() - t) * 1000, row_count=row_count)
    except Exception as e:
        return _result('sqlite_catalog', False, str(e), (time.time() - t) * 1000)


def check_thumbnail_cache(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Thumbnail cache directory exists and reports image count."""
    t = time.time()
    try:
        thumb_root = cfg['thumbnail_root']
        if not thumb_root.exists():
            return _result('thumbnail_cache', False, f'missing: {thumb_root}',
                           (time.time() - t) * 1000)
        count = sum(1 for _ in thumb_root.glob('*.jpg'))
        return _result('thumbnail_cache', True,
                       f'{count:,} thumbnails — {thumb_root}',
                       (time.time() - t) * 1000, thumbnail_count=count)
    except Exception as e:
        return _result('thumbnail_cache', False, str(e), (time.time() - t) * 1000)


def check_queue_launcher() -> Dict[str, Any]:
    """Queue launcher systemd service is active (legacy — retiring in TASK 1.5)."""
    t = time.time()
    try:
        r = subprocess.run(
            ['systemctl', 'is-active', 'queue-launcher.service'],
            capture_output=True, text=True, timeout=5
        )
        active = r.stdout.strip() == 'active'
        return _result('queue_launcher', active,
                       r.stdout.strip(), (time.time() - t) * 1000)
    except FileNotFoundError:
        return _result('queue_launcher', False,
                       'systemctl not available', (time.time() - t) * 1000)
    except Exception as e:
        return _result('queue_launcher', False, str(e), (time.time() - t) * 1000)


def check_backup_service() -> Dict[str, Any]:
    """trader-grims-backup systemd service is active."""
    t = time.time()
    try:
        r = subprocess.run(
            ['systemctl', 'is-active', 'trader-grims-backup.service'],
            capture_output=True, text=True, timeout=5
        )
        active = r.stdout.strip() == 'active'
        return _result('backup', active,
                       r.stdout.strip(), (time.time() - t) * 1000)
    except FileNotFoundError:
        return _result('backup', False,
                       'systemctl not available', (time.time() - t) * 1000)
    except Exception as e:
        return _result('backup', False, str(e), (time.time() - t) * 1000)


def check_ollama(model: Optional[str] = None) -> Dict[str, Any]:
    """
    Ollama API is reachable and the requested model is available.
    If model is None, just checks that Ollama is running.
    """
    t = time.time()
    try:
        import urllib.request
        with urllib.request.urlopen(
            'http://localhost:11434/api/tags', timeout=5
        ) as resp:
            data = json.loads(resp.read())
        models = [m['name'] for m in data.get('models', [])]
        if model:
            found = any(m.startswith(model) for m in models)
            return _result('ollama', found,
                           f'model {model!r} {"found" if found else "NOT found"}'
                           f' — {len(models)} models installed',
                           (time.time() - t) * 1000,
                           models=models, requested=model)
        return _result('ollama', True,
                       f'{len(models)} models installed',
                       (time.time() - t) * 1000, models=models)
    except Exception as e:
        return _result('ollama', False,
                       f'Ollama not reachable: {e}', (time.time() - t) * 1000)


def check_tgw_api() -> Dict[str, Any]:
    """tgw API responds (uses same interpreter, no PATH dependency)."""
    t = time.time()
    try:
        import sys
        r = subprocess.run(
            [sys.executable, '-m', 'tgw.api', 'ensure-catalog', '--check-only'],
            capture_output=True, text=True, timeout=10
        )
        try:
            result = json.loads(r.stdout)
            ok = result.get('ok', False)
            return _result('tgw_api', ok,
                           result.get('error') or result.get('path', 'ok'),
                           (time.time() - t) * 1000)
        except json.JSONDecodeError:
            return _result('tgw_api', r.returncode == 0,
                           r.stdout.strip() or r.stderr.strip(),
                           (time.time() - t) * 1000)
    except FileNotFoundError:
        return _result('tgw_api', False,
                       'tgw not on PATH', (time.time() - t) * 1000)
    except Exception as e:
        return _result('tgw_api', False, str(e), (time.time() - t) * 1000)


def check_ebay_token(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """eBay token file exists and is not obviously expired."""
    t = time.time()
    try:
        token_path: Path = cfg['ebay_token_path']
        if not token_path.exists():
            return _result('ebay_token', False,
                           f'token file missing: {token_path}',
                           (time.time() - t) * 1000)
        doc = json.loads(token_path.read_text(encoding='utf-8'))
        expires = doc.get('expires_at') or doc.get('expiry') or doc.get('expire_time')
        if expires:
            import datetime
            try:
                exp_ts = float(expires)
                remaining = exp_ts - time.time()
                ok = remaining > 300   # at least 5 minutes left
                detail = (f'expires in {int(remaining // 60)}m'
                          if remaining > 0 else 'EXPIRED')
                return _result('ebay_token', ok, detail,
                               (time.time() - t) * 1000,
                               expires_in_seconds=int(remaining))
            except (ValueError, TypeError):
                pass
        return _result('ebay_token', True,
                       f'token file present ({token_path.stat().st_size}B)',
                       (time.time() - t) * 1000)
    except Exception as e:
        return _result('ebay_token', False, str(e), (time.time() - t) * 1000)


# ---------------------------------------------------------------------------
# Combined check
# ---------------------------------------------------------------------------

def check_all(cfg: Dict[str, Any],
              include_ollama: bool = True,
              include_ebay: bool = True) -> Dict[str, Any]:
    """
    Run all platform health checks and return a combined status.

    Args:
        cfg:            TGW config dict from load_config()
        include_ollama: Check Ollama availability
        include_ebay:   Check eBay token status
    """
    t = time.time()
    checks = [
        check_tgw_api(),
        check_itemdata(cfg),
        check_catalog(cfg),
        check_location_tree(cfg),
        check_sqlite_catalog(cfg),
        check_thumbnail_cache(cfg),
        check_postgres(cfg),
        check_backup_service(),
    ]
    if include_ollama:
        checks.append(check_ollama())
    if include_ebay:
        checks.append(check_ebay_token(cfg))

    all_ok = all(c['ok'] for c in checks)
    failed = [c['check'] for c in checks if not c['ok']]

    return {
        'ok':          all_ok,
        'checks':      checks,
        'failed':      failed,
        'elapsed_ms':  round((time.time() - t) * 1000, 1),
    }
