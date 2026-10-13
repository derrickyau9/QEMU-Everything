# SPDX-License-Identifier: AGPL-3.0-or-later
"""Owned offline Z50 runner; frames and menu input use original guest firmware.

Session methods derive from WonderLantern/z30-qemu tools/display_session.py
(GPL-2.0-or-later), pinned 0ba0c3547ccbec09e7c861eaca35db8e82795f15.
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

MAIN_SIZE = 31404090
MAIN_SHA256 = '57e834fac8f921c18c99abe0b1469edbaca71cb1957794bb9f138bd223d30fc1'
REVISION = '0ba0c3547ccbec09e7c861eaca35db8e82795f15'
HASHES = {'tools/display_session.py': 'a6a16f4cf6378dd970fcd6155cac15431fa346d22afbcfd10761df40dd769fdc', 'tools/guest_call.py': 'b7217f8c998819add965b96843d19520acd0efd29eed6a3de04837ed7b32249d', 'tools/rsp.py': '85da838d2e4ab349945b99c5f349e8a51208c97ba7065874b00951aba774e29e', 'tools/run.py': 'b2a13a2e6f5c050b312a2ddc8df2189f0471ccd602c18f17ffe95919dfdeeeca'}
EVENTS = {'up':0x242,'down':0x243,'left':0x247,'right':0x248,'back':0x249,'ok':0x24a}
MENU_FRAME_SHA256 = 'efc9558932d0e195a047e8a3875ebf0388fac5a11907599d31fcf34a7bf9d08f'
ADD_ITEMS_FRAME_SHA256 = '18d061f0dd6af80e1664d826f71fef88353549692d37a243360e84e1d87bc613'
PAGE = '''<!doctype html><html lang="en"><meta charset="utf-8"><title>Nikon Z50 C2.60 menu</title>
<style>body{background:#17191d;color:#eee;font:16px system-ui;max-width:1020px;margin:30px auto}img{width:min(720px,94vw);aspect-ratio:3/2;background:#000}.layout{display:flex;gap:20px;align-items:center;flex-wrap:wrap}.pad{display:grid;grid-template-columns:repeat(3,64px);gap:8px}button{padding:16px;background:#353a43;color:white;border:1px solid #67717f;border-radius:6px}p{color:#abb2bd}</style>
<h1>Nikon Z50 · C2.60 original menu / 原厂菜单</h1><p>GDB assisted startup and input / GDB 辅助启动与输入。Natural power-on, live-view and photography have not been verified.</p><div id="status">Starting…</div><div class="layout"><img id="lcd" alt="Original guest GPU frame"><div class="pad"><span></span><button data-key="up">↑</button><span></span><button data-key="left">←</button><button data-key="ok">OK</button><button data-key="right">→</button><span></span><button data-key="down">↓</button><span></span><button data-key="back" style="grid-column:span 3">Back / 返回</button></div></div>
<script>const token='TOKEN';let ready=false,frame=0;async function key(k){if(!ready)return;ready=false;await fetch('/input',{method:'POST',headers:{'Content-Type':'application/json','X-Z50-Token':token},body:JSON.stringify({key:k})});}document.querySelectorAll('[data-key]').forEach(b=>b.onclick=()=>key(b.dataset.key));document.onkeydown=e=>{const k={ArrowUp:'up',ArrowDown:'down',ArrowLeft:'left',ArrowRight:'right',Enter:'ok',Escape:'back',Backspace:'back'}[e.key];if(k){e.preventDefault();if(!e.repeat)key(k);}};async function poll(){try{const s=await(await fetch('/status')).json();ready=s.ready&&!s.busy&&!s.error;document.getElementById('status').textContent=s.error||s.phase;document.querySelectorAll('button').forEach(b=>b.disabled=!ready);if(s.frame!==frame){frame=s.frame;document.getElementById('lcd').src='/frame.png?v='+frame;}}catch(e){ready=false;}setTimeout(poll,400);}poll();</script></html>'''


def regular(path):
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError('Expected an absolute local lab path')
    for item in (path, *path.parents):
        if item.is_symlink():
            raise ValueError('Symlink in Z50 lab path')
    metadata = path.stat()
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        raise ValueError('Expected a regular non-hardlinked Z50 input')
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


def main():
    if len(sys.argv) != 2 or len(sys.argv[1]) > 16384:
        raise ValueError('Expected one bounded Z50 configuration')
    cfg = json.loads(sys.argv[1])
    project, image, qemu, out = (Path(cfg[key]) for key in ('project', 'main', 'qemu', 'out'))
    if cfg.get('revision') != REVISION or cfg.get('script_hashes') != HASHES:
        raise ValueError('Z50 reviewed upstream pin differs')
    if cfg.get('main_size') != MAIN_SIZE or cfg.get('main_sha256') != MAIN_SHA256:
        raise ValueError('Z50 main profile differs')
    if regular(image).stat().st_size != MAIN_SIZE or digest(image) != MAIN_SHA256:
        raise ValueError('Z50 prepared main changed before launch')
    if qemu != project / '.build/build/qemu-system-arm' or digest(qemu) != cfg['qemu_sha256']:
        raise ValueError('Z50 QEMU binary changed before launch')
    if digest(Path(__file__).absolute()) != cfg['runner_sha256']:
        raise ValueError('Z50 owned runner changed before launch')
    for name, expected in HASHES.items():
        if digest(project / name) != expected:
            raise ValueError('Z50 reviewed upstream Python source changed')
    if not out.is_absolute() or '..' in out.parts or any(p.is_symlink() for p in (out, *out.parents)) or not out.is_dir() or list(out.iterdir()):
        raise ValueError('Z50 output must be a newly owned empty directory')
    # Imports only reviewed modules from the pinned checkout; firmware cannot
    # supply host code, import paths, settings or executable plugins.
    # Compile the reviewed source directly: an unrelated stale/tampered .pyc
    # must not substitute code for a correctly hashed Python source file.
    class ReviewedLoader(importlib.machinery.SourceFileLoader):
        def get_code(self, fullname):
            data = regular(self.path).read_bytes()
            if hashlib.sha256(data).hexdigest() != HASHES['tools/' + fullname + '.py']:
                raise ValueError('Z50 reviewed Python input changed during import')
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
    import display_session
    from rsp import Remote
    from run import command
    stop = threading.Event(); owned = []; process_lock = threading.Lock(); cleanup_lock = threading.Lock()
    state_lock = threading.Lock(); token = secrets.token_hex(24); session_id = secrets.token_hex(16)
    state = dict(session=session_id, phase='booting', ready=False, busy=True, error=None, frame=0)
    latest_png = [b'']; actions = queue.Queue(maxsize=1); evidence = dict(
        source_revision=REVISION, main_sha256=MAIN_SHA256, qemu_sha256=cfg['qemu_sha256'],
        profile='nikon-z50-display', assisted_startup=True, guest_state_modified=True,
        firmware_file_modified=False, natural_power_on_verified=False, camera_access=False,
        original_ui_verified=False, menu_runtime_verified=False,
        live_view_verified=False, photography_verified=False, frames=[])
    started = time.monotonic(); seconds = cfg.get('seconds', 0); owned_spools = []
    if cfg['mode'] not in ('probe', 'interactive') or (cfg['mode'] == 'probe' and not 20 <= seconds <= 120):
        raise ValueError('Invalid Z50 mode or probe bound')

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

    class Session(display_session.Session):
        # Reuse only the reviewed native GDB-call/context-restoration methods.
        # This constructor owns the process and catches connection failure.
        def __init__(self):
            self.out = out; self.graphics = out / 'graphics'; self.graphics.mkdir()
            with socket.socket() as sock:
                sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
            self.args = command(qemu, image, 'assisted', 2, port, True)
            self.args[self.args.index('-M') + 1] = 'nikon-z50-display'
            self.args += ['-icount', 'shift=6,align=off,sleep=off', '-d', 'guest_errors', '-nic', 'none']
            env = {k:v for k,v in os.environ.items() if not k.startswith('Z50_') and k not in ('PYTHONPATH','PYTHONHOME')}
            env.update(Z50_MMIO_QUIET='1', Z50_FRAME_DIR=str(self.graphics))
            self.device_options = {k:v for k,v in env.items() if k.startswith('Z50_')}
            self.log = (out / 'devices.log').open('wb')
            self.remote = None; self.packet = None; self.audit = []
            try:
                with process_lock:
                    if stop.is_set(): raise RuntimeError('Z50 launch cancelled')
                    self.proc = subprocess.Popen(self.args, stdin=subprocess.DEVNULL, stdout=self.log,
                        stderr=self.log, env=env, start_new_session=True)
                    owned.append(self.proc)
                evidence['guest_pid'] = self.proc.pid; evidence['command'] = self.args
                self.remote = Remote(port)
            except BaseException:
                cleanup(); self.log.close(); raise
        def close(self):
            try: self.write_audit()
            finally:
                if self.remote: self.remote.sock.close()
                cleanup(); self.log.close()

    def capture(s, name):
        path = s.graphics / 'gui-latest.ppm'
        if not path.is_file() or path.stat().st_size > 3 * 1024**2:
            raise ValueError('Z50 guest did not produce a bounded original GPU frame')
        data = regular(path).read_bytes()
        if not data.startswith(b'P6\n720 480\n255\n') or len(data) != 1036815:
            raise ValueError('Z50 frame must be exactly 720x480 RGB')
        png = display_session.png_from_ppm(data)
        (out / (name + '.png')).write_bytes(png)
        (out / (name + '.ppm')).write_bytes(data)
        pixels = data.split(b'\n', 3)[3]
        colors = len(set(pixels[i:i+3] for i in range(0, len(pixels), 3)))
        frame = dict(name=name, sha256=hashlib.sha256(data).hexdigest(),
            png_sha256=hashlib.sha256(png).hexdigest(), width=720,height=480,bytes=len(data),colors=colors)
        evidence['frames'].append(frame)
        with state_lock: latest_png[0] = png; state['frame'] += 1
        return frame

    def key(s, name):
        result = s.input(0x1038449a, [EVENTS[name]], seconds=8)
        if not result.get('completed') or not result.get('context_restored'):
            raise RuntimeError('Z50 native input did not restore its original context')
        s.run_for(2)
        return result

    def worker():
        s = None
        try:
            s = Session(); s.run_to(0x100531bc, seconds=15)
            calls = [s.input(0x1012177a, [('scratch',0)], payload=bytes(12), seconds=8),
                     s.input(0x1041178a, [], seconds=8), s.input(0x10411c10, [], seconds=8),
                     s.input(0x10208d02, [0xc1833138,1], seconds=12)]
            if not all(x.get('completed') and x.get('context_restored') for x in calls):
                raise RuntimeError('Z50 native initialization/menu API did not restore its context')
            if not calls[0].get('scratch',{}).get('restored'):
                raise RuntimeError('Z50 native memory-profile scratch was not restored')
            evidence['initialization_calls'] = calls
            evidence['synthetic_memory_profile'] = {'bytes':'00'*12, 'profile':0,
                'method':'native 1012177a registration using temporary restored stack scratch'}
            evidence['renderer'] = 'original guest GPU GUI-surface capture'
            evidence['lcd_scanout_verified'] = False
            s.run_for(3); first = capture(s, '01-my-menu')
            if first['colors'] < 32 or first['sha256'] != MENU_FRAME_SHA256:
                raise RuntimeError('Z50 original initial menu frame differs from the verified C2.60 profile')
            evidence['menu_runtime_verified'] = evidence['original_ui_verified'] = True
            with state_lock: state.update(phase='running', ready=True, busy=False)
            if cfg['mode'] == 'probe':
                evidence['enter_call'] = key(s,'ok'); second = capture(s,'02-add-items')
                evidence['back_call'] = key(s,'back'); third = capture(s,'03-back-my-menu')
                if second['sha256'] != ADD_ITEMS_FRAME_SHA256 or third['sha256'] != first['sha256']:
                    raise RuntimeError('Z50 native input did not enter and return from the original menu')
                evidence['single_instance_menu_roundtrip_verified'] = True
                evidence['original_api_audit'] = 'session.json'
                emit('result',menu_runtime_verified=True,original_ui_verified=True,single_instance_menu_roundtrip_verified=True)
                stop.set(); return
            while not stop.is_set():
                try: name = actions.get(timeout=.1)
                except queue.Empty:
                    s.run_for(.3)
                    path=s.graphics/'gui-latest.ppm'
                    if path.is_file() and path.stat().st_size == 1036815:
                        png=display_session.png_from_ppm(regular(path).read_bytes())
                        with state_lock:
                            if latest_png[0] != png: latest_png[0]=png;state['frame']+=1
                    continue
                key(s,name);capture(s,f'action-{len(evidence["frames"]):04d}-{name}')
                with state_lock: state['busy']=False
        except Exception as error:
            if stop.is_set(): evidence['interrupted_guest_operation']=str(error)[:4000]
            else: evidence['error']=str(error)[:4000]
            with state_lock: state.update(phase='error',ready=False,busy=False,error=str(error)[:4000])
            if not stop.is_set(): emit('error',message=str(error)[:4000])
            stop.set()
        finally:
            if s:
                try: s.close()
                except Exception as error: evidence['close_error']=str(error)[:4000]

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
                    evidence['error'] = 'Z50 run exceeded its time/output bound'
                    emit('error', message=evidence['error']); stop.set()
        except OSError as error:
            evidence['error'] = 'Z50 output supervision failed: ' + str(error)[:2000]
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
                    if self.path != '/input' or self.headers.get('X-Z50-Token') != token:
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
