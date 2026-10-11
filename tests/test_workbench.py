"""Guard the adapter split: generic entries must never use camera boot/tools."""
import json
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtWidgets import QApplication
from devkit.application import Window
from devkit.core import write_json
from devkit.library import import_firmware

def test_generic_library_is_configurable_without_camera_identity(tmp_path):
    app = QApplication.instance() or QApplication([])
    source = tmp_path / 'unknown.bin'; source.write_bytes(b'not-a-camera-image')
    device = import_firmware(source, tmp_path / 'home/library')
    window = Window(tmp_path / 'home')
    try:
        assert window.windowTitle() == 'QEMU Everything'
        assert window.device == device
        assert window.model_choice.count() == 0
        assert not window.start.isEnabled()
        assert not window.build_button.isEnabled()
        assert not window.import_app_button.isEnabled()
        assert window.save_boot_button.isEnabled()
        assert 'needs adaptation' in window.details.text() or '待适配' in window.details.text()
        window.use_board(window.board_choice.findData('x86_64-pc'))
        assert json.loads(window.boot_editor.toPlainText())['board'] == 'x86_64-pc'
        errors = []; window.error = errors.append
        window.boot_editor.setPlainText('[]')
        window.use_board(window.board_choice.findData('arm64-virt'))
        assert errors and 'JSON object' in errors[-1]
    finally: window.close()

def test_legacy_camera_entry_keeps_ui_start_and_app_tools(tmp_path):
    app = QApplication.instance() or QApplication([])
    device = tmp_path / 'library/camera'
    write_json(device / 'device.json', {'name':'Hasselblad X2D II','version':'1.2.7.16',
        'sha256':'0'*64,'abi':'arm64','model':'x2dii','validated':True})
    window = Window(tmp_path)
    try:
        assert window.start.isEnabled()
        assert window.build_button.isEnabled()
        assert window.model_choice.currentData() == 'x2dii'
        assert not window.save_boot_button.isEnabled()
    finally: window.close()
