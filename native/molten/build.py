# SPDX-License-Identifier: GPL-2.0-or-later
"""Build a separate, archive-pinned AArch64 MOLTEN research backend (Linux)."""
from pathlib import Path
import argparse
import hashlib
import json
import os
import subprocess
import tarfile
import urllib.request

VERSION = '10.2.4'
SHA256 = '821b545b92f165e57dddccac5077d76d4d436a226595b8813ad59306bbfd0746'
HERE = Path(__file__).resolve().parent


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def write_changed(path, data):
    if not path.exists() or path.read_bytes() != data:
        path.write_bytes(data)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work-dir', required=True, type=Path)
    parser.add_argument('--archive', type=Path)
    parser.add_argument('--jobs', type=int, default=4)
    args = parser.parse_args()
    if os.name != 'posix' or not 1 <= args.jobs <= 16:
        parser.error('Use Linux/WSL and --jobs 1..16')
    work = args.work_dir.resolve()
    work.mkdir(parents=True, exist_ok=True)
    archive_path = args.archive or work / f'qemu-{VERSION}.tar.xz'
    if not archive_path.exists():
        temporary = archive_path.with_suffix('.download')
        urllib.request.urlretrieve(f'https://download.qemu.org/qemu-{VERSION}.tar.xz', temporary)
        if digest(temporary) != SHA256:
            raise ValueError('QEMU archive hash mismatch')
        temporary.replace(archive_path)
    if digest(archive_path) != SHA256:
        raise ValueError('QEMU archive hash mismatch')
    source = work / f'qemu-{VERSION}'
    marker = source / '.molten-source'
    with tarfile.open(archive_path, 'r:xz') as archive:
        if not marker.exists():
            if source.exists():
                raise ValueError('Use a fresh work directory for clean pinned source')
            def checked(member, destination):
                try:
                    return tarfile.data_filter(member, destination)
                except (tarfile.AbsoluteLinkError, tarfile.LinkOutsideDestinationError):
                    return None
            archive.extractall(work, filter=checked)
            marker.write_text(SHA256 + '\n')
        if marker.read_text().strip() != SHA256:
            raise ValueError('Source pin marker mismatch')
        original = archive.extractfile(f'qemu-{VERSION}/hw/arm/virt.c').read()
        config = archive.extractfile(f'qemu-{VERSION}/configs/devices/aarch64-softmmu/default.mak').read()
    write_changed(source / 'hw/arm/virt.c', original + b'\n' + (HERE / 'molten.c.inc').read_bytes())
    write_changed(source / 'configs/devices/aarch64-softmmu/default.mak',
        config + b'\nCONFIG_ARM_VIRT=y\nCONFIG_PXB=y\nCONFIG_CXL=y\nCONFIG_CXL_MEM_DEVICE=y\n'
        b'CONFIG_PCIE_PORT=y\nCONFIG_PCIE_PCI_BRIDGE=y\nCONFIG_PCI_BRIDGE=y\nCONFIG_GENERIC_LOADER=y\n')
    build = work / 'build'
    build.mkdir(exist_ok=True)
    options = ['--target-list=aarch64-softmmu', '--without-default-devices', '--enable-plugins',
               '--disable-docs', '--disable-tools', '--disable-user', '--disable-werror',
               '--disable-gtk', '--disable-sdl', '--disable-vnc', '--disable-opengl',
               '--disable-guest-agent', '--disable-slirp', '--disable-rust', '--disable-debug-info']
    if not (build / 'build.ninja').exists():
        subprocess.run([str(source / 'configure'), *options], cwd=build, check=True)
    subprocess.run(['ninja', '-j', str(args.jobs), 'qemu-system-aarch64'], cwd=build, check=True)
    probe = build / 'molten-probe.so'
    flags = subprocess.check_output(['pkg-config', '--cflags', 'glib-2.0'], text=True).split()
    subprocess.run(['cc', '-shared', '-fPIC', '-fvisibility=hidden', '-Wall', '-Wextra',
                    '-I' + str(source / 'include/qemu'), *flags,
                    str(HERE / 'plugin_probe.c'), '-o', str(probe)], check=True)
    binary = build / 'qemu-system-aarch64'
    record = dict(schema_version=1, qemu_version=VERSION, archive_sha256=SHA256,
                  binary=str(binary), binary_sha256=digest(binary), machine='molten-aarch64',
                  probe=str(probe), probe_sha256=digest(probe), configure=options,
                  sources={p.name: digest(p) for p in (Path(__file__), HERE / 'molten.c.inc', HERE / 'plugin_probe.c')},
                  generated_virt_sha256=digest(source / 'hw/arm/virt.c'),
                  reserved_regions={'dtb': ['0x47f00000', '0x48000000'],
                                    'stack': ['0x47e00000', '0x47f00000']},
                  checkpoint='unsupported', replay='clean-restart', vcpus=1)
    (work / 'build-manifest.json').write_text(json.dumps(record, indent=2) + '\n')
    print(json.dumps(record, indent=2))


if __name__ == '__main__':
    main()
