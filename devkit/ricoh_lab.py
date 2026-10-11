"""Bounded Ricoh service-core boot with original, fingerprinted firmware bytes.

This lab substitutes a development DTB and a minimal bootloader GIC setup.
It boots vendor Linux and vendor rootfs; it does not emulate the camera RTOS UI.
QEMU/DTB are built locally by scripts/boards/ricoh/build.py and never bundled.
"""
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

ARCHIVE_SHA256 = '821b545b92f165e57dddccac5077d76d4d436a226595b8813ad59306bbfd0746'
DTB_SHA256 = 'b4d20ce3efe9d078fa2098030288b7b748ca72911b5c5ec584a56ceddae9dcd3'
BOARD_SHA256 = 'c2051f73a84fb22bcad612d7ee44d9f64a133f82d153cb5f237d5cf44553485e'
DTS_SHA256 = 'e9735d76ffc0537c7109c97078effc0fe00d5c163c2a4409a63f41c0cfce76e7'
OLD_KERNEL = ('kernel.zImage', 2993048, '9996a8968beecf199f3d9cdbd8a51e4f44204cf1b733a4f6bb800a8736e4ea1b')
OLD_ROOTFS = ('ramdisk.cpio.gz', 13898903, 'f8c2efc7c1ddcc917ded5f3b751f6fa54020534545ec57fdb625d586b410c657')
PROFILES = {
    'd6a3c9c288d784080e7b283222ecdd8752adfeed6faa36dab44f73115354bfd4': {
        'model': 'GR III', 'firmware': '2.10', 'kernel_version': '4.4.15', 'source_size': 30391780,
        'kernel': OLD_KERNEL, 'rootfs': OLD_ROOTFS, 'rootfs_type': 'cpio'},
    '708e79f3b7b96a7e1581b4acf4c412233050a1832718ef7f06c8397db1b431c7': {
        'model': 'GR IIIx', 'firmware': '1.60', 'kernel_version': '4.4.15', 'source_size': 30391788,
        'kernel': OLD_KERNEL, 'rootfs': OLD_ROOTFS, 'rootfs_type': 'cpio'},
    'a2f664dfca034059eb0fd6e18ab08684c326b4a034d85c164dad7e1ec9b5655f': {
        'model': 'GR IV', 'firmware': '1.11', 'kernel_version': '5.15.190', 'source_size': 38648776,
        'kernel': ('kernel.zImage', 5709552, '9e6bdf2174b6beec0252ce4c8d58ccdb5a13631401f48e10b4e4d2553d2c9eca'),
        'rootfs': ('rootfs.squashfs', 21847529, 'fafc70b9048fe8f1eb8502e15f909ff65a7ce5d28367a1bb45f5e20f1f19ea16'),
        'rootfs_type': 'squashfs'},
}

INTERVENTIONS = [
    'Development DTB: one Cortex-A7 CPU at index 0, 256 MiB RAM, fixed UART clock, architected timer.',
    'Development DTB: non-empty vendor interrupt blacklist reserves unused SPI 0.',
    'Minimal independent bootloader setup writes GICC_CTLR=1 before entering the original kernel.',
    'RTOS control RAM 0x4fed0000..0x4fefffff and metadata RAM 0x4ff00000..0x4fffffff reserved; zeroed 8 MiB payload transport mapped at 0x50000000. Original DTB shared_commem pointer locations retained; no RTOS allocations or IPC replies synthesized.',
    'RTOS cores, display, sensor, storage controller, USB UDC and camera IPC are not emulated.',
    'Shell/services probe selects original /bin/sh as PID 1; mounts proc/sys/devtmpfs and volatile tmpfs only.',
]

