# SPDX-License-Identifier: GPL-2.0-or-later
"""Loopback-only viewer of completed original guest frames."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import uuid

PAGE=b'''<!doctype html><meta charset="utf-8"><title>Ricoh GR IV original menu</title>
<style>body{background:#151515;color:#eee;font:16px system-ui;margin:30px}h1{font-size:24px}img{width:min(720px,85vw);border:1px solid #555}button{font-size:24px;padding:12px 30px;margin:12px}p{color:#bbb;max-width:900px}</style>
<h1>Ricoh GR IV \xc2\xb7 Original menu / \xe5\x8e\x9f\xe5\x8e\x82\xe8\x8f\x9c\xe5\x8d\x95</h1>
<p>Original TopMenu, fonts and icons in an auxiliary software display. Normal camera power-on, live-view and photography are not available.</p>
<img id="frame" alt="Original guest frame"><br><button data-key="up">\xe2\x86\x91 Up</button><button data-key="down">\xe2\x86\x93 Down</button><span id="status">Ready</span>
<script>let busy=false;const buttons=[...document.querySelectorAll('button')];
function refresh(){document.querySelector('#frame').src='/frame.png?t='+Date.now()}refresh();
async function input(key){if(busy)return;busy=true;buttons.forEach(x=>x.disabled=true);document.querySelector('#status').textContent='Processing original event\xe2\x80\xa6';try{const r=await fetch('/input',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({key})});const j=await r.json();if(!r.ok)throw Error(j.error||'Input failed');refresh();document.querySelector('#status').textContent='Native event '+j.sequence+' completed'}catch(e){document.querySelector('#status').textContent=e.message}finally{busy=false;buttons.forEach(x=>x.disabled=false)}}
buttons.forEach(x=>x.onclick=()=>input(x.dataset.key));addEventListener('keydown',e=>{if(e.key==='ArrowUp'||e.key==='ArrowDown'){e.preventDefault();input(e.key==='ArrowUp'?'up':'down')}})</script>'''


class Viewer:
    def __init__(self,control,port,stop_event=None):
        self.control=control
        self.session=uuid.uuid4().hex
        self.stop_event=stop_event or threading.Event()
        page=PAGE.replace(b'<span id="status">',b'<button data-key="confirm">OK</button><button data-key="back">Cancel / Back</button><span id="status">')
        page=page.replace(b"'Content-Type':'application/json'",b"'Content-Type':'application/json','X-Session':'"+self.session.encode()+b"'")
        parent=self
        class Handler(BaseHTTPRequestHandler):
            def setup(self):
                super().setup();self.connection.settimeout(25)
            def log_message(self,*args):pass
            def reply(self,status,body,content_type='application/json'):
                self.send_response(status);self.send_header('Content-Type',content_type)
                self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(len(body)))
                self.end_headers();self.wfile.write(body)
            def do_GET(self):
                path=self.path.split('?',1)[0]
                if path=='/':return self.reply(200,page,'text/html; charset=utf-8')
                if path=='/frame.png':
                    with control.lock:frame=control.frame
                    return self.reply(200,frame,'image/png') if frame else self.reply(503,b'{"error":"Original frame not ready"}')
                if path=='/status':
                    with control.lock:
                        value={'ready':control.frame is not None,
                               'phase':'running' if control.frame else 'starting',
                               'model':'Ricoh GR IV','firmware':'1.11',
                               'scope':'Original TopMenu and Focus settings auxiliary software-display component',
                               'session':parent.session,
                               'natural_power_on_verified':False,'synthetic_pixels':False,
                               'last_event':control.audit[-1] if control.audit else None}
                    return self.reply(200,json.dumps(value).encode())
                self.reply(404,b'{"error":"Unknown route"}')
            def do_POST(self):
                if self.path not in ('/input','/stop'):return self.reply(404,b'{"error":"Unknown route"}')
                if self.headers.get('X-Session')!=parent.session:return self.reply(403,b'{"error":"Owned session token required"}')
                origin=self.headers.get('Origin')
                if origin and origin!=f'http://127.0.0.1:{port}':return self.reply(403,b'{"error":"Local origin required"}')
                if self.path=='/stop':
                    parent.stop_event.set();return self.reply(200,b'{"stopping":true}')
                try:
                    size=int(self.headers.get('Content-Length','0'))
                    if not 0<size<=128 or self.headers.get('Content-Type')!='application/json':raise ValueError('Bounded JSON input required')
                    value=json.loads(self.rfile.read(size))
                    if not isinstance(value,dict) or set(value)!={'key'} or value['key'] not in ('up','down','confirm','back'):raise ValueError('Unsupported original menu input')
                    frame,event=control.key(value['key']);self.reply(200,json.dumps(event).encode())
                except (ValueError,OSError,EOFError) as error:self.reply(400,json.dumps({'error':str(error)}).encode())
        self.server=ThreadingHTTPServer(('127.0.0.1',port),Handler);self.server.daemon_threads=True
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
    def close(self):self.server.shutdown();self.server.server_close();self.thread.join(5)
