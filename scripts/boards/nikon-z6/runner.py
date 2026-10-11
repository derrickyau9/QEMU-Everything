# SPDX-License-Identifier: AGPL-3.0-or-later
"""Owned offline Z6 runner; frames and menu input use original guest firmware.

Session initialization follows WonderLantern/z6-qemu tools/interactive.py
(GPL-2.0-or-later), pinned 419a1f2a91a1f2d3a0209027c29606475f7fe26f.
Upstream methods perform explicit GDB calls and context restoration. This is
assisted menu execution, not an unmodified natural camera power-on.
"""
import hashlib
import http.server
import importlib.abc
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import queue
import secrets
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time

MAIN_SIZE = 33502842
MAIN_SHA256 = '1e6348d58750e67372d0a8e363932c57ee9c04d0eb8f62803bcd19812df0d25c'
REVISION = '419a1f2a91a1f2d3a0209027c29606475f7fe26f'
HASHES = {'tools/interactive.py': '4e2da4aa0acdfe623de80bd6e8cf287fefc01e5051832235eac9db6dd6ff833d',
 'tools/probe.py': 'a90a19676b4ab1395e71a52014e800fd878f680029e78e07cced20f419a0a558',
 'tools/rsp.py': '616a175b0b0c68d32dcbe774c3ea54da4aaa8c94e2d3e376210cdecc60e202ed',
 'tools/focus_display.py': '3cb71eda138e3d89a0f7cc1bd7acffe4f8ac259a4c91d16985b46166e6cae0c9',
 'tools/capture_diagnostics.py': 'd7439e0ac09981b9006a5fc04153a824bc8d2855f28fa218c36343505e87a557',
 'tools/raw_input.py': '9acd60ef68594c5a78f00e2812261c0c78f442fa009cb3b21bf075cdaf1c8f34',
 'tools/cmos_config.py': '2cc72804d7cfd1f7608ea43236fbcd442d2a8a07378098360b2081ab422cbe3d',
 'tools/shoulder_display.py': '7cf35e065a39b34543bb7b26cf41fc70efe37532ceebf4bbf6a44d11e06f778e'}
EVENTS = {key: key for key in ('up','down','left','right','ok','menu')}
EVENTS['back'] = 'left'
MENU_FINGERPRINT_SHA256 = 'b7fe783476104432d380f3b26b5248bc03db6a7e7fc9e66007dc2b9ea5b87764'
NEXT_FINGERPRINT_SHA256 = 'd5f17407ba15b94f073b33e688e52cf79636c07fed4d629b1fec4e6de5805469'
FINGERPRINT_MASK = {'x': 646, 'y': 448, 'width': 32, 'height': 4,
                    'reason': 'Observed blinking disabled slide-show marker; displayed pixels remain original'}
PAGE = '''<!doctype html><html lang="en"><meta charset="utf-8"><title>Nikon Z6 C3.80 menu</title>
<style>body{background:#17191d;color:#eee;font:16px system-ui;max-width:1020px;margin:30px auto}img{width:min(720px,94vw);aspect-ratio:3/2;background:#000}.layout{display:flex;gap:20px;align-items:center;flex-wrap:wrap}.pad{display:grid;grid-template-columns:repeat(3,64px);gap:8px}button{padding:16px;background:#353a43;color:white;border:1px solid #67717f;border-radius:6px}p{color:#abb2bd}</style>
<h1>Nikon Z6 · C3.80 original menu / 原厂菜单</h1><p>GDB assisted startup and input / GDB 辅助启动与输入。Natural power-on, live-view and photography have not been verified.</p><div id="status">Starting…</div><div class="layout"><img id="lcd" alt="Original guest GPU frame"><div class="pad"><span></span><button data-key="up">↑</button><span></span><button data-key="left">←</button><button data-key="ok">OK</button><button data-key="right">→</button><span></span><button data-key="down">↓</button><span></span><button data-key="back" style="grid-column:span 3">Back / 返回</button></div></div>
<script>const token='TOKEN';let ready=false,frame=0;async function key(k){if(!ready)return;ready=false;await fetch('/input',{method:'POST',headers:{'Content-Type':'application/json','X-Z6-Token':token},body:JSON.stringify({key:k})});}document.querySelectorAll('[data-key]').forEach(b=>b.onclick=()=>key(b.dataset.key));document.onkeydown=e=>{const k={ArrowUp:'up',ArrowDown:'down',ArrowLeft:'left',ArrowRight:'right',Enter:'ok',Escape:'back',Backspace:'back'}[e.key];if(k){e.preventDefault();if(!e.repeat)key(k);}};async function poll(){try{const s=await(await fetch('/status')).json();ready=s.ready&&!s.busy&&!s.error;document.getElementById('status').textContent=s.error||s.phase;document.querySelectorAll('button').forEach(b=>b.disabled=!ready);if(s.frame!==frame){frame=s.frame;document.getElementById('lcd').src='/frame.png?v='+frame;}}catch(e){ready=false;}setTimeout(poll,400);}poll();</script></html>'''


