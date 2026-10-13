#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Build pinned QEMU + original HERO8 Tara software platform, without firmware."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import time
sys.path.insert(0,str(Path(__file__).resolve().parent))
from component import ARCHIVE_SHA256, PROFILE, ROOT, SOURCES, compile_bridge, digest


def build(archive, work, jobs=2):
    archive = Path(archive).absolute(); work = Path(work).absolute()
    if type(jobs) is not int or not 1 <= jobs <= 4: raise ValueError('jobs must be 1..4')
    if digest(archive) != ARCHIVE_SHA256: raise ValueError('QEMU 10.2.4 archive fingerprint mismatch')
    if '..' in work.parts or any(p.is_symlink() for p in (work,*work.parents)):
        raise ValueError('Build work directory contains a symlink or traversal')
    work.mkdir(parents=True,exist_ok=True)
    source = work/'qemu-10.2.4'; stamp = work/'source-complete.json'
    if not stamp.exists():
        if source.exists(): raise ValueError('Incomplete source tree exists; choose another independent work directory')
        with tarfile.open(archive,'r:xz') as packed:
            members = [m for m in packed.getmembers() if m.name != 'qemu-10.2.4/roms/edk2/EmulatorPkg/Unix/Host/X11IncludeHack']
            # This exact unused upstream EDK2 absolute link is excluded. All
            # other links/paths must pass the standard safe extraction filter.
            packed.extractall(work,members=members,filter='data')
        stamp.write_text(json.dumps({'sha256':ARCHIVE_SHA256,'skipped_nonbuild_link':'roms/edk2/EmulatorPkg/Unix/Host/X11IncludeHack'})+'\n')
    if json.loads(stamp.read_text()).get('sha256') != ARCHIVE_SHA256:
        raise ValueError('Work directory was not built from pinned QEMU')
    if source.is_symlink(): raise ValueError('QEMU source directory cannot be a symlink')
    board=source/'hw/arm/gopro-hero8-reset-lab.c'
    if not board.exists() or digest(board)!=digest(ROOT/'gopro-hero8-reset-lab.c'):
        shutil.copyfile(ROOT/'gopro-hero8-reset-lab.c',board)
    meson = source/'hw/arm/meson.build'; line = "arm_ss.add(when: 'CONFIG_GOPRO_HERO8_RESET_LAB', if_true: files('gopro-hero8-reset-lab.c'))"
    if line not in meson.read_text(): meson.write_text(meson.read_text()+'\n'+line+'\n')
    kconfig = source/'hw/arm/Kconfig'; block = '\nconfig GOPRO_HERO8_RESET_LAB\n    bool\n    default y\n    depends on TCG && ARM\n    select A15MPCORE\n    select GENERIC_LOADER\n'
    original = kconfig.read_text()
    if '\nconfig GOPRO_HERO8_RESET_LAB' in original:
        # This builder owns an isolated overlay appended at the end of Kconfig.
        original = original[:original.index('\nconfig GOPRO_HERO8_RESET_LAB')]
    if kconfig.read_text()!=original+block:kconfig.write_text(original+block)
    devices = source/'configs/devices/arm-softmmu/default.mak'; line = 'CONFIG_GOPRO_HERO8_RESET_LAB=y'
    if line not in devices.read_text(): devices.write_text(devices.read_text()+'\n'+line+'\n')
    binary_dir = work/'build'; binary_dir.mkdir(exist_ok=True)
    options = ['--target-list=arm-softmmu','--without-default-devices','--disable-docs','--disable-tools',
               '--disable-user','--disable-werror','--disable-gtk','--disable-sdl','--disable-vnc',
               '--disable-opengl','--disable-slirp','--disable-rust','--disable-debug-info']
    if not (binary_dir/'build.ninja').exists():
        subprocess.run([str(source/'configure'),*options],cwd=binary_dir,check=True)
    subprocess.run(['ninja','-j',str(jobs),'qemu-system-arm'],cwd=binary_dir,check=True)
    platform = compile_bridge(work/('platform-'+str(time.time_ns())))
    binary = binary_dir/'qemu-system-arm'
    manifest = dict(profile=PROFILE,qemu_version='10.2.4',machine='gopro-hero8-reset-lab',
                    upstream_archive_sha256=ARCHIVE_SHA256,source_directory=str(source),
                    binary=str(binary),binary_sha256=digest(binary),configure=options,
                    overlay_sha256={name:digest(ROOT/name) for name in SOURCES},**platform,
                    scope='Original HERO8 Tara UI assisted component; original cold reset and physical camera hardware unverified')
    path = work/'gopro-ui-manifest.json'; path.write_text(json.dumps(manifest,indent=2)+'\n')
    return path, manifest


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive',type=Path,required=True)
    parser.add_argument('--work-dir',type=Path,required=True)
    parser.add_argument('--jobs',type=int,default=2)
    args=parser.parse_args()
    path,manifest=build(args.archive,args.work_dir,args.jobs)
    print(json.dumps({'manifest':str(path),**manifest}),flush=True)


if __name__=='__main__':main()
