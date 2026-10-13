# SPDX-License-Identifier: GPL-2.0-or-later
"""Loopback original framebuffer viewer with bounded native pointer input."""
import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
import secrets
import time
from component import HEIGHT, WIDTH, rgba_png

HTML='''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>GoPro HERO8 · QEMU Everything</title>
<style>body{background:#181818;color:#eee;font:16px system-ui;margin:20px}img{max-width:100%;touch-action:none;border:1px solid #555}p{max-width:950px}button{padding:8px}</style>
<h1>GoPro HERO8 · 原厂界面 / Original UI</h1>
<p>HD8.01.02.51.00 · 原厂 Tara 界面、字体、图标及触摸事件。辅助启动使用软件显示、内存、时钟和互斥锁；拍摄、传感器、物理 LCD 和完整开机未验证。<br>Original Tara UI, fonts, icons and input. Assisted software display, heap, clock and mutex; capture, sensor, physical LCD and full boot are unverified.</p>
<img id="screen" draggable="false" alt="Actual original guest pixels"><p id="status">Connecting…</p><button id="stop">停止会话 / Stop session</button>
<script>const token='TOKEN',img=document.querySelector('#screen'),s=document.querySelector('#status');let chain=Promise.resolve(),held=false,lastMove=0;
function send(op,e){const b=img.getBoundingClientRect(),x=Math.max(0,Math.min(1057,Math.floor((e.clientX-b.left)*1058/b.width))),y=Math.max(0,Math.min(539,Math.floor((e.clientY-b.top)*540/b.height)));chain=chain.then(()=>fetch('/pointer',{method:'POST',headers:{'Content-Type':'application/json','X-GoPro-Token':token},body:JSON.stringify({operation:op,x,y})}).then(async r=>{const t=await r.json();s.textContent=JSON.stringify(t);if(!r.ok)throw Error(t.error)})).catch(e=>s.textContent=e.message);}
img.onpointerdown=e=>{e.preventDefault();held=true;img.setPointerCapture(e.pointerId);send(3,e);send(1,e)};
img.onpointerup=e=>{e.preventDefault();held=false;send(2,e)};img.onpointercancel=e=>{held=false;send(2,e)};
img.onpointermove=e=>{if(held&&performance.now()-lastMove>100){lastMove=performance.now();send(3,e)}};
let refreshing=false;setInterval(()=>{if(refreshing)return;refreshing=true;fetch('/frame.png').then(r=>{if(!r.ok)throw Error('Session ended');return r.blob()}).then(b=>{const u=URL.createObjectURL(b),old=img.src;img.onload=()=>{if(old.startsWith('blob:'))URL.revokeObjectURL(old);refreshing=false};img.src=u}).catch(e=>{s.textContent=e.message;refreshing=false})},250);
document.querySelector('#stop').onclick=()=>fetch('/stop',{method:'POST',headers:{'Content-Type':'application/json','X-GoPro-Token':token},body:'{}'});</script></html>'''


