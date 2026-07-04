"""
tgw.ops_digest — one-screen daily operational digest (PP-QUOTA-001 / R2.1, session 42).

The missing organ session 42's diagnosis named: the platform logs everything
but nothing *reads it on the operator's behalf*. This module pulls the four
signals that historically rotted silently into one screen with deltas:

  1. Health — failed/warning checks from tgw health (including the new quota check)
  2. Dead letters — per-queue counts, with delta vs the previous digest run
  3. Worker restart counters — systemd NRestarts (the tgw-clipd 15,769-restart
     crash loop would have been line one here)
  4. Quota — per-pool spend, background stand-downs, 429 incidents today
  5. Plan inbox — oldest unprocessed note (pm_intake stalls showed up this way)

State: the previous run's snapshot is kept at var/run/ops-digest-last.json so
deltas are "since you last looked", whatever cadence that is.

Run: `tgw ops-digest` (text) or `tgw ops-digest --json`.
"""

from __future__ import annotations

import json
import logging
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

_DEFAULT_SNAPSHOT = '/opt/TGW/var/run/ops-digest-last.json'
_RESTART_FLAG_THRESHOLD = 5  # NRestarts above this since last digest = a line


def _snapshot_path(cfg: Dict[str, Any]) -> Path:
    raw = cfg.get('raw', cfg)
    return Path(raw.get('ops_digest_snapshot_path', _DEFAULT_SNAPSHOT))


def _unit_restarts() -> Dict[str, int]:
    """NRestarts for every tgw-* systemd unit (services only)."""
    out: Dict[str, int] = {}
    try:
        units = subprocess.run(
            ['systemctl', 'list-units', '--no-legend', '--plain', 'tgw-*'],
            capture_output=True, text=True, timeout=15).stdout
        names = [line.split()[0] for line in units.splitlines()
                 if line.strip() and line.split()[0].endswith('.service')]
        for name in names:
            show = subprocess.run(
                ['systemctl', 'show', name, '-p', 'NRestarts'],
                capture_output=True, text=True, timeout=15).stdout.strip()
            if show.startswith('NRestarts='):
                out[name] = int(show.split('=', 1)[1])
    except Exception as exc:  # noqa: BLE001 — digest must degrade, not die
        log.warning('restart-counter collection failed: %s', exc)
    return out


_DEFAULT_CATALOG_VERIFY_SIDECAR = '/opt/TGW/var/run/catalog-verify-nightly.json'


