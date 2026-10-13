"""Exact-firmware original X3 GUI component lab; separate from full RTOS boot."""
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

from .core import GuestLock, Task, checked_child, read_json, write_json
from .board_assets import board_directory
from .insta360_lab import _linux_path

SOURCE_SHA256 = 'c745287e1243fb20861a110374e09ef59ec97c288c7da51659ffae19db9302f7'
RTOS_SHA256 = 'a89070d6edc57cd489c9f5e3e81afca7750d08dd5d39d423e7a86efef73a7569'
RTOS_SIZE = 49344520
PORT = 8791
RUNNER = board_directory('insta360') / 'run_ui.py'


def _path(root, name):
    root = Path(root).absolute()
    if '..' in root.parts: raise ValueError('Parent component in X3 lab path')
    target = checked_child(root, name)
    raw = root / name.replace('\\','/')
    for item in (raw, *raw.parents):
        try: meta = item.lstat()
        except FileNotFoundError: continue
        if stat.S_ISLNK(meta.st_mode) or getattr(meta,'st_file_attributes',0)&0x400:
            raise ValueError('X3 lab path contains a symlink/junction')
        if stat.S_ISREG(meta.st_mode) and meta.st_nlink != 1: raise ValueError('X3 lab input is hardlinked')
    return target


def _json(root, name, limit=256*1024):
    path = _path(root,name)
    if not path.is_file() or path.stat().st_size > limit: raise ValueError('Missing or oversized X3 JSON: '+name)
    value = read_json(path)
    if not isinstance(value,dict): raise ValueError('X3 JSON must be an object')
    return value


def _hash(path, task):
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024**2),b''):
            task.check(); value.update(block)
    return value.hexdigest()


def _config(home):
    value = _json(home,'insta360-ui-lab.json',16384)
    if not isinstance(value.get('distro'),str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}',value['distro']):
        raise ValueError('Select an explicit WSL distribution for the X3 GUI lab')
    manifest = value.get('manifest')
    if not isinstance(manifest,str) or not manifest.startswith('/') or any(c in manifest for c in ('\\','\0','\n','\r')) or '..' in manifest.split('/'):
        raise ValueError('X3 GUI manifest must be an absolute Linux path')
    return value


def _inputs(device):
    metadata = _json(device,'device.json')
    extraction = _json(device,'extracted/extraction.json')
    if metadata.get('sha256') != SOURCE_SHA256 or extraction.get('source_sha256') != SOURCE_SHA256:
        raise ValueError('X3 GUI requires the exact v1.1.4.1_build1 extraction')
    artifacts = extraction.get('artifacts')
    if not isinstance(artifacts,list) or len(artifacts)>4096: raise ValueError('Invalid X3 extraction manifest')
    matches = [a for a in artifacts if isinstance(a,dict) and a.get('sha256')==RTOS_SHA256 and a.get('size')==RTOS_SIZE]
    if len(matches)!=1 or not isinstance(matches[0].get('path'),str): raise ValueError('Exact original X3 RTOS missing')
    path = _path(device,'extracted/'+matches[0]['path'])
    if not path.is_file() or path.stat().st_size != RTOS_SIZE: raise ValueError('X3 RTOS size changed')
    return path


def lab_available(device, home):
    """Cheap eligibility; launch rechecks original bytes and built artifacts."""
    try: _config(home); _inputs(device); return RUNNER.is_file()
    except (OSError,ValueError,TypeError,AttributeError,KeyError): return False


def _build_command_locked(device, home, task, mode, seconds):
    config = _config(home); firmware = _inputs(device)
    if _hash(firmware,task) != RTOS_SHA256: raise ValueError('Original X3 RTOS changed; reextract')
    if mode=='interactive':
        with socket.socket() as listener:
            try: listener.bind(('127.0.0.1',PORT))
            except OSError: raise RuntimeError('X3 loopback port is busy; no existing process was stopped') from None
    root = _path(device,'insta360-ui'); root.mkdir(exist_ok=True)
    out = _path(device,'insta360-ui/runs/'+uuid.uuid4().hex)
    runner = _path(RUNNER.parent,RUNNER.name)
    payload = dict(manifest=config['manifest'],rtos=_linux_path(firmware),destination=_linux_path(out),
                   mode=mode,seconds=seconds if mode=='probe' else 50,port=PORT)
    prefix = ['wsl.exe','-d',config['distro'],'--exec','python3','-u','-B','-I'] if os.name=='nt' else [os.sys.executable,'-u','-B','-I']
    prepared = dict(profile='insta360-x3-ui-v1',firmware_sha256=RTOS_SHA256,assisted_startup=True,
                    natural_power_on_verified=False,full_threadx_boot_verified=False,camera_access=False)
    return prefix+[_linux_path(runner),json.dumps(payload)],f'http://127.0.0.1:{PORT}',out,prepared


