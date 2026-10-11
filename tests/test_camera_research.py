"""Cross-reader CLI and transaction regressions; fixtures contain no firmware."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace
import zipfile

import pytest

from devkit import camera_formats, camera_research, cli
from devkit.core import Cancelled, Task, read_json, write_json
from devkit.inspection import inspect_firmware
from devkit.library import import_firmware
from devkit.support import create_support, diagnose, export_support

from test_camera_canon import _fir
from test_camera_insta360 import make_firmware as insta_firmware
from test_camera_ricoh import _container, _literal
from test_camera_sony import make_firmware as sony_firmware, dat_from_encrypted


def _fixture(brand):
    if brand == 'canon':
        return _fir()
    if brand == 'insta360':
        return insta_firmware()
    if brand == 'ricoh':
        return _container(_literal(b'CameraTestAB'), b'CameraTestAB')[0]
    if brand == 'sony':
        return sony_firmware()[0]
    raise AssertionError(brand)


def _source(tmp_path, brand='canon'):
    source = tmp_path / 'sample.bin'
    source.write_bytes(_fixture(brand))
    return source


def _no_process(*args, **kwargs):
    raise AssertionError('Offline import/extraction attempted to execute a host process')


def _clean(parent, destination):
    assert not destination.exists()
    assert not list(parent.glob('camera-unpack-*'))


@pytest.mark.parametrize('brand', ['canon', 'insta360', 'ricoh', 'sony'])
def test_reader_contract_and_cli_library_flow(tmp_path, brand, capsys, monkeypatch):
    source = _source(tmp_path, brand)
    original = source.read_bytes()
    monkeypatch.setattr(subprocess, 'Popen', _no_process)
    identity = camera_formats.identify_camera(source, Task(lambda _: None))
    reader = camera_formats.reader_for(identity)
    assert identity['id'] in reader.FORMAT_IDS
    assert callable(reader.extract) and identity['extractable']
    home = tmp_path / 'home'
    assert cli.main(['--home', str(home), 'inspect', str(source)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report['camera']['id'] == identity['id']
    assert cli.main(['--home', str(home), 'import', str(source)]) == 0
    device = Path(capsys.readouterr().out.strip())
    assert read_json(device / 'device.json')['adapter'] == 'generic'
    assert cli.main(['--home', str(home), 'unpack-device', str(device)]) == 0
    capsys.readouterr()
    manifest = read_json(device / 'extracted/extraction.json')
    assert manifest['source_sha256'] == hashlib.sha256(original).hexdigest()
    assert manifest['milestones'] == {
        'container_validated': True, 'components_extracted': True,
        'kernel_booted': False, 'userspace_booted': False, 'original_ui_verified': False,
    }
    assert all(not Path(artifact['path']).is_absolute() and '..' not in Path(artifact['path']).parts
               for artifact in manifest['artifacts'])
    assert diagnose(device)['components_extracted']
    assert not diagnose(device)['guest_boot_verified']
    assert source.read_bytes() == original
    assert (device / 'source/firmware.bin').read_bytes() == original
    # Repeat refuses to replace the existing reviewed extraction.
    manifest_before = (device / 'extracted/extraction.json').read_bytes()
    assert cli.main(['unpack-device', str(device)]) == 1
    assert (device / 'extracted/extraction.json').read_bytes() == manifest_before


def test_unknown_executable_is_never_run_or_unpacked(tmp_path, monkeypatch):
    source = tmp_path / 'untrusted.exe'
    original = b'MZ unknown camera installer; exit 99'
    source.write_bytes(original)
    monkeypatch.setattr(subprocess, 'Popen', _no_process)
    device = import_firmware(source, tmp_path / 'library', Task(lambda _: None))
    assert not read_json(device / 'inspection.json').get('camera')
    with pytest.raises(ValueError, match='No reviewed'):
        camera_research.unpack_device(device, Task(lambda _: None))
    assert source.read_bytes() == original
    assert not (device / 'extracted').exists()


def test_later_checksum_failure_removes_partial_transaction(tmp_path):
    _, encrypted = sony_firmware()
    damaged = bytearray(encrypted)
    damaged[2048] ^= 1
    source = tmp_path / 'BODYDATA.DAT'
    source.write_bytes(dat_from_encrypted(damaged))
    original = source.read_bytes()
    assert camera_formats.identify_camera(source, Task(lambda _: None))['extractable']
    destination = tmp_path / 'out'
    with pytest.raises(ValueError, match='checksum'):
        camera_research.unpack_firmware(source, destination, Task(lambda _: None))
    _clean(tmp_path, destination)
    assert source.read_bytes() == original


def test_source_change_during_extraction_rolls_back(tmp_path, monkeypatch):
    source = _source(tmp_path)
    destination = tmp_path / 'out'
    reader = camera_formats.reader_for(camera_formats.identify_camera(source, Task(lambda _: None)))
    original_extract = reader.extract

    def change_source(path, stage, task):
        details = original_extract(path, stage, task)
        with path.open('ab') as stream:
            stream.write(b'changed')
        return details

    monkeypatch.setattr(reader, 'extract', change_source)
    with pytest.raises(ValueError, match='Input changed'):
        camera_research.unpack_firmware(source, destination, Task(lambda _: None))
    _clean(tmp_path, destination)


def test_cancellation_rolls_back_after_partial_output(tmp_path, monkeypatch):
    source = _source(tmp_path)
    destination = tmp_path / 'out'

    def cancel(source, stage, task):
        (stage / 'partial.bin').write_bytes(b'partial')
        task.cancelled.set()
        task.check()

    monkeypatch.setattr(camera_research, 'reader_for', lambda _: SimpleNamespace(extract=cancel))
    with pytest.raises(Cancelled):
        camera_research.unpack_firmware(source, destination, Task(lambda _: None))
    _clean(tmp_path, destination)


@pytest.mark.parametrize('kind', ['file-symlink', 'directory-symlink', 'hardlink'])
def test_transaction_rejects_links_before_inspection(tmp_path, monkeypatch, kind):
    source = _source(tmp_path)
    destination = tmp_path / 'out'
    outside = tmp_path / 'outside'
    outside.mkdir()
    kept = outside / 'keep.txt'
    kept.write_text('preserved')

    def linked(source, stage, task):
        try:
            if kind == 'hardlink':
                os.link(kept, stage / 'artifact.bin')
            elif kind == 'directory-symlink':
                (stage / 'link').symlink_to(outside, target_is_directory=True)
            else:
                (stage / 'link.bin').symlink_to(kept)
        except OSError as error:
            pytest.skip(f'Host does not permit this link fixture: {error}')
        return {}

    monkeypatch.setattr(camera_research, 'reader_for', lambda _: SimpleNamespace(extract=linked))
    with pytest.raises(ValueError, match='link|non-regular'):
        camera_research.unpack_firmware(source, destination, Task(lambda _: None))
    _clean(tmp_path, destination)
    assert kept.read_text() == 'preserved'


def test_output_limit_and_empty_reader_roll_back(tmp_path, monkeypatch):
    source = _source(tmp_path)
    destination = tmp_path / 'out'
    monkeypatch.setattr(camera_research, 'MAX_FILES', 0)
    with pytest.raises(ValueError, match='limit'):
        camera_research.unpack_firmware(source, destination, Task(lambda _: None))
    _clean(tmp_path, destination)
    monkeypatch.setattr(camera_research, 'reader_for', lambda _: SimpleNamespace(extract=lambda *_: {}))
    with pytest.raises(ValueError, match='no artifacts'):
        camera_research.unpack_firmware(source, destination, Task(lambda _: None))
    _clean(tmp_path, destination)


def test_existing_output_and_input_path_are_preserved(tmp_path):
    source = _source(tmp_path)
    before = source.read_bytes()
    with pytest.raises(FileExistsError):
        camera_research.unpack_firmware(source, source, Task(lambda _: None))
    assert source.read_bytes() == before
    destination = tmp_path / 'out'
    destination.mkdir()
    kept = destination / 'keep.bin'
    kept.write_bytes(b'kept')
    with pytest.raises(FileExistsError):
        camera_research.unpack_firmware(source, destination, Task(lambda _: None))
    assert kept.read_bytes() == b'kept'


def test_library_source_path_escape_is_rejected(tmp_path):
    source = _source(tmp_path)
    device = import_firmware(source, tmp_path / 'library', Task(lambda _: None))
    inspection = read_json(device / 'inspection.json')
    inspection['source_path'] = '../../sample.bin'
    write_json(device / 'inspection.json', inspection)
    with pytest.raises(ValueError, match='Unsafe|escapes'):
        camera_research.unpack_device(device, Task(lambda _: None))


def test_path_replacement_during_identification_is_rejected(tmp_path, monkeypatch):
    target = _source(tmp_path)
    source = tmp_path / 'alias.bin'
    try:
        source.symlink_to(target)
    except OSError as error:
        pytest.skip(f'Host does not permit the path-replacement link fixture: {error}')

    def replace_path(path, task):
        changed = path.with_suffix('.changed')
        changed.write_bytes(b'new and different source')
        changed.replace(path)
        return None

    monkeypatch.setattr(camera_formats, 'identify_camera', replace_path)
    with pytest.raises(ValueError, match='changed'):
        inspect_firmware(source, Task(lambda _: None))


def test_library_dangling_output_link_cannot_escape(tmp_path):
    source = _source(tmp_path)
    device = import_firmware(source, tmp_path / 'library', Task(lambda _: None))
    outside = tmp_path / 'outside-output'
    try:
        (device / 'extracted').symlink_to(outside, target_is_directory=True)
    except OSError as error:
        pytest.skip(f'Host does not permit symbolic links: {error}')
    with pytest.raises((ValueError, FileExistsError)):
        camera_research.unpack_device(device, Task(lambda _: None))
    assert not outside.exists()


def test_support_export_does_not_follow_extraction_parent_link(tmp_path):
    source = _source(tmp_path)
    device = import_firmware(source, tmp_path / 'library', Task(lambda _: None))
    outside = tmp_path / 'outside'
    outside.mkdir()
    private = outside / 'extraction.json'
    private.write_text('{"private":"must not be exported"}')
    try:
        (device / 'extracted').symlink_to(outside, target_is_directory=True)
    except OSError as error:
        pytest.skip(f'Host does not permit symbolic links: {error}')
    archive = tmp_path / 'support.zip'
    try:
        export_support(device, archive)
    except ValueError:
        assert not archive.exists()
    else:
        with zipfile.ZipFile(archive) as bundle:
            assert 'extracted/extraction.json' not in bundle.namelist()


def test_support_parent_link_cannot_write_outside_device(tmp_path):
    source = _source(tmp_path)
    device = import_firmware(source, tmp_path / 'library', Task(lambda _: None))
    support = device / 'support'
    for child in support.iterdir():
        child.unlink()
    support.rmdir()
    outside = tmp_path / 'outside'
    outside.mkdir()
    kept = outside / 'diagnostics.json'
    kept.write_text('{"private":"preserve"}')
    try:
        support.symlink_to(outside, target_is_directory=True)
    except OSError as error:
        pytest.skip(f'Host does not permit symbolic links: {error}')
    with pytest.raises(ValueError, match='Unsafe|escapes|link'):
        create_support(device)
    assert kept.read_text() == '{"private":"preserve"}'
    assert not (outside / 'report.md').exists()


def test_support_does_not_report_removed_components_as_available(tmp_path):
    source = _source(tmp_path)
    device = import_firmware(source, tmp_path / 'library', Task(lambda _: None))
    camera_research.unpack_device(device, Task(lambda _: None))
    manifest = read_json(device / 'extracted/extraction.json')
    (device / 'extracted' / manifest['artifacts'][0]['path']).unlink()
    assert not diagnose(device)['components_extracted']


@pytest.mark.skipif('nikon' not in camera_formats.MODULES, reason='Nikon reader registration pending')
def test_nikon_key_file_cli_contract_and_no_key_export(tmp_path, capsys):
    from test_camera_nikon import _keys, _body, _encrypt
    key, tables = _keys(tmp_path)
    source = tmp_path / 'Z_30_0120.bin'
    source.write_bytes(_encrypt(_body(), tables))
    device = import_firmware(source, tmp_path / 'library', Task(lambda _: None))
    assert read_json(device / 'inspection.json')['camera']['key_required']
    assert cli.main(['unpack-device', str(device)]) == 1
    assert not (device / 'extracted').exists()
    assert cli.main(['unpack-device', str(device), '--key-file', str(key)]) == 0
    capsys.readouterr()
    manifest = (device / 'extracted/extraction.json').read_text()
    assert str(key) not in manifest and 'self_check' not in manifest
    assert (device / 'extracted/module-002.bin').read_bytes() == b'main payload'
    bundle = export_support(device, tmp_path / 'support.zip')
    with zipfile.ZipFile(bundle) as archive:
        assert not any('key' in name.lower() for name in archive.namelist())


def test_gui_unknown_and_supported_camera_states(tmp_path, monkeypatch):
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtWidgets import QApplication
    from devkit.application import Window
    app = QApplication.instance() or QApplication([])
    home = tmp_path / 'home'
    source = _source(tmp_path)
    device = import_firmware(source, home / 'library', Task(lambda _: None))
    monkeypatch.setattr('devkit.application.discover', lambda *_: None)
    monkeypatch.setattr('devkit.application.discover_qemu', lambda *_: None)
    window = Window(home)
    try:
        assert window.device == device
        assert window.unpack_button.isEnabled()
        assert not window.start.isEnabled()
        camera_research.unpack_device(device, Task(lambda _: None))
        window.update_controls()
        assert not window.unpack_button.isEnabled()
        monkeypatch.setattr(window, 'busy', lambda: True)
        window.update_controls()
        assert not window.report_button.isEnabled() and not window.export_button.isEnabled()
    finally:
        window.timer.stop()
        # Avoid a patched busy() blocking the standard close path.
        monkeypatch.setattr(window, 'busy', lambda: False)
        window.close()
        app.processEvents()


@pytest.mark.parametrize('key_choice', ['selected', 'home-default', 'cancel'])
def test_gui_nikon_key_selection_reaches_only_local_unpack(tmp_path, monkeypatch, key_choice):
    from test_camera_nikon import _keys, _body, _encrypt
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtWidgets import QApplication
    from devkit.application import Window
    app = QApplication.instance() or QApplication([])
    home = tmp_path / 'home'
    key, tables = _keys(tmp_path)
    source = tmp_path / 'Z_30_0120.bin'
    source.write_bytes(_encrypt(_body(), tables))
    device = import_firmware(source, home / 'library', Task(lambda _: None))
    monkeypatch.setattr('devkit.application.discover', lambda *_: None)
    monkeypatch.setattr('devkit.application.discover_qemu', lambda *_: None)
    selected = []
    if key_choice == 'home-default':
        expected = home / 'camera-keys/nikon-tables.json'
        expected.parent.mkdir(parents=True)
        expected.write_bytes(key.read_bytes())
    else:
        expected = key

    def choose(*_):
        selected.append(True)
        return ('', '') if key_choice == 'cancel' else (str(key), 'JSON (*.json)')

    monkeypatch.setattr('devkit.application.QFileDialog.getOpenFileName', choose)
    calls = []

    def unpack(path, task, *, key_file):
        calls.append((path, key_file))
        return path / 'extracted'

    monkeypatch.setattr(camera_research, 'unpack_device', unpack)
    window = Window(home)
    monkeypatch.setattr(window, 'run_job', lambda work, done: work(Task(lambda _: None)))
    try:
        assert window.device == device and window.unpack_button.isEnabled()
        window.unpack_camera()
        assert selected == ([] if key_choice == 'home-default' else [True])
        assert calls == ([] if key_choice == 'cancel' else [(device, expected)])
        assert not (device / 'extracted').exists()
    finally:
        window.timer.stop()
        window.close()
        app.processEvents()