# Process ownership stays in Linux: EOF/cancel terminates only this QEMU group.
# Logs have a 4 MiB limit each and there are no shared host disks, USB or NICs.
SUPERVISOR = r'''
import hashlib,json,os,shutil,signal,socket,stat,subprocess,sys,tempfile,threading,time
from pathlib import Path
c=json.loads(sys.argv[1]);p=None;stop=threading.Event();serial=bytearray();errors=bytearray();limit=4*1024**2
def emit(event,**fields):print(json.dumps(dict(event=event,**fields)),flush=True)
def sha(path):
 h=hashlib.sha256()
 with open(path,'rb') as f:
  for b in iter(lambda:f.read(1024**2),b''):h.update(b)
 return h.hexdigest()
def regular(path):
 p=Path(path)
 if not p.is_absolute() or any(a.is_symlink() for a in (p,*p.parents)) or not stat.S_ISREG(p.stat().st_mode):raise ValueError('Invalid local lab file: '+str(p))
 return p
def input_control():
 for line in sys.stdin:
  if line.strip()=='stop':break
 stop.set()
def drain(stream,destination,log):
 while True:
  block=os.read(stream.fileno(),4096)
  if not block:break
  if len(destination)+len(block)>limit:stop.set();emit('error',message='Ricoh console log exceeded 4 MiB');break
  destination.extend(block)
  log.write(block);log.flush()
result={};threads=[];started=time.monotonic()
try:
 m=json.loads(regular(c['manifest']).read_text())
 if m!=c['build']:raise ValueError('Ricoh build manifest changed before launch')
 for key in ('binary','dtb'):
  if sha(regular(m[key]))!=m[key+'_sha256']:raise ValueError('Ricoh '+key+' hash mismatch')
 for item in c['inputs']:
  path=regular(item['path'])
  if path.stat().st_size!=item['size'] or sha(path)!=item['sha256']:raise ValueError('Ricoh input snapshot changed')
 with tempfile.TemporaryDirectory(prefix='ricoh-lab-') as temp:
  qmp=Path(temp)/'qmp'
  frozen=[]
  for item,name in zip(c['inputs'],('kernel.zImage','rootfs.bin')):
   target=Path(temp)/name;shutil.copyfile(regular(item['path']),target)
   if target.stat().st_size!=item['size'] or sha(target)!=item['sha256']:raise ValueError('Ricoh input changed while freezing Linux snapshot')
   frozen.append(str(target))
  dtb=Path(temp)/'board.dtb';shutil.copyfile(regular(m['dtb']),dtb)
  if sha(dtb)!=m['dtb_sha256']:raise ValueError('Ricoh DTB changed while freezing Linux snapshot')
  command=[m['binary'],'-machine','ricoh-milbeaut-lab','-m','256','-kernel',frozen[0],'-dtb',str(dtb),'-initrd',frozen[1],'-append',c['cmdline'],'-display','none','-monitor','none','-serial','stdio','-nic','none','-no-reboot','-qmp',f'unix:{qmp},server=on,wait=off']
  env=dict(os.environ);env.pop('PYTHONPATH',None);env.pop('PYTHONHOME',None)
  p=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True,env=env)
  emit('started',pid=p.pid,command=command)
  serial_file=open(c['serial_log'],'wb');stderr_file=open(c['stderr_log'],'wb')
  threads=[threading.Thread(target=drain,args=(p.stdout,serial,serial_file),daemon=True),threading.Thread(target=drain,args=(p.stderr,errors,stderr_file),daemon=True)]
  for t in threads:t.start()
  threading.Thread(target=input_control,daemon=True).start()
  started=time.monotonic();sent=False;ready=False
  while p.poll() is None and not stop.wait(.05) and time.monotonic()-started<c['seconds']:
   if not ready and b'console [ttyUSI1] enabled' in serial:
    ready=True;emit('console-ready')
   if not sent and c['script'] and b'/ # ' in serial:
    p.stdin.write(c['script'].encode());p.stdin.flush();sent=True;emit('oracle-sent')
  result=dict(duration_seconds=round(time.monotonic()-started,3),oracle_sent=sent,cancelled=stop.is_set())
  if p.poll() is None:
   try:
    with socket.socket(socket.AF_UNIX) as s:
     s.settimeout(2);s.connect(str(qmp));f=s.makefile('rwb',buffering=0);f.readline();f.write(b'{"execute":"qmp_capabilities"}\n');f.readline()
     f.write(b'{"execute":"human-monitor-command","arguments":{"command-line":"info registers"}}\n')
     while True:
      row=json.loads(f.readline())
      if 'return' in row:result['registers']=row['return'];break
   except (OSError,ValueError):pass
except Exception as e:
 result['error']=str(e);emit('error',message=str(e))
finally:
 cleanup_error=None
 if p and p.poll() is None:
  try:os.killpg(p.pid,signal.SIGTERM)
  except ProcessLookupError:pass
  try:p.wait(5)
  except subprocess.TimeoutExpired:
   try:os.killpg(p.pid,signal.SIGKILL)
   except ProcessLookupError:pass
   try:p.wait(3)
   except subprocess.TimeoutExpired:cleanup_error='Owned Ricoh QEMU did not stop'
 for t in threads:t.join(1)
 for name in ('serial_file','stderr_file'):
  if name in globals():globals()[name].close()
 for key,data in (('serial',serial),('stderr',errors)):
  Path(c[key+'_log']).write_bytes(data)
  result[key+'_size']=len(data);result[key+'_sha256']=hashlib.sha256(data).hexdigest()
 result['qemu_exit_code']=p.returncode if p else None
 result['process_reaped']=bool(p and p.poll() is not None)
 if cleanup_error:result['cleanup_error']=cleanup_error
 emit('finished',result=result)
 if result.get('error') or cleanup_error:sys.exit(1)
'''


