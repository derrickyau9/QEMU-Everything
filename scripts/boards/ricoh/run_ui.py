#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Finite owned original GR IV menu; JSON config/events and stdin stop."""
import hashlib
import json
import os
from pathlib import Path
import signal
import stat
import sys
import tempfile
import threading
import traceback
sys.path.insert(0,str(Path(__file__).resolve().parent))
from build_ui import PROFILE, ROOT, SOURCES
from rtos_probe import PAYLOAD_SHA256, digest, probe


def emit(event,**fields):print(json.dumps(dict(event=event,**fields)),flush=True)


def regular(path,limit,allow_empty=False):
    path=Path(path).absolute()
    if '..' in path.parts or any(p.is_symlink() for p in (path,*path.parents)):
        raise ValueError('UI input contains a link/traversal')
    meta=path.stat()
    if not stat.S_ISREG(meta.st_mode) or meta.st_nlink!=1 or not (0 if allow_empty else 1)<=meta.st_size<=limit:
        raise ValueError('UI input must be a bounded unlinked regular file')
    return path


def freeze(source,target,expected,limit,executable=False,allow_empty=False):
    source=regular(source,limit,allow_empty)
    descriptor=os.open(source,os.O_RDONLY|os.O_NOFOLLOW)
    with os.fdopen(descriptor,'rb') as stream,target.open('xb') as output:
        before=os.fstat(stream.fileno());value=hashlib.sha256();total=0
        for block in iter(lambda:stream.read(1024**2),b''):
            total+=len(block)
            if total>limit:raise ValueError('Frozen input exceeds bound')
            output.write(block);value.update(block)
        after=os.fstat(stream.fileno())
    if (before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns)!=(after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns) or value.hexdigest()!=expected:
        raise ValueError('UI input changed during snapshot creation')
    if executable:target.chmod(0o700)
    return target


def export_guest(source,destination,record):
    """Copy bounded closed guest artifacts; never sample active DrvFS logs."""
    if record.get('process_reaped') is not True:
        return dict(status='withheld',reason='Owned guest cleanup was not confirmed')
    source=Path(source);destination=Path(destination)
    if source.is_symlink():raise ValueError('Guest evidence contains a link')
    if not source.is_dir():return dict(status='absent')
    if any(p.is_symlink() for p in (destination,*destination.parents)):
        raise ValueError('Guest evidence destination contains a link')
    files=[];total=0;entries=0
    for path in source.rglob('*'):
        entries+=1
        if entries>1024:raise ValueError('Guest evidence exceeds entry bound')
        if path.is_symlink():raise ValueError('Guest evidence contains a link')
        meta=path.stat()
        relative=path.relative_to(source)
        if len(relative.parts)>4:raise ValueError('Guest evidence exceeds directory depth')
        if stat.S_ISDIR(meta.st_mode):continue
        path=regular(path,64*1024**2,True);total+=meta.st_size
        if len(files)>=512 or total>256*1024**2:
            raise ValueError('Guest evidence exceeds export bound')
        files.append((path,relative,digest(path),meta.st_size))
    destination.mkdir(exist_ok=False)
    for path,relative,expected,size in files:
        target=destination/relative;target.parent.mkdir(parents=True,exist_ok=True)
        freeze(path,target,expected,64*1024**2,allow_empty=True)
    return dict(status='exported',files=len(files),total_bytes=total,
                artifacts=[dict(path=str(relative),sha256=expected,size=size)
                           for _,relative,expected,size in files],
                timing='After owned QEMU exit/reap; active workspace is Linux native temporary storage')


def expose_exported_frames(record):
    """Publish portable artifact paths while retaining the actual runtime audit."""
    exported=record.get('guest_evidence_export',{})
    if exported.get('status')!='exported':return
    artifacts={item['path'].replace('\\','/'):item for item in exported['artifacts']}
    frames={}
    for name in ('initial','down','up','confirm','back','canvas'):
        filename='original-canvas.png' if name=='canvas' else 'roundtrip-'+name+'.png'
        if filename not in artifacts:continue
        artifact=artifacts[filename]
        value=record.get('original_canvas_capture') if name=='canvas' else record.get('original_native_ui_roundtrip',{}).get(name)
        if isinstance(value,dict):
            if value.get('png_sha256') and value['png_sha256']!=artifact['sha256']:
                raise ValueError('Exported original frame hash differs from captured frame')
            if 'frame_path' in value:value['runtime_frame_path']=value['frame_path']
            value['frame_path']='guest/'+filename
            value['frame_path_base']='Directory containing evidence.json'
        frames[name]=dict(path='guest/'+filename,png_sha256=artifact['sha256'],size=artifact['size'])
    record['exported_frames']=frames
    record['exported_frames_path_base']='Directory containing evidence.json'


