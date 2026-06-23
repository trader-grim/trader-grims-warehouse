#!/opt/TGW/.venvironments/tgw/bin/python3
"""
tgw-mc-status — human-readable TGW status for the MC F2 menu viewer.

Usage:
    tgw-mc-status.py health
    tgw-mc-status.py queue
    tgw-mc-status.py services
    tgw-mc-status.py catalog
    tgw-mc-status.py item /path/to/tgwXXX.json
"""
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, '/opt/TGW/src/trader-grims-warehouse/src')
CONFIG_PATH = Path('/opt/TGW/config/tgw-api-config.json')
DSN = 'dbname=state_machine user=tgw'
CATALOG = Path('/opt/TGW/data/ItemCatalog/search-catalog.json')

W = 60  # display width


def hr(char='─'):
    return char * W


def ts():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


# ---------------------------------------------------------------------------
# health
# ---------------------------------------------------------------------------

def show_health():
    from tgw.config import load_config
    from tgw.health import check_all
    cfg = load_config(CONFIG_PATH)
    r = check_all(cfg)

    print(f'TGW Platform Health  —  {ts()}')
    print(hr())
    for c in r['checks']:
        flag = 'OK  ' if c['ok'] else 'FAIL'
        name = str(c.get('check', ''))
        detail = str(c.get('detail', ''))
        ms = c.get('elapsed_ms', 0)
        print(f'{flag}  {name:20s}  {detail}  [{ms:.0f}ms]')
    print(hr())
    total = r.get('elapsed_ms', 0)
    if r['ok']:
        print(f'All checks passed  ({total:.0f}ms total)')
    else:
        print(f'FAILED: {", ".join(r["failed"])}  ({total:.0f}ms total)')


# ---------------------------------------------------------------------------
# queue
# ---------------------------------------------------------------------------