def regular(path):
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError('Expected an absolute local lab path')
    for item in (path, *path.parents):
        if item.is_symlink():
            raise ValueError('Symlink in Z6 lab path')
    metadata = path.stat()
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        raise ValueError('Expected a regular non-hardlinked Z6 input')
    return path


def digest(path):
    value = hashlib.sha256()
    with regular(path).open('rb') as stream:
        while block := stream.read(1024**2): value.update(block)
    return value.hexdigest()


def save(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')


def emit(event, **fields):
    print(json.dumps(dict(event=event, **fields), ensure_ascii=False), flush=True)


def menu_fingerprint(pixels, width, height):
    if (width, height) != (720, 480) or len(pixels) != 720 * 480 * 3:
        raise ValueError('Z6 menu fingerprint requires exactly 720x480 RGB')
    stable = bytearray(pixels)
    # The exact 32x4 marker is the only observed animation in this probe.
    # Hash all remaining pixels; save and display the unmodified full frame.
    for y in range(448, 452):
        stable[(y * 720 + 646) * 3:(y * 720 + 678) * 3] = bytes(32 * 3)
    return hashlib.sha256(stable).hexdigest()


def main():
    if len(sys.argv) != 2 or len(sys.argv[1]) > 16384:
        raise ValueError('Expected one bounded Z6 configuration')
    cfg = json.loads(sys.argv[1])
    project, image, qemu, out = (Path(cfg[key]) for key in ('project', 'main', 'qemu', 'out'))
    if cfg.get('revision') != REVISION or cfg.get('script_hashes') != HASHES:
        raise ValueError('Z6 reviewed upstream pin differs')
    if cfg.get('main_size') != MAIN_SIZE or cfg.get('main_sha256') != MAIN_SHA256:
        raise ValueError('Z6 main profile differs')
    if regular(image).stat().st_size != MAIN_SIZE or digest(image) != MAIN_SHA256:
        raise ValueError('Z6 prepared main changed before launch')
    if qemu != project / '.build/build/qemu-system-arm' or digest(qemu) != cfg['qemu_sha256']:
        raise ValueError('Z6 QEMU binary changed before launch')
    if digest(Path(__file__).absolute()) != cfg['runner_sha256']:
        raise ValueError('Z6 owned runner changed before launch')
    for name, expected in HASHES.items():
        if digest(project / name) != expected:
            raise ValueError('Z6 reviewed upstream Python source changed')
    if not out.is_absolute() or '..' in out.parts or any(p.is_symlink() for p in (out, *out.parents)) or not out.is_dir() or list(out.iterdir()):
        raise ValueError('Z6 output must be a newly owned empty directory')
    # Imports only reviewed modules from the pinned checkout; firmware cannot
    # supply host code, import paths, settings or executable plugins.
    # Compile the reviewed source directly: an unrelated stale/tampered .pyc
    # must not substitute code for a correctly hashed Python source file.
    class ReviewedLoader(importlib.machinery.SourceFileLoader):
        def get_code(self, fullname):
            data = regular(self.path).read_bytes()
            if hashlib.sha256(data).hexdigest() != HASHES['tools/' + fullname + '.py']:
                raise ValueError('Z6 reviewed Python input changed during import')
            return compile(data, self.path, 'exec', dont_inherit=True)
    class ReviewedFinder(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, path=None, target=None):
            name = 'tools/' + fullname + '.py'
            if name in HASHES:
                source = project / name
                return importlib.util.spec_from_file_location(fullname, source,
                    loader=ReviewedLoader(fullname, str(source)))
    sys.path[:] = [p for p in sys.path if p and 'site-packages' not in p]
    sys.meta_path.insert(0, ReviewedFinder())
    import interactive
    stop = threading.Event(); owned = []; process_lock = threading.Lock(); cleanup_lock = threading.Lock()
    state_lock = threading.Lock(); token = secrets.token_hex(24); session_id = secrets.token_hex(16)
    state = dict(session=session_id, phase='booting', ready=False, busy=True, error=None, frame=0)
    latest_png = [b'']; actions = queue.Queue(maxsize=1); evidence = dict(
        source_revision=REVISION, main_sha256=MAIN_SHA256, qemu_sha256=cfg['qemu_sha256'],
        profile='nikon-z6-display', assisted_startup=True, guest_state_modified=True,
        firmware_file_modified=False, natural_power_on_verified=False, camera_access=False,
        original_ui_verified=False, menu_runtime_verified=False,
        live_view_verified=False, photography_verified=False, frames=[])
    evidence['frame_fingerprint_mask'] = FINGERPRINT_MASK
    started = time.monotonic(); seconds = cfg.get('seconds', 0); owned_spools = []
    if cfg['mode'] not in ('probe', 'interactive') or (cfg['mode'] == 'probe' and not 20 <= seconds <= 120):
        raise ValueError('Invalid Z6 mode or probe bound')

    def cleanup():
        with cleanup_lock:
            with process_lock:
                processes = list(owned)
            for process in processes:
                if process.poll() is None:
                    try: os.killpg(process.pid, signal.SIGTERM)
                    except ProcessLookupError: pass
                    try: process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        try: os.killpg(process.pid, signal.SIGKILL)
                        except ProcessLookupError: pass
                        try: process.wait(timeout=3)
                        except subprocess.TimeoutExpired: pass
            return all(p.poll() is not None for p in processes)

    original_popen = subprocess.Popen
    def owned_popen(command, **kwargs):
        if not isinstance(command, list) or command[0] != str(qemu) or '-bios' not in command or command[command.index('-bios') + 1] != str(image):
            raise ValueError('Z6 reviewed boot attempted an unexpected host executable/input')
        # Upstream enables the experimental LCD/YUV scanout unconditionally.
        # A menu-only guest has no RAW/YUV producer; its black scanout hides
        # the original GPU menu surface. Use the board's existing GUI-surface
        # probe for this profile and record that distinction explicitly.
        kwargs['env'].pop('Z6_LCD_SCANOUT_TEST', None)
        evidence['renderer'] = 'upstream original-GPU GUI-surface probe'
        evidence['lcd_scanout_verified'] = False
        kwargs.update(start_new_session=True, stdin=subprocess.DEVNULL)
        with process_lock:
            if stop.is_set(): raise RuntimeError('Z6 launch cancelled')
            process = original_popen(command, **kwargs); owned.append(process)
        evidence['guest_pid'] = process.pid; evidence['command'] = command
        return process

    class Camera(interactive.Camera):
        def __init__(self):
            # Upstream creates a unique session directory. Redirect only that
            # constructor to this runner's new output directory, then restore
            # PROJECT for pinned source checks in its original boot method.
            interactive.PROJECT = out
            try: super().__init__(cfg['port'], firmware_sha256=MAIN_SHA256)
            finally: interactive.PROJECT = project
            if Path('/tmp').is_symlink(): raise ValueError('Z6 temporary root must not be a symlink')
            self.owned_temp = tempfile.TemporaryDirectory(prefix='qemu-everything-z6-', dir='/tmp')
            self.owned_spool = Path(self.owned_temp.name) / 'nikon-z6' / self.stamp
            self.owned_spool.mkdir(parents=True, exist_ok=False)
            owned_spools.append(self.owned_spool)
        def boot(self):
            original_gettempdir = interactive.tempfile.gettempdir
            interactive.tempfile.gettempdir = lambda: self.owned_temp.name
            try: return super().boot()
            finally: interactive.tempfile.gettempdir = original_gettempdir
        def close_guest(self):
            for handle in (self.remote, self.monitor):
                if handle:
                    try: handle.sock.close() if hasattr(handle, 'sock') else handle.close()
                    except OSError: pass
            self.remote = self.monitor = None
            cleanup(); self.proc = None; self.running = False
            if self.console: self.console.close(); self.console = None
            if hasattr(self, 'live_log'):
                if self.live_log.exists():
                    import shutil
                    shutil.copyfile(self.live_log, out / 'devices.log'); self.live_log.unlink()
                del self.live_log
            if hasattr(self, 'frame_spool'):
                (self.frame_spool / 'screen.ppm').unlink(missing_ok=True)
                try: self.frame_spool.rmdir()
                except OSError: pass
                del self.frame_spool
            if hasattr(self, 'owned_temp'): self.owned_temp.cleanup()

    def settle(camera, seconds=.8):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if stop.wait(.1): raise RuntimeError('Z6 operation cancelled')
            camera.refresh()

    def capture(camera, name):
        path = camera.frame_spool / 'screen.ppm'
        if camera.frame_spool not in owned_spools: owned_spools.append(camera.frame_spool)
        if not path.is_file() or path.stat().st_size > 3 * 1024**2:
            raise ValueError('Z6 original guest did not produce a bounded RGB frame')
        data = path.read_bytes(); w, h, pixels = interactive.ppm_pixels(data)
        png = interactive.rgb_png(w, h, pixels); (out / (name + '.png')).write_bytes(png)
        colors = len(set(pixels[i:i+3] for i in range(0, len(pixels), 3)))
        frame = dict(name=name, sha256=hashlib.sha256(pixels).hexdigest(),
                     fingerprint_sha256=menu_fingerprint(pixels, w, h),
                     width=w, height=h, bytes=len(data), colors=colors)
        evidence['frames'].append(frame)
        with state_lock:
            latest_png[0] = png; state['frame'] += 1
        return frame

    def key(camera, name):
        camera.pause(); packet = camera.run_to(0x100531bc)
        camera.key(packet, EVENTS[name]); camera.continue_guest(); settle(camera)

    def worker():
        camera = None
        try:
            interactive.FIRMWARE = image
            interactive.BUILD_MANIFEST = project / '.local/build-manifest.json'
            interactive.subprocess.Popen = owned_popen
            # Unknown process environment cannot switch on hardware fixtures.
            for name in list(os.environ):
                if name.startswith('Z6_'): os.environ.pop(name)
            camera = Camera(); camera.boot(); settle(camera, 1.5)
            first = capture(camera, '01-menu')
            if first['colors'] < 32 or first['fingerprint_sha256'] != MENU_FINGERPRINT_SHA256:
                raise RuntimeError('Z6 original initial menu frame differs from the verified C3.80 profile')
            evidence['menu_runtime_verified'] = True; evidence['original_ui_verified'] = True
            with state_lock: state.update(phase='running', ready=True, busy=False)
            if cfg['mode'] == 'probe':
                key(camera, 'down'); second = capture(camera, '02-next-item')
                key(camera, 'up'); third = capture(camera, '03-return-item')
                if first['fingerprint_sha256'] != third['fingerprint_sha256'] or second['fingerprint_sha256'] != NEXT_FINGERPRINT_SHA256:
                    raise RuntimeError('Z6 native keys did not change and restore the original menu frame')
                evidence['single_instance_menu_roundtrip_verified'] = True
                evidence['original_api_audit'] = str(camera.audit_path.relative_to(out))
                emit('result', menu_runtime_verified=True, original_ui_verified=True,
                     single_instance_menu_roundtrip_verified=True)
                stop.set(); return
            while not stop.is_set():
                try: name = actions.get(timeout=.1)
                except queue.Empty:
                    camera.refresh()
                    with state_lock:
                        if latest_png[0] != camera.png: latest_png[0] = camera.png; state['frame'] += 1
                    continue
                key(camera, name); capture(camera, f'action-{len(evidence["frames"]):04d}-{name}')
                with state_lock: state['busy'] = False
        except Exception as error:
            if stop.is_set(): evidence['interrupted_guest_operation'] = str(error)[:4000]
            else: evidence['error'] = str(error)[:4000]
            with state_lock: state.update(phase='error', ready=False, busy=False, error=str(error)[:4000])
            if not stop.is_set(): emit('error', message=str(error)[:4000])
            stop.set()
        finally:
            if camera:
                try: camera.close_guest()
                except Exception as error: evidence['close_error'] = str(error)[:4000]
            interactive.subprocess.Popen = original_popen

    def stdin_watch():
        # EOF (including a dead owned Windows transport) is also a stop request.
        try:
            for line in sys.stdin:
                if line.strip() == 'stop': break
        finally: stop.set()
    def watchdog():
        try:
            while not stop.wait(.2):
                files = list(out.rglob('*')); total = 0
                for spool_root in list(owned_spools):
                    if spool_root.is_dir(): files += [p for p in spool_root.rglob('*') if p.is_file()]
                for path in files:
                    try:
                        metadata = path.stat()
                    except FileNotFoundError:
                        continue  # QEMU can atomically replace its current frame.
                    if stat.S_ISREG(metadata.st_mode): total += metadata.st_size
                with state_lock: ready = state['ready']
                if (not ready and time.monotonic() - started > 40) or (cfg['mode'] == 'probe' and time.monotonic() - started > seconds) or len(files) > 512 or total > 64 * 1024**2:
                    evidence['error'] = 'Z6 run exceeded its time/output bound'
                    emit('error', message=evidence['error']); stop.set()
        except OSError as error:
            evidence['error'] = 'Z6 output supervision failed: ' + str(error)[:2000]
            emit('error', message=evidence['error']); stop.set()
        finally:
            cleanup()
    for sig in (signal.SIGINT, signal.SIGTERM): signal.signal(sig, lambda *_: stop.set())
    threading.Thread(target=stdin_watch, daemon=True).start()
    threading.Thread(target=watchdog, daemon=True).start()
    thread = threading.Thread(target=worker, daemon=True)
    server = None; worker_started = False
    try:
        if cfg['mode'] == 'interactive':
            class Handler(http.server.BaseHTTPRequestHandler):
                def log_message(self, *_): pass
                def setup(self):
                    super().setup(); self.connection.settimeout(2)
                def reply(self, status, kind, data):
                    self.send_response(status); self.send_header('Content-Type', kind)
                    self.send_header('Content-Length', str(len(data))); self.send_header('Cache-Control', 'no-store')
                    self.send_header('X-Content-Type-Options', 'nosniff'); self.end_headers(); self.wfile.write(data)
                def do_GET(self):
                    if self.path == '/': return self.reply(200, 'text/html; charset=utf-8', PAGE.replace('TOKEN', token).encode())
                    if self.path == '/status':
                        with state_lock: data = json.dumps(state).encode()
                        return self.reply(200, 'application/json', data)
                    if self.path.startswith('/frame.png'):
                        with state_lock: data = latest_png[0]
                        return self.reply(200 if data else 503, 'image/png', data)
                    self.reply(404, 'text/plain', b'Not found')
                def do_POST(self):
                    if self.path != '/input' or self.headers.get('X-Z6-Token') != token:
                        return self.reply(403, 'text/plain', b'Forbidden')
                    try:
                        size = int(self.headers.get('Content-Length', '0'))
                        if not 0 < size < 256: raise ValueError('Invalid body size')
                        key = json.loads(self.rfile.read(size))['key']
                        if key not in EVENTS: raise ValueError('Unknown key')
                        with state_lock:
                            if not state['ready'] or state['busy']: return self.reply(409, 'text/plain', b'Busy')
                            actions.put_nowait(key); state['busy'] = True
                    except (ValueError, TypeError, KeyError, queue.Full):
                        return self.reply(400, 'text/plain', b'Invalid input')
                    self.reply(202, 'application/json', b'{"accepted":true}')
            server = http.server.ThreadingHTTPServer(('127.0.0.1', cfg['port']), Handler)
            server.daemon_threads = True; server.timeout = .1
        thread.start(); worker_started = True; announced = False
        emit('started', pid=os.getpid(), session=session_id)
        while not stop.is_set():
            if server: server.handle_request()
            else: stop.wait(.1)
            with state_lock: ready = state['ready']
            if server and ready and not announced:
                announced = True; emit('ready', url=f'http://127.0.0.1:{cfg["port"]}', session=session_id)
    finally:
        stop.set()
        if server: server.server_close()
        # Only Popen objects created by this runner are signalled, never a
        # process name, unrelated port owner or the WSL distribution.
        evidence['owned_guest_processes_reaped'] = cleanup()
        if worker_started: thread.join(timeout=2)
        evidence['worker_stopped'] = not worker_started or not thread.is_alive()
        evidence['elapsed_seconds'] = time.monotonic() - started
        evidence['owned_guest_pids'] = [p.pid for p in owned]
        evidence['guest_exit_codes'] = [p.returncode for p in owned]
        save(out / 'evidence.json', evidence)
        emit('stopped', owned_guest_processes_reaped=evidence['owned_guest_processes_reaped'])
    return 1 if evidence.get('error') or not evidence['owned_guest_processes_reaped'] or not evidence['worker_stopped'] else 0


if __name__ == '__main__':
    try: raise SystemExit(main())
    except Exception as error:
        emit('error', message=str(error)[:4000]); raise SystemExit(2)
