#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Owned original HERO8 Tara UI runner; one JSON config, stdin stop, JSON events."""
import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
# The host launches with Python -I. Only this reviewed script directory is
# added explicitly; cwd/user site/environment are not module search inputs.
sys.path.insert(0,str(Path(__file__).resolve().parent))
from component import (ARCHIVE_SHA256, HEIGHT, MENU_SHA256, NAMES, PROFILE, ROOT,
                       RTOS_SHA256, RTOS_SIZE, SHOOT_SHA256, SOURCES, WIDTH,
                       digest, freeze, patched_snapshot, regular, rgba_png)


def emit(event, **fields):
    print(json.dumps(dict(event=event, **fields)),flush=True)


def validate(cfg):
    manifest = json.loads(regular(cfg['manifest'],65536).read_text())
    if (manifest.get('profile') != PROFILE or manifest.get('qemu_version') != '10.2.4'
            or manifest.get('upstream_archive_sha256') != ARCHIVE_SHA256
            or manifest.get('machine') != 'gopro-hero8-reset-lab'):
        raise ValueError('Requires pinned HERO8 Tara UI QEMU manifest')
    for name in SOURCES:
        if digest(ROOT/name) != manifest['overlay_sha256'].get(name):
            raise ValueError('GoPro platform source changed; rebuild: '+name)
    for field in ('binary','platform'):
        if digest(manifest[field]) != manifest[field+'_sha256']:
            raise ValueError('GoPro build artifact changed: '+field)
    source_board = Path(manifest['source_directory'])/'hw/arm/gopro-hero8-reset-lab.c'
    if digest(source_board) != manifest['overlay_sha256']['gopro-hero8-reset-lab.c']:
        raise ValueError('Compiled board source changed')
    if regular(cfg['rtos']).stat().st_size != RTOS_SIZE or digest(cfg['rtos']) != RTOS_SHA256:
        raise ValueError('Requires original HERO8 HD8.01.02.51.00 RTOS')
    if cfg.get('mode') not in ('probe','interactive'):
        raise ValueError('Unknown GoPro UI mode')
    seconds = cfg.get('seconds')
    if isinstance(seconds,bool) or not isinstance(seconds,(int,float)) or not 60 <= seconds <= 180:
        raise ValueError('Startup/probe budget must be 60..180 seconds')
    if cfg.get('port') != 8794:
        raise ValueError('GoPro UI uses loopback port 8794')
    duration = cfg.get('session_seconds',900)
    if type(duration) is not int or not 30 <= duration <= 3600:
        raise ValueError('Finite interactive session must be 30..3600 seconds')
    out = Path(cfg['destination'])
    if not out.is_absolute() or '..' in out.parts or any(p.is_symlink() for p in (out,*out.parents)):
        raise ValueError('Invalid output path')
    if ',' in str(out): raise ValueError('Output path cannot contain QEMU option delimiter')
    return manifest


class QMP:
    def __init__(self,path):
        self.socket = self.stream = None
        try:
            self.socket=socket.socket(socket.AF_UNIX);self.socket.settimeout(5);self.socket.connect(str(path))
            self.stream=self.socket.makefile('rb')
            line=self.stream.readline(65537)
            if not line or len(line)>65536:raise ValueError('Invalid QMP greeting')
            json.loads(line);self.call('qmp_capabilities')
        except BaseException:
            self.close();raise
    def call(self,name,args=None):
        self.socket.sendall((json.dumps({'execute':name,'arguments':args or {}})+'\n').encode())
        while True:
            line=self.stream.readline(65537)
            if not line or len(line)>65536:raise RuntimeError('Invalid QMP reply')
            response=json.loads(line)
            if 'error' in response:raise RuntimeError(str(response['error']))
            if 'return' in response:return response['return']
    def close(self):
        if self.stream is not None:self.stream.close();self.stream=None
        if self.socket is not None:self.socket.close();self.socket=None


def remote_command(remote,command):
    data=command.encode('ascii');remote.sendall(b'$'+data+b'#'+f'{sum(data)%256:02x}'.encode())
    def byte():
        value=remote.recv(1)
        if not value:raise EOFError('Owned debugger closed')
        return value[0]
    while True:
        while byte()!=ord('$'):pass
        data=bytearray()
        while True:
            value=byte()
            if value==ord('#'):break
            if len(data)>=65536:raise ValueError('Debugger response limit')
            data.append(value)
        expected=int(bytes((byte(),byte())),16)
        if sum(data)%256!=expected:raise ValueError('Debugger reply checksum')
        remote.sendall(b'+')
        if data.startswith((b'T',b'S')):continue
        return data.decode('ascii')


