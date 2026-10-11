"""Bridge eligibility, runtime integrity, argument safety and owned cleanup."""
import hashlib
import io
import json
from pathlib import Path
import socket
import subprocess
from types import SimpleNamespace

import pytest

from devkit import nikon_lab
from devkit.core import Task, Cancelled, write_json


def setup_lab(tmp_path, monkeypatch):
    device, home, project = (tmp_path / name for name in ('device', 'home', 'lab project'))
    device.mkdir(); home.mkdir(); project.mkdir()
    payload = device / 'extracted/main.bin'
    payload.parent.mkdir()
    payload.write_bytes(b'authored fixture for bridge validation')
    digest = hashlib.sha256(payload.read_bytes()).hexdigest()
    monkeypatch.setattr(nikon_lab, 'PAYLOAD_SIZE', payload.stat().st_size)
    monkeypatch.setattr(nikon_lab, 'PAYLOAD_SHA256', digest)
    write_json(device / 'extracted/extraction.json', {'artifacts': [{'path': 'main.bin', 'size': payload.stat().st_size, 'sha256': digest}]})
    hashes = {'tools/interactive.py': hashlib.sha256(b'authored test script').hexdigest()}
    monkeypatch.setattr(nikon_lab, 'SCRIPT_HASHES', hashes)
    (project / 'tools').mkdir(); (project / 'tools/interactive.py').write_bytes(b'authored test script')
    write_json(project / '.local/build-manifest.json', {'qemu_version': '10.2.4', 'binary': '/local/qemu-system-arm'})
    write_json(home / 'nikon-lab.json', {'distro': 'Debian', 'project': str(project), 'qemu': '/local/qemu-system-arm', 'revision': nikon_lab.REVISION})
    monkeypatch.setattr(nikon_lab.subprocess, 'run', lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=nikon_lab.REVISION + '\n'))
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0)); port = s.getsockname()[1]
    monkeypatch.setattr(nikon_lab, 'PORT', port)
    return device, home, project, payload


def test_quick_eligibility_does_not_hash_or_execute(tmp_path, monkeypatch):
    device, home, _, _ = setup_lab(tmp_path, monkeypatch)
    monkeypatch.setattr(nikon_lab, '_hash', lambda *args: pytest.fail('Eligibility must not hash firmware'))
    monkeypatch.setattr(nikon_lab.subprocess, 'run', lambda *args, **kwargs: pytest.fail('Eligibility must not execute'))
    assert nikon_lab.lab_available(device, home)
    assert not nikon_lab.lab_available(tmp_path / 'missing', home)


def test_actual_main_hash_checked_before_execution(tmp_path, monkeypatch):
    device, home, _, payload = setup_lab(tmp_path, monkeypatch)
    payload.write_bytes(b'x' * payload.stat().st_size)
    assert nikon_lab.lab_available(device, home)
    with pytest.raises(ValueError, match='actual SHA-256'):
        nikon_lab.build_lab_command(device, home, Task())


def test_script_tampering_rejected(tmp_path, monkeypatch):
    device, home, project, _ = setup_lab(tmp_path, monkeypatch)
    (project / 'tools/interactive.py').write_bytes(b'changed script')
    with pytest.raises(ValueError, match='script changed'):
        nikon_lab.build_lab_command(device, home, Task())


def test_unreviewed_revision_rejected(tmp_path, monkeypatch):
    device, home, _, _ = setup_lab(tmp_path, monkeypatch)
    monkeypatch.setattr(nikon_lab.subprocess, 'run', lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout='0' * 40))
    with pytest.raises(ValueError, match='source revision'):
        nikon_lab.build_lab_command(device, home, Task())


