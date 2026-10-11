import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import threading
from unittest.mock import Mock

import pytest

from devkit import insta360_lab as lab
from devkit.core import Cancelled, Task, read_json, write_json


@pytest.fixture
def known_guest(tmp_path, monkeypatch):
    device, home = tmp_path / 'guest', tmp_path / 'home'
    expected = {}
    artifacts = []
    for name, content in (('kernel.bin', b'original kernel'), ('rootfs.squashfs', b'original rootfs')):
        sha = hashlib.sha256(content).hexdigest()
        expected[name] = (len(content), sha)
        path = device / 'extracted' / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        artifacts.append(dict(path=name, size=len(content), sha256=sha))
    monkeypatch.setattr(lab, 'EXPECTED', expected)
    write_json(device / 'extracted/extraction.json', dict(artifacts=artifacts))
    write_json(home / 'insta360-lab.json', dict(distro='Debian', manifest='/private/build/insta360-lab-build.json'))
    return device, home


def test_available_is_cheap_but_exact(known_guest, monkeypatch):
    device, home = known_guest
    monkeypatch.setattr(lab, '_hash', Mock(side_effect=AssertionError('eligibility must not stream firmware')))
    monkeypatch.setattr(lab.subprocess, 'Popen', Mock(side_effect=AssertionError('eligibility must not launch anything')))
    assert lab.lab_available(device, home)
    (device / 'extracted/kernel.bin').write_bytes(b'truncated')
    assert not lab.lab_available(device, home)


def test_duplicate_fingerprint_rejected(known_guest):
    device, home = known_guest
    data = read_json(device / 'extracted/extraction.json')
    data['artifacts'].append(data['artifacts'][0])
    write_json(device / 'extracted/extraction.json', data)
    assert not lab.lab_available(device, home)


def test_artifact_path_cannot_escape(known_guest):
    device, home = known_guest
    data = read_json(device / 'extracted/extraction.json')
    data['artifacts'][0]['path'] = '../../kernel.bin'
    write_json(device / 'extracted/extraction.json', data)
    assert not lab.lab_available(device, home)


def test_junction_or_symlink_root_rejected(known_guest, monkeypatch):
    device, home = known_guest
    original = Path.lstat
    def lstat(path, *args, **kwargs):
        value = original(path, *args, **kwargs)
        if path == device / 'extracted':
            return Mock(st_mode=value.st_mode, st_file_attributes=0x400)
        return value
    monkeypatch.setattr(Path, 'lstat', lstat)
    assert not lab.lab_available(device, home)


@pytest.mark.parametrize('config', [dict(distro='--exec', manifest='/private/build.json'),
                                  dict(distro='Debian', manifest='relative.json'),
                                  dict(distro='Debian', manifest='/private/a\n.json'), []])
def test_invalid_config_rejected(known_guest, config):
    device, home = known_guest
    write_json(home / 'insta360-lab.json', config)
    assert not lab.lab_available(device, home)


def test_changed_real_payload_rejected_before_process(known_guest, monkeypatch):
    device, home = known_guest
    (device / 'extracted/kernel.bin').write_bytes(b'changed  kernel')
    popen = Mock()
    monkeypatch.setattr(lab.subprocess, 'Popen', popen)
    with pytest.raises(ValueError, match='SHA-256'):
        lab.run_lab(device, home, Task(report=lambda _: None), seconds=10)
    popen.assert_not_called()


@pytest.mark.parametrize('seconds', [False, 0, 4, 121, 30.0, '30'])
def test_bounded_duration_required(known_guest, seconds):
    device, home = known_guest
    with pytest.raises(ValueError, match='5..120'):
        lab.build_lab_command(device, home, device / 'run', Task(), seconds)


def test_argument_vector_keeps_paths_and_json_as_single_args(known_guest):
    device, home = known_guest
    command = lab.build_lab_command(device, home, device / 'space directory/run', Task(), 12)
    assert isinstance(command, list)
    data = json.loads(command[-1])
    assert data['seconds'] == 12
    assert data['destination'].endswith('/space directory/run')
    assert command[-2].endswith('/scripts/boards/insta360/run.py')
    assert '--exec' in command or command[0] == 'python3'


class FakeProcess:
    def __init__(self, output=b''):
        self.stdin, self.stdout = io.BytesIO(), io.BytesIO(output)
        self.returncode = None
        self.waits = []
    def poll(self):
        return self.returncode
    def wait(self, timeout):
        self.waits.append(timeout)
        self.returncode = 0
        return 0
    def terminate(self):
        self.returncode = -15
    def kill(self):
        self.returncode = -9


def test_reader_start_failure_cleans_owned_process(known_guest, monkeypatch):
    device, home = known_guest
    process = FakeProcess()
    monkeypatch.setattr(lab.subprocess, 'Popen', lambda *args, **kwargs: process)
    monkeypatch.setattr(lab.threading.Thread, 'start', Mock(side_effect=RuntimeError('reader start failed')))
    with pytest.raises(RuntimeError, match='reader start failed'):
        lab.run_lab(device, home, Task(report=lambda _: None), seconds=10)
    assert process.returncode == 0
    assert process.waits == [9]
    assert process.stdin.closed and process.stdout.closed
    assert read_json(device / 'insta360-lab-session.json')['process_reaped']


