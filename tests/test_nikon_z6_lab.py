"""Authored CRC fixtures and process supervision; no vendor code or keys."""
import binascii
import hashlib
import io
import importlib.util
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
from types import SimpleNamespace
import sys

import pytest

from devkit import nikon_z6_lab as lab
from devkit.core import Cancelled, Task, write_json


def setup_lab(tmp_path, monkeypatch):
    device, home, project = (tmp_path / item for item in ('device', 'home', 'upstream project'))
    for path in (device, home, project): path.mkdir()
    # Construct a valid three-module Nikon plaintext, including original CRCs.
    payload = b'authored main'
    payload += next(struct.pack('>H', value) for value in range(65536)
                    if binascii.crc_hqx(payload + struct.pack('>H', value), 0) == 0xf92d)
    payloads = [b'authored MCU', b'authored headers', payload]
    directory = 48 + 3 * 32; offset = directory
    body = bytearray(b' ' * 32 + struct.pack('>II', 3, directory) + bytes(8))
    stored = []
    for index, data in enumerate(payloads):
        module = data + struct.pack('>H', binascii.crc_hqx(data, 0))
        body += f'module{index}.bi'.encode().ljust(16, b'\0') + struct.pack('>II', offset, len(module)) + bytes(8)
        stored.append(module); offset += len(module)
    body += b''.join(stored); body += struct.pack('>H', binascii.crc_hqx(body, 0)) + bytes(14)
    plain = bytes(32) + body; source_bytes = bytes([17]) * len(plain)
    source = device / 'source/firmware.bin'; source.parent.mkdir(); source.write_bytes(source_bytes)
    extracted = device / 'extracted'; extracted.mkdir()
    decrypted = extracted / 'decrypted.bin'; decrypted.write_bytes(plain)
    main = extracted / 'module-003.bin'; main.write_bytes(payload)
    fingerprints = {'SOURCE_SIZE': len(plain), 'SOURCE_SHA256': hashlib.sha256(source_bytes).hexdigest(),
                    'DECRYPTED_SHA256': hashlib.sha256(plain).hexdigest(), 'MODULE_SIZE': len(payload),
                    'MODULE_SHA256': hashlib.sha256(payload).hexdigest(),
                    'MAIN_OFFSET': 32 + directory + len(stored[0]) + len(stored[1]),
                    'MAIN_SIZE': len(stored[2]), 'MAIN_SHA256': hashlib.sha256(stored[2]).hexdigest()}
    for key, value in fingerprints.items(): monkeypatch.setattr(lab, key, value)
    write_json(device / 'device.json', {'sha256': lab.SOURCE_SHA256})
    write_json(device / 'inspection.json', {'source_path': 'source/firmware.bin'})
    write_json(extracted / 'extraction.json', {'source_sha256': lab.SOURCE_SHA256, 'source_size': lab.SOURCE_SIZE,
               'artifacts': [{'path': path.name, 'size': path.stat().st_size, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
                             for path in (decrypted, main)]})
    scripts = {'tools/interactive.py': hashlib.sha256(b'authored script').hexdigest()}
    boards = {'qemu/nikon-z6.c': hashlib.sha256(b'authored board').hexdigest()}
    monkeypatch.setattr(lab, 'SCRIPT_HASHES', scripts); monkeypatch.setattr(lab, 'BOARD_HASHES', boards)
    (project / 'tools').mkdir(); (project / 'tools/interactive.py').write_bytes(b'authored script')
    (project / 'qemu').mkdir(); (project / 'qemu/nikon-z6.c').write_bytes(b'authored board')
    binary = project / '.build/build/qemu-system-arm'; binary.parent.mkdir(parents=True); binary.write_bytes(b'authored fake binary')
    write_json(project / '.local/build-manifest.json', {'qemu_version': lab.QEMU_VERSION,
               'upstream_archive_sha256': lab.ARCHIVE_SHA256, 'binary': lab._wsl_path(binary) if os.name == 'nt' else str(binary),
               'binary_sha256': hashlib.sha256(binary.read_bytes()).hexdigest(), 'sources': boards})
    write_json(home / 'nikon-z6-lab.json', {'distro': 'Debian', 'revision': lab.REVISION, 'project': str(project)})
    monkeypatch.setattr(Task, 'run', lambda *a, **k: lab.REVISION + '\n')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
    monkeypatch.setattr(lab, 'PORT', port)
    return device, home, project, source, decrypted, main


def test_prepare_recovers_original_crc_and_does_not_modify_source(tmp_path, monkeypatch):
    device, home, _, source, decrypted, main = setup_lab(tmp_path, monkeypatch)
    before = tuple(p.read_bytes() for p in (source, decrypted, main))
    record = lab.prepare_lab(device, home, Task(lambda _: None))
    output = Path(record['main'])
    assert output.read_bytes() == main.read_bytes() + bytes.fromhex('f92d')
    assert record['restored_crc16'] == 'f92d' and not record['natural_power_on_verified']
    assert record['assisted_startup'] and record['guest_state_modified']
    assert tuple(p.read_bytes() for p in (source, decrypted, main)) == before
    assert lab.prepare_lab(device, home, Task(lambda _: None))['main'] == str(output)


def test_quick_eligibility_never_hashes_or_executes(tmp_path, monkeypatch):
    device, home, *_ = setup_lab(tmp_path, monkeypatch)
    monkeypatch.setattr(lab, '_hash', lambda *a: pytest.fail('UI eligibility must remain cheap'))
    assert lab.lab_available(device, home)
    assert not lab.lab_available(tmp_path / 'missing', home)


@pytest.mark.parametrize('which', [3, 4, 5])
def test_same_size_input_tampering_is_rejected_before_prepare(tmp_path, monkeypatch, which):
    values = setup_lab(tmp_path, monkeypatch); device, home = values[:2]
    path = values[which]; path.write_bytes(b'x' * path.stat().st_size)
    with pytest.raises(ValueError, match='changed'):
        lab.prepare_lab(device, home, Task(lambda _: None))
    assert not (device / 'nikon-z6/main.bin').exists()


def test_existing_unknown_prepared_main_is_never_overwritten(tmp_path, monkeypatch):
    device, home, *_ = setup_lab(tmp_path, monkeypatch)
    target = device / 'nikon-z6/main.bin'; target.parent.mkdir(); target.write_bytes(b'keep')
    with pytest.raises(ValueError, match='refusing to overwrite'):
        lab.prepare_lab(device, home, Task(lambda _: None))
    assert target.read_bytes() == b'keep'


@pytest.mark.parametrize('bad_path', ['../outside.bin', '/outside.bin', 'C:/outside.bin', 'sub/../outside.bin'])
def test_artifact_path_escape_refused(tmp_path, monkeypatch, bad_path):
    device, home, *_ = setup_lab(tmp_path, monkeypatch)
    manifest = json.loads((device / 'extracted/extraction.json').read_text())
    manifest['artifacts'][0]['path'] = bad_path
    write_json(device / 'extracted/extraction.json', manifest)
    assert not lab.lab_available(device, home)
    with pytest.raises(ValueError): lab.prepare_lab(device, home)


def test_extracted_hardlink_refused(tmp_path, monkeypatch):
    device, home, _, _, _, main = setup_lab(tmp_path, monkeypatch)
    outside = tmp_path / 'hardlinked.bin'; os.link(main, outside)
    with pytest.raises(ValueError, match='hardlinked'): lab.prepare_lab(device, home)
    assert not lab.lab_available(device, home)


def test_junction_flag_on_lock_is_refused_before_open(tmp_path, monkeypatch):
    device, home, *_ = setup_lab(tmp_path, monkeypatch)
    lock = device / 'session.lock'; lock.write_bytes(b'keep'); original = Path.lstat
    def lstat(path, *a, **k):
        metadata = original(path, *a, **k)
        return SimpleNamespace(st_mode=metadata.st_mode, st_file_attributes=0x400) if path == lock else metadata
    monkeypatch.setattr(Path, 'lstat', lstat)
    with pytest.raises(ValueError, match='junction'): lab.prepare_lab(device, home)
    assert lock.read_bytes() == b'keep'


def test_cancel_during_output_write_cleans_only_temporary_file(tmp_path, monkeypatch):
    device, home, *_ = setup_lab(tmp_path, monkeypatch)
    class CancellingTask(Task):
        def check(self):
            if (device / 'nikon-z6').exists() and list((device / 'nikon-z6').glob('main-*.tmp')):
                self.cancelled.set()
            super().check()
    with pytest.raises(Cancelled): lab.prepare_lab(device, home, CancellingTask(lambda _: None))
    assert not list((device / 'nikon-z6').glob('main*'))
    assert (device / 'extracted/module-003.bin').is_file()


def test_input_change_during_copy_rejected_before_commit(tmp_path, monkeypatch):
    device, home, _, source, *_ = setup_lab(tmp_path, monkeypatch)
    real_hash = lab._hash; calls = []
    def changing_hash(path, task):
        calls.append(path)
        if path == source and len(calls) > 3: source.write_bytes(b'x' * source.stat().st_size)
        return real_hash(path, task)
    monkeypatch.setattr(lab, '_hash', changing_hash)
    with pytest.raises(ValueError, match='changed during preparation'):
        lab.prepare_lab(device, home, Task(lambda _: None))
    assert not list((device / 'nikon-z6').glob('main*'))


@pytest.mark.parametrize('filename', ['tools/interactive.py', 'qemu/nikon-z6.c', '.build/build/qemu-system-arm'])
def test_pinned_sources_and_binary_rechecked_before_launch(tmp_path, monkeypatch, filename):
    device, home, project, *_ = setup_lab(tmp_path, monkeypatch)
    (project / filename).write_bytes(b'tampered')
    with pytest.raises(ValueError, match='changed|differs'):
        lab.build_lab_command(device, home, Task(lambda _: None))


def test_command_is_separate_arguments_and_explicit_owned_output(tmp_path, monkeypatch):
    device, home, *_ = setup_lab(tmp_path, monkeypatch)
    command, url = lab.build_lab_command(device, home, Task(lambda _: None))
    cfg = json.loads(command[-1])
    assert cfg['mode'] == 'interactive' and cfg['main_sha256'] == lab.MAIN_SHA256
    assert 'upstream project' in cfg['project']
    assert url == f'http://127.0.0.1:{lab.PORT}'
    assert cfg['out'].endswith(next((device / 'nikon-z6/runs').iterdir()).name)
    if os.name == 'nt': assert command[:5] == ['wsl.exe', '-d', 'Debian', '--exec', 'python3']


def test_another_listener_is_not_used_or_terminated(tmp_path, monkeypatch):
    device, home, *_ = setup_lab(tmp_path, monkeypatch)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', lab.PORT)); sock.listen()
        with pytest.raises(RuntimeError, match='port is busy'): lab.build_lab_command(device, home)


@pytest.mark.parametrize('distro', ['--shutdown', 'Debian\nother', ''])
def test_invalid_distribution_never_becomes_a_wsl_option(tmp_path, monkeypatch, distro):
    device, home, project, *_ = setup_lab(tmp_path, monkeypatch)
    write_json(home / 'nikon-z6-lab.json', {'distro': distro, 'project': str(project), 'revision': lab.REVISION})
    assert not lab.lab_available(device, home)


class RecordedInput(io.BytesIO):
    def close(self): self.saved = self.getvalue(); super().close()


class FakeProcess:
    def __init__(self, output):
        self.stdin = RecordedInput(); self.stdout = io.BytesIO(output)
        self.returncode = None; self.waited = False
    def poll(self): return self.returncode
    def wait(self, timeout=None): self.waited = True; self.returncode = 0; return 0
    def terminate(self): pytest.fail('Expected graceful owned stop')


def fake_popen(monkeypatch, process, *, evidence=True):
    def create(command, **kwargs):
        if evidence:
            cfg = json.loads(command[-1]); mapped = cfg['out']
            if os.name == 'nt': mapped = mapped[5].upper() + ':/' + mapped[7:]
            write_json(Path(mapped) / 'evidence.json', {'owned_guest_processes_reaped': True})
        return process
    monkeypatch.setattr(lab.subprocess, 'Popen', create)


def test_cancel_stops_only_owned_transport_and_releases_guest_lock(tmp_path, monkeypatch):
    device, home, *_ = setup_lab(tmp_path, monkeypatch)
    url = f'http://127.0.0.1:{lab.PORT}'
    process = FakeProcess(json.dumps({'event': 'ready', 'url': url, 'session': 'owned'}).encode() + b'\n')
    fake_popen(monkeypatch, process); task = Task(lambda _: None); callbacks = []
    def ready(value): callbacks.append(value); task.cancelled.set()
    with pytest.raises(Cancelled): lab.run_lab(device, home, task, on_ready=ready)
    assert callbacks == [url] and process.waited and process.stdin.saved == b'stop\n'
    record = json.loads((device / 'nikon-z6/session.json').read_text())
    assert record['process_reaped'] and record['owned_guest_processes_reaped'] and record['status'] == 'stopped'
    assert not record['natural_power_on_verified']
    with lab.GuestLock(device): pass


@pytest.mark.parametrize('failure', ['construct', 'start'])
def test_reader_failure_still_reaps_transport(tmp_path, monkeypatch, failure):
    device, home, *_ = setup_lab(tmp_path, monkeypatch); process = FakeProcess(b''); fake_popen(monkeypatch, process)
    class BrokenThread:
        def __init__(self, *a, **k):
            if failure == 'construct': raise RuntimeError('authored reader failure')
        def start(self): raise RuntimeError('authored reader failure')
    monkeypatch.setattr(lab.threading, 'Thread', BrokenThread)
    with pytest.raises(RuntimeError, match='reader failure'): lab.run_lab(device, home)
    assert process.waited and process.stdin.saved == b'stop\n' and process.stdout.closed


def test_spoofed_url_never_opens_foreign_server(tmp_path, monkeypatch):
    device, home, *_ = setup_lab(tmp_path, monkeypatch)
    process = FakeProcess(b'{"event":"ready","url":"http://example.invalid"}\n'); fake_popen(monkeypatch, process)
    with pytest.raises(RuntimeError, match='unexpected local URL'):
        lab.run_lab(device, home, on_ready=lambda _: pytest.fail('Unexpected URL reached callback'))
    assert process.waited


def test_oversized_supervisor_output_stops_owned_transport(tmp_path, monkeypatch):
    device, home, *_ = setup_lab(tmp_path, monkeypatch)
    process = FakeProcess(b'x' * 5000 + b'\n'); fake_popen(monkeypatch, process)
    with pytest.raises(RuntimeError, match='exceeds its bound'):
        lab.run_lab(device, home, Task(lambda _: None))
    assert process.waited and process.stdin.saved == b'stop\n'


def test_zero_exit_without_menu_evidence_is_not_success(tmp_path, monkeypatch):
    device, home, *_ = setup_lab(tmp_path, monkeypatch)
    process = FakeProcess(b'{"event":"stopped"}\n'); fake_popen(monkeypatch, process, evidence=False)
    with pytest.raises(RuntimeError, match='without verified menu evidence'):
        lab.run_menu_probe(device, home, Task(lambda _: None))
    record = json.loads((device / 'nikon-z6/session.json').read_text())
    assert not record['menu_runtime_verified'] and record['status'] == 'cleanup-incomplete'


def test_all_kill_waits_are_bounded_and_incomplete_cleanup_is_recorded(tmp_path, monkeypatch):
    device, home, *_ = setup_lab(tmp_path, monkeypatch)
    class Unreaped(FakeProcess):
        def __init__(self): super().__init__(b'{"event":"stopped"}\n'); self.timeouts = []
        def wait(self, timeout=None):
            assert timeout is not None; self.timeouts.append(timeout)
            raise subprocess.TimeoutExpired('authored', timeout)
        def terminate(self): pass
        def kill(self): pass
    process = Unreaped(); fake_popen(monkeypatch, process)
    with pytest.raises(RuntimeError): lab.run_menu_probe(device, home, Task(lambda _: None))
    assert process.timeouts == [12, 3, 3]
    record = json.loads((device / 'nikon-z6/session.json').read_text())
    assert not record['process_reaped'] and record['status'] == 'cleanup-incomplete'


@pytest.mark.parametrize('seconds', [True, 0, 19, 121, float('nan'), '60'])
def test_probe_budget_is_bounded(seconds):
    with pytest.raises(ValueError, match='20..120'): lab.run_menu_probe(Path('unused'), Path('unused'), seconds=seconds)


def runner_fixture(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location('authored_z6_runner_test', lab.RUNNER)
    runner = importlib.util.module_from_spec(spec); spec.loader.exec_module(runner)
    project, out = tmp_path / 'source', tmp_path / 'run'
    project.mkdir(); out.mkdir(); (project / 'tools').mkdir()
    qemu = project / '.build/build/qemu-system-arm'; qemu.parent.mkdir(parents=True); qemu.write_bytes(b'not executable authored binary')
    image = tmp_path / 'main.bin'; image.write_bytes(b'authored CRC main')
    hashes = {'tools/interactive.py': hashlib.sha256(b'authored helper marker').hexdigest()}
    (project / 'tools/interactive.py').write_bytes(b'authored helper marker')
    monkeypatch.setattr(runner, 'HASHES', hashes)
    monkeypatch.setattr(runner, 'MAIN_SIZE', image.stat().st_size)
    monkeypatch.setattr(runner, 'MAIN_SHA256', hashlib.sha256(image.read_bytes()).hexdigest())
    configuration = dict(project=str(project), main=str(image), qemu=str(qemu), out=str(out),
                         revision=runner.REVISION, script_hashes=hashes, mode='probe', seconds=20, port=8789,
                         main_size=runner.MAIN_SIZE, main_sha256=runner.MAIN_SHA256,
                         qemu_sha256=hashlib.sha256(qemu.read_bytes()).hexdigest(),
                         runner_sha256=hashlib.sha256(lab.RUNNER.read_bytes()).hexdigest())
    monkeypatch.setattr(sys, 'argv', [str(lab.RUNNER), json.dumps(configuration)])
    # Suppress only the runner's transport/limit watcher threads; the actual
    # worker thread still constructs and owns a fake Popen child below.
    original_thread = runner.threading.Thread
    class Suppressed:
        def start(self): pass
    def thread_factory(*a, **k):
        if k.get('target').__name__ in ('stdin_watch', 'watchdog'): return Suppressed()
        return original_thread(*a, **k)
    monkeypatch.setattr(runner.threading, 'Thread', thread_factory)
    return runner, configuration, project, out, qemu, image


def test_runner_rechecks_firmware_before_import_or_process_creation(tmp_path, monkeypatch):
    runner, _, _, _, _, image = runner_fixture(tmp_path, monkeypatch)
    image.write_bytes(b'x' * image.stat().st_size)
    monkeypatch.setattr(runner.subprocess, 'Popen', lambda *a, **k: pytest.fail('No child may start after a changed MAIN'))
    with pytest.raises(ValueError, match='changed before launch'): runner.main()


def test_runner_rechecks_helper_hash_before_import(tmp_path, monkeypatch):
    runner, _, project, _, _, _ = runner_fixture(tmp_path, monkeypatch)
    (project / 'tools/interactive.py').write_bytes(b'tampered')
    with pytest.raises(ValueError, match='Python source changed'): runner.main()


def test_runner_constructor_connection_failure_reaps_its_owned_child(tmp_path, monkeypatch):
    runner, cfg, _, out, binary, image = runner_fixture(tmp_path, monkeypatch)
    child = FakeProcess(b''); child.pid = 12345; child.waits = []; child.started = False
    def wait(timeout=None):
        assert timeout is not None; child.waits.append(timeout); child.returncode = 0; return 0
    child.wait = wait
    def create(*a, **k):
        assert k['start_new_session'] and k['stdin'] == subprocess.DEVNULL
        assert 'Z6_LCD_SCANOUT_TEST' not in k['env']
        child.started = True; return child
    monkeypatch.setattr(runner.subprocess, 'Popen', create)
    killed = []
    monkeypatch.setattr(runner.os, 'killpg', lambda pid, sig: killed.append((pid, sig)), raising=False)
    class AuthoredCamera:
        def __init__(self, *args, **kwargs):
            self.remote = self.monitor = self.proc = self.console = None
            self.stamp = 'authored-owned-session'
        def boot(self):
            self.proc = runner.subprocess.Popen([str(binary), '-bios', str(image)], env={'Z6_LCD_SCANOUT_TEST': '1'})
            raise OSError('authored RSP constructor failure')
    monkeypatch.setitem(sys.modules, 'interactive', SimpleNamespace(Camera=AuthoredCamera, subprocess=runner.subprocess, tempfile=runner.tempfile))
    actual_temporary_directory = runner.tempfile.TemporaryDirectory
    monkeypatch.setattr(runner.tempfile, 'TemporaryDirectory',
                        lambda **kwargs: actual_temporary_directory(prefix='authored-z6-', dir=tmp_path))
    # main narrows its import path by design; preserve pytest's module paths.
    monkeypatch.setattr(sys, 'path', list(sys.path))
    monkeypatch.setattr(sys, 'meta_path', list(sys.meta_path))
    assert runner.main() == 1
    evidence = json.loads((out / 'evidence.json').read_text())
    assert child.started and child.waits == [3] and len(killed) == 1
    assert killed[0][0] == child.pid and evidence['owned_guest_processes_reaped']
    assert evidence['owned_guest_pids'] == [child.pid]
    assert not evidence['menu_runtime_verified'] and not evidence['natural_power_on_verified']
    assert 'RSP constructor failure' in evidence['error']
    assert evidence['renderer'] == 'upstream original-GPU GUI-surface probe'
    assert not evidence['lcd_scanout_verified']


def test_valid_but_poisoned_bytecode_cannot_replace_reviewed_source(tmp_path, monkeypatch):
    import importlib._bootstrap_external as bootstrap
    runner, cfg, project, *_ = runner_fixture(tmp_path, monkeypatch)
    source = project / 'tools/interactive.py'
    reviewed = b"raise RuntimeError('reviewed-source-executed')\n"
    source.write_bytes(reviewed)
    cfg['script_hashes'] = {'tools/interactive.py': hashlib.sha256(reviewed).hexdigest()}
    monkeypatch.setattr(runner, 'HASHES', cfg['script_hashes'])
    cache = Path(importlib.util.cache_from_source(str(source))); cache.parent.mkdir()
    poison = compile("raise RuntimeError('poisoned-cache-executed')", str(source), 'exec')
    cache.write_bytes(bootstrap._code_to_timestamp_pyc(poison, int(source.stat().st_mtime), len(reviewed)))
    monkeypatch.setattr(sys, 'argv', [str(lab.RUNNER), json.dumps(cfg)])
    monkeypatch.setattr(sys, 'path', list(sys.path)); monkeypatch.setattr(sys, 'meta_path', list(sys.meta_path))
    monkeypatch.delitem(sys.modules, 'interactive', raising=False)
    monkeypatch.setattr(runner.subprocess, 'Popen', lambda *a, **k: pytest.fail('Source import must precede execution'))
    with pytest.raises(RuntimeError, match='reviewed-source-executed'): runner.main()


def test_ready_alone_with_unverified_guest_cleanup_cannot_return_success(tmp_path, monkeypatch):
    device, home, *_ = setup_lab(tmp_path, monkeypatch)
    url = f'http://127.0.0.1:{lab.PORT}'
    process = FakeProcess((json.dumps({'event': 'ready', 'url': url, 'session': 'owned'}) + '\n'
                           + json.dumps({'event': 'stopped'}) + '\n').encode())
    fake_popen(monkeypatch, process, evidence=False)
    with pytest.raises(RuntimeError, match='cleanup could not be verified'):
        lab.run_lab(device, home, Task(lambda _: None))
    assert json.loads((device / 'nikon-z6/session.json').read_text())['status'] == 'cleanup-incomplete'


def test_fingerprint_excludes_only_the_measured_blink_marker(tmp_path, monkeypatch):
    runner, *_ = runner_fixture(tmp_path, monkeypatch)
    pixels = bytearray(720 * 480 * 3)
    baseline = runner.menu_fingerprint(pixels, 720, 480)
    pixels[(448 * 720 + 646) * 3] = 64
    assert runner.menu_fingerprint(pixels, 720, 480) == baseline
    pixels[(447 * 720 + 646) * 3] = 64
    assert runner.menu_fingerprint(pixels, 720, 480) != baseline
    with pytest.raises(ValueError, match='exactly'):
        runner.menu_fingerprint(pixels, 1024, 682)


@pytest.mark.parametrize('output', ['.local/build-manifest.json', '.build/build/qemu-system-arm'])
def test_builder_never_runs_or_changes_an_existing_install(tmp_path, monkeypatch, output):
    project = tmp_path / 'checkout'; project.mkdir()
    target = project / output; target.parent.mkdir(parents=True); target.write_bytes(b'keep existing install')
    script = (lab.RUNNER.parent / 'build.sh').read_text(encoding='utf-8').split("<<'PY'\n", 1)[1].rsplit('\nPY', 1)[0]
    monkeypatch.setattr(sys, 'argv', ['build', str(project), str(tmp_path / 'new-work')])
    monkeypatch.setattr(subprocess, 'check_output', lambda *a, **k: pytest.fail('Preflight must precede git/build execution'))
    monkeypatch.setattr(subprocess, 'run', lambda *a, **k: pytest.fail('Existing installation must not launch the builder'))
    with pytest.raises(ValueError, match='Refusing to overwrite'): exec(compile(script, 'authored-build-preflight', 'exec'), {})
    assert target.read_bytes() == b'keep existing install' and not (tmp_path / 'new-work').exists()


def test_builder_rejects_hardlinked_manifest_before_execution(tmp_path, monkeypatch):
    project = tmp_path / 'checkout'; project.mkdir(); (project / '.local').mkdir()
    target = project / '.local/build-manifest.json'; target.write_bytes(b'keep linked manifest')
    os.link(target, tmp_path / 'linked-outside.json')
    script = (lab.RUNNER.parent / 'build.sh').read_text(encoding='utf-8').split("<<'PY'\n", 1)[1].rsplit('\nPY', 1)[0]
    monkeypatch.setattr(sys, 'argv', ['build', str(project), str(tmp_path / 'new-work')])
    monkeypatch.setattr(subprocess, 'run', lambda *a, **k: pytest.fail('A linked manifest must not launch the builder'))
    with pytest.raises(ValueError, match='Hardlinked'): exec(compile(script, 'authored-build-preflight', 'exec'), {})
    assert target.read_bytes() == b'keep linked manifest'
