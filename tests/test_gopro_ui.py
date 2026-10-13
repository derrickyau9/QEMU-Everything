"""Bounded update import and exact-profile UI selection, using authored fixtures."""
import hashlib
import io
import json
import struct
import zipfile

import pytest
from devkit import gopro_firmware as firmware
from devkit.core import Task, Cancelled


@pytest.fixture
def authored_update(tmp_path, monkeypatch):
    rtos = b'authored RTOS input, never executable firmware'
    other = b'other volume'
    data = (struct.pack('<8sHHI', b'MILBEAUT', 3, 0, len(other)) + other +
            struct.pack('<8sHHI', b'MILBEAUT', 4, 4, len(rtos)) + rtos)
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('CAMFWV.bin', b'authored version')
        archive.writestr('DATA.bin', data)
        archive.writestr('FWUPD.TXT', b'authored')
    zipped = output.getvalue()
    for label, payload in [('DATA', data), ('ZIP', zipped), ('RTOS', rtos)]:
        monkeypatch.setattr(firmware, label + '_SIZE', len(payload))
        monkeypatch.setattr(firmware, label + '_SHA256', hashlib.sha256(payload).hexdigest())
    source = tmp_path / 'UPDATE.zip'; source.write_bytes(zipped)
    raw = tmp_path / 'DATA.bin'; raw.write_bytes(data)
    return source, raw, rtos


@pytest.mark.parametrize('container', [0, 1])
def test_import_preserves_container_identity_and_original_rtos(tmp_path, authored_update, container):
    source = authored_update[container]; target = tmp_path / 'entry'
    assert firmware.import_update(source, target) == target
    metadata = json.loads((target / 'device.json').read_text())
    extraction = json.loads((target / 'extracted/extraction.json').read_text())
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    assert metadata['sha256'] == extraction['source_sha256'] == digest
    assert extraction['data_sha256'] == firmware.DATA_SHA256
    assert (target / 'source/firmware.bin').read_bytes() == source.read_bytes()
    assert (target / 'extracted/rtos.bin').read_bytes() == authored_update[2]
    assert metadata['original_ui_verified'] is False
    assert extraction['full_rtos_boot_verified'] is False
    assert [item['id'] for item in extraction['volumes']] == [0, 4]


def test_library_import_auto_selects_exact_gopro_profile(tmp_path, authored_update):
    from devkit.library import import_firmware
    source = authored_update[0]
    target = import_firmware(source, tmp_path / 'library')
    assert target.name == firmware.ZIP_SHA256[:16]
    assert json.loads((target / 'device.json').read_text())['adapter'] == firmware.PROFILE
    assert import_firmware(source, tmp_path / 'library') == target


def test_changed_same_size_source_does_not_create_entry(tmp_path, authored_update):
    source = authored_update[1]; data = source.read_bytes()
    source.write_bytes(data[:-1] + bytes([data[-1] ^ 1]))
    with pytest.raises(ValueError, match='Unknown'):
        firmware.import_update(source, tmp_path / 'entry')
    assert not (tmp_path / 'entry').exists()


def test_wrong_rtos_digest_rolls_back_without_overwriting_other_files(tmp_path, authored_update, monkeypatch):
    monkeypatch.setattr(firmware, 'RTOS_SHA256', '0' * 64)
    sentinel = tmp_path / 'keep'; sentinel.write_bytes(b'keep')
    with pytest.raises(ValueError, match='RTOS checksum'):
        firmware.import_update(authored_update[0], tmp_path / 'entry')
    assert sentinel.read_bytes() == b'keep'
    assert not (tmp_path / 'entry').exists()
    assert not list(tmp_path.glob('gopro-import-*'))


def test_stale_inspection_rejected(tmp_path, authored_update):
    with pytest.raises(ValueError, match='after inspection'):
        firmware.import_update(authored_update[0], tmp_path / 'entry', inspection={'sha256': '0' * 64})
    assert not (tmp_path / 'entry').exists()


def test_cancelled_import_does_not_publish_entry(tmp_path, authored_update):
    task = Task(); task.cancelled.set()
    with pytest.raises(Cancelled):
        firmware.import_update(authored_update[0], tmp_path / 'entry', task)
    assert not (tmp_path / 'entry').exists()


def test_volume_length_cannot_escape_input(tmp_path, authored_update):
    source = authored_update[1]; data = bytearray(source.read_bytes())
    struct.pack_into('<I', data, 12, len(data) + 1)
    source.write_bytes(data)
    with pytest.raises(ValueError, match='extent'):
        firmware._extract_rtos(source, tmp_path / 'rtos.bin', Task())


def test_gopro_ui_route_requires_exact_eligibility_and_config(tmp_path, monkeypatch):
    from devkit import camera_ui, gopro_ui
    monkeypatch.setattr(camera_ui.nikon_menus, 'available_lab', lambda *a: None)
    monkeypatch.setattr(gopro_ui, 'lab_available', lambda device, home: device.name == 'known')
    assert camera_ui.available_lab(tmp_path / 'known', tmp_path) is None
    (tmp_path / 'gopro-ui-lab.json').write_text('{}')
    assert camera_ui.available_lab(tmp_path / 'unknown', tmp_path) is None
    selected = camera_ui.available_lab(tmp_path / 'known', tmp_path)
    assert selected['id'] == 'gopro-hero8' and selected['assisted_startup'] is True
    calls = []
    monkeypatch.setattr(gopro_ui, 'run_lab', lambda *a, **k: calls.append((a, k)) or 'started')
    assert camera_ui.run_lab(tmp_path / 'known', tmp_path) == 'started'
    assert calls[0][0][0] == tmp_path / 'known'


def test_gopro_cli_routes_probe_and_import(tmp_path, monkeypatch, capsys):
    from devkit import gopro_ui
    from devkit.cli import main
    calls = []
    monkeypatch.setattr(gopro_ui, 'run_menu_probe', lambda *a, **k: calls.append((a, k)) or {'authored': True})
    assert main(['gopro-ui-check', str(tmp_path), '--seconds', '80']) == 0
    assert calls[0][1] == {'seconds': 80}
    monkeypatch.setattr(firmware, 'import_update', lambda *a: calls.append(a) or tmp_path / 'entry')
    assert main(['gopro-ui-import', str(tmp_path / 'UPDATE.zip'), '--output', str(tmp_path / 'entry')]) == 0
    assert calls[1][:2] == (tmp_path / 'UPDATE.zip', tmp_path / 'entry')


def test_gopro_probe_rejects_unbounded_budget(tmp_path):
    from devkit.gopro_ui import run_menu_probe
    for seconds in [True, 0, 59, 181, float('inf'), float('nan')]:
        with pytest.raises(ValueError, match='60..180'):
            run_menu_probe(tmp_path, tmp_path, seconds=seconds)
