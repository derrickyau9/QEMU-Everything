# SPDX-License-Identifier: GPL-2.0-or-later
"""Build the independent Sony CXD90057 Linux laboratory board."""
from pathlib import Path
import argparse
import hashlib
import json
import os
import subprocess
import tarfile

VERSION = '10.2.4'
ARCHIVE_SHA256 = '821b545b92f165e57dddccac5077d76d4d436a226595b8813ad59306bbfd0746'
HERE = Path(__file__).resolve().parent

def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024**2), b''): h.update(block)
    return h.hexdigest()

def write_changed(path, content):
    if not path.exists() or path.read_bytes() != content:
        path.write_bytes(content)

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--archive', type=Path, required=True)
    p.add_argument('--work-dir', type=Path, required=True)
    p.add_argument('--jobs', type=int, default=4)
    args = p.parse_args()
    if os.name == 'nt': p.error('Build in Linux/WSL')
    if not 1 <= args.jobs <= 16: p.error('jobs must be 1..16')
    if digest(args.archive) != ARCHIVE_SHA256: raise ValueError('QEMU archive hash mismatch')
    work = args.work_dir.resolve()
    work.mkdir(parents=True, exist_ok=True)
    source = work / f'qemu-{VERSION}'
    marker = source / '.qe-source'
    if not marker.exists():
        if source.exists(): raise ValueError('Use a new work directory for verified clean QEMU source')
        def checked(member, dest):
            try: return tarfile.data_filter(member, dest)
            except (tarfile.AbsoluteLinkError, tarfile.LinkOutsideDestinationError): return None
        with tarfile.open(args.archive, 'r:xz') as archive: archive.extractall(work, filter=checked)
        marker.write_text(ARCHIVE_SHA256 + '\n')
    if marker.read_text().strip() != ARCHIVE_SHA256: raise ValueError('Source pin marker mismatch')
    # Reconstruct this one upstream source from the verified archive on each
    # build; generated changes never stack or modify any existing Nikon build.
    with tarfile.open(args.archive, 'r:xz') as archive:
        original = archive.extractfile(f'qemu-{VERSION}/hw/arm/virt.c').read()
        config = archive.extractfile(f'qemu-{VERSION}/configs/devices/aarch64-softmmu/default.mak').read()
    overlay = HERE / 'cxd90057-mailbox.c.inc'
    map_anchor = b'    if (ms->ram_slots > ACPI_MAX_RAM_SLOTS) {'
    if original.count(map_anchor) != 1: raise ValueError('Pinned virt memory-map anchor changed')
    original = original.replace(map_anchor, b'''    /* Independently specified CXD90057 addresses, verified against Sony's
     * ILCE-7M4 Ver.2.00 GPL source (head_init.h, uart.h, memmap DTSI).
     * The lab still omits camera peripherals and uses a single CPU. */
    if (object_dynamic_cast(OBJECT(vms), MACHINE_TYPE_NAME("sony-cxd90057-lab"))) {
        vms->memmap[VIRT_MEM].base = 0x400000000ULL;
        vms->memmap[VIRT_GIC_DIST].base = 0xf13a1000;
        vms->memmap[VIRT_GIC_CPU].base = 0xf13a2000;
        vms->memmap[VIRT_GIC_HYP].base = 0xf13a4000;
        vms->memmap[VIRT_GIC_VCPU].base = 0xf13a6000;
        vms->memmap[VIRT_UART0].base = 0xf1000000;
    }

''' + map_anchor)
    irq_anchor = b'    int irq = vms->irqmap[uart];'
    if original.count(irq_anchor) != 1: raise ValueError('Pinned virt UART anchor changed')
    original = original.replace(irq_anchor, irq_anchor + b'''
    if (uart == VIRT_UART0 && object_dynamic_cast(OBJECT(vms), MACHINE_TYPE_NAME("sony-cxd90057-lab"))) {
        irq = 218;
    }''')
    write_changed(source / 'hw/arm/virt.c', original + b'\n' + overlay.read_bytes())
    # virt's ACPI implementation references the CXL helpers even when the
    # guest does not instantiate any CXL device.
    write_changed(source / 'configs/devices/aarch64-softmmu/default.mak',
        config + b'\nCONFIG_ARM_VIRT=y\nCONFIG_PXB=y\nCONFIG_CXL=y\nCONFIG_CXL_MEM_DEVICE=y\n'
        b'CONFIG_PCIE_PORT=y\nCONFIG_PCIE_PCI_BRIDGE=y\nCONFIG_PCI_BRIDGE=y\nCONFIG_GENERIC_LOADER=y\n')
    build = work / 'build'
    build.mkdir(exist_ok=True)
    options = ['--target-list=aarch64-softmmu', '--without-default-devices', '--disable-docs',
               '--disable-tools', '--disable-user', '--disable-werror', '--disable-gtk', '--disable-sdl',
               '--disable-vnc', '--disable-opengl', '--disable-guest-agent', '--disable-slirp',
               '--disable-rust', '--disable-debug-info']
    if not (build / 'build.ninja').exists():
        subprocess.run([str(source / 'configure'), *options], cwd=build, check=True)
    subprocess.run(['ninja', '-j', str(args.jobs), 'qemu-system-aarch64'], cwd=build, check=True)
    binary = build / 'qemu-system-aarch64'
    dts = HERE / 'cxd90057-lab.dts'
    dtb = work / 'cxd90057-lab.dtb'
    subprocess.run(['dtc', '-I', 'dts', '-O', 'dtb', '-o', str(dtb), str(dts)], check=True)
    record = {'qemu_version': VERSION, 'archive_sha256': ARCHIVE_SHA256, 'binary': str(binary),
              'binary_sha256': digest(binary), 'machine': 'sony-cxd90057-lab',
              'sources': {str(path.relative_to(HERE)): digest(path) for path in (Path(__file__), overlay, dts)},
              'dtb': str(dtb), 'dtb_sha256': digest(dtb),
              'generated_virt_sha256': digest(source / 'hw/arm/virt.c'), 'configure': options,
              'changes': ['Single core', 'Boot handoff RAM at 0xfe005000..0xfe005fff',
                          'Sony RAM, GIC and PL011 addresses; PL011 SPI 218',
                          'Four hardware timers; GPIO and SCU register latches',
                          'DDR mode-command completion only; no DDR PHY model',
                          'AES-XTS initialization registers only; encrypted storage I/O is absent',
                          '2 MiB noncached reserved pool covering DMA instructions at 0x4102e4000',
                          'Use cortex-a35,cntfrq=2000000 to match the development DTB',
                          'Unsupported Sony USB initcall is explicitly blacklisted in the lab command line',
                          'Development DTB and fixed bootloader parameter placement are required'],
              'firmware_instructions_modified': False, 'complete_soc_emulated': False}
    (work / 'build-manifest.json').write_text(json.dumps(record, indent=2) + '\n')
    print(json.dumps(record, indent=2))

if __name__ == '__main__': main()
