#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Build the GPL QEMU H22 experimental board separately from the workbench."""
from pathlib import Path
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tarfile
from urllib.request import urlopen

QEMU_VERSION = '10.2.4'
ARCHIVE_SHA256 = '821b545b92f165e57dddccac5077d76d4d436a226595b8813ad59306bbfd0746'
SOURCE_URL = f'https://download.qemu.org/qemu-{QEMU_VERSION}.tar.xz'
FLAGS = ['--target-list=aarch64-softmmu', '--without-default-devices', '--disable-docs',
         '--disable-tools', '--disable-user', '--disable-werror', '--disable-gtk',
         '--disable-sdl', '--disable-vnc', '--disable-opengl', '--disable-guest-agent',
         '--disable-slirp', '--disable-rust', '--disable-debug-info']

def digest(path):
    sha = hashlib.sha256()
    with Path(path).open('rb') as source:
        for block in iter(lambda: source.read(1024**2), b''):
            sha.update(block)
    return sha.hexdigest()

def build(work, archive, jobs):
    if os.name == 'nt':
        raise ValueError('Run through an explicitly selected WSL Linux distribution')
    overlay = Path(__file__).resolve().parent
    work = Path(work).resolve(); work.mkdir(parents=True, exist_ok=True)
    archive = Path(archive).resolve() if archive else work / f'qemu-{QEMU_VERSION}.tar.xz'
    if not archive.exists():
        temporary = archive.with_suffix(archive.suffix + '.partial')
        with urlopen(SOURCE_URL, timeout=60) as response, temporary.open('xb') as output:
            copied = 0
            while block := response.read(1024**2):
                copied += len(block)
                if copied > 256 * 1024**2:
                    raise ValueError('QEMU source archive exceeds its download bound')
                output.write(block)
        if digest(temporary) != ARCHIVE_SHA256:
            raise ValueError('QEMU source archive checksum mismatch')
        temporary.replace(archive)
    if digest(archive) != ARCHIVE_SHA256:
        raise ValueError('Use the reviewed original QEMU 10.2.4 source archive')
    source = work / f'qemu-{QEMU_VERSION}'
    extraction_marker = source / '.h22-extraction-complete'
    if not extraction_marker.exists():
        def source_filter(member, destination):
            # Re-check an older extraction which may already contain QEMU's
            # unused absolute X11 convenience link. Unlink only the link entry
            # in this build tree; never follow or modify its external target.
            target = Path(destination) / member.name
            if member.issym() and target.is_symlink() and target.parent.resolve().is_relative_to(Path(destination).resolve()):
                target.unlink()
            try: return tarfile.data_filter(member, destination)
            except (tarfile.AbsoluteLinkError, tarfile.LinkOutsideDestinationError): return None
        with tarfile.open(archive, 'r:xz') as packed:
            packed.extractall(work, filter=source_filter)
        extraction_marker.write_text(ARCHIVE_SHA256)
    shutil.copyfile(overlay / 'insta360-h22-lab.c', source / 'hw/arm/insta360-h22-lab.c')
    meson = source / 'hw/arm/meson.build'
    registration = "arm_ss.add(when: 'CONFIG_INSTA360_H22_LAB', if_true: files('insta360-h22-lab.c'))"
    legacy = "arm_ss.add(files('insta360-h22-lab.c'))"
    if legacy in meson.read_text():
        meson.write_text(meson.read_text().replace(legacy, registration))
    if registration not in meson.read_text():
        meson.write_text(meson.read_text() + '\n' + registration + '\n')
    kconfig = source / 'hw/arm/Kconfig'
    if 'config INSTA360_H22_LAB' not in kconfig.read_text():
        kconfig.write_text(kconfig.read_text() + '\nconfig INSTA360_H22_LAB\n    bool\n    depends on TCG && ARM\n    select ARM_GIC\n')
    old_config = kconfig.read_text()
    new_config = old_config.replace('    default y\n    depends on AARCH64\n', '    depends on TCG && ARM\n')
    if new_config != old_config:
        kconfig.write_text(new_config)
    defaults = source / 'configs/devices/aarch64-softmmu/default.mak'
    if 'CONFIG_INSTA360_H22_LAB=y' not in defaults.read_text():
        defaults.write_text(defaults.read_text() + '\nCONFIG_INSTA360_H22_LAB=y\n')
    directory = work / 'build'; directory.mkdir(exist_ok=True)
    environment = dict(os.environ)
    for name in ('PYTHONPATH', 'PYTHONHOME', 'PYTHONSAFEPATH'):
        environment.pop(name, None)
    environment['PYTHONNOUSERSITE'] = '1'
    if not (directory / 'build.ninja').exists():
        subprocess.run([str(source / 'configure'), *FLAGS], cwd=directory, env=environment, check=True)
    subprocess.run(['ninja', '-j', str(jobs), 'qemu-system-aarch64'], cwd=directory, env=environment, check=True)
    dtb = directory / 'insta360-linux-lab.dtb'
    subprocess.run(['dtc', '-I', 'dts', '-O', 'dtb', '-o', str(dtb), str(overlay / 'minimal-linux.dts')], check=True)
    binary = directory / 'qemu-system-aarch64'
    manifest = {'schema_version': 1, 'qemu_version': QEMU_VERSION,
                'upstream_archive_sha256': ARCHIVE_SHA256, 'binary': str(binary),
                'binary_sha256': digest(binary), 'dtb': str(dtb), 'dtb_sha256': digest(dtb),
                'source_directory': str(source), 'overlay_directory': str(overlay),
                'overlay_sha256': {name: digest(overlay / name) for name in
                                   ('build.py', 'run.py', 'insta360-h22-lab.c', 'minimal-linux.dts')},
                'configure': FLAGS, 'peripherals_stubbed': True, 'original_ui_verified': False}
    (directory / 'insta360-lab-build.json').write_text(json.dumps(manifest, indent=2))
    print('Build manifest:', directory / 'insta360-lab-build.json', flush=True)

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work-dir', type=Path, default=Path.home() / '.cache/qemu-everything/insta360-h22')
    parser.add_argument('--archive', type=Path)
    parser.add_argument('--jobs', type=int, default=min(8, os.cpu_count() or 1))
    args = parser.parse_args()
    if not 1 <= args.jobs <= 64:
        parser.error('--jobs must be 1..64')
    build(args.work_dir, args.archive, args.jobs)
