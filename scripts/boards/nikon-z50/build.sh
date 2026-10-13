#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
set -euo pipefail
if [ "$#" -lt 2 ] || [ "$#" -gt 3 ]; then
    echo 'Usage: build.sh NEW_PROFILE_DIRECTORY NEW_BUILD_DIRECTORY [QEMU_10.2.4_ARCHIVE]' >&2
    exit 2
fi
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
python3 -I -S - "$script_dir" "$@" <<'PY'
from pathlib import Path
import hashlib,json,os,shutil,subprocess,sys
family=Path(sys.argv[1]).absolute();project=Path(sys.argv[2]).absolute();work=Path(sys.argv[3]).absolute()
for path in (family,project,work):
    if '..' in path.parts or any(p.is_symlink() for p in (path,*path.parents)):
        raise ValueError('Build paths must not contain parent components or symlinks')
if project.exists() or work.exists():raise ValueError('Select new independent Z50 profile and build directories')
profile=family/'profile-source-hashes.json'
if profile.stat().st_size > 16384 or hashlib.sha256(profile.read_bytes()).hexdigest() != 'a96c9ac647c07968e0ccc7d328285c6d5d7223cd2192e455b12654aef5150faa':
    raise ValueError('Z50 public source manifest changed')
meta=json.loads(profile.read_text())
expected={**meta['scripts'],**meta['boards'],'tools/build.py':meta['builder_sha256']}
# Reject links, unexpected files and changed sources before creating output.
for name,wanted in expected.items():
    source=family/'source'/name
    if any(p.is_symlink() for p in (source,*source.parents)) or not source.is_file() or source.stat().st_nlink!=1:
        raise ValueError('Linked or missing Z50 public source')
    if hashlib.sha256(source.read_bytes()).hexdigest()!=wanted:raise ValueError('Z50 public source changed: '+name)
project.mkdir(parents=True,exist_ok=False)
for name in expected:
    destination=project/name;destination.parent.mkdir(parents=True,exist_ok=True)
    with (family/'source'/name).open('rb') as r,destination.open('xb') as w:shutil.copyfileobj(r,w)
environment={k:v for k,v in os.environ.items() if k not in ('PYTHONPATH','PYTHONHOME')}
environment.update(PYTHONSAFEPATH='1',PYTHONNOUSERSITE='1')
command=[sys.executable,'-I','-S','-B',str(project/'tools/build.py'),'--work-dir',str(work),'--jobs','2']
if len(sys.argv)==5:command+=['--archive',str(Path(sys.argv[4]).absolute())]
subprocess.run(command,check=True,env=environment)
manifest=project/'.local/build-manifest.json'
built=json.loads(manifest.read_text());built['source_revision']=meta['revision'];built['adapter_revision']='z50-c260-1'
manifest.write_text(json.dumps(built,indent=2)+'\n')
print('Configured Z50 source project:',project)
print('Use nikon-z50-lab.json with revision, adapter_revision, project and distro; see README.')
PY
