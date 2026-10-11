"""Launch a separately installed WonderLantern Z7 lab through an owned WSL process.

No emulator source, firmware or decryption tables are distributed here. The
reviewed GPL-2.0-or-later upstream is WonderLantern/z7-qemu at REVISION.
This is assisted menu startup, not an unmodified natural camera power-on.
"""
from __future__ import annotations

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

from .core import Task, GuestLock, checked_child, read_json, write_json

REVISION = '97abf0b81cd3211f8374a38987a0beaf4549b90f'
PAYLOAD_SHA256 = 'e6df1764323fb1c287159dd3ce1c84ac93c72b96ba6eb09c070295c093fecbff'
PAYLOAD_SIZE = 33052088
PORT = 8787
SCRIPT_HASHES = {
    'tools/interactive.py': '07f0aed308b6a30c0f5eac9ec206af87f29a603c081a051648383edbeb9470cc',
    'tools/probe.py': '45397146248621b07272579ee16d3c7b38f2b5681978a5d61b164eba3eaf9971',
    'tools/rsp.py': '616a175b0b0c68d32dcbe774c3ea54da4aaa8c94e2d3e376210cdecc60e202ed',
    'tools/capture_diagnostics.py': '0c2fb5be043a15f04930ea596d2ddc718a3b7e26582852fc9141936d79faf1e7',
    'tools/raw_input.py': '76d1895fdd42869ad4d0046c3a316207eb04abc981751e88a22e8b095047ccc7',
    'tools/cmos_config.py': '2cc72804d7cfd1f7608ea43236fbcd442d2a8a07378098360b2081ab422cbe3d',
    'tools/interactive.html': 'dd15e1005db9c41006f5040cad586a41fc3c6a83866b8c181eea354055ea661e',
}

# This source is our process supervision glue, not code from the emulator. Its
# control stdout contains only our JSON; upstream stdout goes to a private log.
# The owned child creates a new process group. EOF or a stop command signals only
# that group; no process-name matching or distribution shutdown is used.
SUPERVISOR = r'''
import hashlib,json,os,signal,socket,stat,subprocess,sys,threading,time
from pathlib import Path
from urllib.request import urlopen
c=json.loads(sys.argv[1]); child=None; log=None; stopping=threading.Event()
def emit(event,**values):
 print(json.dumps(dict(event=event,**values)),flush=True)
def digest(path):
 h=hashlib.sha256()
 with open(path,'rb') as f:
  for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
 return h.hexdigest()
def stop_input():
 for line in sys.stdin:
  if line.strip()=='stop':break
 stopping.set()
def owns_listener(pid,port):
 try:
  sockets=set()
  for fd in Path('/proc',str(pid),'fd').iterdir():
   try:target=os.readlink(fd)
   except OSError:continue
   if target.startswith('socket:['):sockets.add(target[8:-1])
  for row in Path('/proc/net/tcp').read_text().splitlines()[1:]:
   fields=row.split()
   if fields[1]==f'0100007F:{port:04X}' and fields[3]=='0A' and fields[9] in sockets:return True
 except OSError:pass
 return False
try:
 project=Path(c['project']); payload=Path(c['payload'])
 if payload.stat().st_size!=c['payload_size'] or digest(payload)!=c['payload_sha256']:raise ValueError('Z7 MAIN changed before launch')
 for name,expected in c['scripts'].items():
  path=project/name
  if path.is_symlink() or digest(path)!=expected:raise ValueError('Reviewed lab script changed: '+name)
 manifest=json.loads((project/'.local/build-manifest.json').read_text())
 if manifest['qemu_version']!='10.2.4' or manifest['binary']!=c['qemu'] or digest(c['qemu'])!=manifest['binary_sha256']:raise ValueError('QEMU build manifest or binary mismatch')
 # Match HTTPServer's Linux reuse behavior so its previous TIME_WAIT sockets
 # do not block an immediate restart. An active listener still rejects bind.
 with socket.socket() as probe:
  probe.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
  probe.bind(('127.0.0.1',c['port']))
 log=open(c['log'],'ab',buffering=0)
 env=dict(os.environ);env.pop('PYTHONPATH',None);env.pop('PYTHONHOME',None)
 command=[sys.executable,'-u','-B',str(project/'tools/interactive.py'),'--firmware',str(payload),'--build-manifest',str(project/'.local/build-manifest.json'),'--profile','menu','--port',str(c['port']),'--no-current']
 child=subprocess.Popen(command,stdout=log,stderr=subprocess.STDOUT,start_new_session=True,env=env,cwd=project)
 emit('started',pid=child.pid,command=command)
 threading.Thread(target=stop_input,daemon=True).start()
 start=time.monotonic();ready=False
 while child.poll() is None and not stopping.wait(.1):
  if not ready:
   if time.monotonic()-start>45:raise TimeoutError('Owned Z7 server did not become ready')
   if owns_listener(child.pid,c['port']):
    try:
     with urlopen(c['url']+'/api/status',timeout=1) as r:state=json.load(r)
    except (OSError,ValueError):continue
    if state.get('phase') in ('booting','running') and isinstance(state.get('session'),str):
     ready=True;emit('ready',url=c['url'],session=state['session'])
  elif time.monotonic()-start<180:
   try:
    with urlopen(c['url']+'/api/status',timeout=1) as r:state=json.load(r)
    if state.get('phase')=='error':raise RuntimeError('Z7 startup failed: '+str(state.get('error')))
   except OSError:pass
 if child.poll() is not None and not stopping.is_set():raise RuntimeError('Owned Z7 launcher exited: '+str(child.returncode))
except Exception as error:
 emit('error',message=str(error));sys.stderr.write(str(error)+'\n')
finally:
 cleanup_failed=False
 if child and child.poll() is None:
  try:os.killpg(child.pid,signal.SIGINT)
  except ProcessLookupError:pass
  try:child.wait(timeout=8)
  except subprocess.TimeoutExpired:
   try:os.killpg(child.pid,signal.SIGKILL)
   except ProcessLookupError:pass
   try:child.wait(timeout=3)
   except subprocess.TimeoutExpired:
    cleanup_failed=True;emit('error',message='Owned Z7 process group did not exit after SIGKILL')
 if log:log.close()
 emit('stopped',exit_code=child.returncode if child else None)
 if cleanup_failed:sys.exit(1)
'''


