import hashlib
import json
import zipfile
from pathlib import Path
import pytest
from devkit.core import Task, Cancelled, read_json, write_json, data_home
from devkit.library import import_firmware, is_camera
from devkit.support import create_support, export_support
from devkit.cli import main

def test_unknown_firmware_is_preserved_and_gets_honest_workspace(tmp_path):
    source = tmp_path / 'unknown.dat'; original = b'proprietary-firmware-with-no-known-header'
    source.write_bytes(original)
    device = import_firmware(source, tmp_path / 'library')
    assert source.read_bytes() == (device / 'source/firmware.bin').read_bytes() == original
    assert read_json(device / 'device.json')['sha256'] == hashlib.sha256(original).hexdigest()
    assert not is_camera(read_json(device / 'device.json'))
    assert read_json(device / 'boot.json')['board'] == ''
    assert not read_json(device / 'support/diagnostics.json')['guest_boot_verified']
    assert (device / 'support/report.md').is_file()
    assert import_firmware(source, tmp_path / 'library') == device

def test_new_camera_route_reuses_validated_adapter(monkeypatch, tmp_path):
    from devkit import firmware
    source = tmp_path / 'camera.cim'; source.write_bytes(b'stand-in')
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    monkeypatch.setitem(firmware.PROFILES, digest, ('camera', '1', 'product'))
    target = tmp_path / 'library' / digest[:16]
    def stage(src, library, task):
        assert src == source
        write_json(target / 'device.json', {'name':'camera','sha256':digest,'version':'1','validated':True})
        return target
    monkeypatch.setattr(firmware, 'import_firmware', stage)
    assert import_firmware(source, tmp_path / 'library') == target
    assert is_camera(read_json(target / 'device.json'))
    assert read_json(target / 'inspection.json')['adapter'] == 'hasselblad-ui'

def test_support_export_excludes_firmware_and_preserves_user_plan(tmp_path):
    source = tmp_path / 'image.bin'; source.write_bytes(b'unknown-image')
    device = import_firmware(source, tmp_path / 'library')
    plan = device / 'support/adapter-plan.json'
    write_json(plan, {'soc':'my-research'})
    (device / 'generic-console.log').write_text('Kernel panic: unable to mount root', encoding='utf-8')
    create_support(device)
    assert read_json(plan) == {'soc':'my-research'}
    assert len(read_json(device / 'support/diagnostics.json')['log_findings']) == 2
    bundle = export_support(device, tmp_path / 'support.zip')
    with zipfile.ZipFile(bundle) as archive:
        assert 'support/diagnostics.json' in archive.namelist()
        assert not any(n.startswith('source/') for n in archive.namelist())

def test_cancelled_generic_import_does_not_leave_library_entry(tmp_path):
    source = tmp_path / 'file.bin'; source.write_bytes(b'unknown')
    task = Task(lambda _:None); task.cancelled.set()
    with pytest.raises(Cancelled): import_firmware(source, tmp_path / 'library', task)
    assert not list((tmp_path / 'library').glob('*/device.json'))

@pytest.mark.parametrize('name', ['boot.json', 'source/firmware.bin', 'support/bundle.zip'])
def test_support_cannot_overwrite_workspace_files(tmp_path, name):
    source = tmp_path / 'image.bin'; source.write_bytes(b'unknown-image')
    device = import_firmware(source, tmp_path / 'library')
    preserved = (device / 'source/firmware.bin').read_bytes()
    with pytest.raises(ValueError): export_support(device, device / name)
    assert (device / 'source/firmware.bin').read_bytes() == preserved
    assert read_json(device / 'boot.json')['schema'] == 1

def test_support_preserves_existing_zip_input(tmp_path):
    source = tmp_path / 'update.zip'; source.write_bytes(b'unknown-image')
    device = import_firmware(source, tmp_path / 'library')
    with pytest.raises(FileExistsError): export_support(device, source)
    assert source.read_bytes() == b'unknown-image'

def test_inspect_cli_emits_json_and_returns_error_for_missing_file(tmp_path, capsys):
    source = tmp_path / 'file.dat'; source.write_bytes(b'unknown')
    assert main(['inspect', str(source)]) == 0
    assert json.loads(capsys.readouterr().out)['filename'] == source.name
    assert main(['inspect', str(tmp_path / 'missing')]) == 1

def test_new_home_setting_has_priority_and_legacy_home_is_preserved(tmp_path, monkeypatch):
    monkeypatch.delenv('QEMU_EVERYTHING_HOME', raising=False)
    monkeypatch.delenv('X2DII_DEVKIT_HOME', raising=False)
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path))
    assert data_home() == (tmp_path / 'QEMU-Everything').resolve()
    (tmp_path / 'X2DII-DevKit').mkdir()
    assert data_home() == (tmp_path / 'X2DII-DevKit').resolve()
    monkeypatch.setenv('QEMU_EVERYTHING_HOME', str(tmp_path / 'override'))
    assert data_home() == (tmp_path / 'override').resolve()

def test_qemu_discovery_requires_selected_architecture(tmp_path, monkeypatch):
    import os
    from devkit.runtime import discover_qemu, setup_qemu
    partial = tmp_path / 'arm-only'; partial.mkdir()
    extension = '.exe' if os.name == 'nt' else ''
    (partial / ('qemu-system-aarch64' + extension)).touch()
    write_json(tmp_path / 'qemu.json', {'qemu':str(partial)})
    monkeypatch.setattr('devkit.runtime.shutil.which', lambda _:None)
    assert discover_qemu(tmp_path)['qemu'] == str(partial.resolve())
    assert discover_qemu(tmp_path, 'qemu-system-x86_64') is None
    complete = tmp_path / 'runtime/qemu'; complete.mkdir(parents=True)
    for binary in ('qemu-system-aarch64','qemu-system-arm','qemu-system-x86_64','qemu-system-riscv64'):
        (complete / (binary + extension)).touch()
    assert discover_qemu(tmp_path, 'qemu-system-x86_64')['qemu'] == str(complete.resolve())
    assert setup_qemu(tmp_path)['qemu'] == str(complete.resolve())
