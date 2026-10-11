#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
set -euo pipefail
if [ "$#" -lt 2 ] || [ "$#" -gt 3 ]; then
    echo 'Usage: build.sh PINNED_Z6_CHECKOUT NEW_BUILD_DIRECTORY [QEMU_10.2.4_ARCHIVE]' >&2
    exit 2
fi
python3 -I -S - "$@" <<'PY'
from pathlib import Path
import hashlib, json, os, shutil, subprocess, sys

project = Path(sys.argv[1]).absolute()
work = Path(sys.argv[2]).absolute()
revision = '419a1f2a91a1f2d3a0209027c29606475f7fe26f'
for path in (project, work):
    if '..' in path.parts or any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('Build paths must not contain parent components or symlinks')
if work.exists():
    raise ValueError('Select a new independent Z6 build directory')
# Upstream writes the build manifest itself; preflight all existing parent and
# destination paths before invoking it, rather than discovering an overwrite
# or redirected manifest only after a successful build.
manifest = project/'.local/build-manifest.json'
dest = project/'.build/build/qemu-system-arm'
for target in (manifest, dest):
    for path in (target, *target.parents):
        if path.is_symlink(): raise ValueError('Linked build output or ancestor')
        if path.is_file() and path.stat().st_nlink != 1:
            raise ValueError('Hardlinked build output or ancestor')
    if target.exists(): raise ValueError('Refusing to overwrite an existing build output')
if subprocess.check_output(['git','-C',str(project),'rev-parse','HEAD'], text=True).strip() != revision:
    raise ValueError('Checkout does not match the reviewed Z6 revision')
if subprocess.check_output(['git','-C',str(project),'status','--porcelain','--untracked-files=no'], text=True).strip():
    raise ValueError('Tracked upstream source changed; use the fixed original checkout')
command = [sys.executable, '-I', '-S', '-B', str(project/'tools/build.py'), '--work-dir', str(work), '--jobs', '6']
if len(sys.argv) == 4: command += ['--archive', str(Path(sys.argv[3]).absolute())]
environment = {k:v for k,v in os.environ.items() if k not in ('PYTHONPATH','PYTHONHOME')}
environment.update(PYTHONSAFEPATH='1', PYTHONNOUSERSITE='1')
subprocess.run(command, check=True, env=environment)
meta = json.loads(manifest.read_text())
source = Path(meta['binary'])
if source != work/'build/qemu-system-arm' or source.is_symlink() or source.stat().st_nlink != 1:
    raise ValueError('Unexpected or linked independent QEMU build output')
if dest.exists() or any(p.is_symlink() for p in (dest, *dest.parents)):
    raise ValueError('Refusing to overwrite or follow a link in the copied QEMU destination')
dest.parent.mkdir(parents=True, exist_ok=True)
with source.open('rb') as reader, dest.open('xb') as writer: shutil.copyfileobj(reader, writer)
dest.chmod(0o755)
if hashlib.sha256(dest.read_bytes()).hexdigest() != meta['binary_sha256']:
    raise ValueError('Copied independent QEMU binary checksum differs')
meta['original_build_binary'] = str(source); meta['binary'] = str(dest)
manifest.write_text(json.dumps(meta, indent=2)+'\n')
print('Z6 copied binary:', dest)
PY
