#!/usr/bin/env python3
import argparse
import json
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Tuple

DEFAULT_ROOT = Path('/opt/TGW/data/ItemData')


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile('w', encoding='utf-8', delete=False, dir=path.parent) as tmp:
        tmp.write(text)
        tmp_path = Path(tmp.name)
    tmp_path.replace(path)


def find_item_jsons(root: Path) -> List[Path]:
    paths: List[Path] = []
    if not root.exists():
        return paths
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        candidate = child / f'{child.name}.json'
        if candidate.exists():
            paths.append(candidate)
    return paths


def load_json_strict(path: Path) -> Any:
    def hook(pairs):
        out = {}
        seen = set()
        dups = []
        for k, v in pairs:
            if k in seen:
                dups.append(k)
            seen.add(k)
            out[k] = v
        if dups:
            raise ValueError(f'duplicate keys: {sorted(set(dups))}')
        return out
    with path.open('r', encoding='utf-8', errors='replace') as f:
        return json.load(f, object_pairs_hook=hook)


def strip_bom(text: str) -> Tuple[str, bool]:
    if text.startswith('\ufeff'):
        return text.lstrip('\ufeff'), True
    return text, False


def remove_trailing_commas(text: str) -> Tuple[str, bool]:
    out = []
    in_str = False
    esc = False
    changed = False
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if in_str:
            out.append(ch)
            if esc:
                esc = False
            elif ch == '\\':
                esc = True
            elif ch == '"':
                in_str = False
            i += 1
            continue
        if ch == '"':
            in_str = True
            out.append(ch)
            i += 1
            continue
        if ch == ',':
            j = i + 1
            while j < n and text[j] in ' \t\r\n':
                j += 1
            if j < n and text[j] in ']}':
                changed = True
                i += 1
                continue
        out.append(ch)
        i += 1
    return ''.join(out), changed


def replace_single_quoted_keys(text: str) -> Tuple[str, bool]:
    pattern = re.compile(r'(?P<prefix>[\{,]\s*)\'(?P<key>[^\'\\\n\r]+)\'\s*:')
    changed = False
    while True:
        new_text, count = pattern.subn(lambda m: f'{m.group("prefix")}"{m.group("key").replace(chr(34), r"\"")}":', text)
        if count == 0:
            break
        text = new_text
        changed = True
    return text, changed


def replace_single_quoted_strings(text: str) -> Tuple[str, bool]:
    out = []
    in_double = False
    in_single = False
    esc = False
    changed = False
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if in_double:
            out.append(ch)
            if esc:
                esc = False
            elif ch == '\\':
                esc = True
            elif ch == '"':
                in_double = False
            i += 1
            continue
        if in_single:
            if esc:
                out.append(ch)
                esc = False
            elif ch == '\\':
                out.append(ch)
                esc = True
            elif ch == "'":
                out.append('"')
                in_single = False
                changed = True
            elif ch == '"':
                out.append('\\"')
                changed = True
            else:
                out.append(ch)
            i += 1
            continue
        if ch == '"':
            in_double = True
            out.append(ch)
            i += 1
            continue
        if ch == "'":
            prev = text[i - 1] if i > 0 else ''
            nxt = text[i + 1] if i + 1 < n else ''
            if prev in '{[:,\n\r\t ' or prev == '':
                out.append('"')
                in_single = True
                changed = True
                i += 1
                continue
        out.append(ch)
        i += 1
    return ''.join(out), changed


def remove_comments(text: str) -> Tuple[str, bool]:
    out = []
    in_str = False
    esc = False
    changed = False
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        nxt = text[i + 1] if i + 1 < n else ''
        if in_str:
            out.append(ch)
            if esc:
                esc = False
            elif ch == '\\':
                esc = True
            elif ch == '"':
                in_str = False
            i += 1
            continue
        if ch == '"':
            in_str = True
            out.append(ch)
            i += 1
            continue
        if ch == '/' and nxt == '/':
            changed = True
            i += 2
            while i < n and text[i] not in '\r\n':
                i += 1
            continue
        if ch == '/' and nxt == '*':
            changed = True
            i += 2
            while i + 1 < n and not (text[i] == '*' and text[i + 1] == '/'):
                i += 1
            i += 2
            continue
        out.append(ch)
        i += 1
    return ''.join(out), changed