class Guest:
    def __init__(self,qmp,gdbpath,out,child,stop):
        self.qmp=qmp;self.gdbpath=gdbpath;self.out=out;self.child=child;self.stop=stop
        self.events=[];self.frames=[];self.last_raw=None;self.last_oracle=None
    def memory(self,address,size,label):
        if not 1<=size<=WIDTH*HEIGHT*4:raise ValueError('Memory capture bound')
        path=self.out/(label+'.bin')
        self.qmp.call('pmemsave',{'val':address,'size':size,'filename':str(path)})
        blob=path.read_bytes()
        if len(blob)!=size:raise ValueError('Short guest memory capture')
        path.unlink();return blob
    def oracle(self):
        result=dict(zip(NAMES,struct.unpack('<27I',self.memory(0xa9200000,108,'oracle'))))
        if result['magic']!=0x47504557 or result['exception'] or result['fatal_code'] or result['failed_size']:
            raise RuntimeError('Original component failed: '+json.dumps(result))
        extended=struct.unpack('<13I',self.memory(0xa920006c,52,'platform'))
        result.update(dict(zip(('hit_result mutex_created mutex_wait mutex_unlock mutex_error log_bytes log_calls '
                              'log_truncated display_updates display_error native_bitmap native_pixels native_stride').split(),extended)))
        if result['display_error']:raise RuntimeError('Native software display failed: '+str(result['display_error']))
        if (result['width'],result['height'])!=(WIDTH,HEIGHT):raise ValueError('Original viewport dimensions changed')
        self.last_oracle=result;return result
    def frame(self,label=None):
        self.qmp.call('stop')
        current=self.oracle();raw=self.memory(0xaf000000,WIDTH*HEIGHT*4,'frame')
        value=hashlib.sha256(raw).hexdigest();self.last_raw=raw
        item=dict(rgba_sha256=value,oracle=current)
        if label:
            path=self.out/(label+'.png');png=rgba_png(raw);path.write_bytes(png)
            item.update(path=str(path),png_sha256=hashlib.sha256(png).hexdigest())
            self.frames.append(item)
        return item
    def running(self,seconds):
        self.qmp.call('cont');until=time.monotonic()+seconds
        while time.monotonic()<until:
            if self.stop.is_set():raise RuntimeError('Owner requested stop')
            if self.child.poll() is not None:raise RuntimeError('Owned QEMU exited')
            time.sleep(min(.05,max(0,until-time.monotonic())))
        self.qmp.call('stop')
    def submit(self,op,x,y):
        if type(op)is not int or type(x)is not int or type(y)is not int or op not in (1,2,3,4) or not 0<=x<WIDTH or not 0<=y<HEIGHT:
            raise ValueError('Invalid native pointer operation/coordinates')
        if len(self.events)>=2000:raise ValueError('Finite session input limit reached')
        self.qmp.call('stop');before=self.oracle()
        if before['request']!=before['complete']:raise ValueError('Previous input command pending')
        seq=before['request']+1
        remote=socket.socket(socket.AF_UNIX);remote.settimeout(5)
        try:
            remote.connect(str(self.gdbpath))
            if remote_command(remote,'Qqemu.PhyMemMode:1')!='OK':raise ValueError('Physical debugger mode unavailable')
            args=struct.pack('<2I',x,y)
            if remote_command(remote,f'Ma92000a0,8:{args.hex()}')!='OK':raise ValueError('Pointer argument write failed')
            mailbox=struct.pack('<4I',seq,before['complete'],op,0)
            if remote_command(remote,f'Ma920005c,10:{mailbox.hex()}')!='OK':raise ValueError('Input mailbox write failed')
            if remote_command(remote,'D')!='OK':raise ValueError('Owned debugger detach failed')
        finally:remote.close()
        deadline=time.monotonic()+5
        while True:
            self.running(.05);current=self.oracle()
            if current['complete']==seq:
                if current['event_result']!=1:raise RuntimeError('Original component rejected native event')
                item=self.frame(f'event-{seq:04d}')
                result=dict(sequence=seq,operation=op,x=x,y=y,completed=seq,status=current['event_result'],
                            frame_sha256=item['rgba_sha256'],frame_path=item['path'],
                            host_writes='Input mailbox and coordinates only; no widget/pixel writes')
                self.events.append(result);return result
            if time.monotonic()>deadline:raise TimeoutError('Native input deadline')
    def tap(self,x,y):
        self.submit(3,x,y);self.submit(1,x,y);self.running(.05);self.submit(2,x,y)
    def await_frame(self,wanted,deadline,label):
        for attempt in range(200):
            if time.monotonic()>=deadline:raise TimeoutError('Original UI frame deadline: '+label)
            self.running(.2)
            # Native CoreRoot time + EW timer/signal processing, no UI-state writes.
            self.submit(4,0,0)
            item=self.frame(label+f'-{attempt:03d}')
            if item['rgba_sha256'] in wanted:return item
        raise TimeoutError('Original UI frame iteration limit')


