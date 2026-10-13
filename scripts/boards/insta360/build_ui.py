#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Build the separate X3 original GUI component laboratory (Linux/WSL)."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
from urllib.request import urlopen

VERSION = '10.2.4'
ARCHIVE_SHA256 = '821b545b92f165e57dddccac5077d76d4d436a226595b8813ad59306bbfd0746'
SOURCES = ('build_ui.py', 'run_ui.py', 'x3-ui-lab.c', 'ui-platform.S', 'ui-platform.ld')
FLAGS = ['--target-list=aarch64-softmmu', '--without-default-devices', '--disable-docs',
         '--disable-tools', '--disable-user', '--disable-werror', '--disable-gtk',
         '--disable-sdl', '--disable-vnc', '--disable-opengl', '--disable-guest-agent',
         '--disable-slirp', '--disable-rust', '--disable-debug-info']


def digest(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024**2), b''): value.update(block)
    return value.hexdigest()


def source_filter(member, destination):
    # The verified release contains edk2 X11 include convenience links outside
    # the archive. They are unused by this ARM board; never extract them.
    target = Path(destination) / member.name
    if member.issym() and target.is_symlink() and target.parent.resolve().is_relative_to(Path(destination).resolve()):
        target.unlink()  # Only the old link entry inside this build directory.
    try: return tarfile.data_filter(member, destination)
    except (tarfile.AbsoluteLinkError, tarfile.LinkOutsideDestinationError): return None


def build(work, archive, jobs):
    if os.name == 'nt': raise ValueError('Build inside an explicitly selected WSL/Linux environment')
    overlay = Path(__file__).resolve().parent
    work = Path(work).resolve(); work.mkdir(parents=True, exist_ok=True)
    archive = Path(archive).resolve() if archive else work / f'qemu-{VERSION}.tar.xz'
    if not archive.exists():
        partial = archive.with_suffix('.partial')
        with urlopen(f'https://download.qemu.org/qemu-{VERSION}.tar.xz', timeout=60) as response, partial.open('xb') as output:
            copied = 0
            while block := response.read(1024**2):
                copied += len(block)
                if copied > 256 * 1024**2: raise ValueError('QEMU archive exceeds download bound')
                output.write(block)
        if digest(partial) != ARCHIVE_SHA256: raise ValueError('QEMU source archive checksum mismatch')
        partial.replace(archive)
    if digest(archive) != ARCHIVE_SHA256: raise ValueError('Requires the reviewed QEMU source archive')
    source = work / f'qemu-{VERSION}'
    marker = source / '.x3-ui-extraction-complete'
    if not marker.exists():
        with tarfile.open(archive) as packed: packed.extractall(work, filter=source_filter)
        marker.write_text(ARCHIVE_SHA256)
    shutil.copyfile(overlay / 'x3-ui-lab.c', source / 'hw/arm/x3-ui-lab.c')
    for path, text in (
        (source / 'hw/arm/meson.build', "arm_ss.add(when: 'CONFIG_X3_UI_LAB', if_true: files('x3-ui-lab.c'))"),
        (source / 'hw/arm/Kconfig', 'config X3_UI_LAB\n    bool\n    depends on TCG && ARM\n    select ARM_GIC'),
        (source / 'configs/devices/aarch64-softmmu/default.mak', 'CONFIG_X3_UI_LAB=y\nCONFIG_GENERIC_LOADER=y')):
        old = path.read_text()
        if text not in old: path.write_text(old + '\n' + text + '\n')
    directory = work / 'build'; directory.mkdir(exist_ok=True)
    environment = dict(os.environ)
    for key in ('PYTHONPATH', 'PYTHONHOME', 'PYTHONSAFEPATH'): environment.pop(key, None)
    environment['PYTHONNOUSERSITE'] = '1'
    if not (directory / 'build.ninja').exists():
        subprocess.run([str(source / 'configure'), *FLAGS], cwd=directory, env=environment, check=True)
    subprocess.run(['ninja', '-j', str(jobs), 'qemu-system-aarch64'], cwd=directory, env=environment, check=True)
    platform = directory / 'ui-platform.elf'
    subprocess.run(['arm-linux-gnueabihf-as', '-o', str(directory / 'ui-platform.o'), str(overlay / 'ui-platform.S')], check=True)
    subprocess.run(['arm-linux-gnueabihf-ld', '-T', str(overlay / 'ui-platform.ld'), '-o', str(platform), str(directory / 'ui-platform.o')], check=True)
    binary = directory / 'qemu-system-aarch64'
    manifest = dict(schema_version=1, profile='insta360-x3-ui-v1', qemu_version=VERSION,
        upstream_archive_sha256=ARCHIVE_SHA256, binary=str(binary), binary_sha256=digest(binary),
        platform=str(platform), platform_sha256=digest(platform), source_directory=str(source),
        overlay_sha256={name: digest(overlay / name) for name in SOURCES}, configure=FLAGS,
        assisted_startup=True, full_threadx_boot_verified=False, natural_power_on_verified=False)
    output = directory / 'insta360-ui-build.json'
    output.write_text(json.dumps(manifest, indent=2) + '\n')
    print('Build manifest:', output, flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work-dir', type=Path, default=Path.home() / '.cache/qemu-everything/insta360-x3-ui')
    parser.add_argument('--archive', type=Path)
    parser.add_argument('--jobs', type=int, default=2)
    args = parser.parse_args()
    if not 1 <= args.jobs <= 64: parser.error('--jobs must be 1..64')
    build(args.work_dir, args.archive, args.jobs)
