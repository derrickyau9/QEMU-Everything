#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Offline wrapper around the separately installed GPL2orLater pinned builder.
set -euo pipefail
if [[ $# != 3 ]]; then
  echo 'Usage: build-nikon-z30.sh /absolute/z30-qemu /absolute/qemu-10.2.4.tar.xz /absolute/new-work-dir' >&2
  exit 2
fi
python3 -I -S - "$1" "$2" "$3" <<'PY'
import hashlib
import os
from pathlib import Path
import subprocess
import sys

project, archive, work = (Path(value) for value in sys.argv[1:])
for path in (project, archive, work):
    if not path.is_absolute() or '..' in path.parts or any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('Build paths must be absolute and contain no symlinks/parent escape')
revision = subprocess.check_output(['git', '-C', str(project), 'rev-parse', 'HEAD'], timeout=10, text=True).strip()
if revision != '0ba0c3547ccbec09e7c861eaca35db8e82795f15':
    raise ValueError('Expected reviewed WonderLantern/z30-qemu commit')
expected = {
 'tools/build.py': '57078f4c13c3c0a997823e635660cd1b5d6ddfea73ee3fc278a4f7d3845b48f2',
 'qemu/prepare_source.py': 'caca3a6d529eea4cec70489aaffa2625b43b4cbce678a5fd5949f72b2357fd59',
 'qemu/nikon-z30.c': '0d20bd467fd92d0d1c62ebb3516fad29e6bc43c53ea996d25d0ece2c8edbb83b',
 'qemu/nikon-z30-display.c': '1ec30e9d5b0548521fc35b9847d72f8cca8132407a48806845d86557bb268eb1',
 'qemu/nikon-z30-gpu.h': 'd2294dc84a7b05cc29c124459a58f48cc3f82dfa253ce8c555b00ecfef2d5977',
 'qemu/nikon-z30-isp.h': '62fca2e2ae1c8de978d3795985b75391133f870738d87eba0258c05d10e0e53a',
 'qemu/nikon-z30-isp-pixels.h': 'a5554b9e7801e0061fc35dc19b9d73bb6bb9ddd3e81993271440cfc31deeca27',
 'qemu/z30-display-map.h': '3ede39d73b844c0f7ed3998d2b143deec5a89ab7ae8c88f957230e89c7a788c7',
}
def digest(path):
    if not path.is_file() or path.is_symlink() or path.stat().st_nlink != 1:
        raise ValueError('Build input must be a regular non-hardlinked file')
    value = hashlib.sha256()
    with path.open('rb') as stream:
        while block := stream.read(1024**2): value.update(block)
    return value.hexdigest()
for name, value in expected.items():
    if digest(project / name) != value: raise ValueError('Reviewed Z30 build source changed: ' + name)
if digest(archive) != '821b545b92f165e57dddccac5077d76d4d436a226595b8813ad59306bbfd0746':
    raise ValueError('Original QEMU 10.2.4 archive checksum differs')
if work.exists():
    raise ValueError('Choose a new work directory; existing Z7/Z30 builds are preserved')
environment = {k: v for k, v in os.environ.items() if k not in ('PYTHONPATH', 'PYTHONHOME', 'PYTHONSAFEPATH')}
environment['PYTHONNOUSERSITE'] = '1'
subprocess.run([sys.executable, '-I', '-S', '-B', str(project / 'tools/build.py'), '--work-dir', str(work),
                '--archive', str(archive), '--jobs', '2'], check=True, env=environment, cwd=project)
PY
