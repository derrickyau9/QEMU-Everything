# SPDX-License-Identifier: GPL-2.0-or-later
"""Owned local RTOS component control; submit commands, never GUI state."""
import json
from pathlib import Path
import socket
import struct
import threading
import time


class QMP:
    def __init__(self,path):
        self.socket=socket.socket(socket.AF_UNIX);self.socket.settimeout(5)
        self.stream=None
        try:
            self.socket.connect(str(path));self.stream=self.socket.makefile('rwb',buffering=0)
            greeting=json.loads(self.stream.readline())
            if 'QMP' not in greeting:raise ValueError('Missing owned QMP greeting')
            self.request({'execute':'qmp_capabilities'})
        except BaseException:
            self.close();raise
    def request(self,value):
        self.stream.write((json.dumps(value)+'\n').encode())
        while True:
            result=json.loads(self.stream.readline())
            if 'error' in result:raise ValueError('Owned QMP operation failed')
            if 'return' in result:return result['return']
    def save(self,address,size,target):
        self.request({'execute':'human-monitor-command','arguments':{'command-line':
                     f'pmemsave {address:#x} {size} '+json.dumps(str(target))}})
        if not target.is_file() or target.stat().st_size!=size:raise ValueError('Incomplete owned memory capture')
        return target.read_bytes()
    def close(self):
        if self.stream:self.stream.close()
        self.socket.close()


class MailboxRemote:
    """GDB remote only writes the reviewed 16-byte command mailbox."""
    def __init__(self,path):
        self.socket=socket.socket(socket.AF_UNIX);self.socket.settimeout(5)
        try:
            self.socket.connect(str(path));self.buffer=bytearray()
            if self.command('Qqemu.PhyMemMode:1')!='OK':raise ValueError('Owned GDB physical-memory mode unsupported')
        except BaseException:
            self.close();raise
    def byte(self):
        if not self.buffer:
            self.buffer.extend(self.socket.recv(4096))
            if not self.buffer:raise EOFError('Owned GDB socket closed')
        value=self.buffer[0];del self.buffer[0];return value
    def command(self,value):
        raw=value.encode('ascii');self.socket.sendall(b'$'+raw+b'#'+f'{sum(raw)%256:02x}'.encode())
        while True:
            while self.byte()!=ord('$'):pass
            response=bytearray()
            while True:
                value=self.byte()
                if value==ord('#'):break
                response.append(value)
            expected=int(bytes([self.byte(),self.byte()]),16)
            if sum(response)%256!=expected:raise ValueError('Owned GDB packet checksum mismatch')
            self.socket.sendall(b'+')
            # Monitor pauses may generate asynchronous debugger stop replies.
            if response.startswith((b'T',b'S')):continue
            return response.decode('ascii')
    def resume(self):
        if self.command('Hc-1')!='OK':raise ValueError('Owned GDB all-CPU selection failed')
        self.socket.sendall(b'$c#63')
    def submit(self,storage,sequence,completed,operation):
        if not 0x40000000<=storage<0x7fffffdc or not 0<sequence<=0xffffffff or operation not in (1,2,3,4,5,6):
            raise ValueError('Invalid native UI mailbox operation')
        content=struct.pack('<4I',sequence,completed,operation,0)
        if self.command(f'M{storage+20:x},10:{content.hex()}')!='OK':raise ValueError('Owned GDB mailbox submission failed')
    def close(self):self.socket.close()