def _hash(path: Path, task: Task) -> str:
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024**2), b''):
            task.check(); h.update(chunk)
    return h.hexdigest()


def _inside(device: Path, name: str) -> Path:
    device = Path(device).absolute()
    result = checked_child(device, name)
    _no_links(device)
    current = device
    for part in ('', *name.replace('\\', '/').split('/')):
        if part: current /= part
        try: info = current.lstat()
        except FileNotFoundError: continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('Ricoh guest path contains a symbolic link or junction')
    return result


def _no_links(path: Path) -> None:
    for current in (path, *path.parents):
        try: info = current.lstat()
        except FileNotFoundError: continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('Ricoh lab path contains a symbolic link or junction')


def _profile(device: Path) -> dict:
    metadata = read_json(_inside(device, 'extracted/extraction.json'))
    if not isinstance(metadata, dict) or metadata.get('camera', {}).get('id') != 'ricoh-gr':
        raise ValueError('Extract a supported Ricoh GR firmware first')
    source_sha = metadata.get('source_sha256')
    if source_sha not in PROFILES:
        raise ValueError('Ricoh lab requires known GR III 2.10, GR IIIx 1.60 or GR IV 1.11 firmware')
    profile = dict(PROFILES[source_sha], source_sha256=source_sha)
    imported = read_json(_inside(device, 'device.json'))
    if imported.get('adapter') != 'generic' or imported.get('sha256') != source_sha:
        raise ValueError('Ricoh library source does not match its extracted firmware')
    source = _inside(device, 'source/firmware.bin')
    if not stat.S_ISREG(source.stat().st_mode) or source.stat().st_size != profile['source_size']:
        raise ValueError('Known Ricoh original source file is missing or changed')
    artifacts = metadata.get('artifacts')
    if not isinstance(artifacts, list): raise ValueError('Ricoh extraction artifacts missing')
    for role in ('kernel', 'rootfs'):
        name, size, digest = profile[role]
        matches = [a for a in artifacts if isinstance(a, dict) and a.get('path') == name]
        if len(matches) != 1 or matches[0].get('size') != size or matches[0].get('sha256') != digest:
            raise ValueError(f'Known Ricoh {role} artifact is missing or changed')
        path = _inside(device, 'extracted/' + name)
        if not stat.S_ISREG(path.stat().st_mode) or path.stat().st_size != size:
            raise ValueError(f'Known Ricoh {role} file is missing or changed')
    return profile


