"""Exact EOS 70D 1.1.2 ROM-set UI lab; update FIR files are not boot ROMs.

Original reset/menus execute inside pinned qemu-eos, without GDB GUI hooks.
ROM files are separately supplied by the user and are never bundled.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import queue
import re
import socket
import stat
import subprocess
import threading
import time
import uuid

from .board_assets import board_directory
from .canon_lab import _regular
from .core import GuestLock, Task, checked_child, read_json, write_json

PORT = 8793
REVISION = '4b667a1d3c08ab7a55835d15ddbd884fa754946d'
PROFILE = 'canon-70d-112-rom-ui-v1'
ROM_FILES = {
    'ROM0.BIN': (8388608, '841f20ec42c4f20032d2c472fb44ff0f55f1353691233e6fd18757836d3647bc'),
    'ROM1.BIN': (16777216, '3fe42299deb577b269b6901f40597a1939ac5876f22c8c3a0b6ee47219fdd42e'),
    'SFDATA.BIN': (16777216, 'a72aa76c6d58bf9d288cf845d75814508e92387b203de63086f9dccff14ad1ab'),
}
RUNNER = board_directory('canon') / 'run_ui.py'


def _path(root, name):
    root = Path(root).absolute()
    if '..' in root.parts: raise ValueError('Parent component in Canon lab path')
    target = checked_child(root, name)
    for item in (root / name.replace('\\', '/'), *root.parents):
        # Include every raw target ancestor, before checked_child resolves it.
        for part in (item, *item.parents):
            try: meta = part.lstat()
            except FileNotFoundError: continue
            if stat.S_ISLNK(meta.st_mode) or getattr(meta, 'st_file_attributes', 0) & 0x400:
                raise ValueError('Canon lab path contains a symlink/junction')
            if stat.S_ISREG(meta.st_mode) and meta.st_nlink != 1:
                raise ValueError('Canon lab file is hardlinked')
    return target


def _json(root, name, limit=16384):
    path = _path(root, name)
    if not path.is_file() or path.stat().st_size > limit: raise ValueError('Missing/oversized Canon JSON: ' + name)
    value = read_json(path)
    if not isinstance(value, dict): raise ValueError('Canon JSON must be an object')
    return value


def _hash(path, task):
    data = _regular(path, task, limit=32 * 1024**2)
    return hashlib.sha256(data).hexdigest()


def _config(home):
    value = _json(home, 'canon-ui-lab.json')
    if value.get('revision') != REVISION: raise ValueError('Canon QEMU revision is not the reviewed pin')
    distro = value.get('distro')
    if not isinstance(distro, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}', distro):
        raise ValueError('Select an explicit WSL distribution for Canon')
    qemu = value.get('qemu')
    if (not isinstance(qemu, str) or not qemu.startswith('/') or
            any(c in qemu for c in ('\\', '\0', '\n', '\r')) or '..' in qemu.split('/')):
        raise ValueError('Canon QEMU must have an absolute Linux path')
    if not isinstance(value.get('qemu_sha256'), str) or not re.fullmatch('[0-9a-f]{64}', value['qemu_sha256']):
        raise ValueError('Record the separately built Canon QEMU SHA256')
    return value


def _inputs(device):
    metadata = _json(device, 'device.json')
    if (metadata.get('adapter') != 'canon-rom-set' or metadata.get('sha256') != ROM_FILES['ROM1.BIN'][1]
            or metadata.get('version') != '1.1.2' or metadata.get('model') != 'EOS 70D'):
        raise ValueError('Canon UI requires the exact separately imported EOS 70D 1.1.2 ROM set')
    manifest = _json(device, 'canon-roms.json')
    if manifest.get('profile') != PROFILE or manifest.get('revision') != REVISION:
        raise ValueError('Unreviewed Canon ROM profile')
    files = manifest.get('files')
    if not isinstance(files, list) or len(files) != 3:
        raise ValueError('Canon requires exactly ROM0, ROM1 and serial-flash data')
    expected = {name: (size, digest, f'roms/70D/{name}') for name, (size, digest) in ROM_FILES.items()}
    found = {}
    for row in files:
        if not isinstance(row, dict) or row.get('name') not in expected or row['name'] in found:
            raise ValueError('Duplicate/unknown Canon ROM manifest file')
        size, digest, name = expected[row['name']]
        if row.get('path') != name or row.get('size') != size or row.get('sha256') != digest:
            raise ValueError('Canon ROM manifest checksum/extent mismatch')
        path = _path(device, name)
        if not path.is_file() or path.stat().st_size != size: raise ValueError('Canon ROM file size changed')
        found[row['name']] = path
    return found


def import_roms(rom0: Path, rom1: Path, sf: Path, destination: Path, task: Task | None = None) -> Path:
    """Import a new private library entry, accepting only the reviewed ROM set."""
    task = task or Task(); destination = Path(destination).absolute()
    _path(destination.parent, destination.name)
    if destination.exists(): raise FileExistsError('Choose a new Canon ROM entry directory')
    data = {}
    for name, path in zip(ROM_FILES, (rom0, rom1, sf)):
        size, digest = ROM_FILES[name]
        blob = _regular(Path(path), task, expected=size, limit=32 * 1024**2)
        if hashlib.sha256(blob).hexdigest() != digest:
            raise ValueError('Canon ROM does not match the reviewed EOS 70D 1.1.2 profile: ' + name)
        data[name] = blob
    destination.parent.mkdir(parents=True, exist_ok=True)
    import tempfile
    with tempfile.TemporaryDirectory(prefix='canon-rom-import-', dir=destination.parent) as temporary:
        stage = Path(temporary) / 'entry'; (stage / 'roms' / '70D').mkdir(parents=True)
        (stage / 'source').mkdir()
        files = []
        for name, blob in data.items():
            task.check()
            target = stage / 'roms' / '70D' / name
            with target.open('xb') as stream:
                for offset in range(0, len(blob), 65536): task.check(); stream.write(blob[offset:offset + 65536])
            files.append(dict(name=name, path=f'roms/70D/{name}', size=len(blob), sha256=ROM_FILES[name][1]))
        (stage / 'source' / 'firmware.bin').write_bytes(data['ROM1.BIN'])
        write_json(stage / 'canon-roms.json', dict(profile=PROFILE, revision=REVISION, files=files,
                   original_ui_verified=False, vendor_distribution_included=False))
        write_json(stage / 'device.json', dict(id='canon-70d-112-' + ROM_FILES['ROM1.BIN'][1][:16],
                   adapter='canon-rom-set', format='canon-rom-set', brand='Canon', model='EOS 70D',
                   name='Canon EOS 70D 1.1.2 — user-supplied ROM set', version='1.1.2', firmware_version='1.1.2',
                   architecture='arm', filename='ROM1.BIN', sha256=ROM_FILES['ROM1.BIN'][1],
                   kind='user-supplied-rom-set', original_ui_verified=False))
        write_json(stage / 'inspection.json', dict(source_path='source/firmware.bin', format='canon-rom-set',
                   architecture='arm', filename='ROM1.BIN', sha256=ROM_FILES['ROM1.BIN'][1], size=ROM_FILES['ROM1.BIN'][0],
                   findings=['Separately supplied ROMs; not a Canon update FIR'], candidates=[]))
        for name, path in zip(ROM_FILES, (rom0, rom1, sf)):
            if _hash(Path(path), task) != ROM_FILES[name][1]: raise ValueError('Canon input changed during import')
        task.check(); stage.rename(destination)
    return destination


def lab_available(device: Path, home: Path) -> bool:
    """Cheap eligibility; exact source hashes are checked again on launch."""
    try: _config(home); _inputs(device); return RUNNER.is_file()
    except (OSError, ValueError, TypeError, AttributeError, KeyError): return False


def _linux_path(path):
    path = Path(path).absolute()
    if os.name != 'nt': return str(path)
    if not re.fullmatch('[A-Za-z]:', path.drive): raise ValueError('Canon WSL paths require a local drive')
    return '/mnt/' + path.drive[0].lower() + path.as_posix()[2:]


def _run(device, home, task, mode, seconds, on_ready):
    device = Path(device).absolute(); _path(device, 'session.lock')
    with GuestLock(device):
        previous = _path(device, 'canon-ui/session.json')
        if previous.is_file():
            state = _json(device, 'canon-ui/session.json', 65536)
            if state.get('status') == 'cleanup-incomplete' and (
                    state.get('process_reaped') is not True or state.get('owned_guest_processes_reaped') is not True):
                raise RuntimeError('Previous Canon cleanup is unconfirmed; resolve the saved session before restarting')
        config = _config(home); paths = _inputs(device)
        for name, path in paths.items():
            if _hash(path, task) != ROM_FILES[name][1]: raise ValueError('Canon ROM source changed; reimport')
        if mode == 'interactive':
            with socket.socket() as listener:
                try: listener.bind(('127.0.0.1', PORT))
                except OSError: raise RuntimeError('Canon port is busy; no existing process was stopped') from None
        root = _path(device, 'canon-ui'); root.mkdir(exist_ok=True)
        out = _path(device, 'canon-ui/runs/' + uuid.uuid4().hex)
        runner = _path(RUNNER.parent, RUNNER.name)
        payload = dict(revision=REVISION, qemu=config['qemu'], qemu_sha256=config['qemu_sha256'],
                       roms=_linux_path(paths['ROM0.BIN'].parent), destination=_linux_path(out),
                       mode=mode, seconds=seconds, port=PORT)
        prefix = ['wsl.exe', '-d', config['distro'], '--exec', 'python3', '-u', '-B', '-I'] if os.name == 'nt' else [os.sys.executable, '-u', '-B', '-I']
        command = prefix + [_linux_path(runner), json.dumps(payload)]
        url = f'http://127.0.0.1:{PORT}'
        record_path = _path(device, 'canon-ui/session.json'); _path(device, 'canon-ui/session.json.tmp')
        record = dict(profile=PROFILE, status='starting', mode=mode, command=command, output=str(out),
                      url=url if mode == 'interactive' else None, assisted_startup=False,
                      firmware_instructions_patched=False, original_ui_verified=False, native_navigation_verified=False)
        write_json(record_path, record)
        task.check()
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        messages = queue.Queue(maxsize=64); stop_reader = threading.Event(); reader_started = False; ready = False; result = None
        def read_output():
            total = 0
            try:
                while not stop_reader.is_set():
                    line = process.stdout.readline(4097)
                    if not line: break
                    total += len(line)
                    if len(line) > 4096 or total > 256 * 1024:
                        line = b'{"event":"error","message":"Canon supervisor output exceeds its bound"}\n'; stop_reader.set()
                    try: messages.put(line, timeout=.5)
                    except queue.Full: stop_reader.set(); break
            finally: process.stdout.close()
        try:
            reader = threading.Thread(target=read_output, daemon=True); reader.start(); reader_started = True
            deadline = time.monotonic() + seconds + 20
            task.report('Canon EOS 70D 1.1.2 original ROM menu / 原厂 ROM 菜单启动')
            while True:
                task.check()
                if (mode == 'probe' or not ready) and time.monotonic() > deadline:
                    raise TimeoutError('Canon lab exceeded its startup/probe budget')
                try: raw = messages.get(timeout=.1)
                except queue.Empty:
                    if stop_reader.is_set(): raise RuntimeError('Canon output reader stopped before completion')
                    if process.poll() is not None: break
                    continue
                try: event = json.loads(raw)
                except (ValueError, UnicodeError): continue
                if not isinstance(event, dict): continue
                if event.get('event') == 'ready' and not ready:
                    if mode != 'interactive' or event.get('url') != url: raise RuntimeError('Unexpected Canon local URL')
                    ready = True; record.update(status='running', session=event.get('session')); write_json(record_path, record)
                    if on_ready: on_ready(url)
                elif event.get('event') == 'result':
                    if mode != 'probe' or event.get('native_navigation_verified') is not True or event.get('original_ui_verified') is not True:
                        raise RuntimeError('Canon result did not verify original menu pixels/input')
                    result = event
                elif event.get('event') == 'error': raise RuntimeError(str(event.get('message', 'Canon lab failed'))[:2000])
                elif event.get('event') == 'stopped': break
            if mode == 'interactive' and not ready: raise RuntimeError('Canon lab exited before original menu readiness')
            if mode == 'probe' and result is None: raise RuntimeError('Canon probe exited without verified menu evidence')
            return {**result, 'output': str(out)} if mode == 'probe' else url
        finally:
            cleanup_errors = []
            if process.poll() is None:
                try: process.stdin.write(b'stop\n'); process.stdin.flush(); process.stdin.close()
                except (OSError, ValueError): pass
                try:
                    try: process.wait(timeout=12)
                    except subprocess.TimeoutExpired:
                        process.terminate()
                        try: process.wait(timeout=3)
                        except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=3)
                except (OSError, subprocess.TimeoutExpired) as error:
                    cleanup_errors.append(str(error)[:1000])
            stop_reader.set()
            if reader_started: reader.join(timeout=1)
            for handle in (process.stdin, process.stdout):
                if handle is process.stdout and reader_started and reader.is_alive(): continue
                if handle and not handle.closed: handle.close()
            record.update(status='stopped', ready=ready, process_reaped=process.poll() is not None,
                          transport_exit_code=process.returncode, owned_guest_processes_reaped=False,
                          cleanup_errors=cleanup_errors)
            evidence_path = _path(out, 'evidence.json')
            if evidence_path.is_file():
                evidence = _json(out, 'evidence.json', 65536)
                record.update(original_ui_verified=evidence.get('original_ui_verified') is True,
                              native_navigation_verified=evidence.get('native_navigation_verified') is True,
                              original_reset_boot_verified=evidence.get('original_reset_boot_verified') is True,
                              owned_guest_processes_reaped=evidence.get('process_reaped') is True)
            if cleanup_errors or process.returncode != 0 or not record['process_reaped'] or not record['owned_guest_processes_reaped']:
                record['status'] = 'cleanup-incomplete'
            write_json(record_path, record)
            if os.sys.exc_info()[0] is None:
                if record['status'] == 'cleanup-incomplete': raise RuntimeError('Canon guest cleanup was not verified; inspect session.json')
                if mode == 'probe' and (not record['original_ui_verified'] or not record['native_navigation_verified']):
                    raise RuntimeError('Saved Canon evidence does not confirm the original menu/input')


def run_lab(device: Path, home: Path, task: Task | None = None, *, on_ready=None) -> str:
    return _run(device, home, task or Task(), 'interactive', 45, on_ready)


def run_menu_probe(device: Path, home: Path, task: Task | None = None, *, seconds=30) -> dict:
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not 26 <= seconds <= 120:
        raise ValueError('Canon probe budget must be 26..120 seconds')
    return _run(device, home, task or Task(), 'probe', float(seconds), None)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    imported = commands.add_parser('import-roms')
    for name in ('rom0', 'rom1', 'output'): imported.add_argument('--' + name, type=Path, required=True)
    imported.add_argument('--sf', '--sfdata', dest='sf', type=Path, required=True)
    for name in ('run', 'check'):
        command = commands.add_parser(name); command.add_argument('device', type=Path); command.add_argument('--home', type=Path, required=True)
        if name == 'check': command.add_argument('--seconds', type=float, default=30)
    args = parser.parse_args(argv)
    if args.command == 'import-roms': print(import_roms(args.rom0, args.rom1, args.sf, args.output))
    elif args.command == 'check': print(json.dumps(run_menu_probe(args.device, args.home, seconds=args.seconds), indent=2))
    else: run_lab(args.device, args.home, on_ready=lambda url: print(url, flush=True))
    return 0


if __name__ == '__main__': raise SystemExit(main())