def try_repair_text(text: str) -> Tuple[str, List[str]]:
    fixes: List[str] = []
    new_text, changed = strip_bom(text)
    if changed:
        text = new_text
        fixes.append('strip_bom')
    for label, fn in [
        ('remove_comments', remove_comments),
        ('remove_trailing_commas', remove_trailing_commas),
        ('replace_single_quoted_keys', replace_single_quoted_keys),
        ('replace_single_quoted_strings', replace_single_quoted_strings),
    ]:
        new_text, changed = fn(text)
        if changed:
            text = new_text
            fixes.append(label)
    return text, fixes


def validate_json_text(text: str) -> Tuple[bool, str | None, str | None]:
    try:
        obj = json.loads(text)
        if not isinstance(obj, dict):
            return False, 'top_level_not_object', 'top-level JSON is not an object'
        return True, None, None
    except json.JSONDecodeError as e:
        return False, 'json_decode_error', f'{e.msg}: line {e.lineno} column {e.colno} (char {e.pos})'
    except Exception as e:
        return False, 'other_parse_error', str(e)


def inspect_file(path: Path, apply_fixes: bool, backup_dir: Path | None, dry_run: bool) -> Dict[str, Any]:
    original = path.read_text(encoding='utf-8', errors='replace')
    ok, err_type, err = validate_json_text(original)
    result: Dict[str, Any] = {'path': str(path), 'ok': ok, 'changed': False, 'fixes': [], 'error_type': err_type, 'error': err}
    if ok:
        try:
            load_json_strict(path)
        except Exception as e:
            result['ok'] = False
            result['error_type'] = 'strict_load_error'
            result['error'] = str(e)
        else:
            return result
    repaired, fixes = try_repair_text(original)
    ok2, err_type2, err2 = validate_json_text(repaired)
    result['candidate_ok'] = ok2
    result['candidate_error_type'] = err_type2
    result['candidate_error'] = err2
    result['fixes'] = fixes
    if ok2 and repaired != original and apply_fixes:
        if backup_dir is not None:
            backup_path = backup_dir / path.relative_to(DEFAULT_ROOT)
            backup_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, backup_path)
            result['backup_path'] = str(backup_path)
        if not dry_run:
            atomic_write_text(path, repaired if repaired.endswith('\n') else repaired + '\n')
        result['changed'] = True
        result['ok'] = True
        result['error_type'] = None
        result['error'] = None
    return result


def summarize(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    summary: Dict[str, Any] = {'files_seen': len(results), 'ok': 0, 'bad': 0, 'changed': 0, 'by_error_type': {}, 'by_fix': {}}
    for r in results:
        if r.get('ok'):
            summary['ok'] += 1
        else:
            summary['bad'] += 1
            et = r.get('error_type') or 'unknown'
            summary['by_error_type'][et] = summary['by_error_type'].get(et, 0) + 1
        if r.get('changed'):
            summary['changed'] += 1
        for fx in r.get('fixes', []):
            summary['by_fix'][fx] = summary['by_fix'].get(fx, 0) + 1
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description='Scan and conservatively repair malformed ItemData JSON files.')
    parser.add_argument('--root', default=str(DEFAULT_ROOT), help='ItemData root directory')
    parser.add_argument('--report', default='', help='Write JSON report to this path')
    parser.add_argument('--fix', action='store_true', help='Apply conservative fixes in-place')
    parser.add_argument('--dry-run', action='store_true', help='Show what would change without writing files')
    parser.add_argument('--backup-dir', default='', help='Directory to store backups before fixing')
    parser.add_argument('--limit', type=int, default=0, help='Only process the first N files')
    args = parser.parse_args()

    root = Path(args.root).expanduser()
    backup_dir = Path(args.backup_dir).expanduser() if args.backup_dir else None
    files = find_item_jsons(root)
    if args.limit and args.limit > 0:
        files = files[:args.limit]

    results = [inspect_file(path, apply_fixes=args.fix, backup_dir=backup_dir, dry_run=args.dry_run) for path in files]
    payload = {'root': str(root), 'fix_mode': bool(args.fix), 'dry_run': bool(args.dry_run), 'summary': summarize(results), 'results': results}

    if args.report:
        report_path = Path(args.report).expanduser()
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

    print(json.dumps(payload['summary'], ensure_ascii=False, indent=2))
    bad = payload['summary']['bad']
    return 1 if bad and not args.fix else 0


if __name__ == '__main__':
    raise SystemExit(main())