def native_roundtrip(guest,deadline,cycles):
    initial=guest.await_frame((SHOOT_SHA256,),deadline,'shooting')
    # The original auxiliary tool toggles its tutorial/help controls. Preserve
    # its genuine callback rather than manipulating visibility/widget fields.
    guest.tap(528,265);guest.running(.6)
    guest.await_frame((SHOOT_SHA256,),deadline,'tool-toggled')
    verified=[]
    help_sha='1bd0afd224979d8eb83ebe51dc0f456981485c1ac0b895173d711b9d60d1b357'
    for cycle in range(cycles):
        guest.tap(240,250)
        opened=guest.await_frame((MENU_SHA256,help_sha),deadline,f'cycle-{cycle}-open')
        if opened['rgba_sha256']==help_sha:
            guest.running(.5);guest.tap(240,250)
            opened=guest.await_frame((MENU_SHA256,),deadline,f'cycle-{cycle}-menu')
        guest.tap(54,43)
        returned=guest.await_frame((SHOOT_SHA256,),deadline,f'cycle-{cycle}-back')
        verified.append(dict(menu=opened,returned=returned,original_back_coordinates=[54,43],
                             same_guest_pid=guest.child.pid,verified=True))
    return dict(initial=initial,cycles=verified,original_ui_verified=True,native_touch_verified=True,
                single_instance_menu_roundtrip_verified=True)


