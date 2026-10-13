# SPDX-License-Identifier: GPL-2.0-or-later
"""Run the Z50 research machine; firmware remains local and unmodified."""
import argparse
import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FIRMWARE = ROOT / '.local/firmware/eg1630_026000.bi'
STOCK_SIZE = 31404090
STOCK_HASH = '57e834fac8f921c18c99abe0b1469edbaca71cb1957794bb9f138bd223d30fc1'


def firmware(path, host_version=None):
    path = path.resolve()
    data = path.read_bytes()
    if host_version is not None: raise ValueError('Modified host-version fixtures are not supported by the Z50 profile')
    expected = (STOCK_SIZE, STOCK_HASH)
    if (len(data), hashlib.sha256(data).hexdigest()) != expected:
        raise ValueError('Expected extracted Z50 C2.60 main, not an update container or Z7 payload')
    return path

def command(binary, image, profile='assisted', cpus=2, gdb_port=None, paused=False, *, host_version=None, sd_image=None):
    if cpus not in (1, 2) or profile not in ('zero', 'assisted'):
        raise ValueError('Unsupported Z50 profile or CPU count')
    name = 'nikon-z50' if profile == 'zero' else 'nikon-z50-assisted'
    args = [str(binary), '-M', name, '-smp', str(cpus), '-accel', 'tcg,thread=single',
            '-display', 'none', '-serial', 'none', '-monitor', 'none',
            '-chardev', 'stdio,id=z50uart',
            '-bios', str(firmware(image, host_version))]
    if sd_image is not None:
        sd_image = Path(sd_image).resolve()
        if not sd_image.is_file() or ',' in str(sd_image):
            raise ValueError('SD image must be a regular local file without comma in its path')
        # Writes go to QEMU's temporary overlay, never to the input card image.
        args += ['-drive', f'file={sd_image},if=sd,index=0,format=raw,snapshot=on']
    for cpu in range(cpus):
        args += ['-device', f'loader,addr=0x10020000,cpu-num={cpu}']
    if gdb_port is not None:
        if not 1024 <= gdb_port <= 65535:
            raise ValueError('GDB port must be 1024..65535')
        args += ['-gdb', f'tcp:127.0.0.1:{gdb_port}']
    if paused:
        args += ['-S']
    return args

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--qemu', type=Path, default=ROOT / '.build/build/qemu-system-arm')
    p.add_argument('--firmware', type=Path, default=DEFAULT_FIRMWARE)
    p.add_argument('--profile', choices=['zero', 'assisted'], default='assisted')
    p.add_argument('--cpus', type=int, choices=[1, 2], default=2)
    p.add_argument('--gdb-port', type=int, default=12330)
    p.add_argument('--paused', action='store_true')
    p.add_argument('--dry-run', action='store_true')
    a = p.parse_args()
    args = command(a.qemu.resolve(), a.firmware, a.profile, a.cpus, a.gdb_port, a.paused)
    print(json.dumps(args, ensure_ascii=False), flush=True)
    if not a.dry_run:
        subprocess.run(args, check=True)

if __name__ == '__main__':
    main()