def _config(home: Path) -> tuple[dict, dict]:
    config_path = Path(home).absolute() / 'ricoh-lab.json'
    _no_links(config_path)
    config = read_json(config_path)
    if not isinstance(config, dict) or not isinstance(config.get('manifest'), str):
        raise ValueError('Configure the local Ricoh build manifest in ricoh-lab.json')
    if not isinstance(config.get('distro'), str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}', config['distro']):
        raise ValueError('Configure an explicit valid WSL distribution')
    path = Path(config['manifest'])
    if not path.is_absolute() or path.is_symlink(): raise ValueError('Ricoh manifest must be an absolute local file')
    _no_links(path)
    build = read_json(path)
    if not isinstance(build, dict) or build.get('qemu_version') != '10.2.4' or build.get('machine') != 'ricoh-milbeaut-lab' or build.get('upstream_archive_sha256') != ARCHIVE_SHA256:
        raise ValueError('Ricoh requires its isolated QEMU 10.2.4 build')
    if build.get('sources', {}).get('ricoh-milbeaut-lab.c') != BOARD_SHA256 or build.get('sources', {}).get('ricoh-milbeaut-lab.dts') != DTS_SHA256 or build.get('dtb_sha256') != DTB_SHA256:
        raise ValueError('Ricoh board source or development DTB is not the reviewed build')
    for name in ('binary', 'dtb'):
        value = build.get(name)
        if not isinstance(value, str) or not value.startswith('/') or '\\' in value or '\0' in value:
            raise ValueError('Ricoh build requires absolute Linux paths')
        if not re.fullmatch(r'[0-9a-f]{64}', str(build.get(name + '_sha256'))):
            raise ValueError('Ricoh build hash is missing')
    return config, build


def lab_available(device: Path, home: Path) -> bool:
    """Quick eligibility: no processes and no hashing large firmware files."""
    try:
        _config(home); _profile(device)
        return True
    except (OSError, ValueError, TypeError, AttributeError): return False


def _linux_path(path: Path) -> str:
    if os.name != 'nt': return str(path)
    parsed = PureWindowsPath(path)
    if not parsed.is_absolute() or len(parsed.drive) != 2 or parsed.drive[1] != ':':
        raise ValueError('Ricoh WSL bridge requires a local Windows drive path')
    return '/mnt/' + parsed.drive[0].lower() + '/' + '/'.join(parsed.parts[1:])


def _cmdline(profile: dict, mode: str) -> str:
    common = 'console=ttyUSI1,115200 loglevel=8 ignore_loglevel maxcpus=1 panic=-1'
    if mode == 'services': common += ' user_debug=31'
    init = '/sbin/finit' if mode == 'init' else '/bin/sh'
    if profile['rootfs_type'] == 'squashfs':
        return common + f' root=/dev/ram0 rootfstype=squashfs ro ramdisk_size=32768 init={init}'
    return common + f' rdinit={init}'


def _oracle(nonce: str, mode: str) -> str:
    if mode == 'init': return ''
    script = f"printf 'RICOH_ORACLE_%s\\n' 'START_{nonce}'\n"
    script += 'mount -t proc proc /proc\nmount -t sysfs sysfs /sys\nmount -t devtmpfs devtmpfs /dev\nuname -a\n/bin/busybox.nosuid id\ncat /proc/version\n'
    script += 'ls /usr/bin/camctld /sbin/finit\nmount\n'
    if mode == 'services':
        script += 'mount -t tmpfs tmpfs /tmp\nmount -t tmpfs tmpfs /run\nsyslogd -O /tmp/messages\n'
        script += 'CAMCTLD_LOG=debug /usr/bin/camctld -w >/tmp/camctld.log 2>&1 &\np=$!\nsleep 3\n'
        script += 'if kill -0 "$p" 2>/dev/null; then echo RICOH_CAMCTLD_ALIVE; cat /proc/$p/wchan; else wait "$p"; echo RICOH_CAMCTLD_EXIT=$?; fi\n'
        script += 'cat /tmp/camctld.log\ncat /tmp/messages\ncat /tmp/startup_mode\nls /tmp/camctld_ready\nps\n'
    return script + f"printf 'RICOH_ORACLE_%s\\n' 'END_{nonce}'\n"


