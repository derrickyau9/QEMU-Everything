"""Build a portable Windows folder: Python and Qt included, no firmware bundled."""
from pathlib import Path
import hashlib
import shutil
import subprocess
import sys
import zipfile
import argparse

root=Path(__file__).resolve().parents[1]
p=argparse.ArgumentParser();p.add_argument('--name',default='QEMU-Everything');args=p.parse_args()
name=args.name
if not name.replace('-','').isalnum(): raise SystemExit('Invalid build name')
board_data = []
board_files = []
# Use the same public Git inventory as the source bundle. In particular, do
# not collect JSON/Markdown from ignored board-local build or research caches.
sources=subprocess.check_output(['git','ls-files','--cached','--others','--exclude-standard','-z'],cwd=root).decode().split('\0')
for file in sorted(set(sources)):
    if not file or not file.startswith('scripts/boards/'):
        continue
    path = root / file
    if path.is_file() and '__pycache__' not in path.parts and (path.suffix in ('.py', '.sh', '.c', '.h', '.inc', '.dts', '.md', '.json', '.S', '.ld') or path.name == 'COPYING'):
        board_files.append(path)
        board_data.extend(['--add-data', f'{path.relative_to(root)};{path.parent.relative_to(root)}'])
subprocess.run([sys.executable,'-m','PyInstaller','--noconfirm','--clean','--windowed','--onedir',
    '--name',name,'--collect-submodules','dissect.extfs','--collect-submodules','dissect.cstruct',
    '--collect-submodules','devkit.camera_formats',
    '--exclude-module','unicorn',
    '--add-data','devkit/assets;devkit/assets','--add-data','devkit/helpers;devkit/helpers',
    '--add-data','devkit/templates;devkit/templates','--add-data','devkit/guest/camera_run.sh;devkit/guest',
    '--add-data','devkit/guest/dbus-local.conf;devkit/guest','--add-data','devkit/downloads.json;devkit',
    '--add-data','devkit/guest/legacy_run.sh;devkit/guest','--add-data','devkit/guest/linux_ui.sh;devkit/guest',
    '--add-data','devkit/guest/network.sh;devkit/guest',
    '--add-data','devkit/linux-downloads.json;devkit',
    '--add-data','devkit/camera_catalog.json;devkit',
    *board_data,
    '--add-data','devkit/camera_formats/_sony_vendor/LICENSE.txt;devkit/camera_formats/_sony_vendor',
    'run_app.py'],cwd=root,check=True)
output=root/'dist'/name
for file in ('LICENSE','README.md','README.zh-CN.md','THIRD_PARTY_NOTICES.md'):
    shutil.copy2(root/file,output/file)
shutil.copytree(root/'third-party-licenses',output/'third-party-licenses',dirs_exist_ok=True)
shutil.copytree(root/'docs',output/'docs',dirs_exist_ok=True)
# Keep the public docs' relative links readable outside the frozen interpreter.
for path in board_files:
    target = output / path.relative_to(root)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, target)
# Corresponding project source alongside the binary; no personal caches, firmware or SDK images.
firmware_suffixes = {'.bin', '.dat', '.fir', '.rom', '.cim', '.img', '.qcow2', '.raw', '.dtb'}
with zipfile.ZipFile(output/'DevKit-source.zip','w',zipfile.ZIP_DEFLATED) as z:
    for file in sources:
        if file and Path(file).suffix.lower() not in firmware_suffixes and (root/file).is_file():
            z.write(root/file,file)
archive=Path(shutil.make_archive(str(root/'dist'/f'{name}-Windows-x64'),'zip',root/'dist',name))
digest=hashlib.sha256(archive.read_bytes()).hexdigest()
checksum_name='SHA256SUMS.txt' if name in ('QEMU-Everything','X2DII-DevKit') else f'{name}-SHA256SUMS.txt'
(root/'dist'/checksum_name).write_text(f'{digest}  {archive.name}\n',encoding='ascii')
print(archive)
print(digest)
