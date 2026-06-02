#!/usr/bin/env python3
import json
import shutil
import sys
import os
import time
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory

BASE = Path('/opt/TGW')
CONFIG_PATH = BASE / 'config' / 'queue-config.json'
API_CONFIG_PATH = BASE / 'config' / 'tgw-api-config.json'
MEDIA_EXTS = {'.jpg', '.jpeg', '.png', '.webp', '.gif', '.bmp', '.tif', '.tiff', '.mp4', '.mov', '.avi', '.mkv', '.m4v', '.heic', '.heif'}
ZIP_EXTS = {'.zip'}


def load_json(path):
    with path.open() as f:
        return json.load(f)


def load_config():
    return load_json(CONFIG_PATH)


def load_api_config():
    with API_CONFIG_PATH.open() as f:
        return json.load(f)

def get_api_itemdata_root(api_cfg):
    return Path(api_cfg['itemdata_root'])

def get_worker(cfg):
    return cfg['workers']['newitem_pool']


def get_queue_path(cfg, queue_name):
    return Path(cfg['queues'][queue_name]['path'])


def queue_exists(cfg, queue_name):
    return queue_name in cfg.get('queues', {})


def get_processing_path(cfg):
    worker = get_worker(cfg)
    queue_name = worker.get('processing_queue', 'newitem-processing')
    return get_queue_path(cfg, queue_name)


def get_bridge_path(cfg):
    worker = get_worker(cfg)
    queue_name = worker.get('work_queue', 'newitem-work')
    return get_queue_path(cfg, queue_name)


def get_itemdata_path(cfg):
    return Path(get_worker(cfg)['itemdata_path'])


def get_log_path(cfg, api_cfg):
    worker = get_worker(cfg)
    return Path(worker.get('log_path') or Path(api_cfg.get('log_root', '/opt/TGW/runtime/logs')) / 'newitem-pool.log')


def get_archive_root(api_cfg):
    return Path(api_cfg.get('archive_root', '/opt/TGW/data/ItemArchive'))


def log_line(log_path, queue_name, item_name, msg):
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    ts = time.strftime('%Y-%m-%d %H:%M:%S')
    with log_path.open('a') as f:
        f.write(f'[{ts}] [{queue_name}] {item_name}: {msg}\n')


def ensure_paths(cfg, api_cfg):
    for q in cfg.get('queues', {}).values():
        Path(q['path']).mkdir(parents=True, exist_ok=True)
    worker = get_worker(cfg)
    Path(worker['watch_path']).mkdir(parents=True, exist_ok=True)
    Path(worker['itemdata_path']).mkdir(parents=True, exist_ok=True)
    Path(api_cfg.get('log_root', '/opt/TGW/logs')).mkdir(parents=True, exist_ok=True)
    get_archive_root(api_cfg).mkdir(parents=True, exist_ok=True)


def should_ignore_name(name):
    return name.startswith('.') or name == 'tmp' or name.endswith('.stfolder')


def json_files_in(item_dir):
    return sorted([p for p in item_dir.iterdir() if p.is_file() and p.suffix.lower() == '.json'])


def has_required_json(item):
    if item.is_file():
        return item.suffix.lower() == '.json'
    return len(json_files_in(item)) == 1


def read_primary_json(item_dir):
    files = json_files_in(item_dir)
    if len(files) != 1:
        return None, None
    path = files[0]
    try:
        return path, load_json(path)
    except Exception:
        return path, None


def is_quiet_enough(path, quiet_seconds):
    now = time.time()
    latest = path.stat().st_mtime
    if path.is_dir():
        for child in path.rglob('*'):
            try:
                mtime = child.stat().st_mtime
                if mtime > latest:
                    latest = mtime
            except FileNotFoundError:
                continue
    return (now - latest) >= quiet_seconds


def list_zip_files(item_dir):
    return sorted([p for p in item_dir.iterdir() if p.is_file() and p.suffix.lower() in ZIP_EXTS])