class ComponentControl:
    def __init__(self,qmp,gdb,output,stop_event=None):
        self.qmp_path,self.gdb_path,self.output=Path(qmp),Path(gdb),Path(output)
        self.lock=threading.Lock();self.remote=None;self.audit=[];self.frame=None;self.last=None
        self.live=self.output/'live-component';self.live.mkdir()
        self.stop_event=stop_event
    def capture(self):
        from rtos_probe import capture_native_canvas
        connection=QMP(self.qmp_path)
        try:
            connection.request({'execute':'stop'})
            result=capture_native_canvas(connection.stream,self.live)
            if 'frame_path' not in result:raise ValueError('Original Canvas is not ready')
            self.frame=(self.live/'original-canvas.png').read_bytes();self.last=result
            return result
        finally:
            try:connection.request({'execute':'cont'})
            finally:connection.close()
    def key(self,key):
        operations={'down':1,'up':2,'redraw':3,'confirm':5,'back':6}
        if key not in operations:raise ValueError('Unsupported original menu operation')
        with self.lock:
            if len(self.audit)>=256:raise ValueError('Development session input limit reached')
            connection=QMP(self.qmp_path)
            try:
                connection.request({'execute':'stop'})
                datarom=struct.unpack('<I',connection.save(0x5538c510,4,self.live/'datarom-pointer.bin'))[0]
                if not 0x59000000<=datarom<=0x7fffffe4:raise ValueError('Invalid original DataRom object')
                descriptor=connection.save(datarom,28,self.live/'datarom-object.bin')
                storage=struct.unpack_from('<I',descriptor,8)[0]
                if not 0x59000000<=storage<=0x7fffffdc:raise ValueError('Invalid owned icon storage object')
                fields=struct.unpack('<9I',connection.save(storage,36,self.live/'mailbox.bin'))
                if fields[5]!=fields[6]:raise ValueError('Previous original UI event is still pending')
                sequence=fields[5]+1
                if self.remote is None:self.remote=MailboxRemote(self.gdb_path)
                self.remote.submit(storage,sequence,fields[6],operations[key])
                if self.remote.command('D')!='OK':raise ValueError('Detach failed')
                self.remote.close();self.remote=None
                connection.request({'execute':'cont'})
                deadline=time.monotonic()+20
                while True:
                    if self.stop_event is not None and self.stop_event.is_set():raise RuntimeError('Owner requested stop')
                    time.sleep(.025);connection.request({'execute':'stop'})
                    fields=struct.unpack('<9I',connection.save(storage,36,self.live/'mailbox.bin'))
                    if fields[6]==sequence:
                        if fields[8]!=1:raise ValueError('Native UI mailbox rejected the operation')
                        from rtos_probe import capture_native_canvas
                        frame=capture_native_canvas(connection.stream,self.live)
                        if 'frame_path' not in frame:raise ValueError('Native UI event did not produce a valid Canvas')
                        self.frame=(self.live/'original-canvas.png').read_bytes();self.last=frame
                        record={'sequence':sequence,'key':key,'native_event':{'down':'0xd7','up':'0xd6','confirm':'0xe0'}.get(key),
                                'completed_sequence':fields[6],'status':fields[8],'raw_sha256':frame['raw_sha256'],
                                'png_sha256':frame['png_sha256'],'original_handler':'current controller vtable+0x28' if key!='back' else '0x53182e9c',
                                'native_current_id':frame.get('native_current_id'),
                                'original_transition_settle':'0x535713bc','transition_animations':False,
                                'original_draw':'0x5356dde4','selection_or_pixels_written_by_host':False}
                        self.audit.append(record);return frame,record
                    if time.monotonic()>deadline:raise ValueError('Original UI event did not complete within twenty seconds')
                    connection.request({'execute':'cont'})
            finally:
                try:connection.request({'execute':'cont'})
                finally:connection.close()
    def close(self):
        if self.remote:self.remote.close();self.remote=None


def roundtrip(control):
    """Same native controller, Down then Up; require actual frame equality."""
    initial=control.capture();(control.output/'roundtrip-initial.png').write_bytes(control.frame)
    down,event_down=control.key('down');(control.output/'roundtrip-down.png').write_bytes(control.frame)
    up,event_up=control.key('up');(control.output/'roundtrip-up.png').write_bytes(control.frame)
    result={'scope':'Original GR IV TopMenu and Focus setting page on auxiliary software display; native Confirm/Back and rendering, no normal camera power-on claim',
            'original_gui_code_modified':False,'synthetic_pixels':False,'events':[event_down,event_up],
            'initial_raw_sha256':initial['raw_sha256'],'down_raw_sha256':down['raw_sha256'],'up_raw_sha256':up['raw_sha256'],
            'down_changes_pixels':initial['raw_sha256']!=down['raw_sha256'],
            'up_restores_initial_pixels':initial['raw_sha256']==up['raw_sha256']}
    confirm,event_confirm=control.key('confirm');(control.output/'roundtrip-confirm.png').write_bytes(control.frame)
    back,event_back=control.key('back');(control.output/'roundtrip-back.png').write_bytes(control.frame)
    result['confirm']=confirm;result['back']=back
    result['events'] += [event_confirm,event_back]
    result['confirm_changes_page']=confirm.get('native_current_id') != initial.get('native_current_id')
    result['back_restores_initial_pixels']=initial['raw_sha256']==back['raw_sha256']
    result['verified']=result['down_changes_pixels'] and result['up_restores_initial_pixels'] and result['confirm_changes_page'] and result['back_restores_initial_pixels']
    (control.output/'native-ui-roundtrip.json').write_text(json.dumps(result,indent=2)+'\n')
    return result
