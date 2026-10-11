"""Camera lab dispatch must not claim arbitrary firmware or fall back silently."""
from types import SimpleNamespace

import pytest

from devkit import camera_lab
from devkit.cli import main


def setup_lab(monkeypatch, result):
    calls = []
    module = SimpleNamespace(lab_available=lambda device, home: device.name == 'known',
        run_lab=lambda device, home, task, **options: calls.append(options) or result)
    monkeypatch.setattr(camera_lab, 'LABS', (('sony','Sony test',module,('shell','init')),))
    return calls


def test_unknown_firmware_and_unsupported_mode_do_not_start(tmp_path, monkeypatch):
    calls = setup_lab(monkeypatch, tmp_path/'proof.json')
    assert camera_lab.available_lab(tmp_path/'unknown', tmp_path) is None
    with pytest.raises(ValueError, match='matches'): camera_lab.run_lab(tmp_path/'unknown',tmp_path)
    with pytest.raises(ValueError, match='supports'): camera_lab.run_lab(tmp_path/'known',tmp_path,mode='services')
    assert calls == []


def test_cli_uses_selected_mode_and_reports_evidence(tmp_path, monkeypatch, capsys):
    result = tmp_path/'proof.json'; calls = setup_lab(monkeypatch, result)
    assert main(['--home',str(tmp_path),'camera-lab',str(tmp_path/'known'),'--seconds','12','--mode','init']) == 0
    assert calls == [{'seconds':12,'mode':'init'}]
    assert str(result) in capsys.readouterr().out


def test_start_button_routes_configured_lab_without_generic_boot(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication
    from devkit.application import Window
    from devkit.library import import_firmware
    app = QApplication.instance() or QApplication([])
    source = tmp_path/'firmware.dat'; source.write_bytes(b'unknown')
    device = import_firmware(source,tmp_path/'home/library')
    monkeypatch.setattr(camera_lab,'available_lab',lambda *args: {'id':'sony','label':'Sony','modes':('shell',)})
    window = Window(tmp_path/'home')
    started = []; window.launch_camera_lab = lambda: started.append(device)
    try:
        assert window.start.isEnabled() and window.camera_lab_button.isEnabled()
        assert 'Linux' in window.start.text()
        window.launch()
        assert started == [device]
        assert window.session is None
    finally: window.close()
