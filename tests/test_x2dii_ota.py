import hashlib
import io
import struct
import sys
import threading
import time
import zipfile
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from devkit import firmware, library, x2dii_ota
from devkit.core import Task, read_json


SYNTHETIC_KEY = bytes(range(16))
FINAL_BLOCK = b'F' * 4096
BRIDGE_BLOCK = b'B' * 4096


def ota_zip(block):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('system.transfer.list', '4\n1\n0\n0\nnew 2,0,1\n')
        archive.writestr('system.new.dat', block)
    return buffer.getvalue()


@pytest.fixture
def encrypted_ota(monkeypatch):
    archive = ota_zip(FINAL_BLOCK)
    monkeypatch.setattr(x2dii_ota, 'ZIP_SHA256', hashlib.sha256(archive).hexdigest())

    content_key = bytes(reversed(range(16)))
    wrap = Cipher(algorithms.AES(SYNTHETIC_KEY), modes.ECB()).encryptor()
    wrapped_key = wrap.update(content_key) + wrap.finalize()
    header_size, signature_size = 224, 384
    padding = bytes((-len(archive)) % 32)
    payload_size = len(archive) + len(padding)
    header = bytearray(header_size + signature_size)
    header[:4] = b'IM*H'
    struct.pack_into('<6I', header, 4, 2, len(header) + payload_size, 0,
                     header_size, signature_size, payload_size)
    header[44:48] = b'TBIE'
    header[48:64] = wrapped_key
    struct.pack_into('<I', header, 156, 1)
    struct.pack_into('<4sIIIQ', header, 192, b'0802', 0, len(archive), 0, 0)
    encryptor = Cipher(algorithms.AES(content_key), modes.CTR(bytes(16))).encryptor()
    payload = encryptor.update(archive + padding) + encryptor.finalize()
    return bytes(header) + payload, archive


