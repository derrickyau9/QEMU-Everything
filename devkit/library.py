"""Transactional firmware library with installed adapter extension points."""
from importlib.metadata import entry_points
from pathlib import Path
import hashlib
import tempfile
from .core import Task, read_json, write_json
from .inspection import inspect_firmware
from .boot import boot_template


def is_camera(profile):
    # Older imported libraries predate the adapter field.
    return profile.get('adapter', 'hasselblad-ui') == 'hasselblad-ui'


def adapters():
    """Only explicitly installed Python packages may extend ingestion.

    An entry point in qemu_everything.adapters exposes id, label,
    matches(inspection) and import_firmware(source, library, task).
    Firmware files themselves never contain executable host adapters.
    """
    result = []
    for entry in entry_points(group='qemu_everything.adapters'):
        adapter = entry.load()
        if isinstance(adapter, type):
            adapter = adapter()
        if not all(hasattr(adapter, key) for key in ('id', 'label', 'matches', 'import_firmware')):
            raise ValueError(f'Invalid installed adapter: {entry.name}')
        result.append(adapter)
    return result


def import_firmware(source, library, task=None):
    task = task or Task()
    source, library = Path(source), Path(library)
    report = inspect_firmware(source, task)
    digest = report['sha256']
    device = library / digest[:16]
    if (device / 'device.json').is_file():
        previous = read_json(device / 'device.json')
        if previous.get('sha256') != digest:
            raise ValueError('Library identifier collision; original input unchanged')
        task.report('Already in library / 已在固件库中')
        return device
    from .firmware import PROFILES, import_firmware as import_camera
    if digest in PROFILES:
        device = import_camera(source, library, task)
        report['adapter'] = 'hasselblad-ui'
        write_json(device / 'inspection.json', report)
        return device
    from .gopro_firmware import supported_source, import_update
    if supported_source(digest):
        return import_update(source, device, task, inspection=report)
    from .ricoh_ui import supported_source as ricoh_supported, import_update as import_ricoh
    if ricoh_supported(digest):
        return import_ricoh(source, device, task, inspection=report)
    for adapter in adapters():
        if adapter.matches(report):
            task.report(f'Adapter / 适配器: {adapter.label}')
            return Path(adapter.import_firmware(source, library, task))
    library.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='inspect-', dir=library) as temporary:
        stage = Path(temporary) / 'prepared'
        (stage / 'source').mkdir(parents=True)
        # A fixed basename avoids Windows aliases and QEMU option delimiters.
        saved = stage / 'source/firmware.bin'
        copied = hashlib.sha256()
        task.report('Preserving local input / 保存本地输入副本…')
        with source.open('rb') as src, saved.open('xb') as dest:
            for block in iter(lambda: src.read(1024**2), b''):
                task.check(); copied.update(block); dest.write(block)
        if copied.hexdigest() != digest:
            raise ValueError('Input changed while importing; retry')
        report['source_path'] = 'source/firmware.bin'
        write_json(stage / 'inspection.json', report)
        write_json(stage / 'boot.json', boot_template(report))
        write_json(stage / 'device.json', {
            'schema_version': 1, 'adapter': 'generic', 'name': source.name,
            'version': report['format'], 'sha256': digest, 'filename': source.name,
            'architecture': report.get('architecture'), 'validated': False,
            'support_status': report['status'],
            'camera': report.get('camera'),
        })
        from .support import create_support
        create_support(stage)
        task.check(); stage.rename(device)
    task.report('Imported with support report / 已导入并生成适配报告')
    return device