def classify_item(item_dir):
    json_path, data = read_primary_json(item_dir)
    if json_path is None:
        return 'failed_missing_json'
    if data is None:
        return 'failed_bad_json'
    location = str(data.get('location', '')).strip()
    if list_zip_files(item_dir):
        return 'ready_for_newitem-unzip'
    if not location:
        return 'waiting_for_location'
    return 'ready_for_newitem-processing'


def route_dir(cfg, queue_name, item_dir, log_path):
    dst_queue = get_queue_path(cfg, queue_name)
    dst = dst_queue / item_dir.name
    if dst.exists():
        log_line(log_path, queue_name, item_dir.name, f'route_skipped reason=destination_exists dest={dst}')
        return False
    shutil.move(str(item_dir), str(dst))
    log_line(log_path, queue_name, item_dir.name, f'route_move src={item_dir} dest={dst}')
    return True


def write_queue_state_record(processing_dir, item_name, item_path, state_name, log_path):
    state_path = processing_dir / f'{item_name}.queue.json'
    record = {
        'queue': processing_dir.name,
        'item_name': item_name,
        'item_path': str(item_path),
        'status': state_name,
        'created_at': time.strftime('%Y-%m-%dT%H:%M:%S')
    }
    state_path.write_text(json.dumps(record, indent=2) + '\n')
    log_line(log_path, processing_dir.name, item_name, f'queue_state_record_created path={state_path}')
    return state_path


def create_bridge_link(item_name, bridge_dir, api_cfg, log_path):
    itemdata_root = get_api_itemdata_root(api_cfg)
    target_path = itemdata_root / item_name
    bridge_link = bridge_dir / item_name

    if bridge_link.exists() or bridge_link.is_symlink():
        try:
            if bridge_link.resolve() == target_path.resolve():
                log_line(log_path, bridge_dir.name, item_name, f'bridge_link_already_correct target={target_path}')
                return True
        except FileNotFoundError:
            pass
        if bridge_link.is_dir() and not bridge_link.is_symlink():
            log_line(log_path, bridge_dir.name, item_name, f'bridge_link_replace_failed existing_dir={bridge_link}')
            return False
        bridge_link.unlink()

    rel_target = Path(os.path.relpath(str(target_path), str(bridge_dir)))
    bridge_link.symlink_to(rel_target)
    log_line(log_path, bridge_dir.name, item_name, f'bridge_link_created link={bridge_link} target={rel_target}')
    return True


def is_safe_member(member_name):
    p = Path(member_name)
    return not p.is_absolute() and '..' not in p.parts and member_name.strip() not in {'', '.', '/'}


