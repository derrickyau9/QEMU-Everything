#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Owned X3 QEMU GUI runner. HTML only displays original guest framebuffer pixels."""
import hashlib
import http.server
import json
import os
from pathlib import Path
import queue
import secrets
import signal
import socket
import stat
import struct
import subprocess
import sys
import tempfile
import threading
import time
import zlib

ARCHIVE_SHA256 = '821b545b92f165e57dddccac5077d76d4d436a226595b8813ad59306bbfd0746'
RTOS_SHA256 = 'a89070d6edc57cd489c9f5e3e81afca7750d08dd5d39d423e7a86efef73a7569'
RTOS_SIZE = 49344520
SOURCES = ('build_ui.py', 'run_ui.py', 'x3-ui-lab.c', 'ui-platform.S', 'ui-platform.ld')
REGIONS = {0x5975bc: 16, 0x5975d0: 8, 0x597604: 8, 0x597728: 8,
           0x597744: 8, 0x8cec64: 8, 0x8cecd8: 8, 0x8ced44: 4}
WIDTH, HEIGHT = 352, 476
INITIAL_RGB_SHA256 = '22da241868da95972d6186d86c8565a67d8dceb3fef7e8ad30736ec3e80cd90b'
RESOLUTION_72_SHA256 = '115999d4a7290b3b6285c75f29f93a33212a1868b5ca2505d3f7af91c359c1cb'
RESOLUTION_18_SHA256 = '963796e380ecb3fe8414aa9801c9c0de808349e5d12b219b1a83cea3130d176e'
HTML = '''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Insta360 X3 · QEMU Everything</title><style>body{background:#16181d;color:#ddd;font:16px system-ui;text-align:center;margin:24px}img{width:352px;max-width:95vw;touch-action:none;border:1px solid #444;border-radius:12px}p{max-width:760px;margin:16px auto}button{padding:10px;margin:5px;background:#303843;color:white;border:0;border-radius:8px}</style>
<h1>Insta360 X3 · 原厂界面 / Original UI</h1><p>v1.1.4.1 · Assisted GUI laboratory / 界面辅助启动实验室</p><img id="lcd" alt="Original QEMU guest framebuffer"><p id="status">Connecting…</p>
<p>点击相机画面操作。当前验证范围为原厂菜单绘制与触摸；拍摄、实时取景及完整开机尚未实现。<br>Click the camera screen. Menu rendering and touch are verified; photography, live view and full boot are not implemented.</p>
<button id="reset">打开分辨率菜单 / Open resolution menu</button>
<script>const token='TOKEN',lcd=document.getElementById('lcd');let chain=Promise.resolve(),down=false;
function send(data){chain=chain.then(()=>fetch('/input',{method:'POST',headers:{'Content-Type':'application/json','X-X3-Token':token},body:JSON.stringify(data)})).catch(()=>{});}
function point(e,pressed){const r=lcd.getBoundingClientRect();send({x:Math.max(0,Math.min(351,Math.round((e.clientX-r.left)*352/r.width))),y:Math.max(0,Math.min(475,Math.round((e.clientY-r.top)*476/r.height))),pressed});}
lcd.onpointerdown=e=>{e.preventDefault();down=true;lcd.setPointerCapture(e.pointerId);point(e,true)};lcd.onpointermove=e=>{if(down)point(e,true)};lcd.onpointerup=e=>{down=false;point(e,false)};lcd.onpointercancel=e=>{down=false;point(e,false)};
document.getElementById('reset').onclick=()=>send({screen:'resolution'});
async function poll(){try{const r=await fetch('/frame.png',{cache:'no-store'});if(r.ok){const url=URL.createObjectURL(await r.blob());const old=lcd.src;lcd.src=url;if(old.startsWith('blob:'))URL.revokeObjectURL(old);document.getElementById('status').textContent='QEMU · native framebuffer · 352 × 476';}}catch(e){document.getElementById('status').textContent='Session stopped / 会话已停止'}setTimeout(poll,200)}poll();</script></html>'''


def regular(path, bound=128*1024**2):
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts: raise ValueError('Expected absolute lab path')
    for item in (path, *path.parents):
        if item.is_symlink(): raise ValueError('Lab path contains a symlink')
    metadata = path.stat()
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or metadata.st_size > bound:
        raise ValueError('Expected bounded non-hardlinked regular lab input')
    return path


def digest(path):
    value = hashlib.sha256()
    with regular(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024**2), b''): value.update(block)
    return value.hexdigest()


