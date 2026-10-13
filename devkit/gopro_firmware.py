"""Import the exact user-supplied HERO8 2.51 update for the original UI lab."""
from pathlib import Path
import hashlib
import stat
import struct
import tempfile
import time
import zipfile

from .core import Task, checked_child, write_json

DATA_SHA256 = '220b91d289bc458d2580d8805a079687ba6e73eeb18c94b126a9d61f1058c6ba'
DATA_SIZE = 194069088
ZIP_SHA256 = 'f5db70d5377e109a178be4bd17e2a872d938f450190d66209900d8b8ea5886ef'
ZIP_SIZE = 72310248
RTOS_SHA256 = '49e0d40253f517fc6512f4bc9ff59a9d63c9a86e7cd84a6b0c97f523278de2e5'
RTOS_SIZE = 70503296
PROFILE = 'gopro-hero8-tara-ui-v1'


def supported_source(digest):
    return digest in (DATA_SHA256, ZIP_SHA256)


def _regular(path):
    path = Path(path).absolute()
    if '..' in path.parts:
        raise ValueError('Parent component in GoPro input path')
    for item in (path, *path.parents):
        try:
            meta = item.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(meta.st_mode) or getattr(meta, 'st_file_attributes', 0) & 0x400:
            raise ValueError('GoPro path contains a symlink/junction')
        if stat.S_ISREG(meta.st_mode) and meta.st_nlink != 1:
            raise ValueError('GoPro input is hardlinked')
    return path


def _copy_verified(source, destination, size, digest, task):
    value = hashlib.sha256(); copied = 0
    with destination.open('xb') as output:
        while True:
            task.check()
            block = source.read(min(1024**2, size - copied + 1))
            if not block:
                break
            copied += len(block)
            if copied > size:
                raise ValueError('GoPro input exceeds its exact extent')
            output.write(block); value.update(block)
    if copied != size or value.hexdigest() != digest:
        raise ValueError('GoPro input does not match the exact HERO8 2.51 profile')


def _extract_rtos(data_path, destination, task):
    """Walk every bounded MILBEAUT extent; preserve only the original RTOS."""
    position = 0; ids = set(); volumes = []; selected = False
    with data_path.open('rb') as source:
        while position < DATA_SIZE:
            task.check()
            header = source.read(16)
            if len(header) != 16:
                raise ValueError('Truncated MILBEAUT header')
            magic, kind, ident, size = struct.unpack('<8sHHI', header)
            position += 16
            if magic != b'MILBEAUT' or ident in ids or size > DATA_SIZE - position:
                raise ValueError('Invalid MILBEAUT volume extent or duplicate ID')
            ids.add(ident)
            volumes.append(dict(id=ident, type=kind, offset=position, size=size))
            if ident == 4:
                if kind != 4 or size != RTOS_SIZE:
                    raise ValueError('Unexpected GoPro RTOS volume')
                value = hashlib.sha256(); remaining = size
                with destination.open('xb') as output:
                    while remaining:
                        task.check(); block = source.read(min(1024**2, remaining))
                        if not block:
                            raise ValueError('Truncated GoPro RTOS')
                        output.write(block); value.update(block); remaining -= len(block)
                if value.hexdigest() != RTOS_SHA256:
                    raise ValueError('Original HERO8 RTOS checksum mismatch')
                selected = True
            else:
                source.seek(size, 1)
            position += size
        if source.read(1) or position != DATA_SIZE or not selected:
            raise ValueError('Incomplete GoPro container')
    return volumes


