"""Fingerprinted Sony Linux labs; full camera services/UI remain unverified."""
from __future__ import annotations
import json
import os
from pathlib import Path
import re
import uuid

from .core import GuestLock, Task, read_json
from .insta360_lab import _device_path, _hash, _linux_path, _run_supervised
from . import sony_components
from .board_assets import board_directory

OVERLAY = board_directory('sony')


def _config(home: Path) -> dict:
    config = read_json(Path(home) / 'sony-lab.json')
    if not isinstance(config, dict):
        raise ValueError('Configure the separate Sony board build in sony-lab.json')
    distro = config.get('distro')
    if not isinstance(distro, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}', distro):
        raise ValueError('Select an explicit WSL distribution for the Sony lab')
    manifest = config.get('manifest')
    if not isinstance(manifest, str) or not manifest.startswith('/') or any(c in manifest for c in ('\\', '\0', '\n', '\r')):
        raise ValueError('Sony build manifest must be an absolute Linux path')
    return config


def lab_available(device: Path, home: Path) -> bool:
    """Quick eligibility; preparation can unpack the exact model on first run."""
    try:
        _config(home)
        return sony_components.profile_available(device)
    except (ValueError, OSError, TypeError, AttributeError):
        return False


def build_lab_command(device: Path, home: Path, destination: Path, task: Task,
                      seconds: int, *, mode: str = 'shell', inputs: dict | None = None) -> list[str]:
    if mode not in ('shell', 'init'):
        raise ValueError('Sony lab mode must be shell or init')
    if not isinstance(seconds, int) or isinstance(seconds, bool) or not 5 <= seconds <= 120:
        raise ValueError('Sony lab duration must be 5..120 seconds')
    config = _config(home)
    profile = sony_components.profile_for_device(device)
    if profile is None: raise ValueError('Sony lab firmware profile is unsupported')
    inputs = sony_components.prepare_components(device, task) if inputs is None else inputs
    for name, (_, filename, size, sha) in profile['inputs'].items():
        path = _device_path(device, 'sony-lab-inputs/' + filename)
        if inputs.get(name) != path:
            raise ValueError('Sony input path is outside the prepared guest inputs')
        if path.stat().st_size != size or _hash(path, task) != sha:
            raise ValueError('Sony input changed before launch: ' + name)
    script = OVERLAY / 'run.py'
    bridge = OVERLAY.parent / 'insta360/run.py'
    if not script.is_file() or script.is_symlink() or not bridge.is_file() or bridge.is_symlink():
        raise ValueError('Sony/common board supervisor is missing')
    configuration = {'manifest': config['manifest'], 'seconds': seconds, 'mode': mode,
                     'profile': profile['id'],
                     'destination': _linux_path(destination),
                     'artifacts': {name: _linux_path(path) for name, path in inputs.items()},
                     'supervisor_sha256': _hash(script, task),
                     'bridge_supervisor_sha256': _hash(bridge, task)}
    prefix = ['wsl.exe', '-d', config['distro'], '--exec', 'python3', '-u'] if os.name == 'nt' else ['python3', '-u']
    task.check()
    return prefix + [_linux_path(script), json.dumps(configuration)]


def run_lab(device: Path, home: Path, task: Task | None = None, *, seconds: int = 30,
            mode: str = 'shell') -> Path:
    """Run exact original Linux inputs for a bounded time; return private evidence."""
    task = task or Task()
    device = Path(device).absolute()
    _config(home)
    if mode not in ('shell', 'init'):
        raise ValueError('Sony lab mode must be shell or init')
    if not isinstance(seconds, int) or isinstance(seconds, bool) or not 5 <= seconds <= 120:
        raise ValueError('Sony lab duration must be 5..120 seconds')
    # Unpacking owns its own guest lock. Complete it before acquiring the
    # session lock; a missing extraction must not deadlock on first launch.
    inputs = sony_components.prepare_components(device, task)
    profile = sony_components.profile_for_device(device)
    _device_path(device, 'session.lock')
    session = _device_path(device, 'sony-lab-session.json')
    _device_path(device, 'sony-lab-session.json.tmp')
    with GuestLock(device):
        destination = _device_path(device, 'sony-lab-runs/' + uuid.uuid4().hex)
        command = build_lab_command(device, home, destination, task, seconds, mode=mode, inputs=inputs)
        return _run_supervised(device, destination, session, command, task, seconds,
                run_directory='sony-lab-runs',
                label='Sony ' + profile['model'] + ' ' + profile['version'] +
                      ' original Linux assisted startup / 原厂 Linux 辅助启动：' + mode,
                console_label=profile['model'] + ' original BusyBox console reached / 已进入原厂 BusyBox 控制台')