@pytest.fixture
def final_package(monkeypatch, tmp_path, encrypted_ota):
    container, _ = encrypted_ota
    bridge = ota_zip(BRIDGE_BLOCK)
    raw = bytes(2048) + bridge + container
    source = tmp_path / 'test.cim'
    source.write_bytes(raw)
    digest = hashlib.sha256(raw).hexdigest()
    monkeypatch.setattr(x2dii_ota, 'CIM_SHA256', digest)
    monkeypatch.setattr(x2dii_ota, 'FINAL_ENTRY_NAME', f'{digest[:16]}-final')
    monkeypatch.setitem(firmware.PROFILES, digest,
                        ('Hasselblad X2D II 100C', x2dii_ota.VERSION, '1800000_PVF'))
    monkeypatch.setitem(firmware.BRIDGE_SYSTEM_VERSIONS, digest, '1.3.18.1')

    public = {'size_matches': True, 'sha256': digest, 'product': '1800000_PVF'}
    items = [
        {'name': 'ota.zip', 'offset_in_cim': 2048, 'length': len(bridge)},
        {'name': 'ota.sig', 'offset_in_cim': 2048 + len(bridge), 'length': len(container)},
    ]
    monkeypatch.setattr(firmware.cim, 'load_container', lambda _: (public, False, None, {}, items))
    monkeypatch.setattr(library, 'inspect_firmware',
                        lambda path, task: {'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})

    def extract_system(image, root, task):
        version = {FINAL_BLOCK: x2dii_ota.VERSION, BRIDGE_BLOCK: '1.3.18.1'}[image.read_bytes()]
        (root / 'etc').mkdir(parents=True)
        (root / 'etc/VERSION').write_text(version, encoding='ascii')
        (root / 'build.prop').write_bytes(b'ro.product.device=eagle2_hb722\n')

    def stage(root, payload, task):
        payload.mkdir()
        return 'arm64'

    monkeypatch.setattr(firmware, 'extract_system', extract_system)
    monkeypatch.setattr(firmware, 'stage', stage)
    return source


def test_decrypts_synthetic_final_ota(encrypted_ota, tmp_path):
    container, plaintext = encrypted_ota
    destination = tmp_path / 'final.zip'
    x2dii_ota.decrypt_zip(container, SYNTHETIC_KEY, destination, Task())
    assert destination.read_bytes() == plaintext


def test_rejects_unexpected_container_layout(encrypted_ota, tmp_path):
    container, _ = encrypted_ota
    malformed = bytearray(container)
    struct.pack_into('<I', malformed, 156, 2)
    destination = tmp_path / 'final.zip'
    with pytest.raises(ValueError, match='Unexpected layout'):
        x2dii_ota.decrypt_zip(malformed, SYNTHETIC_KEY, destination, Task())
    assert not destination.exists()


def test_rejects_mismatched_plaintext_fingerprint(encrypted_ota, tmp_path, monkeypatch):
    container, _ = encrypted_ota
    monkeypatch.setattr(x2dii_ota, 'ZIP_SHA256', '0' * 64)
    with pytest.raises(ValueError, match='did not decrypt the expected final OTA'):
        x2dii_ota.decrypt_zip(container, SYNTHETIC_KEY, tmp_path / 'final.zip', Task())


def test_final_import_requests_key_even_when_bridge_is_cached(final_package, tmp_path):
    home = tmp_path / 'library'
    bridge = library.import_firmware(final_package, home, bridge=True)
    assert read_json(bridge / 'device.json')['ota_stage'] == 'bridge'
    with pytest.raises(x2dii_ota.FinalOtaKeyRequired):
        library.import_firmware(final_package, home)
    with pytest.raises(ValueError, match='did not decrypt the expected final OTA'):
        library.import_firmware(final_package, home, ota_key=bytes(16))
    assert list(home.iterdir()) == [bridge]

    final = library.import_firmware(final_package, home, ota_key=SYNTHETIC_KEY)
    profile = read_json(final / 'device.json')
    assert final != bridge
    assert profile['version'] == x2dii_ota.VERSION
    assert profile['ota_stage'] == 'final'
    assert profile['validated'] is False
    assert library.import_firmware(final_package, home) == final
    assert library.import_firmware(final_package, home, bridge=True) == bridge
    assert hashlib.sha256(final_package.read_bytes()).hexdigest() == x2dii_ota.CIM_SHA256
    assert not (final / 'final.zip').exists()
    assert SYNTHETIC_KEY.hex() not in (final / 'device.json').read_text(encoding='utf-8')


@pytest.mark.parametrize('interactive', [True, False])
def test_cli_reads_key_only_in_terminal(final_package, tmp_path, monkeypatch, capsys, interactive):
    from devkit.cli import main

    prompts = []

    def read_key(prompt):
        prompts.append(prompt)
        return SYNTHETIC_KEY.hex()

    monkeypatch.setattr(sys, 'stdin', SimpleNamespace(isatty=lambda: interactive))
    monkeypatch.setattr('getpass.getpass', read_key)
    home = tmp_path / 'cli'
    result = main(['--home', str(home), 'import', str(final_package)])
    output = capsys.readouterr()
    assert SYNTHETIC_KEY.hex() not in output.out + output.err
    if interactive:
        assert result == 0
        assert len(prompts) == 1
        assert 'ota.sig' in prompts[0]
        assert x2dii_ota.FINAL_ENTRY_NAME in output.out
    else:
        assert result == 1
        assert not prompts
        assert 'in a terminal' in output.err
        assert not home.exists()


def test_workbench_masks_key_and_selects_final_entry(final_package, tmp_path, monkeypatch):
    monkeypatch.setenv('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QLineEdit
    from devkit import application

    prompts = []
    errors = []

    def read_key(parent, title, label, echo):
        prompts.append((threading.get_ident(), echo))
        return SYNTHETIC_KEY.hex(), True

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(application.QInputDialog, 'getText', read_key)
    home = tmp_path / 'workbench'
    window = application.Window(home)
    window.error = errors.append
    try:
        window.import_file(str(final_package))
        deadline = time.monotonic() + 3
        while window.device is None and not errors and time.monotonic() < deadline:
            QTest.qWait(10)
        assert not errors
        assert prompts == [(threading.get_ident(), QLineEdit.EchoMode.Password)]
        assert window.device == home / 'library' / x2dii_ota.FINAL_ENTRY_NAME
        assert SYNTHETIC_KEY.hex() not in window.logs.toPlainText()
    finally:
        if window.job is not None:
            window.job.wait(3000)
        window.close()
        app.processEvents()
