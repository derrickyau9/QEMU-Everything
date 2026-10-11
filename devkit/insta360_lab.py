"""Bounded original X3 Linux/BusyBox bring-up; RTOS and original UI are absent."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
import queue
import re
import stat
import subprocess
import threading
import time
import uuid

from .core import GuestLock, Task, checked_child, read_json, write_json

EXPECTED = {
    'kernel.bin': (7788552, 'f1dd76309a3d7e6d0f6cf9d4b4b9b3c2ca55384afbbd97ebb5995a70dd6f3295'),
    'rootfs.squashfs': (15708160, '31aa64fd800d78288fe28c8ae80f6a7b4a7c5a523b1f2dd3417d7a4b1056a89b'),
}
from .board_assets import board_directory

OVERLAY = board_directory('insta360')


def _device_path(device: Path, name: str) -> Path:
    device = Path(device).absolute()
    target = checked_child(device, name)
    current = device
    for component in ('', *name.replace('\\', '/').split('/')):
        if component:
            current /= component
        try:
            metadata = current.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(metadata.st_mode) or getattr(metadata, 'st_file_attributes', 0) & 0x400:
            raise ValueError('Camera guest path contains a symlink or junction')
    return target


def _config(home: Path) -> dict:
    config = read_json(Path(home) / 'insta360-lab.json')
    if not isinstance(config, dict):
        raise ValueError('Configure the separate H22 board build in insta360-lab.json')
    distro = config.get('distro')
    if not isinstance(distro, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}', distro):
        raise ValueError('Select an explicit WSL distribution for the H22 lab')
    manifest = config.get('manifest')
    if not isinstance(manifest, str) or not manifest.startswith('/') or any(c in manifest for c in ('\\', '\0', '\n', '\r')):
        raise ValueError('H22 build manifest must be an absolute Linux path')
    return config


def _artifacts(device: Path) -> dict[str, Path]:
    data = read_json(_device_path(device, 'extracted/extraction.json'))
    if not isinstance(data, dict) or not isinstance(data.get('artifacts'), list):
        raise ValueError('Requires a validated Insta360 X3 extraction')
    artifacts = {}
    for name, (size, sha) in EXPECTED.items():
        matches = [item for item in data['artifacts'] if isinstance(item, dict) and
                   item.get('sha256') == sha and item.get('size') == size]
        if len(matches) != 1 or not isinstance(matches[0].get('path'), str):
            raise ValueError('Requires the known original X3 artifact: ' + name)
        path = _device_path(device, 'extracted/' + matches[0]['path'])
        if not stat.S_ISREG(path.stat().st_mode) or path.stat().st_size != size:
            raise ValueError('X3 artifact is missing or changed: ' + name)
        artifacts[name] = path
    return artifacts


def lab_available(device: Path, home: Path) -> bool:
    """Cheap exact-artifact eligibility; no process launch or firmware hashing."""
    try:
        _config(home); _artifacts(device)
        return True
    except (ValueError, OSError, TypeError, AttributeError):
        return False


def _hash(path: Path, task: Task) -> str:
    sha = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024**2), b''):
            task.check(); sha.update(block)
    return sha.hexdigest()


def _linux_path(path: Path) -> str:
    if os.name != 'nt':
        return str(path)
    parsed = PureWindowsPath(str(path))
    if not parsed.is_absolute() or len(parsed.drive) != 2 or parsed.drive[1] != ':':
        raise ValueError('Camera WSL bridge needs a local Windows drive path')
    return '/mnt/' + parsed.drive[0].lower() + '/' + '/'.join(parsed.parts[1:])


def build_lab_command(device: Path, home: Path, destination: Path, task: Task,
                      seconds: int) -> list[str]:
    if not isinstance(seconds, int) or isinstance(seconds, bool) or not 5 <= seconds <= 120:
        raise ValueError('X3 lab duration must be 5..120 seconds')
    config = _config(home)
    artifacts = _artifacts(device)
    for name, path in artifacts.items():
        if _hash(path, task) != EXPECTED[name][1]:
            raise ValueError('Original X3 artifact SHA-256 changed: ' + name)
    script = OVERLAY / 'run.py'
    if not script.is_file() or script.is_symlink():
        raise ValueError('The H22 board supervisor is missing')
    payload = {'manifest': config['manifest'], 'seconds': seconds,
               'destination': _linux_path(destination),
               'artifacts': {name: _linux_path(path) for name, path in artifacts.items()}}
    prefix = ['wsl.exe', '-d', config['distro'], '--exec', 'python3', '-u'] if os.name == 'nt' else ['python3', '-u']
    task.check()
    return prefix + [_linux_path(script), json.dumps(payload)]


def _stop(process) -> list[str]:
    errors = []
    if process.poll() is None:
        try:
            process.stdin.write(b'stop\n'); process.stdin.flush(); process.stdin.close()
        except (ValueError, OSError):
            pass
        try:
            process.wait(timeout=9)
        except subprocess.TimeoutExpired:
            try:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill(); process.wait(timeout=3)
            except (OSError, subprocess.TimeoutExpired) as error:
                errors.append(str(error))
    if process.poll() is None:
        errors.append('Owned camera lab transport was not reaped')
    return errors


def _run_supervised(device: Path, destination: Path, session: Path, command: list[str],
                    task: Task, seconds: int, *, run_directory: str,
                    label: str, console_label: str) -> Path:
    """Shared owned-process bridge; caller holds the guest lock and checks paths."""
    record = {'status': 'starting', 'assisted_startup': True, 'original_ui_verified': False,
              'camera_access': False, 'evidence': str(destination / 'evidence.json')}
    write_json(session, record)
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    reader = None
    reader_started = False
    stopped = False
    try:
        messages = queue.Queue()
        def read_output():
            try:
                for line in iter(lambda: process.stdout.readline(65537), b''):
                    if len(line) > 65536:
                        messages.put({'event': 'error', 'message': 'Board lab control output exceeded its bound'})
                        break
                    try:
                        messages.put(json.loads(line))
                    except (ValueError, UnicodeError):
                        continue
            finally:
                process.stdout.close()
        reader = threading.Thread(target=read_output, daemon=True)
        reader.start(); reader_started = True
        task.report(label)
        start = time.monotonic()
        while True:
            task.check()
            if time.monotonic() - start > seconds + 60:
                raise TimeoutError('Board lab supervisor exceeded its bounded startup/run window')
            try:
                event = messages.get(timeout=.1)
            except queue.Empty:
                if process.poll() is not None:
                    break
                continue
            if not isinstance(event, dict):
                continue
            if event.get('event') == 'error':
                raise RuntimeError(event.get('message', 'Board lab failed'))
            if event.get('event') == 'console':
                task.report(console_label)
            if event.get('event') == 'stopped':
                if event.get('errors'):
                    raise RuntimeError('; '.join(event['errors']))
                stopped = True
                break
        if not stopped:
            raise RuntimeError('Board lab supervisor exited without its evidence completion event')
        evidence = _device_path(device, run_directory + '/' + destination.name + '/evidence.json')
        result = read_json(evidence)
        if not result.get('process_reaped'):
            raise RuntimeError('Board lab guest cleanup was not confirmed')
        task.report('Evidence: ' + str(evidence))
        return evidence
    finally:
        errors = _stop(process)
        if reader_started:
            reader.join(timeout=1)
        for handle in (process.stdin, process.stdout):
            if handle is process.stdout and reader_started and reader.is_alive():
                continue
            if handle and not handle.closed:
                handle.close()
        record.update(status='stopped' if not errors else 'cleanup-incomplete',
                      completed=stopped, transport_exit_code=process.returncode,
                      process_reaped=process.poll() is not None, cleanup_errors=errors)
        write_json(session, record)


def run_lab(device: Path, home: Path, task: Task | None = None, *, seconds: int = 30) -> Path:
    """Run the original kernel/rootfs for a bounded time and return evidence.json."""
    task = task or Task()
    device = Path(device).absolute()
    _device_path(device, 'session.lock')
    session = _device_path(device, 'insta360-lab-session.json')
    _device_path(device, 'insta360-lab-session.json.tmp')
    with GuestLock(device):
        destination = _device_path(device, 'insta360-lab-runs/' + uuid.uuid4().hex)
        command = build_lab_command(device, home, destination, task, seconds)
        return _run_supervised(device, destination, session, command, task, seconds,
                run_directory='insta360-lab-runs',
                label='Insta360 X3 original Linux + BusyBox assisted startup / 原厂 Linux 与 BusyBox 辅助启动',
                console_label='X3 original BusyBox console reached / 已进入原厂 BusyBox 控制台')