def unzip_item(item_dir, cfg, api_cfg, log_path):
    zip_files = list_zip_files(item_dir)
    if not zip_files:
        return False
    archive_root = get_archive_root(api_cfg) / 'newitem-unzip'
    archive_root.mkdir(parents=True, exist_ok=True)
    for zip_path in zip_files:
        with TemporaryDirectory(prefix='tgw-unzip-') as td:
            extract_root = Path(td) / 'root'
            extract_root.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(zip_path) as zf:
                members = [m for m in zf.namelist() if not m.endswith('/')]
                if not members:
                    raise ValueError(f'zip has no files: {zip_path.name}')
                for member in members:
                    if not is_safe_member(member):
                        raise ValueError(f'unsafe zip member: {member}')
                zf.extractall(path=extract_root)
            extracted_files = [p for p in extract_root.rglob('*') if p.is_file()]
            top_dirs = sorted({p.relative_to(extract_root).parts[0] for p in extracted_files if len(p.relative_to(extract_root).parts) > 1})
            loose_files = [p for p in extract_root.iterdir() if p.is_file()]
            if top_dirs and not loose_files:
                json_path, data = read_primary_json(item_dir)
                if data is None:
                    raise ValueError(f'cannot split without valid seed json: {item_dir.name}')
                base_sku = str(data.get('sku', item_dir.name)).strip()
                base_location = data.get('location', '')
                parent_queue = item_dir.parent
                item_dir_name = item_dir.name
                for idx, top in enumerate(top_dirs):
                    src_dir = extract_root / top
                    new_sku = base_sku if idx == 0 else f'{base_sku}-{idx+1}'
                    new_dir = parent_queue / new_sku
                    new_dir.mkdir(parents=True, exist_ok=False)
                    new_json = dict(data)
                    new_json['sku'] = new_sku
                    new_json['location'] = base_location
                    (new_dir / f'{new_sku}.json').write_text(json.dumps(new_json, indent=2) + '\n')
                    for child in src_dir.rglob('*'):
                        if child.is_file():
                            rel = child.relative_to(src_dir)
                            dest = new_dir / rel.name
                            shutil.move(str(child), str(dest))
                    log_line(log_path, item_dir.parent.name, item_dir_name, f'split_derived_item_created dir={new_dir}')
                archived_zip = archive_root / f'{item_dir_name}--{zip_path.name}'
                shutil.move(str(zip_path), str(archived_zip))
                shutil.rmtree(item_dir)
                log_line(log_path, item_dir.parent.name, item_dir_name, f'multi_folder_zip_split_complete archived={archived_zip}')
                return True
            else:
                for child in extracted_files:
                    if child.suffix.lower() in MEDIA_EXTS:
                        dest = item_dir / child.name
                        if dest.exists():
                            raise ValueError(f'media collision on extract: {dest.name}')
                        shutil.move(str(child), str(dest))
                archived_zip = archive_root / f'{item_dir.name}--{zip_path.name}'
                shutil.move(str(zip_path), str(archived_zip))
                log_line(log_path, item_dir.parent.name, item_dir.name, f'zip_extracted_into_item archived={archived_zip}')
    return True


def stage_watch_to_pool(cfg, api_cfg):
    worker = get_worker(cfg)
    log_path = get_log_path(cfg, api_cfg)
    watch_path = Path(worker['watch_path'])
    pool_path = get_queue_path(cfg, worker['queue_name'])
    quiet_seconds = worker.get('quiet_seconds', 15)
    moved = []
    for item in sorted(watch_path.iterdir()):
        if should_ignore_name(item.name):
            log_line(log_path, pool_path.name, item.name, 'ignored reason=name_rule')
            continue
        if not has_required_json(item):
            log_line(log_path, pool_path.name, item.name, 'verification_failed reason=missing_or_ambiguous_json')
            continue
        if not is_quiet_enough(item, quiet_seconds):
            log_line(log_path, pool_path.name, item.name, 'waiting_for_sync reason=not_quiet_yet')
            continue
        dst = pool_path / item.name
        if dst.exists():
            log_line(log_path, pool_path.name, item.name, f'skipping_intake reason=destination_exists dest={dst}')
            continue
        shutil.move(str(item), str(dst))
        moved.append(item.name)
        log_line(log_path, pool_path.name, item.name, f'intake_move src={item} dest={dst}')
    return moved


def stage_pool_dispatch(cfg, api_cfg):
    worker = get_worker(cfg)
    log_path = get_log_path(cfg, api_cfg)
    pool_path = get_queue_path(cfg, worker['queue_name'])
    process_delay = worker.get('process_delay', 20)
    actions = []
    for item in sorted(pool_path.iterdir()):
        if should_ignore_name(item.name):
            log_line(log_path, pool_path.name, item.name, 'ignored reason=name_rule')
            continue
        if not item.is_dir():
            continue
        age = time.time() - item.stat().st_mtime
        if age < process_delay:
            log_line(log_path, pool_path.name, item.name, f'waiting_for_process_delay age={age:.1f}s')
            continue
        state = classify_item(item)
        if state == 'ready_for_newitem-processing':
            if route_dir(cfg, 'newitem-processing', item, log_path):
                actions.append({'item': item.name, 'state': state})
            continue
        if state == 'ready_for_newitem-unzip' and queue_exists(cfg, 'newitem-unzip'):
            if route_dir(cfg, 'newitem-unzip', item, log_path):
                actions.append({'item': item.name, 'state': state})
            continue
        if state == 'waiting_for_location' and queue_exists(cfg, 'newitem-no-location'):
            if route_dir(cfg, 'newitem-no-location', item, log_path):
                actions.append({'item': item.name, 'state': state})
            continue
        if state.startswith('failed') and queue_exists(cfg, 'failed'):
            if route_dir(cfg, 'failed', item, log_path):
                actions.append({'item': item.name, 'state': state})
            continue
        actions.append({'item': item.name, 'state': state})
    return actions