def test_windows_paths_are_passed_as_arguments_with_no_shell(tmp_path, monkeypatch):
    device, home, _, _ = setup_lab(tmp_path, monkeypatch)
    command, url = nikon_lab.build_lab_command(device, home, Task())
    configuration = json.loads(command[-1])
    assert configuration['payload_sha256'] == nikon_lab.PAYLOAD_SHA256
    assert configuration['url'] == url and url.startswith('http://127.0.0.1:')
    if nikon_lab.os.name == 'nt':
        assert command[:5] == ['wsl.exe', '-d', 'Debian', '--exec', 'python3']
        assert configuration['project'].startswith('/mnt/')
    assert 'lab project' in configuration['project']


def test_occupied_port_rejected_before_start(tmp_path, monkeypatch):
    device, home, _, _ = setup_lab(tmp_path, monkeypatch)
    with socket.socket() as s:
        s.bind(('127.0.0.1', nikon_lab.PORT)); s.listen()
        with pytest.raises(ValueError, match='already occupied'):
            nikon_lab.build_lab_command(device, home, Task())


def test_cancel_while_hashing(tmp_path, monkeypatch):
    device, home, _, _ = setup_lab(tmp_path, monkeypatch)
    task = Task(); task.cancelled.set()
    with pytest.raises(Cancelled):
        nikon_lab.build_lab_command(device, home, task)


def test_distro_option_injection_rejected(tmp_path, monkeypatch):
    device, home, project, _ = setup_lab(tmp_path, monkeypatch)
    write_json(home / 'nikon-lab.json', {'distro': '--shutdown', 'project': str(project), 'qemu': '/local/qemu-system-arm', 'revision': nikon_lab.REVISION})
    # A leading dash is not a distribution name and must never become a wsl option.
    assert not nikon_lab.lab_available(device, home)


def test_extracted_directory_cannot_redirect_outside_device(tmp_path, monkeypatch):
    device, home, _, _ = setup_lab(tmp_path, monkeypatch)
    actual = device / 'extracted'
    actual.rename(tmp_path / 'outside')
    try:
        actual.symlink_to(tmp_path / 'outside', target_is_directory=True)
    except OSError:
        pytest.skip('OS does not permit unprivileged test symlinks')
    assert not nikon_lab.lab_available(device, home)
    with pytest.raises(ValueError, match='escapes|symlink|junction'):
        nikon_lab.build_lab_command(device, home, Task())


def test_session_lock_reparse_point_rejected_before_open(tmp_path, monkeypatch):
    device, home, _, _ = setup_lab(tmp_path, monkeypatch)
    lock = device / 'session.lock'
    lock.write_bytes(b'original')
    original = Path.lstat
    def reparse(path, *args, **kwargs):
        metadata = original(path, *args, **kwargs)
        if path == lock:
            return SimpleNamespace(st_mode=metadata.st_mode, st_file_attributes=0x400)
        return metadata
    monkeypatch.setattr(Path, 'lstat', reparse)
    with pytest.raises(ValueError, match='symlink or junction'):
        nikon_lab.run_lab(device, home, Task())
    assert lock.read_bytes() == b'original'


class RecordedInput(io.BytesIO):
    def close(self):
        self.saved = self.getvalue()
        super().close()


class FakeProcess:
    def __init__(self, output):
        self.stdin = RecordedInput()
        self.stdout = io.BytesIO(output)
        self.returncode = None
        self.waited = False
        self.stopping_input = None
    def poll(self): return self.returncode
    def wait(self, timeout=None):
        self.waited = True; self.returncode = 0
        return 0
    def terminate(self): pytest.fail('Graceful stop should finish before transport termination')