def snapshot(source, destination, size, fingerprint):
    source = regular(source)
    before = source.stat(); value = hashlib.sha256(); count = 0
    descriptor = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, 'rb') as reader, destination.open('xb') as writer:
        opened = os.fstat(reader.fileno())
        if not stat.S_ISREG(opened.st_mode) or opened.st_size != size: raise ValueError('Snapshot input changed')
        for block in iter(lambda: reader.read(1024**2), b''):
            count += len(block)
            if count > size: raise ValueError('Snapshot input grew')
            value.update(block); writer.write(block)
        after = os.fstat(reader.fileno())
    identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
    if count != size or value.hexdigest() != fingerprint or len({identity(s) for s in (before, opened, after, source.stat())}) != 1:
        raise ValueError('Snapshot bytes or source identity changed')
    return destination


def validate(cfg):
    manifest = json.loads(regular(cfg['manifest'], 65536).read_text())
    if (manifest.get('profile') != 'insta360-x3-ui-v1' or manifest.get('qemu_version') != '10.2.4'
            or manifest.get('upstream_archive_sha256') != ARCHIVE_SHA256):
        raise ValueError('Requires the reviewed X3 GUI QEMU build')
    overlay = Path(__file__).resolve().parent
    for name in SOURCES:
        if digest(overlay / name) != manifest['overlay_sha256'].get(name):
            raise ValueError('X3 GUI source changed; rebuild: ' + name)
    for name in ('binary', 'platform'):
        if digest(manifest[name]) != manifest[name + '_sha256']: raise ValueError('X3 build artifact changed: ' + name)
    if digest(Path(manifest['source_directory']) / 'hw/arm/x3-ui-lab.c') != manifest['overlay_sha256']['x3-ui-lab.c']:
        raise ValueError('Compiled X3 board source changed')
    if regular(cfg['rtos']).stat().st_size != RTOS_SIZE or digest(cfg['rtos']) != RTOS_SHA256:
        raise ValueError('Requires the exact original X3 v1.1.4.1 RTOS')
    if cfg.get('mode') not in ('probe', 'interactive'): raise ValueError('Unknown X3 GUI mode')
    seconds = cfg.get('seconds')
    if isinstance(seconds, bool) or not isinstance(seconds, (float, int)) or not 20 <= seconds <= 120:
        raise ValueError('Probe/startup budget must be 20..120 seconds')
    if cfg.get('port') != 8791: raise ValueError('Unexpected X3 loopback port')
    return manifest


def platform_parts(elf):
    if len(elf) > 1024**2 or elf[:7] != b'\x7fELF\x01\x01\x01' or struct.unpack_from('<H', elf, 18)[0] != 40:
        raise ValueError('Expected bounded ARM ELF32 platform')
    phoff = struct.unpack_from('<I', elf, 28)[0]
    phsize, phnum = struct.unpack_from('<HH', elf, 42)
    if phsize != 32 or phnum != len(REGIONS)+1 or phoff + phnum*phsize > len(elf): raise ValueError('Unexpected platform segment directory')
    patches = {}; bootstrap = None
    for i in range(phnum):
        typ, off, va, pa, size, memsz, flags, align = struct.unpack_from('<8I', elf, phoff+i*phsize)
        if typ != 1 or va != pa or size != memsz or size % 4 or off+size > len(elf): raise ValueError('Invalid platform segment')
        if pa == 0x1ffc0000:
            if bootstrap is not None or not 4 <= size <= 4096: raise ValueError('Invalid platform bootstrap')
            bootstrap = elf[off:off+size]
        else:
            if pa in patches or REGIONS.get(pa) != size: raise ValueError('Platform writes outside reviewed RAM hook regions')
            patches[pa] = elf[off:off+size]
    if patches.keys() != REGIONS.keys() or bootstrap is None: raise ValueError('Missing platform segment')
    return bootstrap, patches


