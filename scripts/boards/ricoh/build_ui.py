#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Prepare the original GR IV UI platform from a reviewed RTOS QEMU build."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent))
from rtos_probe import ARCHIVE_SHA256, BOARD_SHA256, digest, regular
from resource_boot import build_bridge

ROOT=Path(__file__).resolve().parent
PROFILE='ricoh-griv-topmenu-ui-v1'
SOURCES=('build_ui.py','run_ui.py','rtos_probe.py','resource_boot.py','resource_backend.c',
         'resources.py','component_control.py','component_viewer.py','ricoh-rtos-lab.c')


def prepare(manifest,work):
    build=json.loads(regular(Path(manifest)).read_text())
    if (build.get('qemu_version')!='10.2.4' or build.get('machine')!='ricoh-rtos-lab' or
        build.get('upstream_archive_sha256')!=ARCHIVE_SHA256 or
        build.get('sources',{}).get('ricoh-rtos-lab.c')!=BOARD_SHA256 or
        digest(regular(Path(build['binary'])))!=build.get('binary_sha256')):
        raise ValueError('Requires the pinned independent Ricoh RTOS QEMU build')
    work=Path(work).absolute()
    if '..' in work.parts or any(p.is_symlink() for p in (work,*work.parents)):
        raise ValueError('Invalid Ricoh UI build directory')
    work.mkdir(parents=True,exist_ok=False)
    bridge,backend,metadata=build_bridge(work,True,{'size':14480480})
    (work/'bridge.bin').write_bytes(bridge);(work/'backend.bin').write_bytes(backend)
    value=dict(profile=PROFILE,qemu_build=build,overlay_sha256={n:digest(ROOT/n) for n in SOURCES},
               bridge=str(work/'bridge.bin'),bridge_sha256=digest(work/'bridge.bin'),
               backend=str(work/'backend.bin'),backend_sha256=digest(work/'backend.bin'),
               platform_metadata=metadata,scope='Original GR IV 1.11 TopMenu/Focus assisted UI; full camera boot unverified')
    target=work/'ricoh-ui-manifest.json';target.write_text(json.dumps(value,indent=2)+'\n')
    return target


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qemu-manifest',type=Path)
    parser.add_argument('--archive',type=Path)
    parser.add_argument('--qemu-work-dir',type=Path)
    parser.add_argument('--work-dir',type=Path,required=True)
    parser.add_argument('--jobs',type=int,default=2)
    args=parser.parse_args()
    manifest=args.qemu_manifest
    if manifest is None:
        if args.archive is None or args.qemu_work_dir is None:parser.error('Provide --qemu-manifest or --archive and --qemu-work-dir')
        if not 1<=args.jobs<=4:parser.error('jobs must be1..4')
        subprocess.run([sys.executable,str(ROOT/'build_rtos.py'),'--archive',str(args.archive),
                        '--work-dir',str(args.qemu_work_dir),'--jobs',str(args.jobs)],check=True)
        manifest=args.qemu_work_dir/'build-manifest.json'
    print(json.dumps({'manifest':str(prepare(manifest,args.work_dir))}),flush=True)


if __name__=='__main__':main()