def main(cfg):
    child=qmp=guest=None;created=False;stop=threading.Event();errors=[]
    out=Path(cfg.get('destination',''))
    record=dict(profile=PROFILE,firmware_sha256=RTOS_SHA256,assisted_startup=True,
                full_rtos_boot_verified=False,natural_power_on_verified=False,camera_access=False,
                firmware_file_modified=False,guest_ram_modified=True,synthetic_pixels=False,
                original_ui_verified=False,native_touch_verified=False,
                single_instance_menu_roundtrip_verified=False,owned_guest_processes_reaped=False,
                scope='Original HERO8 Tara ApplicationTav shooting/preset UI and native Back; sensor/capture/physical LCD unverified',
                interventions=['Original Cortex-A7 code, original MMU table 0x8000, original data/BSS initialization',
                    'Auxiliary original RootDialogsTav::ApplicationTav entry; CPU1/2 park and proprietary RTOS tasks bypassed',
                    'Reviewed 20 eight-byte veneers in owned RAM image: real bounded heap/calloc, deferred free, software recursive mutex, clock, logging, original HERO8 configuration and software display',
                    'Original HERO8 model setter A0BCE408(A5293118) and original settings initializer A0984AE8',
                    'Empty NV backend fails naturally; original firmware chooses its own ROM defaults',
                    'Original driver/battery registries initialize empty and missing hardware errors remain',
                    'Original debug flag mask4 disabled; error mask1 preserved',
                    'Original CoreRoot time/pointer handlers and EW timers/signals/raster/EndUpdate; software mailbox polling',
                    'Original EW garbage collector A11D2C68 after EndUpdate; original root locked, new temporary original Canvas each update as in Tara',
                    '64 MiB deferred-free heap: finite experiment, not a long-running reusable allocator'])
    try:
        manifest=validate(cfg);out.mkdir(parents=True,exist_ok=False);created=True
        startup_deadline=time.monotonic()+cfg['seconds']
        def watch():
            for line in sys.stdin:
                if line.strip()=='stop':break
            stop.set()
        threading.Thread(target=watch,daemon=True).start()
        with tempfile.TemporaryDirectory(prefix='qe-gopro-ui-') as folder:
            temporary=Path(folder)
            binary=freeze(manifest['binary'],temporary/'qemu-system-arm',manifest['binary_sha256']);binary.chmod(0o700)
            platform=freeze(manifest['platform'],temporary/'component.bin',manifest['platform_sha256'],bound=65536)
            original=freeze(cfg['rtos'],temporary/'original-rtos.bin',RTOS_SHA256,RTOS_SIZE)
            snapshot=temporary/'owned-component-rtos.bin'
            patches=patched_snapshot(original,snapshot,manifest['platform_symbols'])
            park=temporary/'park.bin';park.write_bytes(struct.pack('<II',0xe320f001,0xeafffffd))
            qmp_path=temporary/'qmp.sock';gdb_path=temporary/'gdb.sock'
            command=[str(binary),'-M','gopro-hero8-reset-lab','-cpu','cortex-a7','-smp','3','-m','2048',
                     '-display','none','-nic','none','-monitor','none','-serial','none','-S',
                     '-qmp',f'unix:{qmp_path},server=on,wait=off','-gdb',f'unix:{gdb_path},server=on,wait=off',
                     '-d','int,guest_errors','-D',str(out/'qemu.log'),'-device',
                     f'loader,file={snapshot},addr=0xa0000000,force-raw=on','-device',
                     f'loader,file={platform},addr=0xa9000000,cpu-num=0,force-raw=on','-device',
                     f'loader,file={park},addr=0xa9300000,cpu-num=1,force-raw=on','-device','loader,addr=0xa9300000,cpu-num=2']
            record.update(command=command,ram_hooks=patches,overlay_sha256=manifest['overlay_sha256'],
                          qemu_sha256=manifest['binary_sha256'],bridge_sha256=manifest['platform_sha256'],
                          owned_snapshot_sha256=digest(snapshot))
            with (out/'stdout.log').open('wb') as stdout,(out/'stderr.log').open('wb') as stderr:
                child=subprocess.Popen(command,stdout=stdout,stderr=stderr,start_new_session=True)
                record['qemu_pid']=child.pid
                while not qmp_path.exists():
                    if stop.is_set():raise RuntimeError('Owner requested stop before QMP')
                    if child.poll() is not None:raise RuntimeError('QEMU exited before QMP')
                    if time.monotonic()>startup_deadline:raise TimeoutError('QMP startup deadline')
                    time.sleep(.02)
                qmp=QMP(qmp_path);guest=Guest(qmp,gdb_path,out,child,stop)
                # CPU initialization may not yet have written the oracle.
                guest.running(.6)
                record.update(native_roundtrip(guest,startup_deadline,3 if cfg['mode']=='probe' else 1))
                record['events']=guest.events;record['frames']=guest.frames
                record['oracle']=guest.last_oracle
                if cfg['mode']=='probe':
                    emit('result',**{key:record[key] for key in ('original_ui_verified','native_touch_verified','single_instance_menu_roundtrip_verified')})
                if cfg['mode']=='interactive':
                    from component_viewer import serve
                    serve(guest,out,cfg['port'],cfg.get('session_seconds',900),stop,record)
                record['native_log']=guest.memory(0xa9201000,65536,'native-log').split(b'\0',1)[0].decode('utf8','replace')
                record['final_oracle']=guest.oracle()
                if not stop.is_set():qmp.call('quit');child.wait(timeout=3)
    except Exception as exc:
        errors.append(str(exc));emit('error',message=str(exc))
        if guest is not None and child is not None and child.poll() is None:
            try:
                qmp.call('stop')
                record['final_oracle']=dict(zip(NAMES,struct.unpack('<27I',guest.memory(0xa9200000,108,'failure-oracle'))))
                record['fatal_caller']=hex(struct.unpack('<I',guest.memory(0xa92000a8,4,'fatal-caller'))[0])
                record['native_log']=guest.memory(0xa9201000,65536,'failure-log').split(b'\0',1)[0].decode('utf8','replace')
                record['final_registers']=qmp.call('human-monitor-command',{'command-line':'info registers -a'})
                record['events']=guest.events;record['frames']=guest.frames
            except Exception as capture_error:record['diagnostic_error']=str(capture_error)
    finally:
        if qmp is not None:
            try:qmp.close()
            except Exception as exc:errors.append('Owned QMP close: '+str(exc))
        if child is not None and child.poll() is None:
            try:os.killpg(child.pid,signal.SIGTERM)
            except ProcessLookupError:pass
            try:child.wait(timeout=3)
            except subprocess.TimeoutExpired:
                try:os.killpg(child.pid,signal.SIGKILL)
                except ProcessLookupError:pass
                try:child.wait(timeout=3)
                except subprocess.TimeoutExpired:errors.append('Owned QEMU did not exit')
        record.update(errors=errors,owned_guest_processes_reaped=child is None or child.poll() is not None,
                      process_reaped=child is None or child.poll() is not None)
        if created:(out/'evidence.json').write_text(json.dumps(record,indent=2)+'\n')
        emit('stopped',errors=errors,owned_guest_processes_reaped=record['owned_guest_processes_reaped'])
    return 1 if errors or not record['single_instance_menu_roundtrip_verified'] else 0


if __name__=='__main__':
    if len(sys.argv)!=2 or len(sys.argv[1])>16384:raise ValueError('Expected one bounded JSON configuration')
    raise SystemExit(main(json.loads(sys.argv[1])))