def validate(cfg):
    if not isinstance(cfg,dict) or cfg.get('mode') not in ('probe','interactive'):raise ValueError('Invalid UI configuration')
    seconds=cfg.get('seconds')
    if type(seconds)is not int or not 90<=seconds<=180:raise ValueError('UI startup budget must be90..180 seconds')
    if cfg.get('port')!=8792:raise ValueError('GR IV UI uses loopback port8792')
    duration=cfg.get('session_seconds',900)
    if type(duration)is not int or not 30<=duration<=3600:raise ValueError('UI session must be30..3600 seconds')
    manifest=json.loads(regular(cfg['manifest'],65536).read_text())
    if manifest.get('profile')!=PROFILE:raise ValueError('Requires pinned original GR IV UI manifest')
    for name in SOURCES:
        if digest(regular(ROOT/name,1024**2))!=manifest.get('overlay_sha256',{}).get(name):
            raise ValueError('Ricoh platform source changed; rebuild: '+name)
    source=regular(cfg['payload'],67948280)
    if source.stat().st_size!=67948280 or digest(source)!=PAYLOAD_SHA256:
        raise ValueError('Requires the exact original GR IV1.11 decoded payload')
    out=Path(cfg['destination'])
    if not out.is_absolute() or '..' in out.parts or ',' in str(out) or any(p.is_symlink() for p in (out,*out.parents)):
        raise ValueError('Invalid UI destination')
    return manifest


def main(cfg):
    stop=threading.Event();record={};created=False
    def request_stop(*unused):stop.set()
    signal.signal(signal.SIGTERM,request_stop);signal.signal(signal.SIGINT,request_stop)
    def stdin_owner():
        try:
            pending=b''
            while True:
                block=os.read(sys.stdin.fileno(),129)
                if not block:stop.set();return
                pending+=block
                if len(pending)>128:stop.set();return
                while b'\n' in pending:
                    line,pending=pending.split(b'\n',1)
                    if line.strip()==b'stop':stop.set();return
        except (OSError,ValueError):stop.set()
    try:
        manifest=validate(cfg);out=Path(cfg['destination']);out.mkdir(parents=True,exist_ok=False);created=True
        threading.Thread(target=stdin_owner,daemon=True).start()
        with tempfile.TemporaryDirectory(prefix='ricoh-ui-owned-',dir='/tmp' if os.name=='posix' else None) as temporary:
            stage=Path(temporary);build=dict(manifest['qemu_build'])
            binary=freeze(build['binary'],stage/'qemu-system-arm',build['binary_sha256'],128*1024**2,True)
            build['binary']=str(binary);qemu_manifest=stage/'qemu.json';qemu_manifest.write_text(json.dumps(build))
            payload=freeze(cfg['payload'],stage/'payload.bin',PAYLOAD_SHA256,67948280)
            platform=[]
            for name,limit in (('bridge',4096),('backend',8192)):
                path=freeze(manifest[name],stage/(name+'.bin'),manifest[name+'_sha256'],limit)
                platform.append(path.read_bytes())
            def ready(viewer,control,value):
                emit('ready',url=value['viewer_url'],session=viewer.session,profile=PROFILE,
                     original_ui_verified=True,native_input_verified=True,single_instance_menu_roundtrip_verified=True)
            # WSL DrvFS can raise ENODATA while a second process appends a log
            # and pathlib reads it. Keep all live QEMU/capture files native;
            # export exact artifacts only after the probe confirms child reap.
            guest=stage/'guest'
            try:
                record=probe(payload,qemu_manifest,guest,60,True,True,True,True,True,
                             False,True,True,True,True,True,8792 if cfg['mode']=='interactive' else None,
                             ui_platform=(*platform,manifest['platform_metadata']),stop_event=stop,on_ready=ready,
                             startup_budget=cfg['seconds'],session_seconds=cfg.get('session_seconds',900),finish_after_ui=cfg['mode']=='probe')
            finally:
                record['owned_guest_processes_reaped']=record.get('process_reaped') is True
                try:
                    record['guest_evidence_export']=export_guest(guest,out/'guest',record)
                    expose_exported_frames(record)
                except BaseException as error:
                    record['guest_evidence_export']=dict(status='failed',reason=str(error))
                    raise
        record.update(profile=PROFILE,native_input_verified=record.get('original_native_ui_roundtrip',{}).get('verified') is True,
                      single_instance_menu_roundtrip_verified=record.get('original_native_ui_roundtrip',{}).get('verified') is True,
                      owned_guest_processes_reaped=record.get('process_reaped') is True)
        if record.get('error'):raise RuntimeError(record['error'])
        if not record['owned_guest_processes_reaped']:raise RuntimeError('Owned guest cleanup incomplete')
        if not stop.is_set() and not record.get('original_ui_verified'):raise RuntimeError('Original menu roundtrip was not verified')
        if cfg['mode']=='probe' and record.get('original_ui_verified'):
            emit('result',original_ui_verified=True,native_input_verified=True,single_instance_menu_roundtrip_verified=True)
        return 0
    except BaseException as error:
        record['runner_error']=str(error) or type(error).__name__
        record['runner_error_traceback']=traceback.format_exc()[-8192:]
        emit('error',message=record['runner_error']);return 1
    finally:
        if created:(out/'evidence.json').write_text(json.dumps(record,indent=2)+'\n')
        emit('stopped',owned_guest_processes_reaped=record.get('owned_guest_processes_reaped',False))


if __name__=='__main__':
    if os.name!='posix':raise SystemExit('Run this supervisor in Linux/WSL')
    if len(sys.argv)!=2 or len(sys.argv[1])>16384:raise SystemExit('One bounded JSON configuration required')
    raise SystemExit(main(json.loads(sys.argv[1])))