def _hash(path: Path, task: Task) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024**2), b''):
            task.check(); digest.update(block)
    return digest.hexdigest()


def _config(home: Path) -> dict:
    config = read_json(Path(home) / 'nikon-lab.json')
    if not isinstance(config, dict) or config.get('revision') != REVISION:
        raise ValueError('Configure the reviewed WonderLantern Z7 lab revision in nikon-lab.json')
    if not isinstance(config.get('distro'), str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}', config['distro']):
        raise ValueError('Invalid explicit WSL distribution name')
    project, qemu = config.get('project'), config.get('qemu')
    if not isinstance(project, str) or not Path(project).is_absolute():
        raise ValueError('Z7 lab project must be an absolute local directory')
    if not isinstance(qemu, str) or not qemu.startswith('/') or '\\' in qemu or '\0' in qemu:
        raise ValueError('Z7 QEMU must be an absolute Linux binary path')
    return config


def _payload(device: Path) -> Path:
    data = read_json(_device_path(device, 'extracted/extraction.json'))
    for artifact in data.get('artifacts', []):
        if artifact.get('sha256') == PAYLOAD_SHA256 and artifact.get('size') == PAYLOAD_SIZE:
            path = _device_path(device, 'extracted/' + artifact.get('path', ''))
            if not stat.S_ISREG(path.stat().st_mode) or path.stat().st_size != PAYLOAD_SIZE:
                raise ValueError('Known Nikon Z7 MAIN artifact is missing or changed')
            return path
    raise ValueError('Requires extracted Nikon Z7 C 3.80 MAIN with its verified fingerprint')


def _device_path(device: Path, name: str) -> Path:
    """Keep metadata, lock and artifact access inside the actual guest directory."""
    device = Path(device).absolute()
    result = checked_child(device, name)
    current = device
    for component in ('', *name.replace('\\', '/').split('/')):
        if component:
            current = current / component
        try:
            metadata = current.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(metadata.st_mode) or getattr(metadata, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400):
            raise ValueError('Nikon guest path contains a symlink or junction')
    return result


def lab_available(device: Path, home: Path) -> bool:
    """Quick eligibility check; no processes, network or multi-megabyte hashing."""
    try:
        _config(home); _payload(device)
        return True
    except (OSError, ValueError, TypeError, AttributeError):
        return False


def _wsl_path(path: Path) -> str:
    parsed = PureWindowsPath(str(path))
    if not parsed.is_absolute() or len(parsed.drive) != 2 or parsed.drive[1] != ':':
        raise ValueError('WSL bridge requires a local Windows drive path, not a UNC path')
    return '/mnt/' + parsed.drive[0].lower() + '/' + '/'.join(parsed.parts[1:])