def make_command(manifest, firmware, elf, temporary, out):
    command = [manifest['binary'], '-M', 'insta360-x3-ui-lab', '-smp', '3', '-m', '512',
        '-display', 'none', '-nic', 'none', '-monitor', 'none', '-serial', 'file:' + str(out / 'serial.log'),
        '-qmp', 'unix:' + str(temporary / 'qmp') + ',server=on,wait=off']
    def load(path, address): command.extend(['-device', f'loader,file={path},addr={address},force-raw=on'])
    def data(address, blob):
        for index in range(0, len(blob), 8):
            part = blob[index:index+8]
            command.extend(['-device', f'loader,addr={address+index},data={int.from_bytes(part,"little")},data-len={len(part)}'])
    bootstrap, patches = platform_parts(elf)
    table = b''.join(struct.pack('<II', address+i, int.from_bytes(blob[i:i+4], 'little')) for address, blob in patches.items() for i in range(0,len(blob),4)) + bytes(8)
    for name, address, blob in (
        ('bootstrap.bin', 0x1ffc0000, bootstrap), ('patches.bin', 0x1ffd0000, table),
        ('mmu.bin', 0x1ffa0000, b''.join(struct.pack('<I', (i<<20)|(0x11c02 if i<512 else 0xc02)) for i in range(4096)))):
        path = temporary / name; path.write_bytes(blob); load(path, address)
    load(firmware, 0x20000)
    data(0, struct.pack('<8I', *([0xeafffffe]*8)))
    data(0x1ffe0010, struct.pack('<I', 2))
    data(0x1ffe0040, struct.pack('<8I', 1,0,0x1800,0,0x1801,0,0,0))
    command.extend(['-device', 'loader,addr=0x1ffc0000,cpu-num=0'])
    return command, [dict(address=hex(address), size=len(blob), bytes=blob.hex()) for address, blob in patches.items()]


class QMP:
    def __init__(self, path):
        self.socket = socket.socket(socket.AF_UNIX); self.socket.settimeout(3); self.socket.connect(str(path))
        self.stream = self.socket.makefile('rwb', buffering=0)
        json.loads(self.stream.readline(65537)); self.call('qmp_capabilities')
    def call(self, name, arguments=None):
        self.stream.write((json.dumps({'execute': name, **({'arguments': arguments} if arguments else {})})+'\n').encode())
        while True:
            line = self.stream.readline(65537)
            if not line or len(line) > 65536: raise RuntimeError('Invalid QMP response')
            response = json.loads(line)
            if 'error' in response: raise RuntimeError(str(response['error']))
            if 'return' in response: return response['return']
    def close(self): self.stream.close(); self.socket.close()


def decode_ppm(blob):
    # QEMU emits this exact P6 header. Read no external image libraries.
    header = b'P6\n352 476\n255\n'
    if not blob.startswith(header) or len(blob) != len(header)+WIDTH*HEIGHT*3: raise ValueError('Guest frame has unexpected dimensions/encoding')
    return blob[len(header):]


def rgb_png(pixels):
    if len(pixels) != WIDTH*HEIGHT*3: raise ValueError('Invalid RGB frame size')
    def chunk(kind, data): return struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data)&0xffffffff)
    scan = b''.join(b'\0'+pixels[y*WIDTH*3:(y+1)*WIDTH*3] for y in range(HEIGHT))
    return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>2I5B',WIDTH,HEIGHT,8,2,0,0,0))+chunk(b'IDAT',zlib.compress(scan))+chunk(b'IEND',b'')


def resolution_fingerprint(pixels):
    # Only the 120-row native resolution selector. The original preset-change
    # toast above it has independent timing and is preserved in full captures.
    if len(pixels) != WIDTH*HEIGHT*3: raise ValueError('Invalid native frame size')
    return hashlib.sha256(pixels[210*WIDTH*3:330*WIDTH*3]).hexdigest()


def touch(qmp, x, y, pressed):
    if type(x) is not int or type(y) is not int or type(pressed) is not bool or not (0 <= x < WIDTH and 0 <= y < HEIGHT):
        raise ValueError('Invalid native touch coordinates')
    qmp.call('input-send-event', {'events': [
        {'type':'abs','data':{'axis':'x','value':round(x*32767/(WIDTH-1))}},
        {'type':'abs','data':{'axis':'y','value':round(y*32767/(HEIGHT-1))}},
        {'type':'btn','data':{'button':'left','down':pressed}}]})


def emit(event, **fields): print(json.dumps(dict(event=event, **fields)), flush=True)