def serve(guest,out,port,seconds,stop,record):
    if port!=8794 or type(seconds)is not int or not 30<=seconds<=3600:
        raise ValueError('Unexpected bounded GoPro viewer settings')
    from run_ui import emit
    session=secrets.token_hex(16);deadline=time.monotonic()+seconds
    started=datetime.datetime.now(datetime.timezone.utc)
    state=dict(url=f'http://127.0.0.1:{port}',session=session,ready=True,busy=False,phase='running',
               profile='gopro-hero8-tara-ui-v1',width=WIDTH,height=HEIGHT,
               supervisor_pid=os.getpid(),qemu_pid=guest.child.pid,
               started_utc=started.isoformat(),expires_utc=(started+datetime.timedelta(seconds=seconds)).isoformat(),
               scope=record['scope'],events=[],original_ui_verified=record['original_ui_verified'],
               native_touch_verified=record['native_touch_verified'],
               single_instance_menu_roundtrip_verified=record['single_instance_menu_roundtrip_verified'])
    def save():
        (out/'live-status.json').write_text(json.dumps(state,indent=2)+'\n')
    def frame():
        item=guest.frame();state.update(frame_sha256=item['rgba_sha256'],oracle=item['oracle'])
        blob=rgba_png(guest.last_raw);(out/'live-frame.png').write_bytes(blob);save()
        guest.qmp.call('cont');return blob
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def reply(self,code,content,kind='application/json'):
            if not isinstance(content,bytes):content=json.dumps(content).encode()
            self.send_response(code);self.send_header('Content-Type',kind)
            self.send_header('Content-Length',str(len(content)));self.send_header('Cache-Control','no-store')
            self.send_header('X-Content-Type-Options','nosniff');self.end_headers();self.wfile.write(content)
        def allowed(self):
            if self.headers.get('Host') not in (f'127.0.0.1:{port}',f'localhost:{port}'):
                raise ValueError('Only this loopback host is accepted')
            origin=self.headers.get('Origin')
            if origin and origin not in (f'http://127.0.0.1:{port}',f'http://localhost:{port}'):
                raise ValueError('Only this loopback origin is accepted')
        def do_GET(self):
            try:
                self.allowed()
                if self.path=='/':self.reply(200,HTML.replace('TOKEN',session).encode(),'text/html; charset=utf-8')
                elif self.path.split('?')[0]=='/frame.png':self.reply(200,frame(),'image/png')
                elif self.path in ('/status','/state'):self.reply(200,state)
                else:self.reply(404,{'error':'Unknown endpoint'})
            except ValueError as exc:self.reply(400,{'error':str(exc)})
            except (BrokenPipeError,ConnectionResetError):pass
            except Exception as exc:
                state['error']=str(exc);stop.set();save();self.reply(500,{'error':str(exc)})
        def do_POST(self):
            try:
                self.allowed()
                if self.headers.get('X-GoPro-Token')!=session:raise ValueError('Session token required')
                if self.headers.get('Content-Type')!='application/json':raise ValueError('JSON command required')
                length=int(self.headers.get('Content-Length','0'))
                if not 0<length<=512:raise ValueError('Command size limit')
                body=json.loads(self.rfile.read(length))
                if self.path=='/stop':stop.set();self.reply(200,{'stopping':True});return
                if self.path!='/pointer':self.reply(404,{'error':'Unknown endpoint'});return
                if not isinstance(body,dict) or set(body)!=set(('operation','x','y')):
                    raise ValueError('Expected native pointer operation/x/y')
                op,x,y=(body[key] for key in ('operation','x','y'))
                if any(type(value)is not int for value in (op,x,y)) or op not in (1,2,3):
                    raise ValueError('Invalid native pointer event')
                result=guest.submit(op,x,y);state['events'].append(result)
                state.update(frame_sha256=result['frame_sha256'],oracle=guest.last_oracle)
                save();guest.qmp.call('cont');self.reply(200,result)
            except (ValueError,KeyError,TypeError) as exc:self.reply(400,{'error':str(exc)})
            except (BrokenPipeError,ConnectionResetError):pass
            except Exception as exc:
                state['error']=str(exc);stop.set();save();self.reply(500,{'error':str(exc)})
    server=HTTPServer(('127.0.0.1',port),Handler);server.timeout=.2
    record['viewer']=state;save();guest.qmp.call('cont')
    emit('ready',url=state['url'],session=session,state=state)
    try:
        while time.monotonic()<deadline and not stop.is_set():
            if guest.child.poll()is not None:raise RuntimeError('Owned QEMU exited during viewer')
            server.handle_request()
    finally:
        server.server_close();guest.qmp.call('stop')
        state.update(ready=False,phase='stopped',closed_utc=datetime.datetime.now(datetime.timezone.utc).isoformat())
        save();record['events']=guest.events;record['frames']=guest.frames
    if state.get('error'):raise RuntimeError(state['error'])