def test_cancel_sends_only_owned_stop_and_reaps(known_guest, monkeypatch):
    device, home = known_guest
    process = FakeProcess()
    monkeypatch.setattr(lab.subprocess, 'Popen', lambda *args, **kwargs: process)
    task = Task(report=lambda _: task.cancelled.set())
    with pytest.raises(Cancelled):
        lab.run_lab(device, home, task, seconds=10)
    assert process.waits == [9]
    assert read_json(device / 'insta360-lab-session.json')['status'] == 'stopped'


def test_guest_path_for_output_cannot_follow_junction(known_guest, monkeypatch):
    device, home = known_guest
    (device / 'insta360-lab-runs').mkdir()
    original = Path.lstat
    def lstat(path, *args, **kwargs):
        value = original(path, *args, **kwargs)
        if path == device / 'insta360-lab-runs':
            return Mock(st_mode=value.st_mode, st_file_attributes=0x400)
        return value
    monkeypatch.setattr(Path, 'lstat', lstat)
    with pytest.raises(ValueError, match='symlink or junction'):
        lab.run_lab(device, home, Task(report=lambda _: None), seconds=10)


def test_failed_os_kill_wait_is_also_bounded():
    process = FakeProcess()
    process.wait = Mock(side_effect=subprocess.TimeoutExpired('owned', 3))
    process.terminate = Mock()
    process.kill = Mock()
    errors = lab._stop(process)
    assert errors
    assert [call.kwargs['timeout'] for call in process.wait.call_args_list] == [9, 3, 3]
    process.terminate.assert_called_once()
    process.kill.assert_called_once()


def test_linux_reader_start_failure_also_cleans_qemu(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location('x3_supervisor_test', lab.OVERLAY / 'run.py')
    supervisor = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(supervisor)
    process = FakeProcess()
    process.stderr = io.BytesIO()
    dtb = tmp_path/'board.dtb'; dtb.write_bytes(b'development DTB')
    monkeypatch.setattr(supervisor, 'validate', lambda _: dict(binary='/private/qemu', dtb=str(dtb),
                        binary_sha256='binary', dtb_sha256=hashlib.sha256(dtb.read_bytes()).hexdigest(), overlay_sha256={}))
    monkeypatch.setattr(supervisor.subprocess, 'Popen', lambda *args, **kwargs: process)
    signals = []
    monkeypatch.setattr(supervisor.os, 'killpg', lambda *args: signals.append(args), raising=False)
    process.pid = 12345
    class FailedReader:
        created = 0
        def __init__(self, *args, **kwargs):
            FailedReader.created += 1
            self.control = FailedReader.created == 1
        def start(self):
            if not self.control:
                raise RuntimeError('Linux reader failed')
        def is_alive(self):
            return False
        def join(self, timeout):
            pass
    monkeypatch.setattr(supervisor.threading, 'Thread', FailedReader)
    destination = tmp_path / 'new-run'
    sources = {}
    expected = {}
    for name, data in (('kernel.bin', b'kernel'), ('rootfs.squashfs', b'rootfs')):
        path = tmp_path / name
        path.write_bytes(data)
        sources[name] = str(path)
        expected[name] = (len(data), hashlib.sha256(data).hexdigest())
    monkeypatch.setattr(supervisor, 'EXPECTED', expected)
    assert supervisor.main(dict(manifest='/private/manifest', destination=str(destination), seconds=10,
                               artifacts=sources)) == 1
    assert signals and signals[0][0] == 12345
    assert process.waits == [3]
    assert process.stdin.closed and process.stdout.closed and process.stderr.closed
    evidence = read_json(destination / 'evidence.json')
    assert evidence['process_reaped']
    assert not evidence['original_ui_verified']


def test_dtb_replaced_after_validation_is_rejected_before_qemu(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location('x3_dtb_tamper', lab.OVERLAY/'run.py')
    supervisor = importlib.util.module_from_spec(spec); spec.loader.exec_module(supervisor)
    dtb = tmp_path/'board.dtb'; dtb.write_bytes(b'original DTB')
    expected = hashlib.sha256(dtb.read_bytes()).hexdigest()
    def validate(_):
        dtb.write_bytes(b'replaced DTB')
        return dict(binary='qemu',dtb=str(dtb),dtb_sha256=expected,binary_sha256='binary',overlay_sha256={})
    launch = Mock()
    monkeypatch.setattr(supervisor.subprocess,'Popen',launch)
    monkeypatch.setattr(supervisor.sys,'stdin',io.StringIO('stop\n'))
    destination = tmp_path/'session'
    assert supervisor.main(dict(destination=str(destination),seconds=5,artifacts={}),
                           expected={},validator=validate) == 1
    launch.assert_not_called()
    assert 'frozen snapshot' in read_json(destination/'evidence.json')['errors'][0]


def test_frozen_dtb_bytes_are_independent_of_later_source_replacement(tmp_path):
    spec = importlib.util.spec_from_file_location('x3_dtb_snapshot', lab.OVERLAY/'run.py')
    supervisor = importlib.util.module_from_spec(spec); spec.loader.exec_module(supervisor)
    source = tmp_path/'source.dtb'; snapshot = tmp_path/'snapshot.dtb'
    original = b'reviewed DTB contents'; source.write_bytes(original)
    path = supervisor.frozen_copy(source,snapshot,len(original),hashlib.sha256(original).hexdigest())
    source.write_bytes(b'other DTB')
    assert Path(path).read_bytes() == original
    assert supervisor.digest(path) == hashlib.sha256(original).hexdigest()