def evaluate_console(serial: bytes, profile: dict, nonce: str, mode: str) -> dict:
    """Require actual command output; input echo alone cannot prove userspace."""
    text = serial.decode('utf-8', errors='replace').replace('\r', '')
    lines = text.splitlines()
    kernel = bool(re.search(r'Linux version ' + re.escape(profile['kernel_version']) + r'\b', text)
                  and 'console [ttyUSI1] enabled' in text)
    started = f'RICOH_ORACLE_START_{nonce}' in lines
    finished = f'RICOH_ORACLE_END_{nonce}' in lines
    uname = bool(re.search(r'^Linux rdsc ' + re.escape(profile['kernel_version']) + r'.* armv7l GNU/Linux$', text, re.M))
    root = bool(re.search(r'^uid=0\(root\) gid=0\(root\)', text, re.M))
    panic = 'Kernel panic' in text or 'Unable to handle kernel' in text
    # finit does not expose a shell. Kernel's "Run ..." message alone is an
    # attempted exec. Vendor udev output, or the older kernel's named D-Bus
    # child entropy read, demonstrates actual original-init child execution.
    # Neither probe injects a shell command in init mode.
    init_child = bool('udevd[' in text and 'starting version' in text)
    if profile['kernel_version'] == '4.4.15':
        init_child = init_child or bool(re.search(
            r'^\[\s*\d+\.\d+\] random: dbus-daemon urandom read with \d+ bits of entropy available$',
            text, re.M))
    original_init = bool(mode == 'init' and init_child)
    return {'kernel_booted': kernel and not panic,
            'userspace_booted': bool(kernel and not panic and ((started and finished and uname and root) or original_init)),
            'shell_oracle_complete': started and finished and uname and root,
            'original_init_started': original_init,
            'original_ui_verified': False, 'panic_detected': panic,
            'camera_service_ready_file': bool(mode == 'services' and '/tmp/camctld_ready' in lines),
            'camera_service_alive': bool(mode == 'services' and 'RICOH_CAMCTLD_ALIVE' in lines),
            'restrict_mode': bool(mode == 'services' and 'restrict' in lines)}


