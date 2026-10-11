# SPDX-License-Identifier: GPL-2.0-or-later
"""Finite GR IV original RTOS reset trace in a separately built QEMU board.

Run inside Linux/WSL. Firmware is user supplied and never modified. This does
not launch a verified original camera UI or the service-core Linux lab.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import socket
import stat
import subprocess
import tempfile
import time

PAYLOAD_SHA256 = 'c4e597c7c9ca1bc181b30e35139ed90a4fd876ddcca14f12be0e00b6702233ae'
RTOS_SHA256 = 'a9a9a51530c35819df47dee1cce9f4f560a17eeed1da258950167d282884fce1'
BOARD_SHA256 = '3ce7e22d5da568f37c789caaf70db8b246047c6212611fd35fc664238fc3bb51'
ARCHIVE_SHA256 = '821b545b92f165e57dddccac5077d76d4d436a226595b8813ad59306bbfd0746'
MAX_LOG = 4 * 1024**2


def digest(path):
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024**2), b''): result.update(block)
    return result.hexdigest()


def no_links(path):
    path = path.absolute()
    for part in (path, *path.parents):
        if part.is_symlink(): raise ValueError('RTOS probe paths cannot contain symbolic links')
    return path


def regular(path):
    path = no_links(path)
    if not stat.S_ISREG(path.stat().st_mode): raise ValueError('RTOS probe input must be a regular file')
    return path


def probe(payload, manifest, output, seconds=5, bootloader_gic_flag=False):
    if os.name != 'posix': raise ValueError('Run the RTOS probe inside Linux or WSL')
    if isinstance(seconds, bool) or not isinstance(seconds, int) or not 1 <= seconds <= 15:
        raise ValueError('RTOS probe duration must be 1..15 seconds')
    payload, manifest, output = regular(Path(payload)), regular(Path(manifest)), no_links(Path(output))
    build = json.loads(manifest.read_text())
    if (build.get('qemu_version') != '10.2.4' or build.get('machine') != 'ricoh-rtos-lab'
            or build.get('upstream_archive_sha256') != ARCHIVE_SHA256
            or build.get('sources', {}).get('ricoh-rtos-lab.c') != BOARD_SHA256):
        raise ValueError('Build the reviewed separate Ricoh RTOS board first')
    binary = regular(Path(build['binary']))
    if digest(binary) != build.get('binary_sha256'): raise ValueError('RTOS QEMU binary hash mismatch')
    if payload.stat().st_size != 67948280 or digest(payload) != PAYLOAD_SHA256:
        raise ValueError('RTOS probe requires the exact unpacked official GR IV 1.11 payload')
    # Original reset literals copy initialized data from 0x5437b640, length
    # 0x57480. This establishes the needed ROM prefix extent for reset tracing;
    # it does not establish a full standalone RTOS update-partition boundary.
    offset, load = 0x17d10, 0x53000000
    extent = 0x5437b640 + 0x57480 - load
    with payload.open('rb') as stream:
        stream.seek(offset); original = stream.read(extent)
    if len(original) != extent or hashlib.sha256(original).hexdigest() != RTOS_SHA256:
        raise ValueError('Original RTOS reset ROM prefix changed')
    output.mkdir(parents=True, exist_ok=False)
    image = output / 'original-rtos-reset-rom.bin'; image.write_bytes(original)
    record = {'schema_version': 1, 'model': 'Ricoh GR IV', 'firmware': '1.11',
              'build': build, 'source_payload_sha256': PAYLOAD_SHA256,
              'rtos_source_offset': offset, 'rtos_load_address': load,
              'rtos_extent': extent, 'rtos_sha256': RTOS_SHA256,
              'seconds': seconds, 'firmware_modified': False,
              'original_ui_verified': False, 'natural_power_on_verified': False,
              'camera_access': False, 'host_network_exposed': False,
              'interventions': [
                  'Three secure Cortex-A7 cores start at the original vector address; the camera Linux core is absent.',
                  '512 MiB development RAM at 0x40000000; zeroed SRAM at 0x01000000. The original guest publishes its own rendezvous values.',
                  'GIC at 0x1d000000; actual virtual countdown timer 13 at 0x1e0000d0, 6.75 MHz, SPI 99.',
                  'ARM CNTControlBase at 0x1d010000 uses a real 40 MHz elapsed-time counter. Explicit development bootloader configuration starts CNTCR.EN=1, matching the always-on Generic Timer in Ricoh GPL sc2000.dtsi; real-camera bootloader configuration remains unverified.',
                  'USB3_JT_ADJ at 0x1b110298 retains guest-written configuration only; its unknown reset value remains an error on reads before the first write. Official GR IIIx GPL dwc3-sn.c identifies offset 0x18 in the USB3 glue register block.',
                  'No camera IPCU replies, display, storage, sensor, USB transfers or network peripherals are implemented.',
                  'Original ROM prefix loaded at 0x53000000; its original MMU table, initialized data and vector code are unchanged.',
                  'QMP reads CPU registers only. No device-register reads or writes are injected.']}
    if bootloader_gic_flag:
        record['interventions'].append('Explicit bootloader flag 0x0100fff8=0xbeefcafe selects the GIC branch already present in the original reset code; real-camera bootloader provenance remains unverified.')
    process = None
    with tempfile.TemporaryDirectory(prefix='ricoh-rtos-qmp-') as temporary:
        qmp = Path(temporary) / 'qmp'
        command = [str(binary), '-M', 'ricoh-rtos-lab', '-smp', '3', '-m', '512',
                   '-display', 'none', '-monitor', 'none', '-serial', 'null', '-nic', 'none',
                   '-device', f'loader,file={image},addr={load:#x},force-raw=on']
        for cpu in range(3): command.extend(['-device', f'loader,addr={load:#x},cpu-num={cpu}'])
        if bootloader_gic_flag:
            command.extend(['-device', 'loader,addr=0x0100fff8,data=0xbeefcafe,data-len=4'])
        command.extend(['-no-reboot', '-d', 'in_asm,guest_errors,unimp,int', '-D', str(output/'trace.log'),
                        '-qmp', f'unix:{qmp},server=on,wait=off'])
        record['command'] = command
        try:
            # Freeze/hash the actual guest input before QEMU's loader reads it.
            if digest(image) != RTOS_SHA256: raise ValueError('RTOS launch snapshot changed')
            with (output/'stderr.log').open('wb') as errors:
                process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=errors,
                                           start_new_session=True)
                started = time.monotonic()
                while process.poll() is None and time.monotonic()-started < seconds:
                    trace = output/'trace.log'
                    if trace.exists() and trace.stat().st_size > MAX_LOG:
                        record['log_limit_reached'] = True; break
                    time.sleep(.02)
                record['duration_seconds'] = round(time.monotonic()-started, 3)
                if process.poll() is None:
                    with socket.socket(socket.AF_UNIX) as connection:
                        connection.settimeout(2); connection.connect(str(qmp))
                        stream = connection.makefile('rwb', buffering=0); stream.readline()
                        stream.write(b'{"execute":"qmp_capabilities"}\n'); stream.readline()
                        stream.write(b'{"execute":"human-monitor-command","arguments":{"command-line":"info registers -a"}}\n')
                        while True:
                            row = json.loads(stream.readline())
                            if 'return' in row: record['registers'] = row['return']; break
        except BaseException as error:
            record['error'] = str(error) or type(error).__name__
        finally:
            if process is not None and process.poll() is None:
                try: os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError: pass
                try: process.wait(3)
                except subprocess.TimeoutExpired:
                    try: os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError: pass
                    process.wait(3)
            record['qemu_exit_code'] = process.returncode if process else None
            record['process_reaped'] = bool(process and process.poll() is not None)
    trace = (output/'trace.log').read_bytes() if (output/'trace.log').exists() else b''
    record['trace_size'] = len(trace); record['trace_sha256'] = hashlib.sha256(trace).hexdigest()
    text = trace.decode(errors='replace')
    record['original_reset_executed'] = '0x53000000:' in text
    record['irq_exceptions'] = text.count('[IRQ]')
    record['svc_exceptions'] = text.count('[SVC]')
    record['data_abort_faults'] = re.findall(r'DFSR 0x([0-9a-f]+) DFAR 0x([0-9a-f]+)', text)
    record['blockers'] = ['Original RTOS camera tasks still require unimplemented peripherals and bootloader inputs. Linux/camera-control IPC and original display are not connected.']
    if any(address == '28825000' for _, address in record['data_abort_faults']):
        record['blockers'].insert(0, 'Original PC 0x53acc2dc reads 0x28825000 before updating 32 configuration entries; this peripheral block has no confirmed reset values or register behavior in the reviewed official GPL source, so the access remains a real abort.')
    (output/'result.json').write_text(json.dumps(record, indent=2)+'\n')
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--payload', type=Path, required=True)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--seconds', type=int, default=5)
    parser.add_argument('--bootloader-gic-flag', action='store_true')
    args = parser.parse_args()
    record = probe(args.payload, args.manifest, args.output, args.seconds, args.bootloader_gic_flag)
    print(json.dumps({key: record.get(key) for key in ('model','original_reset_executed','irq_exceptions','svc_exceptions','data_abort_faults','original_ui_verified','process_reaped','error')}, indent=2))
    if record.get('error') or not record.get('process_reaped') or not record['original_reset_executed']:
        raise SystemExit(1)


if __name__ == '__main__': main()
