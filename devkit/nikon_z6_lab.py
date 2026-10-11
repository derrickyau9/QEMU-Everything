"""Pinned, separately installed Nikon Z6 C3.80 assisted-menu laboratory.

Board and original menu/API research: WonderLantern/z6-qemu, GPL-2.0-or-later,
commit 419a1f2a91a1f2d3a0209027c29606475f7fe26f. No firmware or keys ship here.
This profile does not establish natural power-on, photography or live-view.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
import queue
import re
import socket
import stat
import subprocess
import threading
import time
import uuid

from .core import GuestLock, Task, checked_child, read_json, write_json
from .board_assets import board_directory

REVISION = '419a1f2a91a1f2d3a0209027c29606475f7fe26f'
SOURCE_SIZE = 34923366
SOURCE_SHA256 = 'ff978ffdf77c58c0b861a06e16d432d5886969f3cdd05d62f6c8e82e8a4cea54'
DECRYPTED_SHA256 = '843f662881bf1d3b0bf3750549b3ddfcedb7896d19e4db0d53e751c7d3334de3'
MODULE_SIZE = 33502840
MODULE_SHA256 = '2953fe932d00d160d9794d8dea0625aeaadf9e0cb82aebca37fe4a79e860ec35'
MAIN_OFFSET = 581844
MAIN_SIZE = MODULE_SIZE + 2
MAIN_SHA256 = '1e6348d58750e67372d0a8e363932c57ee9c04d0eb8f62803bcd19812df0d25c'
QEMU_VERSION = '10.2.4'
ARCHIVE_SHA256 = '821b545b92f165e57dddccac5077d76d4d436a226595b8813ad59306bbfd0746'
PORT = 8789
SCRIPT_HASHES = {'tools/interactive.py': '4e2da4aa0acdfe623de80bd6e8cf287fefc01e5051832235eac9db6dd6ff833d',
 'tools/probe.py': 'a90a19676b4ab1395e71a52014e800fd878f680029e78e07cced20f419a0a558',
 'tools/rsp.py': '616a175b0b0c68d32dcbe774c3ea54da4aaa8c94e2d3e376210cdecc60e202ed',
 'tools/focus_display.py': '3cb71eda138e3d89a0f7cc1bd7acffe4f8ac259a4c91d16985b46166e6cae0c9',
 'tools/capture_diagnostics.py': 'd7439e0ac09981b9006a5fc04153a824bc8d2855f28fa218c36343505e87a557',
 'tools/raw_input.py': '9acd60ef68594c5a78f00e2812261c0c78f442fa009cb3b21bf075cdaf1c8f34',
 'tools/cmos_config.py': '2cc72804d7cfd1f7608ea43236fbcd442d2a8a07378098360b2081ab422cbe3d',
 'tools/shoulder_display.py': '7cf35e065a39b34543bb7b26cf41fc70efe37532ceebf4bbf6a44d11e06f778e'}
BOARD_HASHES = {'qemu/nikon-functional-isp.h': 'd53ebf656d1bb5bf7fba70c749b7caa9f5cca0746344df89dbd46405076181c6',
 'qemu/nikon-gpu.h': 'e3f872b669ef1963b00422d73f924179e0bced6b0e8bb2cd0bc6eb90216c3d24',
 'qemu/nikon-isp-pixels.h': '327d4535af98bd2aeecbfa2d97a2ac5cd85a9ba56af8338f7ee6400228a6dbaa',
 'qemu/nikon-model-config.h': '489a5c2912e240904092061872f83e4c23a5f7c3b607b729737de3ff4997a690',
 'qemu/nikon-z6-profile.h': '301091acd26b268fd315741a17eed8e8940aa004a9d72ab61eb08b74a06a324a',
 'qemu/nikon-z6.c': '9a92f19510f6730312158079cea39717826648da0d245d766ed39ce412e9091c',
 'qemu/prepare_source.py': 'bc42fada385c161907a9de54492b437b78a4dd1a623b685ea96719eb1a91e258'}
RUNNER = board_directory('nikon-z6') / 'runner.py'


def _path(root: Path, name: str) -> Path:
    root = Path(root).absolute()
    if '..' in root.parts:
        raise ValueError('Lab root contains a parent path component')
    target = checked_child(root, name)
    candidate = root / name.replace('\\', '/')
    for item in (root, *root.parents, *list(candidate.parents)[:-1], candidate):
        try:
            metadata = item.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(metadata.st_mode) or getattr(metadata, 'st_file_attributes', 0) & 0x400:
            raise ValueError('Lab path contains a symlink or junction')
        if stat.S_ISREG(metadata.st_mode) and metadata.st_nlink != 1:
            raise ValueError('Lab file is hardlinked')
    return target


def _json(root, name, limit=256 * 1024):
    path = _path(root, name)
    if not path.is_file() or path.stat().st_size > limit:
        raise ValueError(f'Missing or oversized lab JSON: {name}')
    value = read_json(path)
    if not isinstance(value, dict):
        raise ValueError(f'Lab JSON must be an object: {name}')
    return value


def _hash(path: Path, task: Task) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        while block := stream.read(1024**2):
            task.check(); digest.update(block)
    return digest.hexdigest()


def _inputs(device: Path):
    metadata = _json(device, 'device.json')
    if metadata.get('sha256') != SOURCE_SHA256:
        raise ValueError('Z6 lab accepts only the exact official C3.80 update')
    inspection = _json(device, 'inspection.json')
    source_name = inspection.get('source_path', 'source/firmware.bin')
    if not isinstance(source_name, str):
        raise ValueError('Z6 source path must be a relative string')
    source = _path(device, source_name)
    manifest = _json(device, 'extracted/extraction.json')
    if manifest.get('source_sha256') != SOURCE_SHA256 or manifest.get('source_size') != SOURCE_SIZE:
        raise ValueError('Z6 extraction was not made from the fixed C3.80 source')
    artifacts = manifest.get('artifacts')
    if not isinstance(artifacts, list) or len(artifacts) > 4096:
        raise ValueError('Invalid Z6 extraction artifact list')
    wanted = {DECRYPTED_SHA256: (SOURCE_SIZE, None), MODULE_SHA256: (MODULE_SIZE, None)}
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            raise ValueError('Invalid Z6 extraction artifact')
        fingerprint = artifact.get('sha256')
        if not isinstance(fingerprint, str):
            raise ValueError('Invalid Z6 extraction fingerprint')
        if fingerprint in wanted:
            size, previous = wanted[fingerprint]
            if previous is not None or artifact.get('size') != size:
                raise ValueError('Duplicate or wrong-size Z6 module')
            if not isinstance(artifact.get('path'), str):
                raise ValueError('Z6 extraction path must be a relative string')
            path = _path(device, 'extracted/' + artifact['path'])
            if not path.is_file() or path.stat().st_size != size:
                raise ValueError('Z6 extraction artifact size changed')
            wanted[fingerprint] = (size, path)
    if source.stat().st_size != SOURCE_SIZE or any(item[1] is None for item in wanted.values()):
        raise ValueError('Z6 requires the production decrypted container and stripped main module')
    return source, wanted[DECRYPTED_SHA256][1], wanted[MODULE_SHA256][1]


def _config(home: Path):
    value = _json(home, 'nikon-z6-lab.json', 16384)
    if value.get('revision') != REVISION:
        raise ValueError('Z6 upstream revision is not the reviewed pin')
    distro = value.get('distro')
    if not isinstance(distro, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}', distro):
        raise ValueError('Z6 requires an explicit valid WSL distribution name')
    project = value.get('project')
    if not isinstance(project, str) or not Path(project).is_absolute():
        raise ValueError('Z6 project must be an absolute local path')
    _path(Path(project), 'tools/interactive.py')
    return value, Path(project)


def lab_available(device: Path, home: Path) -> bool:
    """Cheap GUI eligibility; full source/script/binary hashing occurs on launch."""
    try:
        _, project = _config(home)
        _inputs(device)
        return _path(project, '.build/build/qemu-system-arm').is_file()
    except (OSError, ValueError, TypeError, AttributeError, KeyError):
        return False


def _prepare_locked(device: Path, home: Path, task: Task):
    _config(home)
    source, decrypted, module = _inputs(device)
    for path, expected in ((source, SOURCE_SHA256), (decrypted, DECRYPTED_SHA256), (module, MODULE_SHA256)):
        if _hash(path, task) != expected:
            raise ValueError('Z6 source or extracted module changed; reimport/reextract')
    from .camera_formats.nikon import _validate
    entries = _validate(decrypted, task)
    entry = next((item for item in entries if item['output'] == 'module-003.bin'), None)
    if not entry or 32 + entry['body_offset'] != MAIN_OFFSET or entry['stored_size'] != MAIN_SIZE:
        raise ValueError('Z6 main extent differs from the fixed original directory')
    lab = _path(device, 'nikon-z6'); lab.mkdir(exist_ok=True)
    main = _path(device, 'nikon-z6/main.bin')
    record = {'source_sha256': SOURCE_SHA256, 'decrypted_sha256': DECRYPTED_SHA256,
              'brand': 'Nikon', 'model': 'Z6', 'firmware_version': 'C3.80',
              'stripped_module_sha256': MODULE_SHA256, 'main_offset': MAIN_OFFSET,
              'main_size': MAIN_SIZE, 'main_sha256': MAIN_SHA256, 'restored_crc16': 'f92d',
              'crc_origin': 'original decrypted container, verified original module/package CRC16',
              'source_revision': REVISION, 'firmware_file_modified': False,
              'natural_power_on_verified': False, 'assisted_startup': True,
              'guest_state_modified': True, 'camera_access': False}
    if main.exists():
        if main.stat().st_size != MAIN_SIZE or _hash(main, task) != MAIN_SHA256:
            raise ValueError('Existing Z6 prepared main differs; refusing to overwrite it')
    else:
        temporary = _path(device, 'nikon-z6/main-' + uuid.uuid4().hex + '.tmp')
        try:
            digest = hashlib.sha256()
            with decrypted.open('rb') as reader, module.open('rb') as stripped, temporary.open('xb') as writer:
                reader.seek(MAIN_OFFSET); remaining = MODULE_SIZE
                while remaining:
                    task.check(); block = reader.read(min(remaining, 1024**2))
                    if not block or stripped.read(len(block)) != block:
                        raise ValueError('Z6 original main does not match the production module')
                    writer.write(block); digest.update(block); remaining -= len(block)
                crc = reader.read(2)
                if crc != bytes.fromhex('f92d'):
                    raise ValueError('Z6 original CRC tail changed')
                writer.write(crc); digest.update(crc)
            if digest.hexdigest() != MAIN_SHA256:
                raise ValueError('Z6 restored main checksum mismatch')
            for path, expected in ((source, SOURCE_SHA256), (decrypted, DECRYPTED_SHA256), (module, MODULE_SHA256)):
                if _hash(path, task) != expected:
                    raise ValueError('Z6 input changed during preparation')
            task.check(); _path(device, 'nikon-z6/main.bin')
            if main.exists():
                raise ValueError('Z6 prepared destination appeared during preparation')
            temporary.rename(main)
        finally:
            temporary.unlink(missing_ok=True)
    # A previously prepared main is also subject to the same final source
    # observation; its valid hash must not hide a changed imported container.
    for path, expected in ((source, SOURCE_SHA256), (decrypted, DECRYPTED_SHA256), (module, MODULE_SHA256)):
        if _hash(path, task) != expected:
            raise ValueError('Z6 input changed during preparation')
    task.check()
    _path(device, 'nikon-z6/preparation.json.tmp')
    write_json(_path(device, 'nikon-z6/preparation.json'), record)
    return main, record


def prepare_lab(device: Path, home: Path, task: Task | None = None) -> dict:
    task = task or Task(); device = Path(device).absolute()
    _path(device, 'session.lock')
    with GuestLock(device):
        main, record = _prepare_locked(device, home, task)
    return {**record, 'main': str(main)}


def _wsl_path(path):
    value = PureWindowsPath(path)
    if not value.is_absolute() or not re.fullmatch(r'[A-Za-z]:', value.drive):
        raise ValueError('Z6 WSL paths require a local Windows drive')
    return '/mnt/' + value.drive[0].lower() + '/' + '/'.join(value.parts[1:])


def _build_command_locked(device, home, task, mode, seconds):
    config, project = _config(home)
    main, prepared = _prepare_locked(device, home, task)
    revision = task.run(['git', '-C', str(project), 'rev-parse', 'HEAD'], timeout=10).strip()
    if revision != REVISION:
        raise ValueError('Z6 project HEAD differs from the fixed upstream revision')
    for name, expected in {**SCRIPT_HASHES, **BOARD_HASHES}.items():
        if _hash(_path(project, name), task) != expected:
            raise ValueError(f'Z6 reviewed upstream source changed: {name}')
    manifest = _json(project, '.local/build-manifest.json')
    qemu = _path(project, '.build/build/qemu-system-arm')
    mapped = _wsl_path if os.name == 'nt' else str
    if (manifest.get('qemu_version') != QEMU_VERSION or
        manifest.get('upstream_archive_sha256') != ARCHIVE_SHA256 or
        manifest.get('sources') != BOARD_HASHES or manifest.get('binary') != mapped(qemu)):
        raise ValueError('Z6 QEMU build manifest does not match the reviewed profile')
    binary_hash = _hash(qemu, task)
    if not re.fullmatch('[0-9a-f]{64}', str(manifest.get('binary_sha256', ''))) or binary_hash != manifest['binary_sha256']:
        raise ValueError('Z6 QEMU binary differs from its build manifest')
    if mode == 'interactive':
        with socket.socket() as listener:
            try: listener.bind(('127.0.0.1', PORT))
            except OSError: raise RuntimeError('Z6 loopback port is busy; no existing process was stopped') from None
    identifier = uuid.uuid4().hex
    out = _path(device, 'nikon-z6/runs/' + identifier)
    out.mkdir(parents=True, exist_ok=False)
    runner = RUNNER.absolute()
    configuration = {'project': mapped(project), 'main': mapped(main), 'qemu': mapped(qemu),
                     'out': mapped(out), 'mode': mode, 'seconds': seconds, 'port': PORT,
                     'revision': REVISION, 'main_sha256': MAIN_SHA256, 'main_size': MAIN_SIZE,
                     'qemu_sha256': binary_hash, 'script_hashes': SCRIPT_HASHES,
                     'runner_sha256': _hash(runner, task)}
    prefix = ['wsl.exe', '-d', config['distro'], '--exec', 'python3', '-u', '-B', '-I'] if os.name == 'nt' else [os.sys.executable, '-u', '-B', '-I']
    return prefix + [mapped(runner), json.dumps(configuration)], f'http://127.0.0.1:{PORT}', out, prepared


def build_lab_command(device: Path, home: Path, task: Task | None = None):
    """Preflight and prepare a command; it has not started a guest."""
    task = task or Task(); device = Path(device).absolute(); _path(device, 'session.lock')
    with GuestLock(device):
        command, url, _, _ = _build_command_locked(device, home, task, 'interactive', 0)
    return command, url


def _run(device, home, task, mode, seconds, on_ready):
    device = Path(device).absolute(); _path(device, 'session.lock')
    with GuestLock(device):
        command, url, out, prepared = _build_command_locked(device, home, task, mode, seconds)
        record_path = _path(device, 'nikon-z6/session.json'); _path(device, 'nikon-z6/session.json.tmp')
        record = {**prepared, 'status': 'starting', 'command': command, 'mode': mode,
                  'output': str(out), 'url': url if mode == 'interactive' else None,
                  'menu_runtime_verified': False, 'original_ui_verified': False}
        write_json(record_path, record)
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        messages = queue.Queue(maxsize=64); stop_reader = threading.Event()
        reader = None; reader_started = False; ready = False; result = None
        def read_output():
            total = 0
            try:
                while not stop_reader.is_set():
                    line = process.stdout.readline(4097)
                    if not line: break
                    total += len(line)
                    if len(line) > 4096 or total > 256 * 1024:
                        line = b'{"event":"error","message":"Z6 supervisor output exceeds its bound"}\n'
                        stop_reader.set()
                    try: messages.put(line, timeout=.5)
                    except queue.Full:
                        stop_reader.set(); break
            finally:
                process.stdout.close()
        try:
            reader = threading.Thread(target=read_output, daemon=True); reader.start(); reader_started = True
            deadline = time.monotonic() + (seconds if mode == 'probe' else 50)
            task.report('Nikon Z6 C3.80 assisted menu / 原厂菜单辅助启动（非自然开机验证）')
            while True:
                task.check()
                if (mode == 'probe' or not ready) and time.monotonic() > deadline:
                    raise TimeoutError('Z6 lab did not finish its bounded startup/probe')
                try: raw = messages.get(timeout=.1)
                except queue.Empty:
                    if stop_reader.is_set(): raise RuntimeError('Z6 output reader stopped before completion')
                    if process.poll() is not None: break
                    continue
                try: event = json.loads(raw)
                except (ValueError, UnicodeError): continue
                if not isinstance(event, dict): continue
                if event.get('event') == 'ready' and not ready:
                    if mode != 'interactive' or event.get('url') != url:
                        raise RuntimeError('Z6 supervisor returned an unexpected local URL')
                    ready = True; record.update(status='running', session=event.get('session'))
                    write_json(record_path, record)
                    if on_ready: on_ready(url)
                elif event.get('event') == 'result':
                    if mode != 'probe' or not event.get('menu_runtime_verified'):
                        raise RuntimeError('Z6 result did not verify original menu execution')
                    result = event
                elif event.get('event') == 'error':
                    raise RuntimeError(str(event.get('message', 'Z6 lab failed'))[:4000])
                elif event.get('event') == 'stopped': break
            if mode == 'interactive' and not ready:
                raise RuntimeError('Z6 lab exited before its own menu server became ready')
            if mode == 'probe' and result is None:
                raise RuntimeError('Z6 probe exited without verified menu evidence')
            if process.poll() not in (None, 0):
                raise RuntimeError(f'Owned Z6 transport failed: {process.returncode}')
            return ({**result, 'output': str(out)} if mode == 'probe' else url)
        finally:
            errors = []
            if process.poll() is None:
                try: process.stdin.write(b'stop\n'); process.stdin.flush(); process.stdin.close()
                except (OSError, ValueError): pass
                try: process.wait(timeout=12)
                except subprocess.TimeoutExpired:
                    try:
                        process.terminate()
                        try: process.wait(timeout=3)
                        except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=3)
                    except (OSError, subprocess.TimeoutExpired) as error: errors.append(str(error))
            stop_reader.set()
            if reader_started: reader.join(timeout=1)
            for handle in (process.stdin, process.stdout):
                if handle is process.stdout and reader_started and reader.is_alive(): continue
                if handle and not handle.closed: handle.close()
            reaped = process.poll() is not None
            if process.returncode not in (None, 0): errors.append(f'Owned transport exit code {process.returncode}')
            record.update(status='stopped' if reaped and not errors else 'cleanup-incomplete', ready=ready,
                          process_reaped=reaped, transport_exit_code=process.returncode, cleanup_errors=errors)
            evidence_file = _path(out, 'evidence.json')
            if evidence_file.is_file():
                evidence = _json(out, 'evidence.json')
                record['menu_runtime_verified'] = evidence.get('menu_runtime_verified') is True
                record['original_ui_verified'] = evidence.get('original_ui_verified') is True
                record['owned_guest_processes_reaped'] = evidence.get('owned_guest_processes_reaped') is True
                if not record['owned_guest_processes_reaped']:
                    record['status'] = 'cleanup-incomplete'
            else:
                record['owned_guest_processes_reaped'] = False
                record['status'] = 'cleanup-incomplete'
            write_json(record_path, record)
            if os.sys.exc_info()[0] is None and record['status'] == 'cleanup-incomplete':
                raise RuntimeError('Owned Z6 guest cleanup could not be verified; inspect the saved session record')
            if os.sys.exc_info()[0] is None and mode == 'probe' and (
                    not record['menu_runtime_verified'] or not record['original_ui_verified'] or
                    evidence.get('single_instance_menu_roundtrip_verified') is not True):
                raise RuntimeError('Saved Z6 evidence did not confirm the original single-instance menu roundtrip')


def run_lab(device: Path, home: Path, task: Task | None = None, *, on_ready=None) -> str:
    """Serve the original guest menu until cancellation; stop only owned processes."""
    return _run(device, home, task or Task(), 'interactive', 0, on_ready)


def run_menu_probe(device: Path, home: Path, task: Task | None = None, *, seconds=60) -> dict:
    """One QEMU instance: original menu -> next item -> return, bounded 20..120 s."""
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not 20 <= seconds <= 120:
        raise ValueError('Z6 probe budget must be 20..120 seconds')
    return _run(device, home, task or Task(), 'probe', float(seconds), None)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('prepare', 'run', 'probe'))
    parser.add_argument('device', type=Path); parser.add_argument('--home', required=True, type=Path)
    parser.add_argument('--seconds', type=float, default=60)
    args = parser.parse_args(argv)
    if args.action == 'prepare': value = prepare_lab(args.device, args.home)
    elif args.action == 'probe': value = run_menu_probe(args.device, args.home, seconds=args.seconds)
    else: value = run_lab(args.device, args.home, on_ready=print)
    print(json.dumps(value, ensure_ascii=False, indent=2)); return 0


if __name__ == '__main__':
    raise SystemExit(main())