def run_lab(device: Path, home: Path, task: Task | None = None, *, seconds=30,
            mode='shell', on_ready=None) -> dict:
    """Run a finite, isolated boot oracle and retain inputs, JSON and real logs."""
    if isinstance(seconds, bool) or not isinstance(seconds, int) or not 8 <= seconds <= 120:
        raise ValueError('Ricoh lab duration must be 8..120 seconds')
    if mode not in ('shell', 'services', 'init'): raise ValueError('Unknown Ricoh lab probe mode')
    task = task or Task(); device = Path(device).absolute()
    _inside(device, 'session.lock')
    with GuestLock(device):
        task.check(); config, build = _config(home); profile = _profile(device)
        if _hash(_inside(device, 'source/firmware.bin'), task) != profile['source_sha256']:
            raise ValueError('Ricoh original firmware SHA-256 no longer matches its recorded source')
        nonce = uuid.uuid4().hex
        run = _inside(device, 'ricoh-lab-runs/' + nonce)
        run.mkdir(parents=True, exist_ok=False)
        inputs = []
        for role in ('kernel', 'rootfs'):
            name, size, digest = profile[role]
            source = _inside(device, 'extracted/' + name)
            target = run / name
            with source.open('rb') as reader, target.open('xb') as writer:
                for chunk in iter(lambda: reader.read(1024**2), b''):
                    task.check(); writer.write(chunk)
            if target.stat().st_size != size or _hash(target, task) != digest:
                raise ValueError('Ricoh artifact changed while creating its launch snapshot')
            inputs.append({'path': _linux_path(target), 'size': size, 'sha256': digest})
        cmdline = _cmdline(profile, mode)
        controls = {'manifest': _linux_path(Path(config['manifest'])), 'build': build,
                    'inputs': inputs, 'cmdline': cmdline, 'seconds': seconds,
                    'script': _oracle(nonce, mode), 'serial_log': _linux_path(run / 'serial.log'),
                    'stderr_log': _linux_path(run / 'stderr.log')}
        prefix = ['wsl.exe', '-d', config['distro'], '--exec'] if os.name == 'nt' else []
        command = prefix + ['python3', '-u', '-c', SUPERVISOR, json.dumps(controls)]
        record = {'schema_version': 1, 'model': profile['model'], 'firmware': profile['firmware'],
                  'source_sha256': profile['source_sha256'], 'mode': mode, 'seconds': seconds,
                  'build': build, 'inputs': inputs, 'cmdline': cmdline, 'guest_oracle_commands': controls['script'],
                  'assisted_startup': True, 'firmware_file_modified': False, 'camera_access': False,
                  'host_network_exposed': False, 'interventions': list(INTERVENTIONS),
                  'natural_power_on_verified': False, 'original_ui_verified': False, 'status': 'starting',
                  'run_directory': str(run)}
        if mode == 'init': record['interventions'][-1] = 'Original /sbin/finit selected as PID 1; no shell commands injected.'
        write_json(run / 'result.json', record)
        task.report(f"Ricoh {profile['model']} original Linux service-core probe / 原 Linux 服务核启动验证")
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT,
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        messages = queue.Queue(); errors = []; result = {}; ready = False
        def read_output():
            for line in iter(process.stdout.readline, b''): messages.put(line)
        reader = threading.Thread(target=read_output, daemon=True); reader_started = False
        started = time.monotonic()
        try:
            reader.start(); reader_started = True
            while True:
                task.check()
                if time.monotonic() - started > seconds + 25:
                    raise TimeoutError('Owned Ricoh supervisor exceeded its bounded runtime')
                try: raw = messages.get(timeout=.1)
                except queue.Empty:
                    if process.poll() is not None: break
                    continue
                try: event = json.loads(raw)
                except (ValueError, UnicodeError): continue
                if event.get('event') == 'started': record['qemu_command'] = event.get('command'); record['qemu_pid'] = event.get('pid')
                elif event.get('event') == 'console-ready':
                    ready = True; task.report('Original Ricoh serial console active / 原内核串口已启动')
                    if on_ready: on_ready(str(run / 'serial.log'))
                elif event.get('event') == 'error': errors.append(str(event.get('message')))
                elif event.get('event') == 'finished': result = event.get('result', {}); break
            process.wait(timeout=3)
            if errors or result.get('error') or not result.get('process_reaped') or process.returncode:
                raise RuntimeError('; '.join(errors) or result.get('error') or 'Ricoh owned process failed to stop cleanly')
            serial = (run / 'serial.log').read_bytes()
            if len(serial) > 4 * 1024**2 or hashlib.sha256(serial).hexdigest() != result.get('serial_sha256'):
                raise ValueError('Ricoh serial log integrity mismatch')
            oracle = evaluate_console(serial, profile, nonce, mode)
            record.update(result, **oracle, status='verified-linux' if oracle['userspace_booted'] else 'unverified')
            record['blockers'] = ['Camera RTOS/UI, camera IPC, display, storage controller and sensor peripherals are not emulated.']
            return record
        except BaseException:
            record['status'] = 'cancelled' if task.cancelled.is_set() else 'failed'
            raise
        finally:
            if process.poll() is None:
                try: process.stdin.write(b'stop\n'); process.stdin.flush(); process.stdin.close()
                except (OSError, ValueError): pass
                try: process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.terminate()
                    try: process.wait(timeout=3)
                    except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=3)
            if reader_started: reader.join(timeout=1)
            record['transport_exit_code'] = process.returncode
            record['transport_reaped'] = process.poll() is not None
            record['console_ready'] = ready
            write_json(run / 'result.json', record)
            write_json(_inside(device, 'ricoh-lab-last.json'), record)
