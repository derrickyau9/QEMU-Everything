"""Boot our own 512-byte BIOS test image; no proprietary firmware required."""
from pathlib import Path
import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from devkit.core import data_home, read_json, write_json
from devkit.library import import_firmware
from devkit.boot import BOARDS, build_command
from devkit.runtime import discover_qemu

MARKER = b'QEMU_EVERYTHING_BOOT_OK'

def boot_sector():
    code = bytearray.fromhex('fa31c08ed88ed0bc007cfbfc')
    # 115200 baud, 8N1 COM1; VGA BIOS + UART output exercise PC boot.
    code += bytes.fromhex('bafb03b080eebaf803b001eebaf90330c0eebafb03b003ee')
    code += b'\xbe'; pointer = len(code); code += b'\0\0'
    code += bytes.fromhex('bb0700')
    loop = len(code); code += bytes.fromhex('ac84c074'); finish_jump = len(code); code += b'\0'
    code += bytes.fromhex('50b40ecd10')
    poll = len(code); code += bytes.fromhex('bafd03eca82074'); poll_jump = len(code); code += b'\0'
    code += bytes.fromhex('58baf803eeeb'); loop_jump = len(code); code += b'\0'
    finish = len(code); code += bytes.fromhex('faf4ebfd')
    message = len(code); code += b'QEMU Everything\r\n' + MARKER + b'\r\n\0'
    code[pointer:pointer+2] = (0x7c00 + message).to_bytes(2, 'little')
    for location, target in ((finish_jump, finish), (poll_jump, poll), (loop_jump, loop)):
        code[location] = (target - location - 1) & 255
    return bytes(code).ljust(510, b'\0') + b'\x55\xaa'

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--home', type=Path, default=data_home())
    parser.add_argument('--output', type=Path, default=Path('.local/generic-verification.json'))
    args = parser.parse_args()
    runtime = discover_qemu(args.home)
    if not runtime: raise SystemExit('Set up QEMU first')
    with tempfile.TemporaryDirectory(prefix='qemu-everything-') as folder:
        root = Path(folder); image = root / 'our-boot.img'; image.write_bytes(boot_sector())
        before = hashlib.sha256(image.read_bytes()).hexdigest()
        device = import_firmware(image, root / 'library')
        profile = read_json(device / 'boot.json')
        profile.update(board='x86_64-pc', cpu=BOARDS['x86_64-pc']['default_cpu'], memory_mib=128,
                       disk='source/firmware.bin', disk_format='raw', cmdline='')
        write_json(device / 'boot.json', profile)
        command = build_command(profile, device, runtime['qemu']) + ['-display', 'none']
        console = root / 'serial.log'
        with console.open('wb') as log:
            process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
            try:
                deadline = time.monotonic() + 20
                while time.monotonic() < deadline and process.poll() is None:
                    if MARKER in console.read_bytes(): break
                    time.sleep(.1)
            finally:
                if process.poll() is None:
                    process.terminate()
                    try: process.wait(timeout=5)
                    except subprocess.TimeoutExpired: process.kill(); process.wait()
        serial = console.read_text(errors='replace')
        copied = device / 'source/firmware.bin'
        result = {'ok': MARKER.decode() in serial,
                  'test_image': 'project-authored x86 BIOS boot sector; not a vendor firmware',
                  'qemu': runtime['qemu'], 'command': command, 'serial': serial,
                  'source_unchanged': hashlib.sha256(image.read_bytes()).hexdigest() == before,
                  'library_input_unchanged': hashlib.sha256(copied.read_bytes()).hexdigest() == before}
        from PySide6.QtCore import QCoreApplication, QTimer
        from devkit.generic_session import GenericSession
        from devkit.core import GuestLock
        app = QCoreApplication.instance() or QCoreApplication([])
        session = GenericSession(device, runtime, headless=True)
        errors, phases, chunks = [], [], []
        def received(text):
            chunks.append(text)
            if MARKER.decode() in ''.join(chunks): session.stop()
        session.console.connect(received); session.phase.connect(phases.append)
        session.failed.connect(errors.append); session.finished.connect(app.quit)
        deadline = QTimer(); deadline.setSingleShot(True); deadline.timeout.connect(session.stop); deadline.start(20000)
        session.start(); app.exec(); session.wait(10000); deadline.stop()
        result['qt_session'] = {'marker_received': MARKER.decode() in ''.join(chunks),
            'errors': errors, 'phases': phases, 'process_stopped': session.process.poll() is not None}
        with GuestLock(device): result['qt_session']['lock_released'] = True
        result['ok'] = result['ok'] and result['qt_session']['marker_received'] and not errors and result['qt_session']['process_stopped']
        write_json(args.output, result)
        print(json.dumps(result, indent=2))
        return 0 if result['ok'] and result['source_unchanged'] and result['library_input_unchanged'] else 1

if __name__ == '__main__': raise SystemExit(main())