def _run(device, home, task, mode, seconds, on_ready):
    device = Path(device).absolute(); _path(device, 'session.lock')
    with GuestLock(device):
        command, url, out, prepared = _build_command_locked(device, home, task, mode, seconds)
        record_path = _path(device, 'insta360-ui/session.json'); _path(device, 'insta360-ui/session.json.tmp')
        record = {**prepared, 'status': 'starting', 'command': command, 'mode': mode,
                  'output': str(out), 'url': url if mode == 'interactive' else None,
                  'native_touch_verified': False, 'original_ui_verified': False}
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
                        line = b'{"event":"error","message":"X3 supervisor output exceeds its bound"}\n'
                        stop_reader.set()
                    try: messages.put(line, timeout=.5)
                    except queue.Full:
                        stop_reader.set(); break
            finally:
                process.stdout.close()
        try:
            reader = threading.Thread(target=read_output, daemon=True); reader.start(); reader_started = True
            deadline = time.monotonic() + (seconds if mode == 'probe' else 50)
            task.report('Insta360 X3 v1.1.4.1 assisted GUI / 原厂菜单辅助启动（非自然开机验证）')
            while True:
                task.check()
                if (mode == 'probe' or not ready) and time.monotonic() > deadline:
                    raise TimeoutError('X3 lab did not finish its bounded startup/probe')
                try: raw = messages.get(timeout=.1)
                except queue.Empty:
                    if stop_reader.is_set(): raise RuntimeError('X3 output reader stopped before completion')
                    if process.poll() is not None: break
                    continue
                try: event = json.loads(raw)
                except (ValueError, UnicodeError): continue
                if not isinstance(event, dict): continue
                if event.get('event') == 'ready' and not ready:
                    if mode != 'interactive' or event.get('url') != url:
                        raise RuntimeError('X3 supervisor returned an unexpected local URL')
                    ready = True; record.update(status='running', session=event.get('session'))
                    write_json(record_path, record)
                    if on_ready: on_ready(url)
                elif event.get('event') == 'result':
                    if mode != 'probe' or not event.get('native_touch_verified'):
                        raise RuntimeError('X3 result did not verify original menu execution')
                    result = event
                elif event.get('event') == 'error':
                    raise RuntimeError(str(event.get('message', 'X3 lab failed'))[:4000])
                elif event.get('event') == 'stopped': break
            if mode == 'interactive' and not ready:
                raise RuntimeError('X3 lab exited before its own menu server became ready')
            if mode == 'probe' and result is None:
                raise RuntimeError('X3 probe exited without verified menu evidence')
            if process.poll() not in (None, 0):
                raise RuntimeError(f'Owned X3 transport failed: {process.returncode}')
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
                record['native_touch_verified'] = evidence.get('native_touch_verified') is True
                record['original_ui_verified'] = evidence.get('original_ui_verified') is True
                record['owned_guest_processes_reaped'] = evidence.get('owned_guest_processes_reaped') is True
                if not record['owned_guest_processes_reaped']:
                    record['status'] = 'cleanup-incomplete'
            else:
                record['owned_guest_processes_reaped'] = False
                record['status'] = 'cleanup-incomplete'
            write_json(record_path, record)
            if os.sys.exc_info()[0] is None and record['status'] == 'cleanup-incomplete':
                raise RuntimeError('Owned X3 guest cleanup could not be verified; inspect the saved session record')
            if os.sys.exc_info()[0] is None and mode == 'probe' and (
                    not record['native_touch_verified'] or not record['original_ui_verified'] or
                    evidence.get('single_instance_menu_roundtrip_verified') is not True):
                raise RuntimeError('Saved X3 evidence did not confirm the original single-instance menu roundtrip')


def run_lab(device: Path, home: Path, task: Task | None = None, *, on_ready=None) -> str:
    """Serve the original guest menu until cancellation; stop only owned processes."""
    return _run(device, home, task or Task(), 'interactive', 0, on_ready)


def run_menu_probe(device: Path, home: Path, task: Task | None = None, *, seconds=20) -> dict:
    """One QEMU instance: native 72MP -> 18MP -> 72MP menu, bounded 20..120 s."""
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not 20 <= seconds <= 120:
        raise ValueError('X3 probe budget must be 20..120 seconds')
    return _run(device, home, task or Task(), 'probe', float(seconds), None)