def import_update(source, destination, task=None, *, inspection=None):
    """Transactional import. Firmware bytes remain in the user's local library."""
    task = task or Task(); source = _regular(source)
    destination = Path(destination).absolute()
    checked_child(destination.parent, destination.name); _regular(destination)
    if destination.exists():
        raise FileExistsError('Choose a new GoPro library entry directory')
    if not source.is_file() or source.stat().st_size not in (ZIP_SIZE, DATA_SIZE):
        raise ValueError('Provide the original HERO8 2.51 UPDATE.zip or DATA.bin')
    digest = hashlib.sha256()
    with source.open('rb') as stream:
        for block in iter(lambda: stream.read(1024**2), b''):
            task.check(); digest.update(block)
    digest = digest.hexdigest()
    if not supported_source(digest):
        raise ValueError('Unknown GoPro update; only the exact HERO8 2.51 profile can run')
    if inspection is not None and inspection.get('sha256') != digest:
        raise ValueError('GoPro input changed after inspection')
    expected_size = ZIP_SIZE if digest == ZIP_SHA256 else DATA_SIZE
    destination.parent.mkdir(parents=True, exist_ok=True)
    task.report('Importing original GoPro HERO8 2.51 / 导入 GoPro HERO8 2.51 原厂固件')
    with tempfile.TemporaryDirectory(prefix='gopro-import-', dir=destination.parent) as temporary:
        stage = Path(temporary) / 'entry'
        (stage / 'source').mkdir(parents=True); (stage / 'extracted').mkdir()
        saved = stage / 'source/firmware.bin'
        with source.open('rb') as stream:
            _copy_verified(stream, saved, expected_size, digest, task)
        data = saved
        if digest == ZIP_SHA256:
            data = Path(temporary) / 'DATA.bin'
            with zipfile.ZipFile(saved) as archive:
                entries = archive.infolist()
                if (len(entries) != 3 or {e.filename for e in entries} != {'CAMFWV.bin', 'DATA.bin', 'FWUPD.TXT'}
                        or any(e.flag_bits & 1 for e in entries)):
                    raise ValueError('Unexpected GoPro update ZIP members')
                entry = archive.getinfo('DATA.bin')
                if entry.file_size != DATA_SIZE:
                    raise ValueError('Unexpected GoPro DATA.bin extent')
                with archive.open(entry) as stream:
                    _copy_verified(stream, data, DATA_SIZE, DATA_SHA256, task)
        volumes = _extract_rtos(data, stage / 'extracted/rtos.bin', task)
        report = dict(inspection or {})
        report.update(source_path='source/firmware.bin', filename=source.name, sha256=digest,
                      size=expected_size, format='gopro-milbeaut', architecture='arm',
                      status='exact-ui-component-profile', adapter=PROFILE,
                      summary='Exact HERO8 2.51 update checked; original Tara RTOS extracted.',
                      blockers=['Original cold boot and physical camera hardware are unverified; configure the separate Tara UI lab.'],
                      camera={'brand': 'GoPro', 'model': 'HERO8 Black', 'version': '2.51', 'extractable': True})
        report.setdefault('candidates', [])
        report.setdefault('findings', [])
        report.setdefault('blockers', [])
        write_json(stage / 'inspection.json', report)
        write_json(stage / 'extracted/extraction.json', dict(
            profile=PROFILE, source_sha256=digest, data_sha256=DATA_SHA256, volumes=volumes,
            method='Exact HERO8 2.51 MILBEAUT volume 4',
            artifacts=[dict(path='rtos.bin', size=RTOS_SIZE, sha256=RTOS_SHA256,
                            format='raw-rtos', architecture='arm')],
            original_ui_verified=False, assisted_startup=True, full_rtos_boot_verified=False))
        write_json(stage / 'device.json', dict(
            schema_version=1, adapter=PROFILE, name='GoPro HERO8 Black 2.51', brand='GoPro',
            model='HERO8 Black', version='2.51', sha256=digest, filename=source.name,
            architecture='arm', validated=False, support_status='exact-ui-component-profile',
            camera=report['camera'], original_ui_verified=False))
        from .boot import boot_template
        write_json(stage / 'boot.json', boot_template(report))
        from .support import create_support
        create_support(stage)
        # Windows antivirus can briefly hold newly written firmware files.
        # Retry the atomic rename only; never replace an existing library entry.
        for attempt in range(10):
            task.check()
            if destination.exists():
                raise FileExistsError('GoPro library entry was created by another importer')
            try:
                stage.rename(destination)
                break
            except PermissionError:
                if attempt == 9:
                    raise
                time.sleep(.1)
    return destination
