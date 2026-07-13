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
import pwd
import stat
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


def check_ownership(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """
    Read-only UID / ownership / permission audit (PP-DEPLOY-001).

    Reports the tgw UID (the migration target is < 1000, the system/user boundary)
    and spot-checks key roots + secrets for owner/mode drift. Diagnoses only —
    never mutates, never walks all of ItemData (roots are stat'd, not recursed).

    ok reflects actual ownership/mode drift (operational risk). The UID-below-1000
    status is an informational migration-planning signal surfaced in detail/extra,
    not a failure — so health stays green on an operationally-sound pre-migration
    system while still flagging the UID for the eventual usermod+chown sweep.
    """
    t = time.time()
    try:
        pw = pwd.getpwnam('tgw')
    except KeyError:
        return _result('ownership', False, "user 'tgw' not found",
                       (time.time() - t) * 1000)

    tgw_uid = pw.pw_uid
    below_boundary = tgw_uid < 1000
    drift: list[str] = []

    # Key roots should be owned by tgw (stat the root only — no recursion).
    for key in ('itemdata_root', 'catalog_root', 'secrets_root', 'data_root'):
        p = cfg.get(key)
        if not p:
            continue
        root = Path(p)
        try:
            if root.exists() and root.stat().st_uid != tgw_uid:
                drift.append(f'{root} owned by uid {root.stat().st_uid} (expected {tgw_uid})')
        except Exception as e:
            drift.append(f'{root}: {e}')

    # Secrets: dir 700, files 600.
    sroot = cfg.get('secrets_root')
    if sroot:
        sroot = Path(sroot)
        try:
            if sroot.exists():
                # Check the real security property — no group/other access —
                # rather than an exact mode, so a benign setgid bit (0o2700)
                # or owner-mode nuance doesn't read as drift.
                dmode = stat.S_IMODE(sroot.stat().st_mode)
                if dmode & 0o077:
                    drift.append(f'{sroot} mode {oct(dmode)} is group/other-accessible (expected 0o700)')
                for f in sroot.iterdir():
                    if f.is_file():
                        fmode = stat.S_IMODE(f.stat().st_mode)
                        if fmode & 0o077:
                            drift.append(f'{f.name} mode {oct(fmode)} is group/other-accessible (expected 0o600)')
        except Exception as e:
            drift.append(f'{sroot}: {e}')

    ok = not drift
    uid_note = f'tgw uid={tgw_uid}'
    if not below_boundary:
        uid_note += ' (>=1000; PP-DEPLOY-001 migration pending)'
    detail = uid_note if ok else f'{uid_note}; {len(drift)} drift: ' + '; '.join(drift[:5])
    return _result('ownership', ok, detail, (time.time() - t) * 1000,
                   uid=tgw_uid, uid_below_1000=below_boundary, drift=drift)


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


def classify_dead_letter_errors(rows: list) -> Dict[str, Dict[str, int]]:
    """Split dead-letter rows ({queue_name, error_detail}) into per-queue
    TRANSIENT vs HARD_FAILURE counts via worker_base.classify_dead_letter."""
    from tgw.queue.worker_base import classify_dead_letter
    out: Dict[str, Dict[str, int]] = {}
    for row in rows:
        verdict, _ = classify_dead_letter(row.get('error_detail') or '')
        bucket = 'transient' if verdict == 'requeue' else 'hard'
        q = out.setdefault(row['queue_name'], {'transient': 0, 'hard': 0})
        q[bucket] += 1
    return out


def check_postgres(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """PostgreSQL reachable and queue_jobs table accessible.

    Dead-letter counts are split TRANSIENT (requeue-able noise, T) vs
    HARD_FAILURE (real signal, H). Adds the PP-DEADLETTER-001 zero-work
    watchdog: a live worker with eligible jobs waiting > zero_work_stall_hours
    and zero completions in that window is a stalled pipeline, not an idle one.
    """
    t = time.time()
    try:
        from tgw.queue.state_machine import (
            dead_letter_count,
            dead_letter_errors,
            init,
            queue_depths,
            zero_work_queues,
        )
        dsn = cfg.get('postgres_dsn', 'dbname=state_machine user=tgw')
        init(dsn)
        depths = queue_depths()
        dl = dead_letter_count()
        dl_classified = classify_dead_letter_errors(dead_letter_errors())
        dl_by_queue = {q: c['transient'] + c['hard'] for q, c in dl_classified.items()}
        dl_transient = sum(c['transient'] for c in dl_classified.values())
        dl_hard = sum(c['hard'] for c in dl_classified.values())

        depth_str = ', '.join(f'{q}:{n}' for q, n in depths.items()) or 'all queues empty'
        if dl_classified:
            dl_str = ', '.join(
                f'{q}:{c["transient"] + c["hard"]}(T{c["transient"]}/H{c["hard"]})'
                for q, c in sorted(dl_classified.items(),
                                   key=lambda kv: -(kv[1]['transient'] + kv[1]['hard']))
            )
            detail = f'depths=[{depth_str}] dead_letter={dl} T{dl_transient}/H{dl_hard} [{dl_str}]'
        else:
            detail = f'depths=[{depth_str}] dead_letter=0'

        warnings: list[str] = []
        stall_hours = float(cfg.get('zero_work_stall_hours', 4.0))
        for stalled in zero_work_queues(stall_hours):
            warnings.append(
                f'zero-work stall: {stalled["queue_name"]} — worker alive, '
                f'{stalled["waiting"]} job(s) waiting {stalled["oldest_wait_h"]}h, '
                f'0 completions in {stall_hours:g}h'
            )
        if warnings:
            detail += '; ' + '; '.join(f'WARN: {w}' for w in warnings)

        result = _result('postgres', True, detail, (time.time() - t) * 1000,
                         queue_depths=depths, dead_letter=dl,
                         dead_letter_by_queue=dl_by_queue,
                         dead_letter_transient=dl_transient,
                         dead_letter_hard=dl_hard,
                         dead_letter_classified=dl_classified,
                         warnings=warnings)
        if warnings:
            result['warn'] = True
        return result
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
        age_s = time.time() - mtime
        return _result('sqlite_catalog', True,
                       f'{row_count:,} rows — updated {int(age_s // 60)}m ago',
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


def check_backups(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """
    PP-BACKUP-001 A4 — backup freshness check (the watcher for the watchers).

    Red (ok=False): db dump >26 h old, or rclone success stamp >26 h old.
    Yellow (ok=True, warn=True): snapshot tree newest file >1 h, or secrets bundle >40 d.
    """
    t = time.time()
    now = time.time()
    issues: list[str] = []
    warnings: list[str] = []

    def _age_h(path: Path) -> Optional[float]:
        try:
            return (now - path.stat().st_mtime) / 3600
        except Exception:
            return None

    # 1. DB dump age — red if >26 h or no dump found
    db_dir: Path = cfg.get('backup_db_dir', Path('/opt/TGW/var/backups/trader_grims_warehouse/db'))
    dumps = sorted(db_dir.glob('*.dump')) if db_dir.exists() else []
    if not dumps:
        issues.append('no db dump found in ' + str(db_dir))
    else:
        age_h = _age_h(dumps[-1])
        if age_h is not None and age_h > 26:
            issues.append(f'db dump stale: {int(age_h)}h old (limit 26h)')

    # 2. rclone cloud sync stamp — red if >26 h or stamp absent
    rclone_stamp: Path = cfg.get('backup_rclone_stamp',
                                  Path('/opt/TGW/var/log/rclone-sync-last-success'))
    if not rclone_stamp.exists():
        issues.append('rclone sync never completed (stamp absent): ' + str(rclone_stamp))
    else:
        age_h = _age_h(rclone_stamp)
        if age_h is not None and age_h > 26:
            issues.append(f'rclone sync stale: {int(age_h)}h old (limit 26h)')

    # 3. Snapshot tree newest entry — yellow if >1 h
    # Check directory mtimes at depth ≤2 only (snapshot dirs are timestamp-named children
    # of subtree dirs; scanning files inside would walk 200 G of hardlinks unnecessarily).
    snap_root: Path = cfg.get('backup_snapshot_root',
                               Path('/opt/TGW/var/local/backups/trader_grims_warehouse'))
    if snap_root.exists():
        try:
            mtimes = [
                child.stat().st_mtime
                for subtree in snap_root.iterdir() if subtree.is_dir()
                for child in subtree.iterdir()
            ]
        except Exception:
            mtimes = []
        if not mtimes:
            warnings.append(f'snapshot tree empty: {snap_root}')
        else:
            age_h = (now - max(mtimes)) / 3600
            if age_h > 1:
                warnings.append(f'snapshot tree stale: {age_h:.1f}h since last entry (limit 1h)')
    else:
        warnings.append(f'snapshot root missing: {snap_root}')

    # 4. Secrets bundle age — yellow if >40 days or no bundle
    # Accepts both .age (current) and .gpg (pre-A3 migration) extensions.
    secrets_dir: Path = cfg.get('backup_secrets_dir',
                                  Path('/opt/TGW/var/local/backups/trader_grims_warehouse/secrets'))
    bundles: list[Path] = []
    if secrets_dir.exists():
        bundles = sorted(
            list(secrets_dir.glob('secrets-*.tar.gz.age')) +
            list(secrets_dir.glob('secrets-*.tar.gz.gpg'))
        )
    if not bundles:
        warnings.append('no encrypted secrets bundle found in ' + str(secrets_dir))
    else:
        age_days = (now - bundles[-1].stat().st_mtime) / 86400
        if age_days > 40:
            warnings.append(f'secrets bundle stale: {int(age_days)}d old (limit 40d)')

    ok = not issues
    parts: list[str] = []
    if issues:
        parts.extend(issues)
    if warnings:
        parts.extend(f'WARN: {w}' for w in warnings)
    detail = '; '.join(parts) if parts else 'all backup tiers fresh'

    result = _result('backups', ok, detail, (time.time() - t) * 1000,
                     issues=issues, warnings=warnings)
    if warnings:
        result['warn'] = True
    return result


def check_taskboard(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """
    PP-PLANDB-001 Phase 2 — generated taskboard freshness.

    Yellow (ok=True, warn=True): TGW-Taskboard.md missing, or older than the
    newest todo mutation by >10 min (the coalesced plan_render job has a 30s
    delay; 10 min covers worker restarts without flapping).
    Never red — a stale taskboard is an annoyance, not an outage.
    """
    t = time.time()
    warnings: list[str] = []

    from tgw.plan_render import taskboard_path
    board = taskboard_path(cfg)
    if not board.exists():
        warnings.append(f'taskboard missing: {board} — run `tgw plan render`')
    else:
        rendered = board.stat().st_mtime
        try:
            import psycopg2
            con = psycopg2.connect(cfg.get('postgres_dsn', 'dbname=state_machine user=tgw'))
            try:
                with con.cursor() as cur:
                    cur.execute(
                        'SELECT EXTRACT(EPOCH FROM GREATEST(max(added_at), max(done_at))) '
                        'FROM todo_items'
                    )
                    row = cur.fetchone()
            finally:
                con.close()
            last_mutation = float(row[0]) if row and row[0] is not None else None
            if last_mutation is not None and last_mutation - rendered > 600:
                lag_min = int((last_mutation - rendered) / 60)
                warnings.append(
                    f'taskboard stale: todo tracker changed {lag_min}min after last render '
                    f'— check tgw-worker@plan_render.service'
                )
        except Exception:
            pass  # DB down is check_postgres's problem, not the taskboard's

    detail = '; '.join(f'WARN: {w}' for w in warnings) if warnings else (
        f'taskboard fresh ({board.name})')
    result = _result('taskboard', True, detail, (time.time() - t) * 1000,
                     warnings=warnings)
    if warnings:
        result['warn'] = True
    return result



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


def check_sync_conflicts(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Count unresolved Syncthing sync-conflict files across configured scan roots.

    Surfaces the count without resolving; the sync_conflict worker resolves them.
    ok=True when no conflicts remain; ok=False signals pending operator attention.
    """
    t = time.time()
    try:
        from tgw.sync_conflict import count_conflicts
        roots = cfg.get('sync_conflict_roots') or []
        n = count_conflicts(roots)
        detail = 'no conflicts' if n == 0 else f'{n} unresolved conflict file(s) — run sync_conflict worker'
        return _result('sync_conflicts', True, detail,
                       (time.time() - t) * 1000, conflict_count=n,
                       warn=(n > 0), roots=[str(r) for r in roots])
    except Exception as e:
        return _result('sync_conflicts', False, str(e), (time.time() - t) * 1000)


# ---------------------------------------------------------------------------
# NATS health check (PP-AIOPS-001 Phase 1)
# ---------------------------------------------------------------------------

def check_nats(cfg: Dict[str, Any]) -> Dict[str, Any]:
    t = time.time()
    url = cfg.get("nats_url", "nats://127.0.0.1:4222")
    try:
        from tgw.apis.nats_client import check_nats as _probe
        result = _probe(url)
        ok = result.get("ok", False)
        latency = result.get("latency_ms")
        streams = result.get("streams", [])
        detail = f"connected ({latency}ms)" if ok else result.get("error", "unreachable")
        return _result("nats", ok, detail, (time.time() - t) * 1000,
                       url=url, latency_ms=latency, streams=streams,
                       warn=not ok)
    except Exception as e:
        return _result("nats", False, str(e), (time.time() - t) * 1000, url=url)


# ---------------------------------------------------------------------------
# ebay_sync per-SKU fallback check (session-39 API audit finding #2)
# ---------------------------------------------------------------------------

def check_ebay_sync_fallback(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """
    Warn if ebay_sync has fallen back to per-SKU offer lookups (eBay error 25707 —
    an orphaned offer with a non-alphanumeric SKU breaks the bulk offer list).

    Green (ok=True): never fell back, or fell back once (transient — the bulk path
    may just have had a one-off hiccup).
    Red (ok=False, warn=True): fell back 2+ consecutive runs — the per-SKU path is
    ~N-fold more expensive in API calls than the bulk list and this is now the
    steady state, not a blip. See todo #1077 (clear the orphaned offer).
    """
    t = time.time()
    root = cfg.get("catalog_root")
    path = Path(root) / "ebay-sync-fallback-state.json" if root else None
    if not path or not path.exists():
        return _result("ebay_sync_fallback", True, "no fallback recorded", (time.time() - t) * 1000)
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return _result("ebay_sync_fallback", True, f"state unreadable: {exc}", (time.time() - t) * 1000)

    consecutive = int(state.get("consecutive_fallback_runs", 0))
    if consecutive >= 2:
        return _result(
            "ebay_sync_fallback", False,
            f"{consecutive} consecutive per-SKU fallback runs — clear orphaned offer (todo #1077)",
            (time.time() - t) * 1000, warn=True, consecutive=consecutive,
        )
    if consecutive == 1:
        return _result("ebay_sync_fallback", True, "1 fallback run (transient)",
                       (time.time() - t) * 1000, warn=True, consecutive=consecutive)
    return _result("ebay_sync_fallback", True, "bulk offer list OK", (time.time() - t) * 1000)


# ---------------------------------------------------------------------------
# Metered-API quota check (PP-QUOTA-001, session 42)
# ---------------------------------------------------------------------------

def _openrouter_key_limit(cfg: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Live per-key spend limit/remaining from OpenRouter's auth/key endpoint.

    Added 2026-07-04 (todo #1132) after a real incident: OpenRouter's
    *account* balance looked fine, but the specific API key TGW uses had
    its own separate spend limit (then $15/week) that was silently
    near-exhausted, causing a 402 pile-up that took a live log-dive to
    diagnose. Surfacing it here means the next time this happens it's
    visible in `tgw health` instead. Returns None (not an error) if the
    OPENROUTER_API_KEY secret is unset (see tgw.apis.secrets.get_api_key())
    or the `requests` call fails — this is a nice-to-have signal, never a
    reason to fail the whole quota check.
    """
    try:
        import requests

        from tgw.apis.secrets import get_api_key
        try:
            key = get_api_key('openrouter')
        except RuntimeError:
            return None
        if not key:
            return None
        resp = requests.get(
            'https://openrouter.ai/api/v1/auth/key',
            headers={'Authorization': f'Bearer {key}'}, timeout=5,
        )
        resp.raise_for_status()
        d = resp.json().get('data', {})
        return {
            'limit': d.get('limit'), 'limit_reset': d.get('limit_reset'),
            'limit_remaining': d.get('limit_remaining'),
        }
    except Exception:
        return None


def check_quota(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """
    Surface today's metered-API spend and any 429 incidents.

    Green (ok=True): no 429s today, no pool past its background-halt fraction.
    Yellow (ok=True, warn=True): a pool is past the halt fraction (background
    callers are being held; operator reserve is protecting interactive use).
    Red (ok=False, warn=True): one or more 429 incidents today — a 429 always
    means a drain bug or budget breach; see var/log/quota-incidents.jsonl.
    """
    t = time.time()
    try:
        from tgw import quota
        st = quota.status(cfg)
    except Exception as exc:
        return _result('quota', True, f'status unavailable: {exc}', (time.time() - t) * 1000)

    incidents = st.get('incidents_today', 0)
    hot = {p: v for p, v in st.get('pools', {}).items()
           if v.get('fraction') is not None and v['fraction'] >= 0.70}
    spent_summary = ', '.join(
        f"{p}={v['spent']}" + (f"/{v['budget']}" if v.get('budget') else '')
        for p, v in sorted(st.get('pools', {}).items())) or 'no calls recorded today'

    or_limit = _openrouter_key_limit(cfg)
    or_note = ''
    or_low = False
    if or_limit and or_limit.get('limit') is not None:
        remaining = or_limit.get('limit_remaining') or 0
        or_note = (f" | openrouter key: ${remaining:.2f} of ${or_limit['limit']} "
                   f"remaining ({or_limit.get('limit_reset')})")
        or_low = remaining < (0.1 * or_limit['limit'])

    if incidents:
        return _result('quota', False,
                       f'{incidents} × 429 incident(s) today — {spent_summary}{or_note}',
                       (time.time() - t) * 1000, warn=True,
                       incidents_today=incidents, pools=st.get('pools', {}),
                       openrouter_key_limit=or_limit)
    if hot or or_low:
        reason = ', '.join(sorted(hot)) if hot else 'openrouter key near its limit'
        return _result('quota', True,
                       f"background halted: {reason} — {spent_summary}{or_note}",
                       (time.time() - t) * 1000, warn=True, pools=st.get('pools', {}),
                       openrouter_key_limit=or_limit)
    return _result('quota', True, f'{spent_summary}{or_note}', (time.time() - t) * 1000,
                   pools=st.get('pools', {}), openrouter_key_limit=or_limit)


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
        check_backups(cfg),
        check_taskboard(cfg),
        check_ownership(cfg),
        check_sync_conflicts(cfg),
    ]
    checks.append(check_nats(cfg))
    checks.append(check_ebay_sync_fallback(cfg))
    checks.append(check_quota(cfg))
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
