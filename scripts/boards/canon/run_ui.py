"""Bounded EOS 70D 1.1.2 original-menu runner. No vendor bytes are included.

The original guest boots from its reset vector; QMP supplies normal MPU keys.
qemu-eos source: 4b667a1d3c08ab7a55835d15ddbd884fa754946d, GPL-2.0-or-later.
This project-authored supervisor is licensed under the repository license.
"""
from pathlib import Path
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import queue
import select
import signal
import socket
import stat
import struct
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import zlib

REVISION = '4b667a1d3c08ab7a55835d15ddbd884fa754946d'
ROM_FILES = {
    'ROM0.BIN': (8388608, '841f20ec42c4f20032d2c472fb44ff0f55f1353691233e6fd18757836d3647bc'),
    'ROM1.BIN': (16777216, '3fe42299deb577b269b6901f40597a1939ac5876f22c8c3a0b6ee47219fdd42e'),
    'SFDATA.BIN': (16777216, 'a72aa76c6d58bf9d288cf845d75814508e92387b203de63086f9dccff14ad1ab'),
}
MENU_RGB_SHA256 = 'e20d2a148fc5879e9be86b6709bc8a1ac4f34fff6f01d638f6a6c0f25ba51c6d'
DOWN_RGB_SHA256 = '3629aef4215d164bddf110a43fe2ef68d28fec2f56a10b48446a6875882e49f4'
KEYS = {'m', 'up', 'down', 'left', 'right', 'spc', 'i', 'q', 'p', 'f1'}
LOG_LIMIT = 4 * 1024**2


def sha(data):
    return hashlib.sha256(data).hexdigest()


def regular(path, *, expected=None, maximum=512 * 1024**2, digest=None):
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError('Canon input must have an absolute path without parent components')
    for item in (path, *path.parents):
        if item.is_symlink():
            raise ValueError('Canon input traverses a symlink')
    initial = path.lstat()
    if not stat.S_ISREG(initial.st_mode) or initial.st_nlink != 1:
        raise ValueError('Canon input must be a singly linked regular file')
    if not 0 < initial.st_size <= maximum or expected is not None and initial.st_size != expected:
        raise ValueError('Canon input size mismatch')
    identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
    value = hashlib.sha256()
    with os.fdopen(os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)), 'rb') as stream:
        if identity(os.fstat(stream.fileno())) != identity(initial):
            raise ValueError('Canon input changed before opening')
        remaining = initial.st_size
        while remaining:
            block = stream.read(min(1024**2, remaining))
            if not block:
                raise ValueError('Canon input was truncated')
            value.update(block); remaining -= len(block)
        if stream.read(1):
            raise ValueError('Canon input grew while reading')
        if identity(os.fstat(stream.fileno())) != identity(initial):
            raise ValueError('Canon input changed while reading')
    if identity(path.lstat()) != identity(initial):
        raise ValueError('Canon input was replaced')
    if digest is not None and value.hexdigest() != digest:
        raise ValueError('Canon input checksum mismatch: ' + path.name)
    return value.hexdigest()


def parse_ppm(data):
    # qemu-eos emits this fixed P6 format; refuse dimensions/extra bytes.
    header = b'P6\n720 480\n255\n'
    if not data.startswith(header) or len(data) != len(header) + 720 * 480 * 3:
        raise ValueError('Canon frame is not an exact 720x480 RGB PPM')
    return data[len(header):]


def png(rgb):
    if len(rgb) != 720 * 480 * 3:
        raise ValueError('Invalid RGB frame length')
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data) & 0xffffffff)
    rows = b''.join(b'\0' + rgb[i:i + 2160] for i in range(0, len(rgb), 2160))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>2I5B', 720, 480, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(rows, 3)) + chunk(b'IEND', b''))


def cards(root):
    volume = root / 'volume.img'
    with volume.open('xb') as stream:
        stream.truncate(126 * 1024**2)
    subprocess.run(['mkfs.vfat', '-F', '16', '-n', 'CANONLAB', str(volume)],
                   check=True, capture_output=True, timeout=10)
    mbr = bytearray(512)
    struct.pack_into('<B3sB3sII', mbr, 446, 0, b'\xfe\xff\xff', 0x0e, b'\xfe\xff\xff', 2048, 126 * 2048)
    mbr[510:] = b'\x55\xaa'
    for name in ('sd.img', 'cf.img'):
        with (root / name).open('xb') as target:
            target.truncate(128 * 1024**2)
            target.write(mbr); target.seek(1024**2)
            with volume.open('rb') as source:
                while block := source.read(1024**2):
                    target.write(block)
    volume.unlink()


def stop_owned(process):
    if process is not None and process.poll() is None:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL); process.wait(timeout=3)


