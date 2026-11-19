"""Bounded real-guest GUI roundtrip check; only virtual guest input is used."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import shutil
from pathlib import Path
import struct
import sys
import time
import uuid

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
from PySide6.QtCore import QCoreApplication, QTimer
from PySide6.QtGui import QImage
from devkit.core import data_home, read_json, write_json
from devkit.desktop_session import DesktopSession, discover_desktop


def save_frame(frame, path):
    header, pixels = frame
    _, width, height, stride, _, _ = struct.unpack_from('<6I', header)
    image = QImage(pixels, width, height, stride, QImage.Format_RGB32).copy()
    if not image.save(str(path)):
        raise RuntimeError('Could not save guest frame')
    return hashlib.sha256(pixels).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--seconds', type=int, default=330)
    parser.add_argument('--device', type=Path)
    parser.add_argument('--memory-profile', type=Path, help='Evidence-derived CPU/RAM profile, using a separate --device')
    parser.add_argument('--headroom-helper', type=Path, help='ARM64 static helper to match captured MemAvailable with synthetic resident load')
    args = parser.parse_args()
    app = QCoreApplication([])
    home = data_home()
    device = (args.device or home/'library/9789d6a9843d3880').resolve()
    desktop = discover_desktop(home)
    if not desktop:
        raise RuntimeError('Prepare .local/xfce-arm64/rootfs.ext4 first')
    if args.headroom_helper:
        if not args.memory_profile: raise ValueError('--headroom-helper requires --memory-profile')
        desktop['headroom_helper'] = str(args.headroom_helper.resolve(strict=True))
    out = PROJECT/'.local/xfce-arm64/runs'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    out.mkdir(parents=True, exist_ok=False)
    if args.memory_profile:
        if not args.device or device.resolve() == (home/'library/9789d6a9843d3880').resolve():
            raise ValueError('Memory tests require an isolated --device workspace')
        from devkit.memory_lab import MemoryDesktopSession
        session = MemoryDesktopSession(device, read_json(home/'runtime.json'), desktop, read_json(args.memory_profile))
    else:
        session = DesktopSession(device, read_json(home/'runtime.json'), desktop)
    result = {'same_kernel_userland_switch':True, 'original_camera_kernel':False,
              'physical_camera_access':False, 'stages':[], 'failures':[]}
    input_token = 'qe_' + uuid.uuid4().hex[:16]
    result['guest_input_verified'] = False
    session.console.connect(lambda line: result.update(guest_input_verified=True) if line.strip()==input_token else None)
    session.message.connect(lambda text: print(text, flush=True))
    def failed(text):
        if result['failures']: return
        result['failures'].append(text)
        print('FAILED:',text,flush=True)
        session.stop()
    session.failed.connect(failed)
    state, due = ['waiting'], [time.monotonic()+args.seconds]
    def headroom_ready(line):
        if args.headroom_helper and line.rstrip().endswith('QE_HEADROOM_READY'):
            result['synthetic_headroom_ready'] = True
            session.probe('camera')
            state[0], due[0] = 'camera', time.monotonic()+5
    session.console.connect(headroom_ready)
    def on_phase(phase):
        if phase == 'running':
            if args.headroom_helper:
                state[0] = 'matching-headroom'
                session.match_headroom()
                return
            if args.memory_profile: session.probe('camera')
            state[0], due[0] = 'camera', time.monotonic()+5
    def on_desktop(phase):
        print('DESKTOP_PHASE:',phase,flush=True)
        if phase == 'desktop':
            if args.memory_profile: session.probe('desktop')
            state[0], due[0] = 'desktop', time.monotonic()+15
            session.commands.put('chroot /data/local/tmp/qe-xfce-root /usr/bin/xwininfo -display :1 -root -tree')
            session.commands.put('cat /data/local/tmp/qe-xfce-root/tmp/qe-xfce.log /data/local/tmp/qe-xfce-root/tmp/qe-vnc.log')
        elif phase == 'camera':
            if args.memory_profile: session.probe('returned')
            state[0], due[0] = 'returned-touch', time.monotonic()+3
    session.phase.connect(on_phase)
    session.desktop_phase.connect(on_desktop)
    start = time.monotonic()
    def tick():
        if result['failures']: return
        if time.monotonic()-start > args.seconds:
            failed('Overall guest GUI timeout')
            return
        if not session.ready or time.monotonic() < due[0]: return
        stage = state[0]
        if stage == 'returned-touch':
            session.touch('down',150,160); session.touch('up',150,160)
            state[0], due[0] = 'returned', time.monotonic()+3
            return
        if stage == 'input-start':
            session.touch('down',200,220); session.touch('up',200,220)
            for letter in input_token:
                session.key(ord(letter),True); session.key(ord(letter),False)
            session.key(0xff0d,True); session.key(0xff0d,False)
            state[0], due[0] = 'input-read', time.monotonic()+3
            return
        if stage == 'input-read':
            session.commands.put('cat /data/local/tmp/qe-xfce-root/tmp/qe-input-result')
            state[0], due[0] = 'input', time.monotonic()+8
            return
        if stage == 'input' and not result['guest_input_verified']:
            failed('Virtual mouse / keyboard did not reach the XFCE terminal')
            return
        if stage not in ('camera','desktop','input','returned'): return
        frame = session.frames.latest if session.frames else None
        if not frame: return
        digest = save_frame(frame, out/f'{stage}.png')
        if stage == 'returned' and digest == result['stages'][0]['pixels_sha256']:
            failed('Original GUI did not redraw after returning and receiving touch')
            return
        if stage == 'desktop':
            pixels = frame[1]
            visible = sum(1 for i in range(0,len(pixels),400) if any(pixels[i:i+3]))
            result['desktop_nonblack_samples'] = visible
            result['desktop_frame_sequence'] = session.desktop_frames.sequence
            if visible < 100:
                failed('XFCE framebuffer contains no desktop content; X server readiness is insufficient')
                return
        result['stages'].append({'stage':stage,'pixels_sha256':digest,'qemu_pid':session.process.pid,
                                 'elapsed_seconds':round(time.monotonic()-start,2)})
        print('CAPTURE:',stage,out/f'{stage}.png',flush=True)
        if stage == 'camera':
            state[0] = 'starting-desktop'; session.switch_desktop()
        elif stage == 'desktop':
            session.commands.put("chroot /data/local/tmp/qe-xfce-root /usr/bin/env -i HOME=/root DISPLAY=:1 PATH=/usr/bin:/bin /usr/bin/xterm -title QE_InputCheck -geometry 60x12+120+160 -e /bin/sh -c 'read value; echo \"$value\" > /tmp/qe-input-result; sleep 120' &")
            state[0], due[0] = 'input-start', time.monotonic()+15
        elif stage == 'input':
            state[0] = 'returning'; session.switch_camera()
        else:
            state[0] = 'done'; session.stop()
    timer=QTimer();timer.timeout.connect(tick);timer.start(200)
    session.finished.connect(app.quit)
    session.start()
    app.exec()
    session.wait(15000)
    for name in ('console.log', 'qemu.log'):
        if (device/name).is_file(): shutil.copy2(device/name,out/name)
    result['xfce_image'] = str(Path(desktop['image']).resolve())
    with Path(desktop['image']).open('rb') as source:
        result['xfce_image_sha256'] = hashlib.file_digest(source,'sha256').hexdigest()
    result['passed'] = (not result['failures'] and result['guest_input_verified'] and [r['stage'] for r in result['stages']]==['camera','desktop','input','returned']
                        and len({r['qemu_pid'] for r in result['stages']})==1)
    if args.memory_profile:
        from devkit.memory_lab import verify_probes
        layout = read_json(device/'memory-layout.json')
        if layout:
            write_json(out/'memory-layout.json', layout)
            result['memory'] = verify_probes((out/'console.log').read_text(encoding='utf-8', errors='replace'), layout)
        else:
            result['memory'] = {'passed':False, 'errors':['No generated memory layout']}
        if args.headroom_helper:
            snapshot = result['memory'].get('snapshots',{}).get('camera',{})
            try:
                pressure = json.loads(snapshot['fields']['synthetic_headroom'])
                result['synthetic_headroom'] = pressure
                matched = abs(pressure['after_available_kib']-pressure['target_available_kib']) < 16*1024
                if not result.get('synthetic_headroom_ready') or not matched:
                    raise ValueError('Synthetic headroom did not match within 16 MiB')
            except (KeyError, ValueError, TypeError) as exc:
                result['memory']['passed'] = False
                result['memory']['errors'].append(str(exc))
        result['passed'] = result['passed'] and result['memory']['passed']
        for name in ('memory-command.json', 'memory-profile.json', 'memory-layout.dtb'):
            if (device/name).exists(): shutil.copy2(device/name, out/name)
    write_json(out/'result.json',result)
    print(out/'result.json')
    print('PASS' if result['passed'] else 'FAIL')
    return 0 if result['passed'] else 1


if __name__=='__main__':
    raise SystemExit(main())
