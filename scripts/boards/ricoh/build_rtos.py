# SPDX-License-Identifier: GPL-2.0-or-later
"""Build an independent QEMU for Ricoh RTOS reset tracing, not UI emulation."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tarfile

ARCHIVE_SHA256 = '821b545b92f165e57dddccac5077d76d4d436a226595b8813ad59306bbfd0746'
ROOT = Path(__file__).resolve().parent


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024**2), b''): h.update(block)
    return h.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', type=Path, required=True)
    parser.add_argument('--work-dir', type=Path, required=True)
    parser.add_argument('--jobs', type=int, default=3)
    args = parser.parse_args()
    if not 1 <= args.jobs <= 16: parser.error('jobs must be 1..16')
    if digest(args.archive) != ARCHIVE_SHA256: raise ValueError('QEMU archive hash mismatch')
    work = args.work_dir.resolve(); work.mkdir(parents=True, exist_ok=True)
    source = work / 'qemu-10.2.4'; marker = source / '.ricoh-rtos-lab-source'
    if not marker.exists():
        if source.exists(): raise ValueError('Use a separate RTOS work directory')
        def checked(member, destination):
            try: return tarfile.data_filter(member, destination)
            except (tarfile.AbsoluteLinkError, tarfile.LinkOutsideDestinationError): return None
        with tarfile.open(args.archive, 'r:xz') as archive:
            archive.extractall(work, filter=checked)
        marker.write_text(ARCHIVE_SHA256, encoding='ascii')
    if marker.read_text(encoding='ascii') != ARCHIVE_SHA256: raise ValueError('Wrong RTOS source marker')
    shutil.copyfile(ROOT / 'ricoh-rtos-lab.c', source / 'hw/arm/ricoh-rtos-lab.c')
    meson = source / 'hw/arm/meson.build'; original = meson.read_text()
    line = "arm_ss.add(when: 'CONFIG_RICOH_RTOS_LAB', if_true: files('ricoh-rtos-lab.c'))"
    if line not in original: meson.write_text(original+'\n'+line+'\n')
    kconfig = source / 'hw/arm/Kconfig'; original = kconfig.read_text()
    config_block = 'config RICOH_RTOS_LAB\n    bool\n    default y\n    depends on TCG && ARM\n    select A15MPCORE\n'
    if 'config RICOH_RTOS_LAB' not in original:
        original += '\n' + config_block
    original = original.replace(config_block, config_block+'    select GENERIC_LOADER\n')
    # Retain idempotence for an already patched isolated source tree.
    original = original.replace('    select GENERIC_LOADER\n    select GENERIC_LOADER\n', '    select GENERIC_LOADER\n')
    if original != kconfig.read_text(): kconfig.write_text(original)
    devices = source / 'configs/devices/arm-softmmu/default.mak'; original = devices.read_text()
    if 'CONFIG_RICOH_RTOS_LAB=y' not in original: devices.write_text(original+'\nCONFIG_RICOH_RTOS_LAB=y\n')
    build = work / 'build'; build.mkdir(exist_ok=True)
    options = ['--target-list=arm-softmmu','--without-default-devices','--disable-docs','--disable-tools','--disable-user','--disable-werror','--disable-gtk','--disable-sdl','--disable-vnc','--disable-opengl','--disable-slirp','--disable-rust','--disable-debug-info']
    if not (build / 'build.ninja').exists(): subprocess.run([str(source/'configure'),*options],cwd=build,check=True)
    subprocess.run(['ninja','-j',str(args.jobs),'qemu-system-arm'],cwd=build,check=True)
    binary = build/'qemu-system-arm'
    manifest = {'qemu_version':'10.2.4','machine':'ricoh-rtos-lab','binary':str(binary),
                'binary_sha256':digest(binary),'upstream_archive_sha256':ARCHIVE_SHA256,
                'sources':{n:digest(ROOT/n) for n in ('ricoh-rtos-lab.c','build_rtos.py')},
                'configure':options,'scope':'Three secure RTOS cores, guest-written SRAM, GIC, timer 13, 40 MHz ARM system counter and USB3_JT_ADJ configuration retention; no USB transfers, IPC replies or display'}
    (work/'build-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print(json.dumps(manifest,indent=2))


if __name__ == '__main__': main()