def test_ready_callback_once_and_cancellation_cleanup(tmp_path, monkeypatch):
    device, home, _, _ = setup_lab(tmp_path, monkeypatch)
    url = f'http://127.0.0.1:{nikon_lab.PORT}'
    output = b'\n'.join(json.dumps(value).encode() for value in [
        {'event': 'started', 'pid': 123}, {'event': 'ready', 'url': url, 'session': 'owned'},
        {'event': 'ready', 'url': url, 'session': 'owned'}]) + b'\n'
    process = FakeProcess(output)
    monkeypatch.setattr(nikon_lab.subprocess, 'Popen', lambda *args, **kwargs: process)
    task, callbacks = Task(lambda _: None), []
    def ready(value):
        callbacks.append(value); task.cancelled.set()
    with pytest.raises(Cancelled):
        nikon_lab.run_lab(device, home, task, on_ready=ready)
    assert callbacks == [url] and process.waited
    assert json.loads((device / 'nikon-lab-session.json').read_text())['status'] == 'stopped'
    with nikon_lab.GuestLock(device):
        pass


def test_unexpected_server_url_never_reaches_callback(tmp_path, monkeypatch):
    device, home, _, _ = setup_lab(tmp_path, monkeypatch)
    process = FakeProcess(json.dumps({'event': 'ready', 'url': 'http://example.invalid', 'session': 'spoof'}).encode() + b'\n')
    monkeypatch.setattr(nikon_lab.subprocess, 'Popen', lambda *args, **kwargs: process)
    with pytest.raises(RuntimeError, match='unexpected local URL'):
        nikon_lab.run_lab(device, home, Task(lambda _: None), on_ready=lambda _: pytest.fail('Foreign server must not open'))
    assert process.waited


@pytest.mark.parametrize('failure', ['construct', 'start'])
def test_reader_startup_failure_still_stops_owned_transport(tmp_path, monkeypatch, failure):
    device, home, _, _ = setup_lab(tmp_path, monkeypatch)
    process = FakeProcess(b'')
    monkeypatch.setattr(nikon_lab.subprocess, 'Popen', lambda *args, **kwargs: process)
    class BrokenThread:
        def __init__(self, *args, **kwargs):
            if failure == 'construct':
                raise RuntimeError('Authored thread startup failure')
        def start(self):
            raise RuntimeError('Authored thread startup failure')
        def join(self, *args, **kwargs):
            pytest.fail('A thread that never started cannot be joined')
    monkeypatch.setattr(nikon_lab.threading, 'Thread', BrokenThread)
    with pytest.raises(RuntimeError, match='thread startup failure'):
        nikon_lab.run_lab(device, home, Task(lambda _: None))
    assert process.waited and process.stdin.saved == b'stop\n'
    assert process.stdout.closed
    record = json.loads((device / 'nikon-lab-session.json').read_text())
    assert record['process_reaped'] and record['status'] == 'stopped'
    with nikon_lab.GuestLock(device):
        pass


def test_even_final_kill_wait_is_bounded_and_records_failure(tmp_path, monkeypatch):
    device, home, _, _ = setup_lab(tmp_path, monkeypatch)
    class Unreaped(FakeProcess):
        def __init__(self):
            super().__init__(b''); self.timeouts = []; self.terminated = self.killed = False
        def wait(self, timeout=None):
            assert timeout is not None, 'No process wait may be unbounded'
            self.timeouts.append(timeout)
            raise subprocess.TimeoutExpired('authored fixture', timeout)
        def terminate(self): self.terminated = True
        def kill(self): self.killed = True
    process = Unreaped()
    monkeypatch.setattr(nikon_lab.subprocess, 'Popen', lambda *args, **kwargs: process)
    def no_thread(*args, **kwargs):
        raise RuntimeError('Authored thread startup failure')
    monkeypatch.setattr(nikon_lab.threading, 'Thread', no_thread)
    with pytest.raises(RuntimeError, match='thread startup failure'):
        nikon_lab.run_lab(device, home, Task(lambda _: None))
    assert process.terminated and process.killed and process.timeouts == [12, 3, 3]
    assert process.stdout.closed and process.stdin.saved == b'stop\n'
    record = json.loads((device / 'nikon-lab-session.json').read_text())
    assert not record['process_reaped'] and record['status'] == 'cleanup-incomplete'
    assert record['cleanup_errors']
    with nikon_lab.GuestLock(device):
        pass
