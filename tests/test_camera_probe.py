"""Bounded probe lifecycle tests using harmless Python children, not firmware."""
import hashlib
import io
import os
from pathlib import Path
import struct
import subprocess
import sys
import threading
import time

import pytest

from devkit import camera_probe
from devkit.core import Cancelled, Task, read_json, write_json


def _image():
    image = bytearray(4096)
    image[56:60] = b'ARM\x64'
    struct.pack_into('<Q', image, 16, len(image))
    return bytes(image)


def _dtb():
    nodes = struct.pack('>4I', 1, 0, 2, 9)
    return struct.pack('>10I', 0xd00dfeed, 72, 56, 72, 40, 17, 16, 0, 0, 16) + bytes(16) + nodes


def _workspace(tmp_path):
    device, runtime = tmp_path / 'device', tmp_path / 'runtime'
    device.mkdir(); runtime.mkdir()
    (device / 'Image').write_bytes(_image())
    write_json(device / 'device.json', {
        'adapter': 'generic', 'name': 'authored kernel fixture',
        'sha256': hashlib.sha256(_image()).hexdigest(),
    })
    binary = 'qemu-system-aarch64' + ('.exe' if os.name == 'nt' else '')
    (runtime / binary).touch()
    return device, runtime


def _python_child(monkeypatch, script, *, on_spawn=None):
    original = subprocess.Popen
    children, commands = [], []

    def spawn(command, **options):
        commands.append(command)
        child = original([sys.executable, '-c', script], **options)
        children.append(child)
        if on_spawn:
            on_spawn(command, child)
        return child

    monkeypatch.setattr(camera_probe.subprocess, 'Popen', spawn)
    return children, commands


def _result(device):
    paths = list((device / 'support/boot-probes').glob('*/result.json'))
    assert len(paths) == 1
    return paths[0].parent, read_json(paths[0])


def _unverified(result):
    assert result['kernel_boot_verified'] is False
    assert result['userspace_boot_verified'] is False
    assert result['original_ui_verified'] is False


@pytest.mark.parametrize('exit_code,output', [(0, ''), (0, 'Linux version fixture\n'), (7, 'Kernel panic fixture\n')])
def test_exit_and_console_text_never_claim_kernel_or_ui_boot(tmp_path, monkeypatch, exit_code, output):
    device, runtime = _workspace(tmp_path)
    original = (device / 'Image').read_bytes()

    def check_snapshot(command, child):
        path = Path(command[command.index('-kernel') + 1])
        assert path != device / 'Image'
        assert path.read_bytes() == original
        assert command[command.index('-nic') + 1] == 'none'
        assert command[command.index('-display') + 1] == 'none'

    children, commands = _python_child(monkeypatch, f'import sys; sys.stdout.write({output!r}); sys.exit({exit_code})', on_spawn=check_snapshot)
    root = camera_probe.probe_boot(device, 'Image', 'arm64-virt', runtime, Task(lambda _: None), seconds=2)
    result = read_json(root / 'result.json')
    assert result['exit_code'] == exit_code and result['process_reaped']
    assert not result['timed_out'] and not result['cancelled']
    assert result['kernel_console_observed'] == ('Linux version ' in output)
    assert result['kernel_panic_observed'] == ('Kernel panic' in output)
    _unverified(result)
    assert children[0].poll() is not None
    assert not list(root.glob('*-input.bin'))
    assert (device / 'Image').read_bytes() == original
    assert not (device / 'boot.json').exists()


def test_timeout_reaps_only_owned_child_and_bounds_elapsed(tmp_path, monkeypatch):
    device, runtime = _workspace(tmp_path)
    children, _ = _python_child(monkeypatch, 'import time; time.sleep(30)')
    started = time.monotonic()
    root = camera_probe.probe_boot(device, 'Image', 'arm64-virt', runtime, Task(lambda _: None), seconds=1)
    result = read_json(root / 'result.json')
    assert result['timed_out'] and not result['cancelled']
    assert result['process_reaped'] and children[0].poll() is not None
    assert time.monotonic() - started < 8
    _unverified(result)


def test_cancel_reaps_process_saves_evidence_and_propagates(tmp_path, monkeypatch):
    device, runtime = _workspace(tmp_path)
    task = Task(lambda _: None)
    children, _ = _python_child(monkeypatch, 'import time; time.sleep(30)', on_spawn=lambda *_: task.cancelled.set())
    with pytest.raises(Cancelled):
        camera_probe.probe_boot(device, 'Image', 'arm64-virt', runtime, task, seconds=2)
    root, result = _result(device)
    assert result['cancelled'] and not result['timed_out']
    assert result['process_reaped'] and children[0].poll() is not None
    assert result['error'].startswith('Cancelled:')
    assert not list(root.glob('*-input.bin'))
    _unverified(result)


