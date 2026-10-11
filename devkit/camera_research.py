"""Transactional offline extraction and explicit camera support milestones."""
from __future__ import annotations

import hashlib
from pathlib import Path
import stat
import tempfile

from .core import GuestLock, Task, read_json, write_json
from .camera_formats import identify_camera, reader_for

MAX_FILES = 4096
MAX_OUTPUT = 4 * 1024**3


def _hash(path: Path, task: Task) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024**2), b''):
            task.check()
            digest.update(block)
    return digest.hexdigest()


def unpack_firmware(source: Path, destination: Path, task: Task | None = None,
                    *, key_file: Path | None = None) -> Path:
    """Create a new directory only after all extraction checks succeed.

    Imported executables, filesystem device nodes and archive links are never
    executed or installed. Extractors write fixed component names, not a chroot.
    """
    from .inspection import inspect_firmware

    task = task or Task()
    source, destination = Path(source).resolve(), Path(destination)
    if destination.is_symlink():
        raise ValueError('Extraction destination must not be a symbolic link')
    destination = destination.resolve()
    task.check()
    if destination.exists():
        raise FileExistsError('Choose a new extraction directory; existing files are preserved / 请使用新的解包目录')
    original = inspect_firmware(source, task)
    identity = original.get('camera') or identify_camera(source, task)
    if not identity:
        raise ValueError('No reviewed camera extractor matches this file / 尚无匹配的相机解包器')
    if not identity.get('extractable'):
        reason = '; '.join(identity.get('blockers', []))
        raise ValueError(f'This format is inspection-only / 此格式目前仅支持分析: {reason}')
    reader = reader_for(identity)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='camera-unpack-', dir=destination.parent) as temporary:
        stage = Path(temporary) / 'components'
        stage.mkdir()
        task.report(f"Offline extraction / 离线解包: {identity['brand']} {identity.get('model') or identity['format']}")
        options = {'key_file': key_file} if identity['id'] == 'nikon-update' else {}
        details = reader.extract(source, stage, task, **options)
        artifacts, total = [], 0
        for path in sorted(stage.rglob('*')):
            task.check()
            mode = path.lstat().st_mode
            if not path.resolve().is_relative_to(stage.resolve()) or path.is_symlink():
                raise ValueError('Extractor produced an escaping artifact or link')
            if stat.S_ISDIR(mode):
                continue
            if not stat.S_ISREG(mode) or path.stat().st_nlink != 1:
                raise ValueError('Extractor produced a non-regular artifact')
            total += path.stat().st_size
            if total > MAX_OUTPUT or len(artifacts) >= MAX_FILES:
                raise ValueError('Extraction output exceeds the workbench limit')
            report = inspect_firmware(path, task)
            artifacts.append({'path': path.relative_to(stage).as_posix(),
                              'size': report['size'], 'sha256': report['sha256'],
                              'format': report['format'], 'architecture': report['architecture'],
                              'candidates': report['candidates'], 'findings': report['findings']})
        if not artifacts:
            raise ValueError('Extractor returned no artifacts')
        if _hash(source, task) != original['sha256']:
            raise ValueError('Input changed during extraction; retry with an unchanged copy')
        data = {'schema_version': 1, 'source_filename': original['filename'],
                'source_sha256': original['sha256'], 'source_size': original['size'],
                'camera': identity, 'method': details.get('method', identity['id']),
                'checks': details.get('checks', []), 'notes': details.get('notes', []),
                'artifacts': artifacts, 'total_output_bytes': total,
                'milestones': {'container_validated': True, 'components_extracted': True,
                               'kernel_booted': False, 'userspace_booted': False,
                               'original_ui_verified': False}}
        write_json(stage / 'extraction.json', data)
        task.check()
        stage.rename(destination)
    task.report('Extracted and checked; original system boot is unverified / 解包与校验完成，原厂系统启动尚未验证')
    return destination


def unpack_device(device: Path, task: Task | None = None,
                  *, key_file: Path | None = None) -> Path:
    device = Path(device).resolve()
    profile = read_json(device / 'device.json')
    inspection = read_json(device / 'inspection.json')
    if profile.get('adapter') != 'generic':
        raise ValueError('Select an imported generic camera firmware / 请选择导入的通用相机固件')
    from .core import checked_child
    source = checked_child(device, inspection.get('source_path', 'source/firmware.bin'))
    destination = checked_child(device, 'extracted')
    if (device / 'extracted').is_symlink():
        raise ValueError('Extraction destination must not be a symbolic link')
    task = task or Task()
    if _hash(source, task) != profile.get('sha256'):
        raise ValueError('Library source no longer matches its recorded SHA-256')
    with GuestLock(device):
        result = unpack_firmware(source, destination, task, key_file=key_file)
        from .support import create_support
        create_support(device)
    return result
