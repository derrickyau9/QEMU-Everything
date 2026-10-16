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
        if not 0x40000000<=storage<0x7fffffdc or not 0<sequence<=0xffffffff or operation not in tuple(range(1,46)):
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
        operations={'down':1,'up':2,'redraw':3,'confirm':5,'back':6,'left':7,'right':8,'menu':9,'display':10,'preview_entry':11,'playback_entry':12,'menu_entry':13,'adj':14,**{name:index for index,name in enumerate(('native_up','native_down','native_left','native_right','native_ok','native_fn','native_movie','native_play','native_front_plus','native_front_minus','native_rear_plus','native_rear_minus','native_adj','native_ev_plus','native_ev_minus','native_half_press','native_half_release','native_full','native_power','native_menu','native_display','native_mode','native_lock','mode_0','mode_1','mode_2','mode_3','mode_4','mode_5','mode_6','mode_7'),15)}}
        if key not in operations:raise ValueError('Unsupported original menu operation')
        current_id=self.last.get('native_current_id') if self.last else None
        if key in ('up','down','left','right') and current_id=='0x8000000':
            operations[key]={'up':15,'down':16,'left':17,'right':18}[key]
        if key=='native_fn' and current_id not in ('0x2000000','0x2000064','0x2000065'):
            raise ValueError('Fn detail requires original ADJ or White Balance')
        if (key.startswith('mode_') or key in ('native_half_press','native_half_release','native_adj')) and current_id!='0x8000000':
            raise ValueError('This key requires the original preview page')

        if key in ('display','adj') and (not self.last or self.last.get('native_current_id')!='0x8000000'):
            raise ValueError(('DISP' if key=='display' else 'ADJ')+' currently requires the original preview page')
        if key=='menu' and self.last and self.last.get('native_current_id','').startswith('0x200'):
            operations['menu']=6
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
                previous_frame=self.last
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
                        record={'sequence':sequence,'key':key,'native_event':{1:'0xd7',2:'0xd6',7:'0xd4',8:'0xd5',5:'0xe0',9:'0x8f',10:'0xb9',14:'0x8e'}.get(operations[key]),
                                'completed_sequence':fields[6],'status':fields[8],'raw_sha256':frame['raw_sha256'],
                                'png_sha256':frame['png_sha256'],'original_handler':('0x53255db4 original AppMain raw key press/release' if operations[key]>=15 else '0x5325b644 UIOperation ADJ component transition' if key=='adj' else '0x5325b644 UIOperation ChangeDispInfo' if key=='display' else 'original factory + Show (assisted component entry)' if key.endswith('_entry') or (operations[key]==6 and previous_frame and previous_frame.get('controller_return_id')=='0x4000000') else '0x5325b644 UI operation handler' if key=='menu' and previous_frame and previous_frame.get('native_current_id')=='0x8000000' else '0x53182e9c' if operations[key]==6 else 'current controller vtable+0x28'),
                                'native_current_id':frame.get('native_current_id'),
                                'original_transition_settle':'0x535713bc','transition_animations':False,
                                'original_draw':'0x5356dde4','selection_or_pixels_written_by_host':False,
                                'original_display_info':frame.get('original_display_info'),
                                'assisted_ui_timer_callback':'0x5318973c' if frame.get('native_current_id')=='0x8000000' else None}
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
    left,event_left=control.key('left');(control.output/'roundtrip-left.png').write_bytes(control.frame)
    right,event_right=control.key('right');(control.output/'roundtrip-right.png').write_bytes(control.frame)
    right_enter,event_enter=control.key('right');(control.output/'roundtrip-right-enter.png').write_bytes(control.frame)
    right_back,event_right_back=control.key('back');(control.output/'roundtrip-right-back.png').write_bytes(control.frame)
    result['events'] += [event_left,event_right,event_enter,event_right_back]
    result['left_changes_pixels']=left['raw_sha256']!=initial['raw_sha256']
    result['right_restores_initial_pixels']=right['raw_sha256']==initial['raw_sha256']
    result['right_enters_page']=right_enter.get('native_current_id')!=initial.get('native_current_id')
    result['right_back_restores_initial_pixels']=right_back['raw_sha256']==initial['raw_sha256']
    result['verified']=(result['down_changes_pixels'] and result['up_restores_initial_pixels'] and
                        result['confirm_changes_page'] and result['back_restores_initial_pixels'] and
                        result['left_changes_pixels'] and result['right_restores_initial_pixels'] and
                        result['right_enters_page'] and result['right_back_restores_initial_pixels'])
    (control.output/'native-ui-roundtrip.json').write_text(json.dumps(result,indent=2)+'\n')
    return result


