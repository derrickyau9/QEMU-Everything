"""Attach the workbench to the externally owned Instax system-QEMU lab.

The controller obtains pixels with QMP screendump and sends pointer events to
the emulated ST1633I. This adapter only transports those files to native Qt.
Closing this display never terminates the independently owned guest.
"""
from pathlib import Path
from types import SimpleNamespace
import json
import queue
import re
import struct
import threading
import time

from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QImage


class NativeInstaxSession(QThread):
    native_instax = True
    message = Signal(str)
    console = Signal(str)
    phase = Signal(str)
    failed = Signal(str)
    status_changed = Signal()

    def __init__(self, run_directory):
        super().__init__()
        self.run_directory = Path(run_directory)
        self.frames = SimpleNamespace(latest=None)
        self.commands, self.touches = queue.Queue(), queue.Queue()
        self.ready = False
        self.state = {}
        self.milestones = set()
        self.cancelled = threading.Event()
        self.frame_signature = None
        self.boot_offset = 0
        self.boot_tail = ''
        self.started_at = None
        self.last_point = (0, 0)
        self.pressed = False
        self.pending_touch = None
        self.press_sent_at = None
        self.release_sent_at = float('-inf')

    def stop(self):
        self.cancelled.set()

    def touch(self, operation, x, y):
        if self.ready and operation in ('down', 'move', 'up'):
            self.touches.put((operation, max(0, min(479, int(x))),
                             max(0, min(853, int(y)))))

    def _append(self, name, text):
        with (self.run_directory / name).open('a', encoding='utf-8', newline='\n') as stream:
            stream.write(text)

    def _flush_touches(self, now=None):
        """Keep quick mouse edges visible across the guest's input polling.

        The controller can read multiple file lines in one iteration, and the
        hardware model scans queued reports only 5 ms apart. A short click can
        thus disappear before the original UI polls its contact state. Preserve
        FIFO order with at least 80 ms down and 30 ms between clicks; never
        shorten a user's longer hold or block frame/console processing.
        """
        clock = time.monotonic if now is None else lambda: now
        while True:
            observed_at = clock()
            if self.pending_touch is None:
                try:
                    self.pending_touch = self.touches.get_nowait()
                except queue.Empty:
                    return .05
            operation, x, y = self.pending_touch
            if not self.ready or (operation in ('move', 'up') and not self.pressed):
                self.pending_touch = None
                continue
            due = float('-inf')
            if operation == 'up':
                due = self.press_sent_at + .080
            elif operation == 'down' and not self.pressed:
                due = self.release_sent_at + .030
            if observed_at + 1e-9 < due:
                return min(.05, due - observed_at)
            pressed = operation != 'up'
            self._append('native-input.txt', f'touch {x} {y} {int(pressed)}\n')
            if pressed and not self.pressed:
                self.press_sent_at = clock()
            elif not pressed:
                self.release_sent_at = clock()
            self.pressed = pressed
            self.last_point = (x, y)
            self.pending_touch = None

    def _read_state(self):
        try:
            state = json.loads((self.run_directory / 'native-live-status.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            state = {}
        fresh = 0 <= time.time() - state.get('updated_at', 0) < 5
        self.ready = bool(state.get('alive') and fresh)
        if state.get('started_at') != self.started_at:
            self.started_at = state.get('started_at')
            self.boot_offset = 0
            self.boot_tail = ''
            self.milestones.clear()
            self.frame_signature = None
            self.pending_touch = None
            self.pressed = False
            self.press_sent_at = None
            self.release_sent_at = float('-inf')
            while not self.touches.empty():
                self.touches.get_nowait()
        self.state = state

    def _read_frame(self):
        frame = self.run_directory / 'native-screen.ppm'
        try:
            stat = frame.stat()
            if not self.state.get('frame_sequence') or stat.st_mtime < (self.started_at or time.time()):
                return
            signature = (stat.st_mtime_ns, stat.st_size)
            if signature == self.frame_signature:
                return
            image = QImage.fromData(frame.read_bytes(), 'PPM')
            if image.isNull() or (image.width(), image.height()) != (480, 854):
                return
            image = image.convertToFormat(QImage.Format_RGB32)
            header = struct.pack('<6I', 0, image.width(), image.height(),
                                 image.bytesPerLine(), 0, self.state['frame_sequence'])
            self.frames.latest = (header, bytes(image.constBits()))
            self.frame_signature = signature
        except OSError:
            pass

    def _read_log(self):
        try:
            path = self.run_directory / 'boot-modules.log'
            if path.stat().st_size < self.boot_offset:
                self.boot_offset = 0
                self.milestones.clear()
            with path.open('rb') as stream:
                stream.seek(self.boot_offset)
                chunk = stream.read(65536)
                self.boot_offset = stream.tell()
            if not chunk:
                return
            text = re.sub(r'\x1b\[[0-9;]*m', '', chunk.decode('utf-8', errors='replace'))
            evidence = self.boot_tail + text
            self.boot_tail = evidence[-2048:]
            for key, marker in {'kernel': 'Linux version', 'rootfs': 'INSTAX_ROOTFS_HANDOFF_READY',
                                'panic': 'Kernel panic', 'fault': 'INSTAX_FAULT',
                                'oops': 'Internal error: Oops'}.items():
                if marker in evidence:
                    self.milestones.add(key)
            if re.search(r'LAB_HANDOFF ENTER[^\n]*symbol=host_main_proc_msg', evidence):
                self.milestones.add('messages')
            if re.search(r'HOST_OSD_EVENT_(?:LANGUAGE_[A-Z_]+|DATE[A-Z_]*|TIME[A-Z_]*)\b', evidence):
                self.milestones.add('ui_input')
            self.console.emit(text.rstrip())
        except OSError:
            pass

    def description(self, chinese=False):
        if not self.ready:
            return '原厂客体连接已过期或未运行；保留最后画面' if chinese else 'Guest offline or connection stale; last frame retained'
        if self.milestones.intersection({'panic', 'fault', 'oops'}):
            return '原厂启动日志有异常；完整启动未验证' if chinese else 'Guest log reports a fault; complete boot unverified'
        if 'ui_input' in self.milestones:
            return '已观察到原厂 UI 输入响应；完整相机功能未验证' if chinese else 'Original UI input observed; full camera functions unverified'
        if 'messages' in self.milestones:
            return '原厂消息循环已进入；交互与完整功能仍待验证' if chinese else 'Original message loop entered; interaction and full function unverified'
        return '原厂 Linux 客体运行中；完整启动未验证' if chinese else 'Original Linux guest running; complete boot unverified'

    def run(self):
        self.phase.emit('starting')
        self.message.emit('Attached native Qt display to the existing Instax system guest. Closing this window only disconnects the display.')
        previous_ready = None
        last_status = 0
        try:
            while not self.cancelled.is_set():
                self._read_state()
                self._read_frame()
                self._read_log()
                if self.ready != previous_ready:
                    previous_ready = self.ready
                    self.phase.emit('running' if self.ready else 'starting')
                input_wait = self._flush_touches()
                while not self.commands.empty():
                    command = self.commands.get_nowait()
                    if self.ready:
                        self._append('native-console-input.txt', command.rstrip('\r\n') + '\n')
                if time.monotonic() - last_status >= .5:
                    self.status_changed.emit()
                    last_status = time.monotonic()
                self.cancelled.wait(input_wait)
        except (OSError, ValueError) as error:
            self.failed.emit(str(error))
        finally:
            if self.pressed and self.ready:
                try:
                    self._append('native-input.txt', f'touch {self.last_point[0]} {self.last_point[1]} 0\n')
                except OSError:
                    pass
            self.ready = False
            self.phase.emit('stopped')
