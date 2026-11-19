"""Host observer for the virtual camera's guest-owned subsystem lifecycle."""
import json
from pathlib import Path
import shutil
import time
from .core import ASSETS, write_json
from .memory_lab import MemoryDesktopSession
from .session import Session


class SubsystemSession(MemoryDesktopSession):
    guest_subsystem = True
    def __init__(self,device,runtime,desktop,profile):
        self.guest_state='camera'
        self.request_in_flight=None
        self.transition_deadline=0
        self.events=[]
        super().__init__(device,runtime,desktop,profile)

    @property
    def frames(self):
        # Always the same guest compositor, socket and FrameStream instance.
        return self._camera_frames

    @frames.setter
    def frames(self,value):
        self._camera_frames=value

    def prepare_extra_payload(self):
        super().prepare_extra_payload()
        for key,name in [('compositor','mini_compositor'),('init','subsystem-init')]:
            source=Path(self.desktop[key]).resolve(strict=True)
            shutil.copy2(source,self.device/'payload-stage'/name)
        (self.device/'payload-stage/camera/subsystem_manager.sh').write_bytes(
            (ASSETS/'guest/subsystem_manager.sh').read_bytes().replace(b'\r\n',b'\n'))
        (self.device/'payload-stage/camera/subsystem_audit.sh').write_bytes(
            (ASSETS/'guest/subsystem_audit.sh').read_bytes().replace(b'\r\n',b'\n'))

    def build_command(self,*ports):
        args=super().build_command(*ports)
        # RFB is entirely inside the camera guest's loopback network. The host
        # only uses the original virtual screen/input channels.
        index=args.index('-netdev')
        del args[index:index+4]
        args+=['-nic','none']
        index=args.index('-drive')
        firmware=[]
        for name,key in [('firmwarevendor','firmware_vendor'),('firmwaresystem','firmware_system')]:
            path=Path(self.desktop[key]).resolve(strict=True)
            if ',' in str(path): raise ValueError('Comma in firmware image path')
            firmware+=['-drive',f'if=none,id={name},file={path},format=raw,readonly=on',
                       '-device',f'virtio-blk-device,drive={name}']
        args[index:index]=firmware
        write_json(self.device/'subsystem-command.json',args)
        return args

    def touch(self,operation,x,y):
        Session.touch(self,operation,x,y)

    def key(self,keysym,down):
        if self.desktop_active:
            Session.touch(self,'keysym',keysym,int(down))

    def on_guest_line(self,line):
        super().on_guest_line(line)
        marker='QE_SUBSYSTEM_EVENT '
        if marker not in line: return
        event=json.loads(line.split(marker,1)[1])
        self.events.append(event)
        self.guest_state=event['state']
        if event['event']=='starting':
            self.desktop_started=True
            self.desktop_phase.emit('starting')
        elif event['event']=='desktop-active':
            self.request_in_flight=None
            self.desktop_active=self.desktop_guest_ready=True
            self.desktop_phase.emit('desktop')
            self.message.emit('Camera guest started its Linux subsystem; one kernel and one display / 机内子系统已启动，共用内核与屏幕')
        elif event['event']=='camera-restored':
            self.request_in_flight=None
            self.desktop_active=self.desktop_started=self.desktop_guest_ready=self.desktop_requested=False
            self.desktop_phase.emit('camera')
            self.message.emit('Camera guest reaped the subsystem and restarted its GUI / 机内已回收子系统并重启相机 GUI')

    def session_step(self):
        if self.request_in_flight and time.monotonic()>self.transition_deadline:
            raise TimeoutError('Guest subsystem transition timed out')
        if self.request_in_flight: return
        action=None
        if self.desktop_requested and self.guest_state=='camera': action='start'
        elif not self.desktop_requested and self.guest_state=='desktop': action='stop'
        if action:
            self.request_in_flight=action
            self.transition_deadline=time.monotonic()+180
            self.commands.put(f"test -p /dev/qe-subsystem/control && printf '{action}\\n' > /dev/qe-subsystem/control")
