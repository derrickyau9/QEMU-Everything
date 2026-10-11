"""Pinned, separately installed Nikon Z30 C1.20 assisted-menu laboratory.

Board and original menu/API research: WonderLantern/z30-qemu, GPL-2.0-or-later,
commit 0ba0c3547ccbec09e7c861eaca35db8e82795f15. No firmware or keys ship here.
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

REVISION = '0ba0c3547ccbec09e7c861eaca35db8e82795f15'
SOURCE_SIZE = 34496798
SOURCE_SHA256 = '1a5449ef505f1f347ed4e066a6a3f588ea542240680e83f4ebcae3b16da3434a'
DECRYPTED_SHA256 = '72bc27397ac2d859a8ba735c4c6fb2f223bc6b38ac5bcf9d69e5d8babab6dc26'
MODULE_SIZE = 33939512
MODULE_SHA256 = 'be78d644675bfc10bbb660208c65935016d7cd33e3069e3ceb44be2e78fc0f5b'
MAIN_OFFSET = 557268
MAIN_SIZE = MODULE_SIZE + 2
MAIN_SHA256 = '8a99ced2d8220729ba067587b0624bf30c81757abc5d728768134b1c7fd457fa'
QEMU_VERSION = '10.2.4'
ARCHIVE_SHA256 = '821b545b92f165e57dddccac5077d76d4d436a226595b8813ad59306bbfd0746'
PORT = 8788
SCRIPT_HASHES = {
    'tools/display_session.py': 'b3113d321524589eeb3c986f65cc74046a018d6eab75cebfa358c23c4e13126c',
    'tools/guest_call.py': 'e3cb98ef8747f741d082faf8bd6f9f8e04415ca1b60b1e8764263f51dae42b79',
    'tools/rsp.py': '616a175b0b0c68d32dcbe774c3ea54da4aaa8c94e2d3e376210cdecc60e202ed',
    'tools/run.py': 'a40513477a59344ca320ce7f2f014e1d3a1eba4724e196eaf47109b4d1adcf57',
}
BOARD_HASHES = {
    'qemu/nikon-z30.c': '0d20bd467fd92d0d1c62ebb3516fad29e6bc43c53ea996d25d0ece2c8edbb83b',
    'qemu/prepare_source.py': 'caca3a6d529eea4cec70489aaffa2625b43b4cbce678a5fd5949f72b2357fd59',
    'qemu/nikon-z30-display.c': '1ec30e9d5b0548521fc35b9847d72f8cca8132407a48806845d86557bb268eb1',
    'qemu/nikon-z30-gpu.h': 'd2294dc84a7b05cc29c124459a58f48cc3f82dfa253ce8c555b00ecfef2d5977',
    'qemu/nikon-z30-isp.h': '62fca2e2ae1c8de978d3795985b75391133f870738d87eba0258c05d10e0e53a',
    'qemu/nikon-z30-isp-pixels.h': 'a5554b9e7801e0061fc35dc19b9d73bb6bb9ddd3e81993271440cfc31deeca27',
    'qemu/z30-display-map.h': '3ede39d73b844c0f7ed3998d2b143deec5a89ab7ae8c88f957230e89c7a788c7',
}
RUNNER = board_directory('nikon-z30') / 'runner.py'


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
        raise ValueError('Z30 lab accepts only the exact official C1.20 update')
    inspection = _json(device, 'inspection.json')
    source_name = inspection.get('source_path', 'source/firmware.bin')
    if not isinstance(source_name, str):
        raise ValueError('Z30 source path must be a relative string')
    source = _path(device, source_name)
    manifest = _json(device, 'extracted/extraction.json')
    if manifest.get('source_sha256') != SOURCE_SHA256 or manifest.get('source_size') != SOURCE_SIZE:
        raise ValueError('Z30 extraction was not made from the fixed C1.20 source')
    artifacts = manifest.get('artifacts')
    if not isinstance(artifacts, list) or len(artifacts) > 4096:
        raise ValueError('Invalid Z30 extraction artifact list')
    wanted = {DECRYPTED_SHA256: (SOURCE_SIZE, None), MODULE_SHA256: (MODULE_SIZE, None)}
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            raise ValueError('Invalid Z30 extraction artifact')
        fingerprint = artifact.get('sha256')
        if not isinstance(fingerprint, str):
            raise ValueError('Invalid Z30 extraction fingerprint')
        if fingerprint in wanted:
            size, previous = wanted[fingerprint]
            if previous is not None or artifact.get('size') != size:
                raise ValueError('Duplicate or wrong-size Z30 module')
            if not isinstance(artifact.get('path'), str):
                raise ValueError('Z30 extraction path must be a relative string')
            path = _path(device, 'extracted/' + artifact['path'])
            if not path.is_file() or path.stat().st_size != size:
                raise ValueError('Z30 extraction artifact size changed')
            wanted[fingerprint] = (size, path)
    if source.stat().st_size != SOURCE_SIZE or any(item[1] is None for item in wanted.values()):
        raise ValueError('Z30 requires the production decrypted container and stripped main module')
    return source, wanted[DECRYPTED_SHA256][1], wanted[MODULE_SHA256][1]


def _config(home: Path):
    value = _json(home, 'nikon-z30-lab.json', 16384)
    if value.get('revision') != REVISION:
        raise ValueError('Z30 upstream revision is not the reviewed pin')
    distro = value.get('distro')
    if not isinstance(distro, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}', distro):
        raise ValueError('Z30 requires an explicit valid WSL distribution name')
    project = value.get('project')
    if not isinstance(project, str) or not Path(project).is_absolute():
        raise ValueError('Z30 project must be an absolute local path')
    _path(Path(project), 'tools/display_session.py')
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
            raise ValueError('Z30 source or extracted module changed; reimport/reextract')
    from .camera_formats.nikon import _validate
    entries = _validate(decrypted, task)
    entry = next((item for item in entries if item['output'] == 'module-003.bin'), None)
    if not entry or 32 + entry['body_offset'] != MAIN_OFFSET or entry['stored_size'] != MAIN_SIZE:
        raise ValueError('Z30 main extent differs from the fixed original directory')
    lab = _path(device, 'nikon-z30'); lab.mkdir(exist_ok=True)
    main = _path(device, 'nikon-z30/main.bin')
    record = {'source_sha256': SOURCE_SHA256, 'decrypted_sha256': DECRYPTED_SHA256,
              'brand': 'Nikon', 'model': 'Z30', 'firmware_version': 'C1.20',
              'stripped_module_sha256': MODULE_SHA256, 'main_offset': MAIN_OFFSET,
              'main_size': MAIN_SIZE, 'main_sha256': MAIN_SHA256, 'restored_crc16': '5bd5',
              'crc_origin': 'original decrypted container, verified original module/package CRC16',
              'source_revision': REVISION, 'firmware_file_modified': False,
              'natural_power_on_verified': False, 'assisted_startup': True,
              'guest_state_modified': True, 'camera_access': False}
    if main.exists():
        if main.stat().st_size != MAIN_SIZE or _hash(main, task) != MAIN_SHA256:
            raise ValueError('Existing Z30 prepared main differs; refusing to overwrite it')
    else:
        temporary = _path(device, 'nikon-z30/main-' + uuid.uuid4().hex + '.tmp')
        try:
            digest = hashlib.sha256()
            with decrypted.open('rb') as reader, module.open('rb') as stripped, temporary.open('xb') as writer:
                reader.seek(MAIN_OFFSET); remaining = MODULE_SIZE
                while remaining:
                    task.check(); block = reader.read(min(remaining, 1024**2))
                    if not block or stripped.read(len(block)) != block:
                        raise ValueError('Z30 original main does not match the production module')
                    writer.write(block); digest.update(block); remaining -= len(block)
                crc = reader.read(2)
                if crc != bytes.fromhex('5bd5'):
                    raise ValueError('Z30 original CRC tail changed')
                writer.write(crc); digest.update(crc)
            if digest.hexdigest() != MAIN_SHA256:
                raise ValueError('Z30 restored main checksum mismatch')
            for path, expected in ((source, SOURCE_SHA256), (decrypted, DECRYPTED_SHA256), (module, MODULE_SHA256)):
                if _hash(path, task) != expected:
                    raise ValueError('Z30 input changed during preparation')
            task.check(); _path(device, 'nikon-z30/main.bin')
            if main.exists():
                raise ValueError('Z30 prepared destination appeared during preparation')
            temporary.rename(main)
        finally:
            temporary.unlink(missing_ok=True)
    _path(device, 'nikon-z30/preparation.json.tmp')
    write_json(_path(device, 'nikon-z30/preparation.json'), record)
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
        raise ValueError('Z30 WSL paths require a local Windows drive')
    return '/mnt/' + value.drive[0].lower() + '/' + '/'.join(value.parts[1:])


def _build_command_locked(device, home, task, mode, seconds):
    config, project = _config(home)
    main, prepared = _prepare_locked(device, home, task)
    revision = task.run(['git', '-C', str(project), 'rev-parse', 'HEAD'], timeout=10).strip()
    if revision != REVISION:
        raise ValueError('Z30 project HEAD differs from the fixed upstream revision')
    for name, expected in {**SCRIPT_HASHES, **BOARD_HASHES}.items():
        if _hash(_path(project, name), task) != expected:
            raise ValueError(f'Z30 reviewed upstream source changed: {name}')
    manifest = _json(project, '.local/build-manifest.json')
    qemu = _path(project, '.build/build/qemu-system-arm')
    mapped = _wsl_path if os.name == 'nt' else str
    if (manifest.get('qemu_version') != QEMU_VERSION or
        manifest.get('upstream_archive_sha256') != ARCHIVE_SHA256 or
        manifest.get('sources') != BOARD_HASHES or manifest.get('binary') != mapped(qemu)):
        raise ValueError('Z30 QEMU build manifest does not match the reviewed profile')
    binary_hash = _hash(qemu, task)
    if not re.fullmatch('[0-9a-f]{64}', str(manifest.get('binary_sha256', ''))) or binary_hash != manifest['binary_sha256']:
        raise ValueError('Z30 QEMU binary differs from its build manifest')
    if mode == 'interactive':
        with socket.socket() as listener:
            try: listener.bind(('127.0.0.1', PORT))
            except OSError: raise RuntimeError('Z30 loopback port is busy; no existing process was stopped') from None
    identifier = uuid.uuid4().hex
    out = _path(device, 'nikon-z30/runs/' + identifier)
    out.mkdir(parents=True, exist_ok=False)
    runner = RUNNER.absolute()
    configuration = {'project': mapped(project), 'main': mapped(main), 'qemu': mapped(qemu),
                     'out': mapped(out), 'mode': mode, 'seconds': seconds, 'port': PORT,
                     'revision': REVISION, 'main_sha256': MAIN_SHA256, 'main_size': MAIN_SIZE,
                     'qemu_sha256': binary_hash, 'script_hashes': SCRIPT_HASHES,
                     'runner_sha256': _hash(runner, task)}
    prefix = ['wsl.exe', '-d', config['distro'], '--exec', 'python3', '-u', '-B'] if os.name == 'nt' else [os.sys.executable, '-u', '-B']
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
        record_path = _path(device, 'nikon-z30/session.json'); _path(device, 'nikon-z30/session.json.tmp')
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
                        line = b'{"event":"error","message":"Z30 supervisor output exceeds its bound"}\n'
                        stop_reader.set()
                    try: messages.put(line, timeout=.5)
                    except queue.Full:
                        stop_reader.set(); break
            finally:
                process.stdout.close()
        try:
            reader = threading.Thread(target=read_output, daemon=True); reader.start(); reader_started = True
            deadline = time.monotonic() + (seconds if mode == 'probe' else 50)
            task.report('Nikon Z30 C1.20 assisted menu / 原厂菜单辅助启动（非自然开机验证）')
            while True:
                task.check()
                if (mode == 'probe' or not ready) and time.monotonic() > deadline:
                    raise TimeoutError('Z30 lab did not finish its bounded startup/probe')
                try: raw = messages.get(timeout=.1)
                except queue.Empty:
                    if stop_reader.is_set(): raise RuntimeError('Z30 output reader stopped before completion')
                    if process.poll() is not None: break
                    continue
                try: event = json.loads(raw)
                except (ValueError, UnicodeError): continue
                if not isinstance(event, dict): continue
                if event.get('event') == 'ready' and not ready:
                    if mode != 'interactive' or event.get('url') != url:
                        raise RuntimeError('Z30 supervisor returned an unexpected local URL')
                    ready = True; record.update(status='running', session=event.get('session'))
                    write_json(record_path, record)
                    if on_ready: on_ready(url)
                elif event.get('event') == 'result':
                    if mode != 'probe' or not event.get('menu_runtime_verified'):
                        raise RuntimeError('Z30 result did not verify original menu execution')
                    result = event
                elif event.get('event') == 'error':
                    raise RuntimeError(str(event.get('message', 'Z30 lab failed'))[:4000])
                elif event.get('event') == 'stopped': break
            if mode == 'interactive' and not ready:
                raise RuntimeError('Z30 lab exited before its own menu server became ready')
            if mode == 'probe' and result is None:
                raise RuntimeError('Z30 probe exited without verified menu evidence')
            if process.poll() not in (None, 0):
                raise RuntimeError(f'Owned Z30 transport failed: {process.returncode}')
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
                raise RuntimeError('Owned Z30 guest cleanup could not be verified; inspect the saved session record')
            if os.sys.exc_info()[0] is None and mode == 'probe' and (
                    not record['menu_runtime_verified'] or not record['original_ui_verified'] or
                    evidence.get('single_instance_menu_roundtrip_verified') is not True):
                raise RuntimeError('Saved Z30 evidence did not confirm the original single-instance menu roundtrip')


def run_lab(device: Path, home: Path, task: Task | None = None, *, on_ready=None) -> str:
    """Serve the original guest menu until cancellation; stop only owned processes."""
    return _run(device, home, task or Task(), 'interactive', 0, on_ready)


def run_menu_probe(device: Path, home: Path, task: Task | None = None, *, seconds=60) -> dict:
    """One QEMU instance: original menu -> Add items -> return, bounded 20..120 s."""
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not 20 <= seconds <= 120:
        raise ValueError('Z30 probe budget must be 20..120 seconds')
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