def show_queue():
    import psycopg2
    import psycopg2.extras

    STATE_ORDER = ['queued', 'running', 'leased', 'retry_wait',
                   'failed', 'dead_letter', 'cancelled', 'succeeded']

    con = psycopg2.connect(DSN)
    try:
        with con.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT job_id::text, queue_name, state::text, operation,
                       attempt_count, max_attempts, error_code, error_detail,
                       created_at, updated_at
                FROM queue_jobs
                ORDER BY state, created_at DESC
            """)
            jobs = [dict(r) for r in cur.fetchall()]
    finally:
        con.close()

    by_state: dict = {}
    for j in jobs:
        by_state.setdefault(str(j['state']), []).append(j)

    print(f'TGW Job Queue  —  {ts()}')
    print(hr())
    print(f'{"State":<14}  {"Count":>5}')
    print(hr('·'))
    for s in STATE_ORDER:
        n = len(by_state.get(s, []))
        if n:
            print(f'{s:<14}  {n:>5}')
    print(hr())

    SHOW_LIMIT = 10
    for s in STATE_ORDER:
        jlist = by_state.get(s, [])
        if not jlist:
            continue
        show = jlist[:SHOW_LIMIT]
        header = f'{s.upper()}  ({len(jlist)})'
        if len(jlist) > SHOW_LIMIT:
            header += f'  [showing first {SHOW_LIMIT}]'
        print()
        print(header)
        print(hr('·'))
        for j in show:
            jid = str(j['job_id'])[:8]
            qn = str(j.get('queue_name', ''))[:18]
            op = str(j.get('operation', ''))[:12]
            att = j.get('attempt_count', 0)
            mx = j.get('max_attempts', 0)
            err = str(j.get('error_code') or '').strip()
            err_str = f'  [{err}]' if err else ''
            print(f'  {qn:18s}  {op:12s}  {jid}  att={att}/{mx}{err_str}')
            if j.get('error_detail'):
                detail = str(j['error_detail'])[:60].strip()
                print(f'  {"":18s}  {detail}')


# ---------------------------------------------------------------------------
# services
# ---------------------------------------------------------------------------

def show_services():
    TGW_SERVICES = [
        'tgw-worker@token_refresh.service',
        'tgw-worker@echo.service',
        'trader-grims-backup.service',
        'postgresql.service',
        'queue-launcher.service',
    ]

    print(f'TGW Services  —  {ts()}')
    print(hr())
    print(f'{"State":8s}  {"Sub":8s}  {"Unit"}')
    print(hr('·'))

    for unit in TGW_SERVICES:
        try:
            r = subprocess.run(
                ['systemctl', 'show', unit,
                 '--property=ActiveState,SubState,Description,ExecMainPID'],
                capture_output=True, text=True, timeout=5
            )
            props = dict(
                line.partition('=')[::2]
                for line in r.stdout.strip().splitlines()
                if '=' in line
            )
        except Exception as e:
            props = {'ActiveState': 'error', 'SubState': str(e)[:20]}

        active = props.get('ActiveState', 'unknown')
        sub = props.get('SubState', '')
        desc = props.get('Description', unit)
        pid = props.get('ExecMainPID', '0')
        flag = 'ACTIVE  ' if active == 'active' else 'INACTIVE'
        pid_str = f'  PID={pid}' if active == 'active' and pid != '0' else ''
        print(f'{flag}  {sub:8s}  {unit.removesuffix(".service")}')
        print(f'         {"":8s}  {desc}{pid_str}')

    print(hr())
    print()
    print('Enter services.tgwsvc in /opt/TGW/mc/ for the full VFS.')


# ---------------------------------------------------------------------------
# catalog
# ---------------------------------------------------------------------------

def show_catalog():
    print(f'TGW Catalog Stats  —  {ts()}')
    print(hr())

    if not CATALOG.exists():
        print(f'search-catalog.json not found: {CATALOG}')
        return

    mtime = datetime.fromtimestamp(CATALOG.stat().st_mtime)
    size_kb = CATALOG.stat().st_size // 1024

    data = json.loads(CATALOG.read_text(encoding='utf-8', errors='replace'))
    if isinstance(data, dict):
        data = [{'sku': k, **v} for k, v in data.items()]

    by_loc: dict = {}
    by_status: dict = {}
    for item in data:
        loc = str(item.get('location') or '').strip() or '_unlisted'
        by_loc.setdefault(loc, []).append(item)
        st = str(item.get('#STATUS') or item.get('status') or '').strip() or '(none)'
        by_status.setdefault(st, 0)
        by_status[st] += 1

    print(f'Source:    {CATALOG}')
    print(f'Updated:   {mtime.strftime("%Y-%m-%d %H:%M:%S")}  ({size_kb} KB)')
    print(f'Items:     {len(data):,}')
    print(f'Locations: {len(by_loc):,}')
    print()
    print('Status breakdown:')
    print(hr('·'))
    for st, n in sorted(by_status.items(), key=lambda x: -x[1]):
        print(f'  {st:30s}  {n:>6,}')
    print(hr())
    print()
    print('Enter catalog.tgwcat in /opt/TGW/mc/ for the full VFS.')


# ---------------------------------------------------------------------------
# item
# ---------------------------------------------------------------------------

def show_item(json_path: str):
    path = Path(json_path)
    if not path.exists():
        print(f'File not found: {json_path}')
        sys.exit(1)

    doc = json.loads(path.read_text(encoding='utf-8', errors='replace'))
    sku = str(doc.get('sku', path.stem))

    mtime = datetime.fromtimestamp(path.stat().st_mtime)

    media = [p for p in sorted(path.parent.iterdir())
             if p.suffix.lower() in {'.jpg', '.jpeg', '.png', '.gif',
                                      '.webp', '.mp4', '.mov', '.mkv'}
             and p.is_file()]

    print(f'TGW Item: {sku}')
    print(hr())

    # Key fields
    key_fields = [
        ('title',     'Title'),
        ('location',  'Location'),
        ('#STATUS',   'Status'),
        ('status',    'Status (alt)'),
        ('#VERIFIED', 'Verified'),
        ('price',     'Price'),
        ('qty',       'Qty'),
        ('condition', 'Condition'),
        ('Item number', 'eBay item #'),
        ('sku',       'SKU'),
    ]
    for field, label in key_fields:
        val = doc.get(field)
        if val is not None and str(val).strip():
            v = str(val).strip().replace('\n', ' ')[:60]
            print(f'  {label:12s}  {v}')

    print()
    print(f'  Fields     {len(doc)} total')
    if media:
        names = ', '.join(p.name for p in media[:4])
        more = f' +{len(media)-4} more' if len(media) > 4 else ''
        print(f'  Photos     {len(media)} ({names}{more})')
    else:
        print('  Photos     none')

    print(f'  Updated    {mtime.strftime("%Y-%m-%d %H:%M:%S")}')
    print(f'  Path       {path}')
    print(hr())
    print()
    print('Open this file in MC (Enter) to enter the item VFS.')
    print('  fields/    — every field as a browsable .txt')
    print('  photos/    — images (F3 to view)')
    print('  meta.json  — full raw record')

    # Show all fields below the fold
    print()
    print('All fields:')
    print(hr('·'))
    for k, v in sorted(doc.items()):
        if isinstance(v, (dict, list)):
            v_str = json.dumps(v, ensure_ascii=False)[:80]
        else:
            v_str = str(v).replace('\n', '↵')[:80] if v is not None else '(null)'
        print(f'  {str(k):30s}  {v_str}')


# ---------------------------------------------------------------------------
# dispatch
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'help'
    try:
        if cmd == 'health':
            show_health()
        elif cmd == 'queue':
            show_queue()
        elif cmd == 'services':
            show_services()
        elif cmd == 'catalog':
            show_catalog()
        elif cmd == 'item':
            if len(sys.argv) < 3:
                print('Usage: tgw-mc-status.py item <path/to/sku.json>', file=sys.stderr)
                sys.exit(1)
            show_item(sys.argv[2])
        else:
            print(__doc__)
    except Exception as e:
        import traceback
        print(f'Error: {e}')
        traceback.print_exc()
        sys.exit(1)
