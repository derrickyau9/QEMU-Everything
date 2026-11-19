"""One QEMU SDK kernel, original GUI and isolated ARM64 XFCE userspace.

This is a display/userland switch, not a kexec or original camera-kernel boot.
All NIC traffic is restricted to an ephemeral loopback-only desktop forward.
"""
from pathlib import Path
import queue
import shutil
import socket
import time
from PySide6.QtCore import Signal
from .core import ASSETS, read_json
from .models import model_id
from .rfb import RfbFrames
from .session import Session


def discover_desktop(home):
    configured = read_json(Path(home)/'xfce-runtime.json')
    image = Path(configured.get('image', ASSETS.parent/'.local/xfce-arm64/rootfs.ext4')).resolve()
    if image.is_file() and image.suffix == '.ext4' and image.stat().st_size <= 8*1024**3:
        with image.open('rb') as file:
            file.seek(1080)
            if file.read(2) == b'\x53\xef':
                return {'image': str(image)}
    return None


class DesktopSession(Session):
    desktop_phase = Signal(str)

    def __init__(self, device, runtime, desktop):
        self._camera_frames = None
        self.desktop_frames = None
        self.desktop_active = self.desktop_requested = False
        self.desktop_started = self.desktop_guest_ready = False
        self.desktop_input = queue.Queue(maxsize=1024)
        self.connect_after = self.connect_deadline = 0
        self.desktop = desktop
        with socket.socket() as reservation:
            reservation.bind(('127.0.0.1', 0))
            self.rfb_port = reservation.getsockname()[1]
        super().__init__(device, runtime, mode='live')

    @property
    def frames(self):
        return self.desktop_frames if self.desktop_active else self._camera_frames

    @frames.setter
    def frames(self, value):
        self._camera_frames = value

    def prepare_extra_payload(self):
        if model_id(read_json(self.device/'device.json')) != 'x2dii':
            raise ValueError('The XFCE userspace lab currently requires X2D II ARM64')
        source = Path(self.desktop['image']).resolve()
        if not source.is_file() or source.suffix != '.ext4':
            raise ValueError('Prepared XFCE ext4 image is missing')
        shutil.copy2(ASSETS/'guest/xfce_start.sh', self.device/'payload-stage/camera/xfce_start.sh')

    def build_command(self, *ports):
        args = super().build_command(*ports)
        index = args.index('-nic')
        if args[index+1] != 'none': raise ValueError('Desktop lab must start without external networking')
        args[index:index+2] = ['-netdev', f'user,id=xfce,restrict=on,hostfwd=tcp:127.0.0.1:{self.rfb_port}-10.0.2.15:5900',
                              '-device', 'virtio-net-device,netdev=xfce']
        # SDK virtio disks enumerate in reverse attachment order. Add first so
        # existing system/payload/frame disk numbers stay intact; XFCE is vdg.
        index = args.index('-drive')
        args[index:index] = ['-drive', f'if=none,id=xfceroot,file={Path(self.desktop["image"]).resolve()},format=raw,snapshot=on',
                            '-device', 'virtio-blk-device,drive=xfceroot']
        return args

    def switch_desktop(self):
        self.desktop_requested = True

    def switch_camera(self):
        self.desktop_requested = False

    def touch(self, operation, x, y):
        if self.desktop_active:
            try:
                event = ('key', 0xffbe+x-59, bool(y)) if operation=='key' and 59<=x<=63 else ('pointer',operation,x,y)
                self.desktop_input.put_nowait(event)
            except queue.Full: pass
        else:
            super().touch(operation, x, y)

    def key(self, keysym, down):
        if self.desktop_active:
            try: self.desktop_input.put_nowait(('key', keysym, down))
            except queue.Full: pass

    def on_guest_line(self, line):
        if line.rstrip().endswith('QE_XFCE_USERSPACE_READY'):
            self.desktop_guest_ready = True
        if 'QE_XFCE_FAILED' in line:
            raise RuntimeError(f'XFCE guest setup failed: {line}')

    def session_step(self):
        if self.desktop_requested and not self.desktop_started:
            self.desktop_started = True
            self.connect_deadline = time.monotonic()+150
            self.commands.put('sh /mnt/x2dii/camera/xfce_start.sh &')
            self.desktop_phase.emit('starting')
            self.message.emit('Starting isolated ARM64 XFCE userspace / 启动独立 ARM64 XFCE 用户态…')
        if self.desktop_started and self.desktop_frames is None and time.monotonic() >= self.connect_after:
            if time.monotonic() > self.connect_deadline:
                raise TimeoutError('XFCE display did not become ready; see guest console.log')
            self.connect_after = time.monotonic()+1
            try: self.desktop_frames = RfbFrames(self.rfb_port)
            except (OSError, EOFError): pass
        if self.desktop_frames and self.desktop_frames.error:
            raise RuntimeError(self.desktop_frames.error)
        if (self.desktop_requested and not self.desktop_active and time.monotonic() > self.connect_deadline
                and (not self.desktop_guest_ready or not self.desktop_frames or not self.desktop_frames.latest
                     or not self.has_desktop_content(self.desktop_frames.latest[1]))):
            raise TimeoutError('XFCE did not draw a desktop; see guest console.log')
        if (self.desktop_requested and not self.desktop_active and self.desktop_frames
                and self.desktop_frames.latest and self.desktop_guest_ready
                and self.has_desktop_content(self.desktop_frames.latest[1])):
            self.commands.put('kill -STOP $(pidof camera-gui-software)')
            self.desktop_active = True
            self.desktop_phase.emit('desktop')
            self.message.emit('XFCE ready; same SDK kernel, camera GUI paused / XFCE 已就绪；共享 SDK 内核，原厂 GUI 已暂停')
        if not self.desktop_requested and self.desktop_active:
            self.desktop_frames.release_all()
            self.desktop_active = False
            self.commands.put('kill -CONT $(pidof camera-gui-software)')
            self.desktop_phase.emit('camera')
            self.message.emit('Returned to original GUI / 已返回原厂 GUI')
        while not self.desktop_input.empty():
            event = self.desktop_input.get_nowait()
            if self.desktop_frames and self.desktop_active:
                if event[0] == 'pointer': self.desktop_frames.pointer(*event[1:])
                else: self.desktop_frames.key(*event[1:])

    @staticmethod
    def has_desktop_content(pixels):
        # Reject an X server's initial black frame with its lone cursor.
        return sum(1 for i in range(0, len(pixels), 400) if any(pixels[i:i+3])) >= 100

    def run(self):
        try:
            super().run()
        finally:
            if self.desktop_frames: self.desktop_frames.close()