def increment_sku(base_sku, offset):
    base_sku = str(base_sku).strip()
    if not base_sku.isdigit():
        raise ValueError(f'non-numeric sku cannot be incremented: {base_sku}')
    width = len(base_sku)
    return str(int(base_sku) + offset).zfill(width)


def is_safe_member(member_name):
    p = Path(member_name)
    return not p.is_absolute() and '..' not in p.parts and member_name.strip() not in {'', '.', '/'}


def unzip_item(item_dir, cfg, api_cfg, log_path):
    zip_files = sorted([p for p in item_dir.iterdir() if p.is_file() and p.suffix.lower() == '.zip'])
    if not zip_files:
        return False

    archive_root = get_archive_root(api_cfg) / 'newitem-unzip'
    archive_root.mkdir(parents=True, exist_ok=True)

    for zip_path in zip_files:
        with TemporaryDirectory(prefix='tgw-unzip-') as td:
            extract_root = Path(td) / 'root'
            extract_root.mkdir(parents=True, exist_ok=True)

            with zipfile.ZipFile(zip_path) as zf:
                members = [m for m in zf.namelist() if not m.endswith('/')]
                if not members:
                    raise ValueError(f'zip has no files: {zip_path.name}')

                for member in members:
                    if not is_safe_member(member):
                        raise ValueError(f'unsafe zip member: {member}')

                zf.extractall(path=extract_root)

            extracted_files = [p for p in extract_root.rglob('*') if p.is_file()]
            top_dirs = sorted({
                p.relative_to(extract_root).parts[0]
                for p in extracted_files
                if len(p.relative_to(extract_root).parts) > 1
            })
            loose_files = [p for p in extract_root.iterdir() if p.is_file()]

            if top_dirs and not loose_files:
                json_path, data = read_primary_json(item_dir)
                if data is None:
                    raise ValueError(f'cannot split without valid seed json: {item_dir.name}')

                base_sku = str(data.get('sku', item_dir.name)).strip()
                base_location = data.get('location', '')
                parent_queue = item_dir.parent
                item_dir_name = item_dir.name

                for idx, top in enumerate(top_dirs):
                    src_dir = extract_root / top
                    new_sku = increment_sku(base_sku, idx)
                    new_dir = parent_queue / new_sku
                    new_dir.mkdir(parents=True, exist_ok=False)

                    new_json = dict(data)
                    new_json['sku'] = new_sku
                    new_json['location'] = base_location
                    (new_dir / f'{new_sku}.json').write_text(json.dumps(new_json, indent=2) + '\n')

                    for child in src_dir.rglob('*'):
                        if child.is_file():
                            dest = new_dir / child.name
                            if dest.exists():
                                raise ValueError(f'collision in split item {new_sku}: {dest.name}')
                            shutil.move(str(child), str(dest))

                    log_line(log_path, item_dir.parent.name, item_dir_name, f'split_derived_item_created dir={new_dir}')

                archived_zip = archive_root / f'{item_dir_name}--{zip_path.name}'
                shutil.move(str(zip_path), str(archived_zip))
                shutil.rmtree(item_dir)
                log_line(log_path, item_dir.parent.name, item_dir_name, f'multi_folder_zip_split_complete archived={archived_zip}')
                return True

            else:
                for child in extracted_files:
                    if child.suffix.lower() in MEDIA_EXTS:
                        dest = item_dir / child.name
                        if dest.exists():
                            raise ValueError(f'media collision on extract: {dest.name}')
                        shutil.move(str(child), str(dest))

                archived_zip = archive_root / f'{item_dir.name}--{zip_path.name}'
                shutil.move(str(zip_path), str(archived_zip))
                log_line(log_path, item_dir.parent.name, item_dir.name, f'zip_extracted_into_item archived={archived_zip}')

    return True

