# SPDX-License-Identifier: GPL-2.0-or-later
"""Loopback-only viewer of completed original guest frames."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import uuid

PAGE='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>Ricoh GR IV original pages</title>
<style>
body{background:#151515;color:#eee;font:16px system-ui;margin:24px}
main{max-width:920px}h1{font-size:24px;margin:0 0 12px}h2{font-size:17px;margin:22px 0 8px}
p{color:#bbb;line-height:1.45}img{background:#000;width:min(720px,100%);border:1px solid #555;display:block}
.controls{display:flex;flex-wrap:wrap;gap:8px;margin:12px 0}.controls button{font-size:16px;padding:10px 14px;min-height:46px}
button{cursor:pointer}button:disabled{cursor:not-allowed;opacity:.5}#status{color:#d8d8d8;margin:10px 0}
.unavailable{font-size:14px;color:#aaa}
</style><main>
<h1>Ricoh GR IV · 原厂界面</h1>
<p>画面由 GR IV 1.11 原固件绘制。预览与空回放通过组件入口打开；预览页可用 DISP 切换显示信息、ADJ. 打开快捷设置；菜单内可使用方向键、OK 和返回。</p>
<div class="controls"><button data-key="preview_entry">打开原厂预览</button><button data-key="playback_entry">打开原厂空回放</button></div>
<img id="frame" alt="Original guest frame">
<h2>机身菜单按键</h2>
<div class="controls">
<button data-key="up">Macro / ↑</button><button data-key="down">WB / ↓</button>
<button data-key="left">ISO / ←</button><button data-key="right">Drive / →</button>
<button data-key="confirm">OK</button><button data-key="menu">MENU</button><button data-key="display">DISP</button><button data-key="back">返回</button>
</div>
<h2>拨盘与其他菜单操作</h2>
<div class="controls">
<button data-key="native_front_plus">前拨盘 ＋</button><button data-key="native_front_minus">前拨盘 −</button>
<button data-key="native_rear_plus">后拨盘 ＋</button><button data-key="native_rear_minus">后拨盘 −</button>
<button data-key="native_adj">ADJ. 快捷设置</button><button data-key="native_fn">Fn / Delete</button>
<button disabled title="原厂事件尚无有效曝光响应">EV ＋</button><button disabled title="原厂事件尚无有效曝光响应">EV −</button>
</div>
<h2>拍摄状态与模式</h2>
<div class="controls" aria-label="需要完整相机状态的按键">
<button data-key="native_half_press">快门半按</button><button data-key="native_half_release">松开快门</button>
<button disabled title="需要拍摄模式状态">Movie / 存储切换</button>

<button disabled title="需要成像链路">快门全按</button>
<button data-key="mode_0">模式 P</button><button data-key="mode_1">模式 Av</button><button data-key="mode_2">模式 Tv</button><button data-key="mode_3">模式 M</button>
<button disabled title="需要冷启动链路">电源</button>
<button disabled title="模式拨盘机械锁">锁定键</button>
</div>
<p class="unavailable">模式与半按运行原厂事件；半按显示变化不代表硬件对焦。灰色键的原厂动作尚未验证。Fn 在 ADJ. 内可打开详细参数。</p>
<p id="status">Ready</p>
<p class="unavailable">键盘：方向键、Enter 确认、Esc/Backspace 返回、D 切换预览显示、A 打开 ADJ.；快捷设置中左右切换分页、上下选择、OK 确认或返回。前后拨盘使用原厂拨盘事件；预览中 ISO 键后可用前拨盘调整。预览没有传感器影像，回放显示原厂 No image 页；回放菜单返回由组件入口辅助完成。</p>
</main><script>
let busy=false,currentPage=null;const buttons=[...document.querySelectorAll('button[data-key]')];
function unavailable(key){
if(['preview_entry','playback_entry'].includes(key))return false;
if(key==='native_fn')return !['0x2000000','0x2000064','0x2000065'].includes(currentPage);
if(['display','adj','native_adj','native_half_press','native_half_release'].includes(key)||key.startsWith('mode_'))return currentPage!=='0x8000000';
if(currentPage==='0x400000f')return key!=='menu';
if(currentPage==='0x8000000')return !['up','down','left','right','menu','native_front_plus','native_front_minus','native_rear_plus','native_rear_minus'].includes(key);
return !currentPage;
}
function updateControls(){buttons.forEach(x=>x.disabled=busy||unavailable(x.dataset.key))}
async function state(){try{const r=await fetch('/status');const j=await r.json();currentPage=j.native_current_id;updateControls()}catch(e){document.querySelector('#status').textContent=e.message}}state();
function refresh(){document.querySelector('#frame').src='/frame.png?t='+Date.now()}refresh();
async function input(key){if(busy||unavailable(key))return;busy=true;updateControls();document.querySelector('#status').textContent='Processing original event…';try{const r=await fetch('/input',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({key})});const j=await r.json();if(!r.ok)throw Error(j.error||'Input failed');currentPage=j.native_current_id;refresh();document.querySelector('#status').textContent='Native event '+j.sequence+' completed'}catch(e){document.querySelector('#status').textContent=e.message}finally{busy=false;updateControls()}}
buttons.forEach(x=>x.onclick=()=>input(x.dataset.key));
const keyboard={ArrowUp:'up',ArrowDown:'down',ArrowLeft:'left',ArrowRight:'right',Enter:'confirm',Escape:'back',Backspace:'back',m:'menu',d:'display',a:'native_adj',f:'native_fn'};
addEventListener('keydown',e=>{const key=keyboard[e.key];if(key){e.preventDefault();input(key)}});
</script></html>'''.encode('utf-8')


class Viewer:
    def __init__(self,control,port,stop_event=None):
        self.control=control
        self.session=uuid.uuid4().hex
        self.stop_event=stop_event or threading.Event()
        page=PAGE.replace(b"'Content-Type':'application/json'",b"'Content-Type':'application/json','X-Session':'"+self.session.encode()+b"'")
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
                               'scope':'Original LiveView HUD, menu and empty PlaybackError on assisted software display',
                               'native_adj_logic_verified':True,'physical_adj_dispatch_verified':False,
                               'native_disp_logic_verified':True,'physical_disp_dispatch_verified':False,
                               'assisted_preview_timer_refresh':True,'assisted_component_entries':True,'assisted_empty_playback_return':True,
                               'native_current_id':control.last.get('native_current_id') if control.last else None,
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
                    if not isinstance(value,dict) or set(value)!={'key'} or value['key'] not in ('up','down','left','right','confirm','back','menu','display','adj','preview_entry','playback_entry','native_front_plus','native_front_minus','native_rear_plus','native_rear_minus','native_adj','native_fn','native_half_press','native_half_release','mode_0','mode_1','mode_2','mode_3'):raise ValueError('Unsupported original menu input')
                    frame,event=control.key(value['key']);self.reply(200,json.dumps(event).encode())
                except (ValueError,OSError,EOFError) as error:self.reply(400,json.dumps({'error':str(error)}).encode())
        self.server=ThreadingHTTPServer(('127.0.0.1',port),Handler);self.server.daemon_threads=True
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
    def close(self):self.server.shutdown();self.server.server_close();self.thread.join(5)