def main(cfg):
    child = server = qmp = None; stop = threading.Event(); errors = []; state = {'png': b''}; output_created = False
    out = Path(cfg['destination']); actions = queue.Queue(maxsize=32); session = secrets.token_hex(16)
    state.update(session=session,phase='booting',ready=False,busy=False,error=None,frame=0)
    result = dict(profile='insta360-x3-ui-v1', firmware_sha256=RTOS_SHA256,
        assisted_startup=True, full_threadx_boot_verified=False, natural_power_on_verified=False,
        firmware_file_modified=False, guest_ram_modified=True, camera_access=False,
        original_ui_verified=False, native_touch_verified=False, frames=[],
        interventions=['ARMv7 Cortex-A15 execution of original ARM32 emWin GUI',
            'Direct entry to original GUI task 0x8ca5f4 with identity MMU/VFP',
            'Eight RAM platform hook regions (68 bytes): clock/delay/task identity/LCD buffer and present/GUI and app dispatch',
            'Original handlers dispatched synchronously; ThreadX scheduler and other tasks bypassed',
            'Original resolution screen requested through its native handler',
            'Diagnostic zero/latch peripheral bank; DSP and camera pipeline absent'])
    try:
        manifest = validate(cfg)
        # Validate all output parents before mkdir; never follow arbitrary links.
        if not out.is_absolute() or '..' in out.parts or any(p.is_symlink() for p in (out,*out.parents)):
            raise ValueError('Invalid output path')
        out.mkdir(parents=True, exist_ok=False)
        output_created = True
        def stdin_watch():
            for line in sys.stdin:
                if line.strip() == 'stop': break
            stop.set()
        threading.Thread(target=stdin_watch, daemon=True).start()
        with tempfile.TemporaryDirectory(prefix='qe-x3-ui-') as folder:
            temporary = Path(folder)
            firmware = snapshot(cfg['rtos'], temporary/'rtos.bin', RTOS_SIZE, RTOS_SHA256)
            platform = regular(manifest['platform'], 1024**2).read_bytes()
            if hashlib.sha256(platform).hexdigest() != manifest['platform_sha256']: raise ValueError('Platform changed while reading')
            command, patches = make_command(manifest, firmware, platform, temporary, out)
            result.update(command=command, ram_hooks=patches, platform_sha256=manifest['platform_sha256'],
                qemu_sha256=manifest['binary_sha256'], overlay_sha256=manifest['overlay_sha256'])
            with (out/'stderr.log').open('wb') as error_log:
                child = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=error_log, start_new_session=True)
            deadline = time.monotonic()+cfg['seconds']
            while not (temporary/'qmp').exists():
                if stop.wait(.05) or child.poll() is not None or time.monotonic()>deadline: raise RuntimeError('QEMU did not start')
            qmp = QMP(temporary/'qmp')
            def capture(name=None):
                path = temporary/'frame.ppm'; qmp.call('screendump', {'filename':str(path)})
                pixels = decode_ppm(path.read_bytes()); png = rgb_png(pixels); state['png'] = png; state['frame'] += 1
                if name:
                    (out/(name+'.png')).write_bytes(png)
                    (out/(name+'.ppm')).write_bytes(path.read_bytes())
                    result['frames'].append(dict(name=name, sha256=hashlib.sha256(pixels).hexdigest(), width=WIDTH, height=HEIGHT))
                return pixels
            if stop.wait(3): raise RuntimeError('Stopped before original UI startup')
            before = capture('01-resolution-72mp')
            if hashlib.sha256(before).hexdigest() != INITIAL_RGB_SHA256:
                raise RuntimeError('Original resolution menu pixels differ from the visually verified fixed profile')
            result['original_ui_verified'] = True
            if cfg['mode'] == 'probe':
                touch(qmp,290,280,True); stop.wait(.12); touch(qmp,290,280,False); stop.wait(.8)
                after = capture('02-resolution-18mp')
                touch(qmp,40,280,True); stop.wait(.12); touch(qmp,40,280,False); stop.wait(.8)
                restored = capture('03-resolution-72mp-return')
                fingerprints = [resolution_fingerprint(frame) for frame in (before,after,restored)]
                result['resolution_fingerprints'] = fingerprints
                result['native_touch_verified'] = fingerprints == [RESOLUTION_72_SHA256,RESOLUTION_18_SHA256,RESOLUTION_72_SHA256]
                result['single_instance_menu_roundtrip_verified'] = result['native_touch_verified']
                if not result['native_touch_verified']: raise RuntimeError('Native touch did not reproduce the verified 72MP -> 18MP -> 72MP pixel roundtrip')
                result['registers'] = qmp.call('human-monitor-command', {'command-line':'info registers'})
                emit('result', original_ui_verified=True, native_touch_verified=True,single_instance_menu_roundtrip_verified=True)
            else:
                token = secrets.token_hex(24); port = cfg['port']; expected_host = f'127.0.0.1:{port}'
                class Handler(http.server.BaseHTTPRequestHandler):
                    def log_message(self, *args): pass
                    def reply(self, code, kind, body):
                        self.send_response(code); self.send_header('Content-Type',kind); self.send_header('Content-Length',str(len(body)))
                        self.send_header('Cache-Control','no-store'); self.send_header('X-Content-Type-Options','nosniff'); self.send_header('Content-Security-Policy',"default-src 'self'; img-src 'self' blob:; script-src 'unsafe-inline'; style-src 'unsafe-inline'; frame-ancestors 'none'")
                        self.end_headers(); self.wfile.write(body)
                    def do_GET(self):
                        if self.headers.get('Host') != expected_host: return self.reply(403,'text/plain',b'Invalid host')
                        if self.path == '/': return self.reply(200,'text/html; charset=utf-8',HTML.replace('TOKEN',token).encode())
                        if self.path == '/status': return self.reply(200,'application/json',json.dumps({k:v for k,v in state.items() if k!='png'}).encode())
                        if self.path.startswith('/frame.png'): return self.reply(200,'image/png',state['png'])
                        return self.reply(404,'text/plain',b'Not found')
                    def do_POST(self):
                        if self.path != '/input' or self.headers.get('Host') != expected_host or self.headers.get('X-X3-Token') != token or self.headers.get('Origin') not in (None,'http://'+expected_host):
                            return self.reply(403,'text/plain',b'Invalid request')
                        try:
                            size = int(self.headers.get('Content-Length','0'))
                            if not 1 <= size <= 512: raise ValueError()
                            value = json.loads(self.rfile.read(size))
                            if not isinstance(value,dict): raise ValueError()
                            if value == {'screen':'resolution'}: pass
                            elif set(value) != {'x','y','pressed'} or type(value['x']) is not int or type(value['y']) is not int or type(value['pressed']) is not bool or not 0<=value['x']<WIDTH or not 0<=value['y']<HEIGHT: raise ValueError()
                            actions.put_nowait(value)
                        except (ValueError,KeyError,queue.Full): return self.reply(400,'text/plain',b'Invalid/busy input')
                        self.reply(202,'application/json',b'{"accepted":true}')
                server = http.server.ThreadingHTTPServer(('127.0.0.1',port),Handler); server.daemon_threads=True
                threading.Thread(target=server.serve_forever,daemon=True).start()
                state.update(phase='running',ready=True)
                emit('ready',url='http://'+expected_host,session=session)
                while not stop.is_set():
                    if child.poll() is not None: raise RuntimeError('Owned QEMU exited during UI session')
                    try: action = actions.get(timeout=.1)
                    except queue.Empty: action = None
                    if action:
                        if 'screen' in action:
                            # The synthetic board maps middle-button to an
                            # explicit request for the original screen handler.
                            qmp.call('input-send-event', {'events':[{'type':'btn','data':{'button':'middle','down':True}},{'type':'btn','data':{'button':'middle','down':False}}]})
                        else: touch(qmp,**action)
                        stop.wait(.05)
                    capture()
                capture('03-session-final')
            result['source_sha256_verified_after'] = digest(cfg['rtos']) == RTOS_SHA256
            if not result['source_sha256_verified_after']: raise RuntimeError('Original source changed during session')
            qmp.call('quit'); child.wait(timeout=3)
    except Exception as error:
        errors.append(str(error)); emit('error',message=str(error))
    finally:
        if server: server.shutdown(); server.server_close()
        if qmp:
            try: qmp.close()
            except OSError: pass
        if child and child.poll() is None:
            try: os.killpg(child.pid,signal.SIGTERM)
            except ProcessLookupError: pass
            try: child.wait(timeout=3)
            except subprocess.TimeoutExpired:
                try: os.killpg(child.pid,signal.SIGKILL)
                except ProcessLookupError: pass
                try: child.wait(timeout=3)
                except subprocess.TimeoutExpired: errors.append('Owned QEMU did not exit')
        result.update(errors=errors, owned_guest_processes_reaped=child is None or child.poll() is not None,
                      process_reaped=child is None or child.poll() is not None)
        if output_created: (out/'evidence.json').write_text(json.dumps(result,indent=2)+'\n')
        emit('stopped',errors=errors)
    return 1 if errors else 0


if __name__ == '__main__':
    if len(sys.argv) != 2 or len(sys.argv[1]) > 16384: raise ValueError('Expected one bounded configuration')
    raise SystemExit(main(json.loads(sys.argv[1])))
