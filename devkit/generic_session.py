"""Owned generic QEMU process with native display and a serial console."""
from pathlib import Path
import os
import queue
import subprocess
import threading
import time
from PySide6.QtCore import QThread, Signal
from .core import Task, Cancelled, GuestLock, read_json, write_json
from .boot import build_command

class GenericSession(QThread):
    message = Signal(str)
    console = Signal(str)
    phase = Signal(str)
    failed = Signal(str)

    def __init__(self, device, runtime, *, headless=False):
        super().__init__()
        self.device, self.runtime = Path(device), runtime
        self.headless = headless
        self.task = Task(self.message.emit)
        self.ready = False
        self.frames = None
        self.commands = queue.Queue()
        self.process = None

    def stop(self):
        self.task.cancelled.set()

    def touch(self, *_):
        pass  # Generic displays receive input in QEMU's own window.

    def run(self):
        process = reader = None
        lock = None
        try:
            lock = GuestLock(self.device)
            if lock:
                self.phase.emit('starting')
                command = build_command(read_json(self.device / 'boot.json'), self.device, self.runtime['qemu'])
                if self.headless: command += ['-display', 'none']
                write_json(self.device / 'generic-command.json', command)
                self.message.emit('QEMU native display + serial console / QEMU 独立显示窗口与串口控制台')
                self.message.emit('Starting a process does not verify guest boot / 进程启动不代表固件启动成功')
                creationflags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
                process = self.process = subprocess.Popen(command, cwd=self.device, stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, creationflags=creationflags)
                output = queue.Queue(maxsize=64)
                def read_output():
                    while block := process.stdout.read1(4096):
                        try: output.put_nowait(block)
                        except queue.Full:
                            try: output.get_nowait()
                            except queue.Empty: pass
                            try: output.put_nowait(block)
                            except queue.Full: pass
                reader = threading.Thread(target=read_output, daemon=True); reader.start()
                self.ready = True; self.phase.emit('running')
                with (self.device / 'generic-console.log').open('wb') as log:
                    while not self.task.cancelled.is_set():
                        batch = bytearray()
                        for _ in range(4):
                            try: batch.extend(output.get_nowait())
                            except queue.Empty: break
                        if batch:
                            if log.tell() + len(batch) > 16 * 1024**2: log.seek(0); log.truncate()
                            log.write(batch); log.flush(); self.console.emit(batch.decode(errors='replace'))
                        if process.poll() is not None:
                            reader.join(timeout=1)
                            while not output.empty():
                                block = output.get_nowait(); log.write(block)
                                self.console.emit(block.decode(errors='replace'))
                            if process.returncode:
                                raise RuntimeError(f'QEMU exited with code {process.returncode}; see generic-console.log')
                            break
                        while not self.commands.empty():
                            process.stdin.write((self.commands.get_nowait() + '\n').encode()); process.stdin.flush()
                        time.sleep(.1)
                self.phase.emit('stopping')
        except Cancelled:
            pass
        except Exception as error:
            self.failed.emit(str(error))
        finally:
            self.ready = False
            if process and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill(); process.wait()
            if process:
                if reader: reader.join(timeout=1)
                for stream in (process.stdin, process.stdout):
                    if stream:
                        stream.close()
            if lock: lock.close()
            self.phase.emit('stopped')