@pytest.mark.parametrize('fail_at', [1, 2])
def test_reader_start_failure_cannot_leak_owned_process_or_pipes(tmp_path, monkeypatch, fail_at):
    device, runtime = _workspace(tmp_path)
    children, _ = _python_child(monkeypatch, 'import time; time.sleep(30)')
    real_thread, starts = threading.Thread, []

    class FailedStart:
        def start(self):
            raise RuntimeError('reader cannot start')

    def thread(*args, **kwargs):
        starts.append(True)
        return FailedStart() if len(starts) == fail_at else real_thread(*args, **kwargs)

    monkeypatch.setattr(camera_probe.threading, 'Thread', thread)
    with pytest.raises(RuntimeError, match='reader cannot start'):
        camera_probe.probe_boot(device, 'Image', 'arm64-virt', runtime, Task(lambda _: None))
    root, result = _result(device)
    assert result['process_reaped'] and children[0].poll() is not None
    assert children[0].stdout.closed and children[0].stderr.closed
    assert not result['cancelled']
    assert not list(root.glob('*-input.bin'))


def test_unresponsive_terminate_uses_bounded_kill_and_wait(tmp_path, monkeypatch):
    device, runtime = _workspace(tmp_path)
    task = Task(lambda _: None)

    class Process:
        stdout, stderr = io.BytesIO(b'fixture'), io.BytesIO(b'')
        returncode = None
        killed = False
        waits = []

        def poll(self): return self.returncode
        def terminate(self): pass
        def kill(self): self.killed = True; self.returncode = -9
        def wait(self, timeout):
            self.waits.append(timeout)
            if self.returncode is None:
                raise subprocess.TimeoutExpired('fixture', timeout)
            return self.returncode

    child = Process()
    def spawn(*args, **kwargs):
        task.cancelled.set()
        return child
    monkeypatch.setattr(camera_probe.subprocess, 'Popen', spawn)
    with pytest.raises(Cancelled):
        camera_probe.probe_boot(device, 'Image', 'arm64-virt', runtime, task)
    assert child.killed and child.waits == [3, 3]
    assert _result(device)[1]['process_reaped']


def test_logs_are_drained_without_storing_more_than_the_limit(tmp_path, monkeypatch):
    device, runtime = _workspace(tmp_path)
    monkeypatch.setattr(camera_probe, 'LOG_LIMIT', 128)
    children, _ = _python_child(monkeypatch, 'import sys; sys.stdout.buffer.write(b"x"*65536); sys.stderr.buffer.write(b"y"*65536)')
    root = camera_probe.probe_boot(device, 'Image', 'arm64-virt', runtime, Task(lambda _: None), seconds=2)
    result = read_json(root / 'result.json')
    assert result['exit_code'] == 0 and result['log_drains_finished']
    assert result['logs_truncated'] == {'serial': True, 'stderr': True}
    assert (root / 'serial.log').stat().st_size == 128
    assert (root / 'stderr.log').stat().st_size == 128
    assert children[0].poll() is not None


@pytest.mark.parametrize('seconds', [0, 61, True, 1.5])
def test_invalid_duration_never_spawns_a_process(tmp_path, monkeypatch, seconds):
    device, runtime = _workspace(tmp_path)
    monkeypatch.setattr(camera_probe.subprocess, 'Popen', lambda *_args, **_kwargs: pytest.fail('Invalid preflight spawned'))
    with pytest.raises(ValueError, match='duration'):
        camera_probe.probe_boot(device, 'Image', 'arm64-virt', runtime, seconds=seconds)


def test_kernel_size_limit_is_applied_before_full_hash_or_qemu_validation(tmp_path, monkeypatch):
    device, runtime = _workspace(tmp_path)
    monkeypatch.setattr(camera_probe, 'MAX_KERNEL_BYTES', 4095)
    monkeypatch.setattr(camera_probe, 'inspect_firmware', lambda *_: pytest.fail('Oversized kernel was hashed'))
    monkeypatch.setattr(camera_probe, 'build_command', lambda *_: pytest.fail('Oversized kernel was validated'))
    with pytest.raises(ValueError, match='size limit'):
        camera_probe.probe_boot(device, 'Image', 'arm64-virt', runtime)


@pytest.mark.parametrize('kernel,board,dtb', [('../Image', 'arm64-virt', ''), ('Image', 'unknown', ''), ('Image', 'arm64-virt', '../outside.dtb')])
def test_explicit_board_and_component_paths_are_validated_before_spawn(tmp_path, monkeypatch, kernel, board, dtb):
    device, runtime = _workspace(tmp_path)
    monkeypatch.setattr(camera_probe.subprocess, 'Popen', lambda *_args, **_kwargs: pytest.fail('Invalid preflight spawned'))
    with pytest.raises(ValueError):
        camera_probe.probe_boot(device, kernel, board, runtime, dtb=dtb)


