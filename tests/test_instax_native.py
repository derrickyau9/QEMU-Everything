import json
import struct
import time

from devkit.instax_native import NativeInstaxSession


def test_native_frame_and_stale_controller(tmp_path):
    session = NativeInstaxSession(tmp_path)
    state = {'alive': True, 'started_at': time.time() - 1,
             'updated_at': time.time(), 'frame_sequence': 7}
    status = tmp_path / 'native-live-status.json'
    status.write_text(json.dumps(state))
    (tmp_path / 'native-screen.ppm').write_bytes(b'P6\n480 854\n255\n' + b'\x12\x34\x56' * (480 * 854))
    session._read_state(); session._read_frame()
    assert session.ready
    header, pixels = session.frames.latest
    assert struct.unpack('<6I', header)[1:] == (480, 854, 1920, 0, 7)
    assert pixels[:4] == b'\x56\x34\x12\xff'
    state['updated_at'] -= 6
    status.write_text(json.dumps(state)); session._read_state()
    assert not session.ready
    session.touch('down', 100, 100)
    assert session.touches.empty()
    assert 'stale' in session.description()


def test_native_touch_transport_and_detach(tmp_path):
    session = NativeInstaxSession(tmp_path)
    state = {'alive': True, 'started_at': time.time() - 1,
             'updated_at': time.time(), 'frame_sequence': 0}
    (tmp_path / 'native-live-status.json').write_text(json.dumps(state))
    session._read_state()
    session.touch('down', -20, 900)
    session.touch('up', 240, 338)
    assert session.touches.get_nowait() == ('down', 0, 853)
    assert session.touches.get_nowait() == ('up', 240, 338)
    session._append('native-input.txt', 'touch 240 338 1\n')
    assert (tmp_path / 'native-input.txt').read_bytes() == b'touch 240 338 1\n'
    session.stop()
    assert session.cancelled.is_set()
    assert json.loads((tmp_path / 'native-live-status.json').read_text()) == state


def test_existing_qt_screen_pointer_mapping(tmp_path):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtGui import QImage
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    from devkit.application import Screen
    app = QApplication.instance() or QApplication([])
    screen = Screen()
    screen.frame = QImage(480, 854, QImage.Format_RGB32)
    screen.resize(480, 854)
    session = NativeInstaxSession(tmp_path)
    session.ready = True
    screen.touch.connect(session.touch)
    QTest.mouseClick(screen, Qt.LeftButton, pos=QPoint(240, 338))
    assert session.touches.get_nowait() == ('down', 240, 338)
    assert session.touches.get_nowait() == ('up', 240, 338)
    screen.close()


def test_quick_clicks_hold_and_release_gap(tmp_path):
    session = NativeInstaxSession(tmp_path)
    session.ready = True
    for operation, x in [('down', 100), ('up', 100), ('down', 200), ('up', 200)]:
        session.touch(operation, x, 300)
    path = tmp_path / 'native-input.txt'
    session._flush_touches(now=10)
    assert path.read_text().splitlines() == ['touch 100 300 1']
    session._flush_touches(now=10.079)
    assert len(path.read_text().splitlines()) == 1
    session._flush_touches(now=10.080)
    assert path.read_text().splitlines()[-1] == 'touch 100 300 0'
    session._flush_touches(now=10.109)
    assert len(path.read_text().splitlines()) == 2
    session._flush_touches(now=10.110)
    assert path.read_text().splitlines()[-1] == 'touch 200 300 1'
    session._flush_touches(now=10.190)
    assert path.read_text().splitlines()[-1] == 'touch 200 300 0'
    assert not session.pressed and session.pending_touch is None


def test_drag_and_long_hold_keep_original_timing(tmp_path):
    session = NativeInstaxSession(tmp_path)
    session.ready = True
    session.touch('down', 20, 30); session._flush_touches(now=1)
    session.touch('move', 40, 50); session._flush_touches(now=1.02)
    assert session.press_sent_at == 1
    session.touch('up', 40, 50); session._flush_touches(now=2)
    assert session.release_sent_at == 2
    assert (tmp_path / 'native-input.txt').read_text().splitlines() == [
        'touch 20 30 1', 'touch 40 50 1', 'touch 40 50 0']


def test_ui_response_status_requires_original_event(tmp_path):
    session = NativeInstaxSession(tmp_path)
    session.ready = True
    log = tmp_path / 'boot-modules.log'
    log.write_text('LAB_HANDOFF ENTER symbol=host_main_proc_msg\n')
    session._read_log()
    assert 'input observed' not in session.description()
    with log.open('a') as stream:
        stream.write('ui_event_id: HOST_OSD_EVENT_LANGUAGE_ENGLISH\n')
    session._read_log()
    assert session.description() == 'Original UI input observed; full camera functions unverified'