class QMP:
    def __init__(self, path):
        self.connection = socket.socket(socket.AF_UNIX)
        self.stream = None
        try:
            self.connection.settimeout(3)
            self.connection.connect(str(path))
            self.stream = self.connection.makefile('rwb', buffering=0)
            if 'QMP' not in self._read():
                raise ValueError('Invalid owned QMP greeting')
            self.query('qmp_capabilities')
        except BaseException:
            self.close(); raise

    def _read(self):
        line = self.stream.readline(65537)
        if not line or len(line) > 65536:
            raise ValueError('QMP response missing or oversized')
        return json.loads(line)

    def query(self, command, arguments=None):
        self.stream.write(json.dumps({'execute': command, 'arguments': arguments or {}}).encode() + b'\n')
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            response = self._read()
            if 'error' in response:
                raise ValueError(str(response['error'])[:1024])
            if 'return' in response:
                return response['return']
        raise TimeoutError('Owned QMP response timed out')

    def key(self, name):
        if name not in KEYS:
            raise ValueError('Unsupported Canon key')
        self.query('send-key', {'keys': [{'type': 'qcode', 'data': name}], 'hold-time': 80})

    def close(self):
        if self.stream:
            self.stream.close()
        self.connection.close()


PAGE = b'''<!doctype html><meta charset="utf-8"><title>Canon EOS 70D 1.1.2</title>
<style>body{background:#181818;color:white;font:16px sans-serif}img{max-width:100%;width:720px}button{padding:12px;margin:4px}</style>
<h2>Canon EOS 70D 1.1.2</h2><p>Original guest menu. Up/Down verified; tabs and submenus may wait for unimplemented hardware. Photography and LiveView are unverified.</p>
<img id="frame" src="/frame.png"><p><button onclick="key('m')">MENU</button><button onclick="key('left')">Left</button><button onclick="key('up')">Up</button><button onclick="key('down')">Down</button><button onclick="key('right')">Right</button><button onclick="key('spc')">SET</button></p>
<script>function key(k){fetch('/key',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({key:k})})}
setInterval(()=>frame.src='/frame.png?t='+Date.now(),700);onkeydown=e=>{let k={ArrowUp:'up',ArrowDown:'down',ArrowLeft:'left',ArrowRight:'right',Enter:'spc',Escape:'m'}[e.key];if(k){e.preventDefault();key(k)}};</script>'''


def emit(event, **fields):
    print(json.dumps({'event': event, **fields}), flush=True)