def _catalog_verify_summary(cfg: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """PP-PHOTOSYNC-001 P7: read the nightly catalog-verify timer's JSON
    sidecar — a cheap file read, never a fresh scan (that's the timer's job,
    not ops-digest's). Missing/stale file degrades to None, not an error."""
    raw = cfg.get('raw', cfg)
    path = Path(raw.get('catalog_verify_sidecar_path', _DEFAULT_CATALOG_VERIFY_SIDECAR))
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    age_h = None
    gen = data.get('generated_at')
    if gen:
        try:
            age_h = round((datetime.now(timezone.utc)
                          - datetime.fromisoformat(gen)).total_seconds() / 3600, 1)
        except ValueError:
            pass
    return {'violations': data.get('violations', 0), 'by_rule': data.get('by_rule', {}),
            'age_hours': age_h}


def _dataset_growth(cfg: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Data Charter observability (todo #1103): eBayCapture bytes/records for
    today's capture file, and ItemArchive coverage (E5, todo #1104). Cheap
    stat/listdir calls only — never re-parses the capture file's contents.

    Purpose (Dave): "a day where the pipeline ran but the dataset didn't
    grow = something is discarding again" — Prime Directive 1, automated.
    """
    raw = cfg.get('raw', cfg)
    capture_root = Path(raw.get('ebay_capture_root', '/opt/TGW/incoming/ebay'))
    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    capture_file = capture_root / f'{today}.jsonl.gz'
    try:
        capture_bytes = capture_file.stat().st_size
    except OSError:
        capture_bytes = 0

    itemdata_root = cfg.get('itemdata_root')
    archive_root = cfg.get('archive_root')
    total_items = 0
    archived_items = 0
    try:
        if itemdata_root:
            total_items = sum(1 for p in Path(itemdata_root).iterdir()
                              if p.is_dir() and p.name.startswith('tgw'))
    except OSError:
        pass
    try:
        if archive_root and Path(archive_root).is_dir():
            archived_items = sum(1 for p in Path(archive_root).iterdir()
                                 if p.suffix == '.zip')
    except OSError:
        pass

    return {
        'capture_bytes_today': capture_bytes,
        'total_items': total_items,
        'archived_items': archived_items,
        'archive_fraction': round(archived_items / total_items, 4) if total_items else None,
    }


def _oldest_inbox_note(cfg: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    inbox = cfg.get('plan_vault_path')
    if not inbox:
        return None
    inbox = Path(inbox) / 'inbox'
    try:
        notes = [p for p in inbox.glob('*.md') if p.name != 'README.md']
        if not notes:
            return None
        oldest = min(notes, key=lambda p: p.stat().st_mtime)
        age_h = (datetime.now(timezone.utc).timestamp() - oldest.stat().st_mtime) / 3600
        return {'name': oldest.name, 'age_hours': round(age_h, 1)}
    except OSError:
        return None


def collect(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Gather the digest and compute deltas vs the previous run's snapshot."""
    from tgw import health, quota
    from tgw.queue import state_machine

    snap_path = _snapshot_path(cfg)
    try:
        prev = json.loads(snap_path.read_text(encoding='utf-8'))
    except Exception:  # noqa: BLE001
        prev = {}

    h = health.check_all(cfg)
    checks_bad = [{'check': c['check'], 'ok': c['ok'],
                   'warn': c.get('warn', False), 'detail': c['detail']}
                  for c in h['checks'] if not c['ok'] or c.get('warn')]

    try:
        state_machine.init(cfg.get('postgres_dsn', 'dbname=state_machine user=tgw'))
        dl = state_machine.dead_letter_breakdown()
        queues = state_machine.queue_state_summary()
        retry_wait = state_machine.retry_wait_breakdown()
        morning_exposure = state_machine.morning_exposure()
    except Exception as exc:  # noqa: BLE001
        dl, queues, retry_wait, morning_exposure = {}, {}, [], []
        checks_bad.append({'check': 'ops_digest_db', 'ok': False, 'warn': True,
                           'detail': f'queue DB unreachable: {exc}'})

    prev_dl = prev.get('dead_letters', {})
    dl_delta = {q: n - prev_dl.get(q, 0) for q, n in dl.items()
                if n - prev_dl.get(q, 0) != 0}
    for q, was in prev_dl.items():
        if q not in dl and was:
            dl_delta[q] = -was

    restarts = _unit_restarts()
    prev_restarts = prev.get('restarts', {})
    restart_flags = {u: {'total': n, 'since_last': n - prev_restarts.get(u, 0)}
                     for u, n in restarts.items()
                     if n - prev_restarts.get(u, 0) > _RESTART_FLAG_THRESHOLD
                     or (u not in prev_restarts and n > _RESTART_FLAG_THRESHOLD)}

    quota_status = quota.status(cfg)
    dataset_growth = _dataset_growth(cfg)
    today_key = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    prev_growth = prev.get('dataset_growth') or {}
    # Only compare deltas within the same UTC day — a fresh day's capture
    # file starting smaller than yesterday's full file is not a stall.
    same_day = prev_growth.get('date') == today_key
    capture_bytes_delta = (
        dataset_growth['capture_bytes_today'] - prev_growth.get('capture_bytes_today', 0)
        if same_day else None
    )
    dataset_growth['date'] = today_key
    dataset_growth['capture_bytes_delta'] = capture_bytes_delta
    # The alarm: eBay calls happened (any pool spent > 0) since last look, but
    # today's capture file didn't grow a single byte — something is
    # discarding raw responses again (Prime Directive 1 / invariant E7).
    ebay_activity = any(p.get('spent', 0) > 0 for pool, p in quota_status.get('pools', {}).items()
                       if pool.startswith('ebay_'))
    dataset_growth['capture_stalled'] = bool(
        same_day and ebay_activity and capture_bytes_delta is not None and capture_bytes_delta <= 0)

    digest = {
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'previous_run': prev.get('generated_at'),
        'health_ok': h['ok'],
        'checks_flagged': checks_bad,
        'queues': queues,
        'dead_letters': dl,
        'dead_letter_delta': dl_delta,
        'restarts': restarts,
        'restart_flags': restart_flags,
        'quota': quota_status,
        'oldest_inbox_note': _oldest_inbox_note(cfg),
        'catalog_verify': _catalog_verify_summary(cfg),
        'retry_wait': retry_wait,
        'morning_exposure': morning_exposure,
        'dataset_growth': dataset_growth,
    }

    try:
        snap_path.parent.mkdir(parents=True, exist_ok=True)
        snap_path.write_text(json.dumps(digest), encoding='utf-8')
    except OSError as exc:
        log.warning('could not write digest snapshot: %s', exc)
    return digest


def render_text(d: Dict[str, Any]) -> str:
    """One screen, worst news first."""
    lines: List[str] = []
    gen = d['generated_at'][:16].replace('T', ' ')
    prev = (d.get('previous_run') or 'never')[:16].replace('T', ' ')
    lines.append(f"TGW OPS DIGEST — {gen}Z (deltas since {prev}Z)")
    lines.append('')

    flagged = d['checks_flagged']
    if flagged:
        lines.append(f"HEALTH — {len(flagged)} flagged:")
        for c in flagged:
            sev = 'RED ' if not c['ok'] else 'warn'
            lines.append(f"  [{sev}] {c['check']}: {c['detail'][:100]}")
    else:
        lines.append('HEALTH — all green')

    q = d.get('quota', {})
    inc = q.get('incidents_today', 0)
    lines.append('')
    lines.append(f"QUOTA — {inc} × 429 incident(s) today" if inc else 'QUOTA — no 429s today')
    for pool, p in sorted(q.get('pools', {}).items()):
        frac = f" ({int(p['fraction'] * 100)}%)" if p.get('fraction') is not None else ''
        flag429 = f"  LAST 429 {p['last_429'][11:16]}Z" if p.get('last_429') else ''
        budget = f"/{p['budget']}" if p.get('budget') else ''
        lines.append(f"  {pool:20s} {p['spent']:>8}{budget}{frac}{flag429}")

    lines.append('')
    dl, delta = d['dead_letters'], d['dead_letter_delta']
    total = sum(dl.values())
    dsum = sum(delta.values())
    lines.append(f"DEAD LETTERS — {total} total ({dsum:+d} since last)" if dl or delta
                 else 'DEAD LETTERS — none')
    for queue, n in sorted(dl.items(), key=lambda kv: -kv[1]):
        dnote = f" ({delta[queue]:+d})" if queue in delta else ''
        lines.append(f"  {queue:20s} {n:>6}{dnote}")

    rw = d.get('retry_wait', [])
    lines.append('')
    if rw:
        flagged_rw = [r for r in rw if r['count'] > 50 or r['oldest_age_hours'] > 24]
        sev = 'RED ' if flagged_rw else ''
        lines.append(f"{sev}RETRY_WAIT — {sum(r['count'] for r in rw)} job(s) pending retry:")
        for r in rw:
            flag = ' [RED]' if r['count'] > 50 or r['oldest_age_hours'] > 24 else ''
            lines.append(f"  {r['queue_name']:20s} {r['count']:>6}  oldest {r['oldest_age_hours']}h{flag}")
    else:
        lines.append('RETRY_WAIT — none')

    exp = d.get('morning_exposure', [])
    lines.append('')
    if exp:
        lines.append(f"MORNING EXPOSURE — {sum(r['count'] for r in exp)} job(s) scheduled to fire before 06:00 PST:")
        for r in exp:
            lines.append(f"  {r['queue_name']:20s} {r['count']:>6}")
    else:
        lines.append('MORNING EXPOSURE — nothing scheduled before 06:00 PST')

    if d['restart_flags']:
        lines.append('')
        lines.append('RESTART FLAGS (crash-looping?):')
        for unit, info in sorted(d['restart_flags'].items()):
            lines.append(f"  {unit}: {info['since_last']:+d} since last "
                         f"(total {info['total']})")

    cv = d.get('catalog_verify')
    if cv is not None:
        lines.append('')
        stale = f" (STALE — {cv['age_hours']}h old)" if cv['age_hours'] and cv['age_hours'] > 30 else ''
        if cv['violations']:
            lines.append(f"CATALOG-VERIFY — {cv['violations']} critical violation(s){stale}")
            for rule, n in sorted(cv['by_rule'].items(), key=lambda kv: -kv[1]):
                lines.append(f"  {rule:28s} {n:>6}")
        else:
            lines.append(f"CATALOG-VERIFY — clean{stale}")

    note = d.get('oldest_inbox_note')
    if note and note['age_hours'] > 24:
        lines.append('')
        lines.append(f"INBOX — oldest unprocessed note {note['age_hours']}h old: "
                     f"{note['name']} (pm_intake stalled?)")

    dg = d.get('dataset_growth')
    if dg is not None:
        lines.append('')
        delta = dg.get('capture_bytes_delta')
        delta_s = f" ({delta:+,} bytes since last look)" if delta is not None else ''
        frac = dg.get('archive_fraction')
        frac_s = f"{int(frac * 100)}%" if frac is not None else '?'
        stalled = dg.get('capture_stalled')
        header = 'RED DATASET GROWTH' if stalled else 'DATASET GROWTH'
        lines.append(f"{header} — eBayCapture today: {dg['capture_bytes_today']:,} bytes{delta_s}; "
                     f"ItemArchive coverage: {dg['archived_items']}/{dg['total_items']} items ({frac_s})")
        if stalled:
            lines.append("  eBay calls happened but the capture file did not grow — "
                        "something is discarding raw responses again (invariant E7)")

    qs = d.get('queues', {})
    lines.append('')
    lines.append(f"QUEUES — {qs.get('queued', '?')} queued, "
                 f"{qs.get('processing', '?')} processing")
    return '\n'.join(lines)