def stage_unzip_queue(cfg, api_cfg):
    log_path = get_log_path(cfg, api_cfg)
    if not queue_exists(cfg, 'newitem-unzip'):
        return []

    unzip_path = get_queue_path(cfg, 'newitem-unzip')
    results = []

    for item in sorted(unzip_path.iterdir()):
        if should_ignore_name(item.name) or not item.is_dir():
            continue

        try:
            unzip_item(item, cfg, api_cfg, log_path)
            state = classify_item(item) if item.exists() else 'split_complete'

            if item.exists() and state == 'ready_for_newitem-processing':
                route_dir(cfg, 'newitem-processing', item, log_path)
            elif item.exists() and state == 'waiting_for_location' and queue_exists(cfg, 'newitem-no-location'):
                route_dir(cfg, 'newitem-no-location', item, log_path)

            results.append({'item': item.name, 'result': state})

        except Exception as e:
            log_line(log_path, unzip_path.name, item.name, f'unzip_failed reason={e}')
            if queue_exists(cfg, 'failed'):
                route_dir(cfg, 'failed', item, log_path)
            results.append({'item': item.name, 'result': f'failed: {e}'})

    return results

def stage_processing_promote(cfg, api_cfg):
    worker = get_worker(cfg)
    log_path = get_log_path(cfg, api_cfg)
    processing_path = get_processing_path(cfg)
    bridge_path = get_bridge_path(cfg)
    itemdata_path = get_itemdata_path(cfg)
    moved = []
    for item in sorted(processing_path.iterdir()):
        if should_ignore_name(item.name) or not item.is_dir():
            continue
        itemdata_dst = itemdata_path / item.name
        if itemdata_dst.exists():
            log_line(log_path, processing_path.name, item.name, f'skipping_promote reason=itemdata_destination_exists dest={itemdata_dst}')
            continue
        shutil.move(str(item), str(itemdata_dst))
        write_queue_state_record(processing_path, item.name, itemdata_dst, 'waiting', log_path)
        create_bridge_link(itemdata_dst, bridge_path, log_path)
        moved.append(item.name)
        log_line(log_path, processing_path.name, item.name, f'promote_move src={item} dest={itemdata_dst}')
    return moved


def run_once(cfg, api_cfg):
    ensure_paths(cfg, api_cfg)
    intake = stage_watch_to_pool(cfg, api_cfg)
    dispatched = stage_pool_dispatch(cfg, api_cfg)
    unzipped = stage_unzip_queue(cfg, api_cfg)
    promoted = stage_processing_promote(cfg, api_cfg)
    return {
        'intake_moved': intake,
        'pool_dispatch': dispatched,
        'unzipped': unzipped,
        'promoted_to_itemdata': promoted,
    }


def loop_forever(cfg, api_cfg):
    log_path = get_log_path(cfg, api_cfg)
    log_line(log_path, get_worker(cfg)['queue_name'], '-', 'starting_worker_loop')
    scan_interval = get_worker(cfg).get('scan_interval', 10)
    while True:
        result = run_once(cfg, api_cfg)
        if any(result.values()):
            log_line(log_path, get_worker(cfg)['queue_name'], '-', f'cycle_result result={result}')
        time.sleep(scan_interval)


def main():
    cfg = load_config()
    api_cfg = load_api_config()
    if '--once' in sys.argv:
        result = run_once(cfg, api_cfg)
        print(json.dumps(result, indent=2))
        return
    loop_forever(cfg, api_cfg)


if __name__ == '__main__':
    main()