def build_lab_command(device: Path, home: Path, task: Task | None = None) -> tuple[list[str], str]:
    """Validate the exact payload, reviewed checkout and build before execution."""
    task = task or Task()
    device, home = Path(device).absolute(), Path(home).resolve()
    config = _config(home)
    payload = _payload(device)
    if _hash(payload, task) != PAYLOAD_SHA256:
        raise ValueError('Nikon MAIN actual SHA-256 no longer matches its manifest')
    project = Path(config['project']).resolve()
    result = subprocess.run(['git', '-C', str(project), 'rev-parse', 'HEAD'], capture_output=True,
                            text=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    if result.returncode or result.stdout.strip() != REVISION:
        raise ValueError('Z7 lab checkout is not the reviewed source revision')
    for name, expected in SCRIPT_HASHES.items():
        script = project / name
        if script.is_symlink() or _hash(script, task) != expected:
            raise ValueError(f'Reviewed Z7 lab script changed: {name}')
    manifest = read_json(project / '.local/build-manifest.json')
    if manifest.get('qemu_version') != '10.2.4' or manifest.get('binary') != config['qemu']:
        raise ValueError('Configure a matching QEMU 10.2.4 lab build manifest')
    url = f'http://127.0.0.1:{PORT}'
    with socket.socket() as probe:
        try:
            probe.bind(('127.0.0.1', PORT))
        except OSError as error:
            raise ValueError(f'Local port {PORT} is already occupied; stop its owner before starting the lab') from error
    if os.name == 'nt':
        linux_project, linux_payload = _wsl_path(project), _wsl_path(payload)
        linux_log = _wsl_path(_device_path(device, 'nikon-lab-console.log'))
        prefix = ['wsl.exe', '-d', config['distro'], '--exec', 'python3', '-u', '-c', SUPERVISOR]
    else:
        linux_project, linux_payload = str(project), str(payload)
        linux_log = str(_device_path(device, 'nikon-lab-console.log'))
        prefix = ['python3', '-u', '-c', SUPERVISOR]
    configuration = {'project': linux_project, 'payload': linux_payload, 'log': linux_log,
                     'qemu': config['qemu'], 'port': PORT, 'url': url, 'payload_size': PAYLOAD_SIZE,
                     'payload_sha256': PAYLOAD_SHA256, 'scripts': SCRIPT_HASHES}
    task.check()
    return prefix + [json.dumps(configuration)], url


def run_lab(device: Path, home: Path, task: Task | None = None, *, on_ready=None) -> str:
    """Run until cancelled or Ctrl+C, then stop only the server and guest we own."""
    task = task or Task()
    device = Path(device).absolute()
    _device_path(device, 'session.lock')
    session_file = _device_path(device, 'nikon-lab-session.json')
    _device_path(device, 'nikon-lab-session.json.tmp')
    with GuestLock(device):
        command, url = build_lab_command(device, home, task)
        record = {'command': command, 'url': url, 'profile': 'menu', 'source_revision': REVISION,
                  'assisted_startup': True, 'guest_state_modified': True, 'natural_power_on_verified': False,
                  'firmware_file_modified': False, 'camera_access': False, 'status': 'starting'}
        write_json(session_file, record)
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        reader, reader_started, ready = None, False, False
        try:
            output = queue.Queue()
            def read_output():
                try:
                    for line in iter(process.stdout.readline, b''):
                        output.put(line)
                finally:
                    process.stdout.close()
            reader = threading.Thread(target=read_output, daemon=True)
            reader.start()
            reader_started = True
            task.report('Nikon Z7 assisted menu startup / 尼康 Z7 原厂菜单辅助启动；模拟状态经过初始化')
            while True:
                task.check()
                try:
                    raw = output.get(timeout=.1)
                except queue.Empty:
                    if process.poll() is not None:
                        break
                    continue
                try:
                    event = json.loads(raw)
                except (ValueError, UnicodeError):
                    continue
                if event.get('event') == 'ready' and not ready:
                    if event.get('url') != url:
                        raise RuntimeError('Supervisor returned an unexpected local URL')
                    ready = True; record.update(status='running', session=event.get('session'))
                    write_json(session_file, record)
                    task.report(url)
                    if on_ready:
                        on_ready(url)
                elif event.get('event') == 'error':
                    raise RuntimeError(event.get('message', 'Z7 lab failed'))
                elif event.get('event') == 'stopped':
                    break
            if not ready and not task.cancelled.is_set():
                raise RuntimeError('Z7 lab exited before its owned browser server was ready')
            if process.poll() not in (None, 0):
                raise RuntimeError(f'Owned Z7 transport exited unexpectedly: {process.returncode}')
            return url
        finally:
            cleanup_errors = []
            if process.poll() is None:
                try:
                    process.stdin.write(b'stop\n'); process.stdin.flush(); process.stdin.close()
                except (OSError, ValueError):
                    pass
                try:
                    process.wait(timeout=12)
                except subprocess.TimeoutExpired:
                    # Only this owned transport is terminated; the supervisor also
                    # treats stdin EOF as stop and never shuts down the distribution.
                    try:
                        process.terminate()
                        try: process.wait(timeout=3)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait(timeout=3)
                    except (OSError, subprocess.TimeoutExpired) as error:
                        cleanup_errors.append(str(error))
            if reader_started:
                reader.join(timeout=1)
            for handle in (process.stdin, process.stdout):
                # A running reader owns stdout's buffered lock; closing it here
                # could block forever if even an OS kill did not reap the child.
                if handle is process.stdout and reader_started and reader.is_alive():
                    continue
                if handle and not handle.closed: handle.close()
            reaped = process.poll() is not None
            if process.returncode not in (None, 0):
                cleanup_errors.append(f'Owned supervisor transport exited with code {process.returncode}; inspect its private log')
            record.update(status='stopped' if reaped and not cleanup_errors else 'cleanup-incomplete', ready=ready,
                          transport_exit_code=process.returncode, process_reaped=reaped,
                          cleanup_errors=cleanup_errors)
            write_json(session_file, record)