def native_pages_roundtrip(control):
    """Verify firmware pages and native menu inputs in one guest instance."""
    frames={};events=[]
    def capture(name,key=None):
        if key is None:frame=control.capture()
        else:
            frame,event=control.key(key);events.append(event)
        (control.output/(name+'.png')).write_bytes(control.frame)
        frames[name]={k:frame.get(k) for k in ('native_current_id','controller_return_id','raw_sha256','png_sha256','nonzero_bytes','distinct_colors','original_display_info')}
        return frame
    preview=capture('native-preview')
    menu=capture('native-preview-menu','menu')
    try:
        control.key('display')
    except ValueError as error:
        display_rejected_outside_preview='requires the original preview page' in str(error)
    else:display_rejected_outside_preview=False
    try:
        control.key('adj')
    except ValueError as error:
        adj_rejected_outside_preview='requires the original preview page' in str(error)
    else:adj_rejected_outside_preview=False
    menu_test=roundtrip(control)
    returned=capture('native-preview-return','back')
    playback=capture('native-playback-empty','playback_entry')
    playback_menu=capture('native-playback-menu','menu')
    down=capture('native-playback-menu-down','down')
    up=capture('native-playback-menu-up','up')
    empty_return=capture('native-playback-return','back')
    final=capture('native-preview-final','preview_entry')
    display_frames=[]
    for index in range(1,5):
        display_frames.append(capture('native-display-'+str(index),'display'))
    (control.output/'display-experiment.json').write_text(json.dumps(display_frames,indent=2)+'\n')
    adj=capture('native-adj','adj')
    adj_right=capture('native-adj-right','right')
    adj_left=capture('native-adj-left','left')
    adj_down=capture('native-adj-down','down')
    adj_up=capture('native-adj-up','up')
    adj_tabs=[]
    for index in range(1,6):
        adj_tabs.append(capture('native-adj-tab-'+str(index),'right'))
    adj_return=capture('native-adj-return','back')
    capture('native-adj-reopen','adj')
    adj_confirm=capture('native-adj-confirm','confirm')
    checks={
        'adj_rejected_outside_preview':adj_rejected_outside_preview,
        'adj_enters_native_component':adj.get('native_current_id')=='0x2000000' and adj.get('distinct_colors',0)>50,
        'adj_right_changes_pixels':adj_right['raw_sha256']!=adj['raw_sha256'],
        'adj_left_restores_pixels':adj_left['raw_sha256']==adj['raw_sha256'],
        'adj_down_changes_pixels':adj_down['raw_sha256']!=adj['raw_sha256'],
        'adj_up_restores_pixels':adj_up['raw_sha256']==adj['raw_sha256'],
        'adj_five_tabs_change_pixels':len({frame['raw_sha256'] for frame in adj_tabs})==5,
        'adj_tabs_cycle_restores_pixels':adj_tabs[-1]['raw_sha256']==adj['raw_sha256'],
        'adj_confirm_restores_preview':adj_confirm['raw_sha256']==final['raw_sha256'],
        'adj_back_restores_preview':adj_return['raw_sha256']==final['raw_sha256'],
        'display_rejected_outside_preview':display_rejected_outside_preview,
        'display_changes_native_mode':[frame.get('original_display_info') for frame in display_frames]==[1,2,3,0],
        'display_first_changes_pixels':display_frames[0]['raw_sha256']!=final['raw_sha256'],
        'display_second_changes_pixels':display_frames[1]['raw_sha256']!=display_frames[0]['raw_sha256'],
        'display_cycle_restores_pixels':display_frames[-1]['raw_sha256']==final['raw_sha256'],
        'preview_is_original_liveview':preview.get('native_current_id')=='0x8000000' and preview.get('distinct_colors',0)>100,
        'preview_menu_enters':menu.get('native_current_id')=='0x2000060',
        'preview_back_restores_pixels':returned['raw_sha256']==preview['raw_sha256'],
        'empty_playback_is_original':playback.get('native_current_id')=='0x400000f' and playback.get('distinct_colors',0)>50,
        'playback_menu_enters':playback_menu.get('native_current_id')=='0x2000063',
        'playback_menu_down_changes_pixels':down['raw_sha256']!=playback_menu['raw_sha256'],
        'playback_menu_up_restores_pixels':up['raw_sha256']==playback_menu['raw_sha256'],
        'assisted_empty_return_restores_pixels':empty_return['raw_sha256']==playback['raw_sha256'],
        'preview_reentry_restores_pixels':final['raw_sha256']==preview['raw_sha256'],
        'original_menu_roundtrip':menu_test['verified'],
    }
    result={'verified':all(checks.values()),'checks':checks,'frames':frames,'events':events,
            'original_gui_code_modified':False,'synthetic_pixels':False,
            'scope':'Original LiveView HUD with ChangeDispInfo cycling, AdjModeController, TopMenu/Focus and PlaybackError No image on an assisted software display',
            'assisted_navigation':['preview_entry and playback_entry use original factory/Show; no physical key dispatch claim',
                                   'Empty playback menu Back uses original empty-component factory/Show because the normal alias requires the unavailable image-loading task'],
            'display_full_physical_dispatch_verified':False,'adj_full_physical_dispatch_verified':False,
            'adj_list_completion':'original 0x535713bc on native current list at +0x1f8',
            'preview_timer_refresh':'Assisted call to original OnAppTimerHandler with original timer ID; original Update computes all visibility and pixels',
            'natural_power_on_verified':False,'sensor_preview_verified':False,'photo_playback_verified':False}
    experiments=[]
    def trial(key,label=None):
        start=control.last
        frame,event=control.key(key)
        (control.output/('probe-'+(label or key)+'.png')).write_bytes(control.frame)
        experiments.append({'key':key,'label':label,'before':start,'after':frame,'event':event})
        (control.output/'raw-input-experiment.json').write_text(json.dumps(experiments,indent=2)+'\n')
        return start,frame
    def check(name,value):checks[name]=bool(value)
    start,wb=trial('down')
    check('wb_opens_native_page',wb['native_current_id']=='0x2000064')
    _,wbd=trial('down','wb-down');_,wbu=trial('up','wb-up')
    check('wb_down_up',wb['raw_sha256']!=wbd['raw_sha256'] and wb['raw_sha256']==wbu['raw_sha256'])
    _,wb_detail=trial('native_fn','wb-detail')
    check('wb_fn_opens_detail',wb_detail['native_current_id']!=wb['native_current_id'])
    control.key('back')
    control.key('back')
    start,drive=trial('right')
    check('drive_opens_native_page',drive['native_current_id']=='0x2000011')
    _,dd=trial('down','drive-down');_,du=trial('up','drive-up')
    check('drive_down_up',drive['raw_sha256']!=dd['raw_sha256'] and drive['raw_sha256']==du['raw_sha256'])
    control.key('back')
    start,iso=trial('left')
    _,isop=trial('native_front_plus','iso-plus');_,isom=trial('native_front_minus','iso-minus')
    check('iso_dial_both_directions_change',iso['raw_sha256']!=isop['raw_sha256'] and isop['raw_sha256']!=isom['raw_sha256'])
    control.key('preview_entry')
    start,adj=trial('native_adj')
    check('raw_adj_opens',adj['native_current_id']=='0x2000000')
    _,detail=trial('native_fn')
    check('fn_opens_original_detail',detail['native_current_id']=='0x2000017')
    _,plus=trial('native_rear_plus','detail-plus');_,minus=trial('native_rear_minus','detail-minus')
    check('detail_rear_dial_roundtrip',detail['raw_sha256']!=plus['raw_sha256'] and detail['raw_sha256']==minus['raw_sha256'])
    control.key('back');control.key('back')
    modes=[]
    for key in ('mode_2','mode_1','mode_3','mode_0'):
        _,frame=trial(key);modes.append(frame)
    check('mode_four_distinct_frames',len({f['raw_sha256'] for f in modes})==4)
    check('mode_keeps_original_preview',all(f['native_current_id']=='0x8000000' for f in modes))
    trial('native_half_press');trial('native_half_release')
    control.key('preview_entry')
    _,menu=trial('native_menu')
    check('raw_menu_opens',menu['native_current_id'].startswith('0x200006'))
    _,fplus=trial('native_front_plus','menu-front-plus');_,fminus=trial('native_front_minus','menu-front-minus')
    check('menu_front_dial_roundtrip',menu['raw_sha256']!=fplus['raw_sha256'] and menu['raw_sha256']==fminus['raw_sha256'])
    control.key('back')
    result['verified']=all(checks.values())
    result['raw_input_experiments']=experiments
    (control.output/'native-pages-roundtrip.json').write_text(json.dumps(result,indent=2)+'\n')
    return menu_test,result
