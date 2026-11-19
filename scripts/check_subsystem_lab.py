"""Verify guest-owned lifecycle, one display, namespaces, and actual cleanup."""
import argparse
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
import struct
import sys
import time
import uuid

PROJECT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(PROJECT))
from PySide6.QtCore import QCoreApplication,QTimer
from devkit.core import data_home,read_json,write_json
from devkit.subsystem_session import SubsystemSession
from scripts.check_xfce_lab import save_frame


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--device',type=Path,required=True)
    parser.add_argument('--seconds',type=int,default=400)
    args=parser.parse_args()
    device=args.device.resolve()
    config=read_json(device/'subsystem-runtime.json')
    out=PROJECT/'.local/subsystem-arm64/runs'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    out.mkdir(parents=True,exist_ok=False)
    app=QCoreApplication([])
    session=SubsystemSession(device,read_json(data_home()/'runtime.json'),config,read_json(device/'memory-profile.json'))
    result={'physical_camera_access':False,'original_camera_kernel':False,'native_services':'mock',
            'host_rfb_connection':False,'stages':[],'failures':[]}
    token='sub_'+uuid.uuid4().hex[:12]
    result['input_verified']=False
    frame_ids=set()
    audit_complete=[False]
    phase=['waiting'];due=[0];start=time.monotonic()
    def fail(text):
        if not result['failures']: result['failures'].append(text)
        print('FAILURE:',text,flush=True);session.stop()
    def line(text):
        if text.strip()=='QE_SUBSYSTEM_AUDIT_END': audit_complete[0]=True
        if text.strip()==token: result['input_verified']=True
        if 'QE_SUBSYSTEM_FAILED_' in text or 'QE_SUBSYSTEM_UNEXPECTED_EXIT' in text:
            result['failures'].append(text)
        if 'QE_SUBSYSTEM_EVENT ' in text: print(text,flush=True)
    session.console.connect(line)
    session.message.connect(lambda m:print(m,flush=True))
    session.failed.connect(fail)
    def native(value):
        if value=='running': phase[0]='camera';due[0]=time.monotonic()+5
    def desktop(value):
        if value=='desktop': phase[0]='desktop';due[0]=time.monotonic()+18
        elif value=='camera':
            phase[0]='returned-touch';due[0]=time.monotonic()+3
    session.phase.connect(native);session.desktop_phase.connect(desktop)
    def capture(stage):
        digest=save_frame(session.frames.latest,out/f'{stage}.png')
        frame_ids.add(id(session.frames))
        result['stages'].append({'stage':stage,'sha256':digest,'qemu_pid':session.process.pid})
    def tick():
        if time.monotonic()-start>args.seconds: fail('Subsystem test timeout');return
        if not session.ready or time.monotonic()<due[0]: return
        state=phase[0]
        if state=='camera' and session.frames.latest:
            capture('camera');session.switch_desktop();phase[0]='starting'
        elif state=='desktop' and session.frames.latest:
            capture('desktop')
            # Invoke a terminal inside the existing PID/mount namespaces, using
            # the guest control command supplied below. It is started by PID 1's
            # existing shell, not a second VM or another root filesystem.
            session.commands.put("printf 'input-test "+token+"\\n' > /dev/qe-subsystem/test-control")
            phase[0]='type';due[0]=time.monotonic()+10
        elif state=='type':
            session.touch('down',200,220);session.touch('up',200,220)
            for c in token: session.key(ord(c),True);session.key(ord(c),False)
            session.key(0xff0d,True);session.key(0xff0d,False)
            phase[0]='read';due[0]=time.monotonic()+3
        elif state=='read':
            session.commands.put('cat /dev/qe-subsystem/input-result')
            phase[0]='input';due[0]=time.monotonic()+5
        elif state=='input':
            if not result['input_verified']: fail('Input did not traverse the guest-owned display path');return
            capture('input')
            # Return is initiated from the Linux subsystem itself.
            session.commands.put("printf 'return\\n' > /dev/qe-subsystem/test-control")
            phase[0]='returning'
        elif state=='returned-touch':
            session.touch('down',150,160);session.touch('up',150,160)
            phase[0]='returned';due[0]=time.monotonic()+3
        elif state=='returned':
            capture('returned')
            session.commands.put('sh /mnt/x2dii/camera/subsystem_audit.sh')
            phase[0]='audit';due[0]=time.monotonic()
        elif state=='audit':
            if audit_complete[0]:
                phase[0]='done';session.stop()
    timer=QTimer();timer.timeout.connect(tick);timer.start(200)
    session.finished.connect(app.quit);session.start();app.exec();session.wait(15000)
    for name in ['console.log','qemu.log','subsystem-command.json','memory-layout.json']:
        if (device/name).exists(): shutil.copy2(device/name,out/name)
    console=(out/'console.log').read_text(errors='replace')
    result['events']=session.events
    events={x['event']:x for x in session.events}
    needed=['controller-ready','starting','camera-gui-stopped','desktop-active','stopping','child-reaped','camera-restored']
    result['one_kernel']=len({x['boot_id'] for x in session.events})==1
    result['one_display_stream']=len(frame_ids)==1
    result['one_qemu_process']=len({s['qemu_pid'] for s in result['stages']})==1
    result['same_memtotal']=len({x['memtotal_kib'] for x in session.events})==1
    child=re.search(r'QE_SUBSYSTEM_CHILD_EVIDENCE_BEGIN\s*\n(.*?)QE_SUBSYSTEM_CHILD_EVIDENCE_END',console,re.S)
    child_data=dict(re.findall(r'^(\w+)=(.*)$',child[1],re.M)) if child else {}
    result['child_evidence']=child_data
    result['child_uses_parent_kernel']=bool(events) and child_data.get('boot_id')==session.events[0]['boot_id']
    result['distinct_pid_namespace']=bool(events) and child_data.get('pid_namespace')!=session.events[0]['parent_pid_ns'] and 'pid:[' in child_data.get('pid_namespace','')
    result['child_cleanup_verified']='QE_AUDIT_CHILDREN=0' in console and 'QE_AUDIT_ROOT_UNMOUNTED' in console
    result['firmware_readonly_verified']='QE_SUBSYSTEM_READONLY_OK' in console
    result['gui_restarted']=events.get('controller-ready',{}).get('camera_pid',0)>1 and events.get('camera-restored',{}).get('camera_pid',0)>1 and events['controller-ready']['camera_pid']!=events['camera-restored']['camera_pid']
    result['passed']=not result['failures'] and all(name in events for name in needed) and all(result[k] for k in [
        'input_verified','one_kernel','one_qemu_process','one_display_stream','same_memtotal','child_uses_parent_kernel',
        'distinct_pid_namespace','child_cleanup_verified','firmware_readonly_verified','gui_restarted'])
    write_json(out/'result.json',result)
    print(out/'result.json');print('PASS' if result['passed'] else 'FAIL')
    return 0 if result['passed'] else 1

if __name__=='__main__':raise SystemExit(main())