def run(config):
    if os.name != 'posix':
        raise ValueError('Run this supervisor under WSL/Linux')
    if config.get('revision') != REVISION or config.get('mode') not in ('probe', 'interactive'):
        raise ValueError('Unreviewed Canon source/profile')
    seconds = config.get('seconds', 30)
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not 26 <= seconds <= 120:
        raise ValueError('Canon probe/initialization budget must be 26..120 seconds')
    port = config.get('port', 8793)
    if isinstance(port, bool) or not isinstance(port, int) or not 1024 <= port <= 65535:
        raise ValueError('Invalid local Canon port')
    qemu = Path(config['qemu'])
    qemu_hash = config.get('qemu_sha256')
    if not isinstance(qemu_hash, str) or len(qemu_hash) != 64 or any(c not in '0123456789abcdef' for c in qemu_hash):
        raise ValueError('Missing Canon QEMU SHA256')
    regular(qemu, digest=qemu_hash)
    roms = Path(config['roms'])
    for name, (size, digest) in ROM_FILES.items():
        regular(roms / name, expected=size, digest=digest)
    out = Path(config['destination'])
    if not out.is_absolute() or '..' in out.parts:
        raise ValueError('Invalid Canon evidence path')
    for item in (out, *out.parents):
        if item.is_symlink():
            raise ValueError('Canon evidence path traverses a symlink')
    out.mkdir(parents=True, exist_ok=False)
    stopped = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stopped.set())
    def read_stop():
        try:
            pending = b''
            descriptor = sys.stdin.fileno()
            while not stopped.is_set():
                if not select.select([descriptor], [], [], .2)[0]: continue
                data = os.read(descriptor, 65)
                if not data: stopped.set(); return
                pending += data
                if len(pending) > 64 or b'\n' in pending:
                    stopped.set(); return
        except (OSError, ValueError):
            stopped.set()
    input_thread = threading.Thread(target=read_stop, daemon=True)
    input_thread.start()
    record = {'model': 'Canon EOS 70D', 'firmware': '1.1.2', 'revision': REVISION,
              'qemu_sha256': qemu_hash, 'rom_sha256': {n: v[1] for n, v in ROM_FILES.items()},
              'original_reset_boot_verified': False, 'original_ui_verified': False,
              'native_navigation_verified': False, 'full_camera_hardware_verified': False,
              'firmware_instructions_patched': False, 'magic_lantern_loaded': False,
              'interventions': ['custom qemu-eos hardware model and recorded MPU replies',
                                'BOOTDISK flag set to zero in the temporary emulated ROM',
                                'empty FAT16 SD/CF snapshot media; networking disabled'],
              'captures': [], 'inputs': []}
    process = None; qmp = None; server = None; server_thread = None
    frame = {'png': b'', 'sha256': None}; commands = queue.Queue(maxsize=32)
    session = uuid.uuid4().hex
    def handler_factory():
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_): pass
            def response(self, status, data, content):
                self.send_response(status); self.send_header('Content-Type', content)
                self.send_header('Content-Length', str(len(data))); self.send_header('Cache-Control', 'no-store')
                self.end_headers(); self.wfile.write(data)
            def do_GET(self):
                path = self.path.split('?', 1)[0]
                if path == '/': self.response(200, PAGE, 'text/html; charset=utf-8')
                elif path == '/frame.png': self.response(200, frame['png'], 'image/png')
                elif path == '/status':
                    self.response(200, json.dumps({'session': session, 'phase': 'running', 'ready': True,
                                                  'busy': False, 'error': None, 'frame': frame['sha256']}).encode(), 'application/json')
                else: self.response(404, b'Not found', 'text/plain')
            def do_POST(self):
                try:
                    if self.path != '/key': raise ValueError('Unknown endpoint')
                    if self.headers.get('Origin') not in (None, f'http://127.0.0.1:{port}'):
                        raise ValueError('Foreign origin')
                    length = int(self.headers.get('Content-Length', '-1'))
                    if not 1 <= length <= 128: raise ValueError('Invalid request length')
                    value = json.loads(self.rfile.read(length))
                    if not isinstance(value, dict) or set(value) != {'key'} or value['key'] not in KEYS:
                        raise ValueError('Invalid key')
                    commands.put_nowait(value['key'])
                    self.response(202, b'{}', 'application/json')
                except (ValueError, TypeError, queue.Full):
                    self.response(400, b'Invalid request', 'text/plain')
        return Handler
    try:
        with tempfile.TemporaryDirectory(prefix='canon70d-') as temporary:
            work = Path(temporary); runtime_roms = work / 'roms' / '70D'; runtime_roms.mkdir(parents=True)
            # Copy only the bytes whose digest was checked, then check both ends again.
            for name, (size, digest) in ROM_FILES.items():
                with (roms / name).open('rb') as source, (runtime_roms / name).open('xb') as target:
                    remaining = size
                    while remaining:
                        if stopped.is_set(): raise InterruptedError('Canon session cancelled')
                        block = source.read(min(1024**2, remaining))
                        if not block: raise ValueError('Canon ROM was truncated during copy')
                        target.write(block); remaining -= len(block)
                    if source.read(1): raise ValueError('Canon ROM grew during copy')
                regular(roms / name, expected=size, digest=digest)
                regular(runtime_roms / name, expected=size, digest=digest)
            cards(work)
            qmp_path = work / 'qmp.sock'
            command = [str(qemu), '-M', '70D,firmware=boot=0',
                       '-drive', 'if=sd,file=sd.img,format=raw,snapshot=on',
                       '-drive', 'if=ide,file=cf.img,format=raw,snapshot=on', '-nic', 'none',
                       '-display', 'none', '-monitor', 'none', '-qmp', f'unix:{qmp_path},server,nowait',
                       '-serial', f'file:{out}/serial.log', '-D', str(out / 'guest-debug.log'), '-d', 'mpu']
            regular(qemu, digest=qemu_hash)
            with (out / 'qemu.log').open('xb') as log:
                process = subprocess.Popen(command, cwd=work, env=dict(os.environ, QEMU_EOS_WORKDIR=str(work / 'roms')),
                                           stdout=log, stderr=log, start_new_session=True)
                record['qemu_pid'] = process.pid
                started = time.monotonic(); deadline = started + seconds
                def wait(seconds_after):
                    while time.monotonic() - started < seconds_after:
                        if stopped.is_set(): raise InterruptedError('Canon session cancelled')
                        if process.poll() is not None: raise ValueError('Canon guest exited unexpectedly')
                        if time.monotonic() > deadline: raise TimeoutError('Canon initialization/probe timed out')
                        if any(p.stat().st_size > LOG_LIMIT for p in out.glob('*.log')):
                            raise ValueError('Canon diagnostic log cap reached')
                        stopped.wait(.05)
                try:
                    while not qmp_path.exists(): wait(time.monotonic() - started + .05)
                    qmp = QMP(qmp_path)
                    def key(name):
                        qmp.key(name); record['inputs'].append({'qcode': name, 'seconds': round(time.monotonic() - started, 3)})
                    def capture(name=None):
                        path = work / 'frame.ppm'; qmp.query('screendump', {'filename': str(path)})
                        with path.open('rb') as stream:
                            raw = stream.read(15 + 720 * 480 * 3 + 1)
                        rgb = parse_ppm(raw); image = png(rgb); fingerprint = sha(rgb)
                        frame.update(png=image, sha256=fingerprint)
                        if name:
                            with (out / (name + '.ppm')).open('xb') as stream: stream.write(raw)
                            with (out / (name + '.png')).open('xb') as stream: stream.write(image)
                            record['captures'].append({'name': name, 'rgb_sha256': fingerprint,
                                                       'ppm_sha256': sha(raw), 'width': 720, 'height': 480})
                        return fingerprint
                    wait(10); key('m'); wait(13); menu = capture('01-menu')
                    if menu != MENU_RGB_SHA256:
                        raise ValueError('Original Canon menu pixels did not match the reviewed ROM profile')
                    serial = (out / 'serial.log').read_bytes()
                    if b'startupInitializeComplete' not in serial:
                        raise ValueError('Canon original reset startup milestone was not observed')
                    record.update(original_reset_boot_verified=True, original_ui_verified=True)
                    if config['mode'] == 'probe':
                        wait(15); key('down'); wait(18); down = capture('02-down')
                        wait(20); key('up'); wait(23); up = capture('03-up')
                        record['native_navigation_verified'] = (down == DOWN_RGB_SHA256 and up == menu)
                        if not record['native_navigation_verified']:
                            raise ValueError('Canon native menu Down/Up pixel roundtrip failed')
                    else:
                        server = ThreadingHTTPServer(('127.0.0.1', port), handler_factory())
                        server.daemon_threads = True
                        server_thread = threading.Thread(target=server.serve_forever, daemon=True); server_thread.start()
                        emit('ready', url=f'http://127.0.0.1:{port}', session=session)
                        interactive_deadline = time.monotonic() + 3600
                        while not stopped.is_set():
                            if time.monotonic() > interactive_deadline: raise TimeoutError('Canon session reached its one-hour limit')
                            if process.poll() is not None: raise ValueError('Canon guest exited unexpectedly')
                            if any(p.stat().st_size > LOG_LIMIT for p in out.glob('*.log')):
                                raise ValueError('Canon diagnostic log cap reached')
                            try:
                                key(commands.get(timeout=.3))
                            except queue.Empty: pass
                            capture()
                finally:
                    if server:
                        server.shutdown(); server.server_close()
                        if server_thread: server_thread.join(timeout=2)
                    if qmp and process.poll() is None:
                        try: qmp.query('quit'); process.wait(timeout=3)
                        except (ValueError, OSError, TimeoutError, subprocess.TimeoutExpired): pass
                    stop_owned(process)
                    if qmp: qmp.close()
                    record.update(qemu_exit_code=process.returncode, process_reaped=process.poll() is not None,
                                  elapsed_seconds=round(time.monotonic() - started, 3))
    except InterruptedError:
        record['cancelled'] = True
    except BaseException as error:
        record['error'] = str(error)[:2000]
    finally:
        stopped.set()
        input_thread.join(timeout=1)
        stop_owned(process)
        record.setdefault('process_reaped', process is None or process.poll() is not None)
        try:
            record['source_hashes_unchanged'] = all(regular(roms / n, expected=s, digest=d) == d for n, (s, d) in ROM_FILES.items())
            record['qemu_hash_unchanged'] = regular(qemu, digest=qemu_hash) == qemu_hash
        except (OSError, ValueError) as error:
            record['source_hashes_unchanged'] = False
            record['error'] = 'Canon source/runtime changed: ' + str(error)[:1000]
        (out / 'evidence.json').write_text(json.dumps(record, indent=2) + '\n')
    if record.get('error'):
        emit('error', message=record['error']); return 1
    if config['mode'] == 'probe':
        emit('result', original_ui_verified=record['original_ui_verified'], native_navigation_verified=record['native_navigation_verified'],
             original_reset_boot_verified=record['original_reset_boot_verified'], process_reaped=record.get('process_reaped') is True)
    emit('stopped', process_reaped=record.get('process_reaped') is True)
    return 0


def main():
    try:
        if len(sys.argv) != 2 or len(sys.argv[1]) > 16384:
            raise ValueError('Expected one bounded JSON configuration argument')
        config = json.loads(sys.argv[1])
        if not isinstance(config, dict): raise ValueError('Configuration must be an object')
        return run(config)
    except BaseException as error:
        emit('error', message=str(error)[:2000]); return 1


if __name__ == '__main__':
    raise SystemExit(main())