def test_valid_dtb_uses_a_hash_checked_private_copy(tmp_path, monkeypatch):
    device, runtime = _workspace(tmp_path)
    (device / 'camera.dtb').write_bytes(_dtb())
    def checked(command, child):
        path = Path(command[command.index('-dtb') + 1])
        assert path.read_bytes() == _dtb() and path != device / 'camera.dtb'
    _python_child(monkeypatch, 'pass', on_spawn=checked)
    root = camera_probe.probe_boot(device, 'Image', 'arm64-virt', runtime, dtb='camera.dtb')
    assert read_json(root / 'result.json')['dtb_sha256'] == hashlib.sha256(_dtb()).hexdigest()
    assert not list(root.glob('*-input.bin'))


@pytest.mark.parametrize('mutation', ['short-header', 'magic-only', 'block-overlap', 'unterminated-reserve', 'bad-token', 'oversized'])
def test_malformed_dtb_structures_are_rejected_before_spawn(tmp_path, monkeypatch, mutation):
    device, runtime = _workspace(tmp_path)
    data = bytearray(_dtb())
    if mutation == 'short-header': data = data[:8]
    elif mutation == 'magic-only': data[8:40] = bytes(32)
    elif mutation == 'block-overlap': struct.pack_into('>I', data, 12, 56); struct.pack_into('>I', data, 32, 16)
    elif mutation == 'unterminated-reserve': data[40:56] = b'x' * 16
    elif mutation == 'bad-token': struct.pack_into('>I', data, 56, 99)
    elif mutation == 'oversized': struct.pack_into('>I', data, 4, camera_probe.MAX_DTB_BYTES + 1)
    (device / 'camera.dtb').write_bytes(data)
    monkeypatch.setattr(camera_probe.subprocess, 'Popen', lambda *_args, **_kwargs: pytest.fail('Invalid DTB spawned'))
    with pytest.raises(ValueError, match='device tree'):
        camera_probe.probe_boot(device, 'Image', 'arm64-virt', runtime, dtb='camera.dtb')


def test_changed_input_is_rejected_before_qemu_receives_it(tmp_path, monkeypatch):
    device, runtime = _workspace(tmp_path)
    original = camera_probe.inspect_firmware
    def change(path, task):
        result = original(path, task)
        path.write_bytes(path.read_bytes()[:-1] + b'x')
        return result
    monkeypatch.setattr(camera_probe, 'inspect_firmware', change)
    monkeypatch.setattr(camera_probe.subprocess, 'Popen', lambda *_args, **_kwargs: pytest.fail('Changed kernel spawned'))
    with pytest.raises(ValueError, match='changed after inspection'):
        camera_probe.probe_boot(device, 'Image', 'arm64-virt', runtime)
    root, result = _result(device)
    assert not result['process_started'] and result['process_reaped']
    assert not list(root.glob('*-input.bin'))


def test_spawn_failure_records_error_without_leaving_input_copies(tmp_path, monkeypatch):
    device, runtime = _workspace(tmp_path)
    def fail(*args, **kwargs):
        raise OSError('runtime could not start')
    monkeypatch.setattr(camera_probe.subprocess, 'Popen', fail)
    with pytest.raises(OSError, match='runtime could not start'):
        camera_probe.probe_boot(device, 'Image', 'arm64-virt', runtime)
    root, result = _result(device)
    assert not result['process_started'] and result['process_reaped']
    assert result['error'] == 'OSError: runtime could not start'
    assert result['exit_code'] is None and not result['cancelled']
    assert not list(root.glob('*-input.bin'))
    _unverified(result)


def test_probe_output_parent_symlink_cannot_escape_the_workspace(tmp_path, monkeypatch):
    device, runtime = _workspace(tmp_path)
    outside = tmp_path / 'outside'
    outside.mkdir()
    try:
        (device / 'support').symlink_to(outside, target_is_directory=True)
    except OSError as error:
        pytest.skip(f'Host cannot create this link: {error}')
    monkeypatch.setattr(camera_probe.subprocess, 'Popen', lambda *_args, **_kwargs: pytest.fail('Escaping output spawned'))
    with pytest.raises(ValueError, match='escapes'):
        camera_probe.probe_boot(device, 'Image', 'arm64-virt', runtime)
    assert not list(outside.iterdir())


def test_pre_cancelled_task_never_creates_or_spawns_probe(tmp_path, monkeypatch):
    device, runtime = _workspace(tmp_path)
    task = Task(lambda _: None)
    task.cancelled.set()
    monkeypatch.setattr(camera_probe.subprocess, 'Popen', lambda *_args, **_kwargs: pytest.fail('Cancelled preflight spawned'))
    with pytest.raises(Cancelled):
        camera_probe.probe_boot(device, 'Image', 'arm64-virt', runtime, task)
    assert not (device / 'support').exists()
