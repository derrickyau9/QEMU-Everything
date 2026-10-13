"""Authored ROM fixtures and owned-process stubs; no Canon bytes or guest runs."""
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import sys
from types import SimpleNamespace
import uuid
import zlib

import pytest

from devkit import canon_ui as lab
from devkit.core import Cancelled, Task, write_json


def quiet():
    return Task(lambda _: None)


@pytest.fixture
def authored_roms(tmp_path, monkeypatch):
    inputs = tmp_path / 'authored inputs'
    inputs.mkdir()
    paths = []
    profile = {}
    for index, name in enumerate(('ROM0.BIN', 'ROM1.BIN', 'SFDATA.BIN')):
        data = bytes([33 + index]) * (128 * (index + 1))
        path = inputs / name
        path.write_bytes(data)
        paths.append(path)
        profile[name] = (len(data), hashlib.sha256(data).hexdigest())
    monkeypatch.setattr(lab, 'ROM_FILES', profile)
    return tuple(paths)


@pytest.fixture
def library(tmp_path, monkeypatch, authored_roms):
    device = lab.import_roms(*authored_roms, tmp_path / 'device', quiet())
    home = tmp_path / 'home'
    home.mkdir()
    write_json(home / 'canon-ui-lab.json', dict(revision=lab.REVISION, distro='Debian',
               qemu='/home/authored/qemu-system-arm', qemu_sha256='a' * 64))
    # Reserve no real service or guest port; use an OS-selected ephemeral port.
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
    monkeypatch.setattr(lab, 'PORT', port)
    return device, home


def test_import_is_atomic_private_copy_without_boot_claim(tmp_path, authored_roms):
    original = tuple(path.read_bytes() for path in authored_roms)
    device = lab.import_roms(*authored_roms, tmp_path / 'new entry', quiet())
    manifest = json.loads((device / 'canon-roms.json').read_text(encoding='utf-8'))
    assert manifest['profile'] == lab.PROFILE and manifest['revision'] == lab.REVISION
    assert not manifest['original_ui_verified'] and not manifest['vendor_distribution_included']
    metadata = json.loads((device / 'device.json').read_text(encoding='utf-8'))
    assert metadata['adapter'] == 'canon-rom-set' and metadata['version'] == '1.1.2'
    assert not metadata['original_ui_verified']
    assert (device / 'source/firmware.bin').read_bytes() == original[1]
    assert tuple((device / 'roms/70D' / path.name).read_bytes() for path in authored_roms) == original
    assert tuple(path.read_bytes() for path in authored_roms) == original
    assert not list(tmp_path.glob('canon-rom-import-*'))


@pytest.mark.parametrize('file_index', [0, 1, 2])
def test_import_rejects_same_size_tampering_before_publish(tmp_path, authored_roms, file_index):
    path = authored_roms[file_index]
    path.write_bytes(b'x' * path.stat().st_size)
    destination = tmp_path / 'entry'
    with pytest.raises(ValueError, match='reviewed'):
        lab.import_roms(*authored_roms, destination, quiet())
    assert not destination.exists() and not list(tmp_path.glob('canon-rom-import-*'))


def test_import_rechecks_original_input_before_commit(tmp_path, monkeypatch, authored_roms):
    original_hash = lab._hash
    changed = False
    def mutate_before_recheck(path, task):
        nonlocal changed
        if not changed:
            changed = True
            path.write_bytes(b'x' * path.stat().st_size)
        return original_hash(path, task)
    monkeypatch.setattr(lab, '_hash', mutate_before_recheck)
    destination = tmp_path / 'entry'
    with pytest.raises(ValueError, match='changed during import'):
        lab.import_roms(*authored_roms, destination, quiet())
    assert changed and not destination.exists() and not list(tmp_path.glob('canon-rom-import-*'))


def test_cancel_partial_import_removes_staging_and_keeps_inputs(tmp_path, authored_roms):
    original = tuple(path.read_bytes() for path in authored_roms)
    class CancelStaged(Task):
        def check(self):
            if any(tmp_path.glob('canon-rom-import-*/entry/roms/70D/ROM0.BIN')):
                raise Cancelled('authored cancellation')
    with pytest.raises(Cancelled):
        lab.import_roms(*authored_roms, tmp_path / 'entry', CancelStaged())
    assert not (tmp_path / 'entry').exists() and not list(tmp_path.glob('canon-rom-import-*'))
    assert tuple(path.read_bytes() for path in authored_roms) == original


@pytest.mark.parametrize('kind', ['file', 'directory'])
def test_import_never_overwrites_existing_destination(tmp_path, authored_roms, kind):
    destination = tmp_path / 'entry'
    if kind == 'directory':
        destination.mkdir()
        marker = destination / 'keep'
    else:
        marker = destination
    marker.write_bytes(b'owned existing content')
    with pytest.raises(FileExistsError):
        lab.import_roms(*authored_roms, destination, quiet())
    assert marker.read_bytes() == b'owned existing content'


@pytest.mark.parametrize('link', ['symlink', 'hardlink'])
def test_linked_rom_source_rejected(tmp_path, authored_roms, link):
    alias = tmp_path / 'linked-ROM0.BIN'
    try:
        if link == 'symlink': alias.symlink_to(authored_roms[0])
        else: os.link(authored_roms[0], alias)
    except OSError:
        pytest.skip('Links unavailable on this host')
    with pytest.raises(ValueError, match='link'):
        lab.import_roms(alias, *authored_roms[1:], tmp_path / 'entry', quiet())
    assert not (tmp_path / 'entry').exists()


def test_destination_symlink_ancestor_rejected(tmp_path, authored_roms):
    target = tmp_path / 'existing'
    target.mkdir()
    link = tmp_path / 'linked'
    try: link.symlink_to(target, target_is_directory=True)
    except OSError: pytest.skip('Symlinks unavailable on this host')
    with pytest.raises(ValueError, match='symlink|junction'):
        lab.import_roms(*authored_roms, link / 'entry', quiet())
    assert not (target / 'entry').exists()


@pytest.mark.parametrize('name', ['../outside', '/absolute', 'roms/../outside', 'roms/CON', 'roms/file:stream'])
def test_path_traversal_windows_devices_and_ads_rejected(tmp_path, name):
    with pytest.raises(ValueError): lab._path(tmp_path, name)


@pytest.mark.parametrize('field,value', [('adapter', 'canon-fir'), ('adapter', 'camera-canon'),
    ('version', '1.1.3'), ('model', 'EOS 50D'), ('sha256', '0' * 64)])
def test_update_fir_and_other_profiles_never_qualify(library, field, value):
    device, home = library
    metadata = json.loads((device / 'device.json').read_text(encoding='utf-8'))
    metadata[field] = value
    write_json(device / 'device.json', metadata)
    assert not lab.lab_available(device, home)
    with pytest.raises(ValueError, match='exact separately imported'):
        lab._inputs(device)


@pytest.mark.parametrize('change', ['duplicate', 'traversal', 'size', 'digest', 'extra', 'revision'])
def test_manifest_bounds_profile_and_unique_file_set(library, change):
    device, _ = library
    manifest = json.loads((device / 'canon-roms.json').read_text(encoding='utf-8'))
    if change == 'duplicate': manifest['files'][1] = dict(manifest['files'][0])
    elif change == 'traversal': manifest['files'][0]['path'] = '../ROM0.BIN'
    elif change == 'size': manifest['files'][0]['size'] += 1
    elif change == 'digest': manifest['files'][0]['sha256'] = '0' * 64
    elif change == 'extra': manifest['files'].append(dict(manifest['files'][0]))
    else: manifest['revision'] = '0' * 40
    write_json(device / 'canon-roms.json', manifest)
    with pytest.raises(ValueError): lab._inputs(device)


@pytest.mark.parametrize('field,value', [('revision', '0' * 40), ('distro', '../Debian'),
    ('distro', 'Debian\ncommand'), ('distro', ''), ('qemu', 'relative/binary'),
    ('qemu', '/home/../binary'), ('qemu', '/home/binary\n'), ('qemu', '/home\\binary'),
    ('qemu_sha256', 'a' * 63), ('qemu_sha256', 'A' * 64)])
def test_config_requires_pin_explicit_distro_and_absolute_linux_binary(library, field, value):
    _, home = library
    config = json.loads((home / 'canon-ui-lab.json').read_text(encoding='utf-8'))
    config[field] = value
    write_json(home / 'canon-ui-lab.json', config)
    with pytest.raises(ValueError): lab._config(home)


def test_eligibility_is_cheap_and_runtime_rehashes_before_process(library, monkeypatch):
    device, home = library
    original_hash = lab._hash
    monkeypatch.setattr(lab, '_hash', lambda *_: pytest.fail('Eligibility must not read all ROMs'))
    assert lab.lab_available(device, home)
    monkeypatch.setattr(lab, '_hash', original_hash)
    path = device / 'roms/70D/ROM1.BIN'
    path.write_bytes(b'x' * path.stat().st_size)
    monkeypatch.setattr(lab.subprocess, 'Popen', lambda *a, **k: pytest.fail('Changed ROM must not start a process'))
    with pytest.raises(ValueError, match='source changed'):
        lab.run_menu_probe(device, home, quiet())
    assert not (device / 'canon-ui').exists()
    with lab.GuestLock(device): pass


def test_hardlinked_session_lock_rejected_before_guest_lock(library, tmp_path, monkeypatch):
    device, home = library
    marker = device / 'session.lock'
    marker.write_bytes(b'keep lock')
    try: os.link(marker, tmp_path / 'outside-lock')
    except OSError: pytest.skip('Hard links unavailable on this host')
    monkeypatch.setattr(lab.subprocess, 'Popen', lambda *a, **k: pytest.fail('Linked lock must fail before launch'))
    with pytest.raises(ValueError, match='hardlinked'):
        lab.run_menu_probe(device, home, quiet())
    assert marker.read_bytes() == b'keep lock'


@pytest.mark.parametrize('seconds', [True, False, 0, 25, 121, float('nan'), float('inf'), '30'])
def test_probe_budget_bounded(seconds):
    with pytest.raises(ValueError, match='26..120'):
        lab.run_menu_probe(Path('unused'), Path('unused'), seconds=seconds)


class RecordedInput(io.BytesIO):
    def close(self):
        self.saved = self.getvalue()
        super().close()


class FakeProcess:
    def __init__(self, output):
        self.stdin = RecordedInput()
        self.stdout = io.BytesIO(output)
        self.returncode = None
        self.waits = []
    def poll(self): return self.returncode
    def wait(self, timeout=None):
        assert timeout is not None
        self.waits.append(timeout)
        self.returncode = 0
        return 0
    def terminate(self): pytest.fail('Expected graceful owned stop')


def host_output(payload):
    value = payload['destination']
    if os.name == 'nt':
        assert value.startswith('/mnt/')
        return Path(value[5].upper() + ':/' + value[7:])
    return Path(value)


def fake_transport(monkeypatch, process, *, evidence=None):
    commands = []
    def create(command, **options):
        commands.append((command, options))
        payload = json.loads(command[-1])
        assert payload['revision'] == lab.REVISION
        assert '-I' in command and '-B' in command
        if evidence is not None:
            write_json(host_output(payload) / 'evidence.json', evidence)
        return process
    monkeypatch.setattr(lab.subprocess, 'Popen', create)
    return commands


def cleanup_evidence(**fields):
    return {'process_reaped': True, 'original_ui_verified': False,
            'native_navigation_verified': False, **fields}


def test_interactive_cancel_reaps_only_owned_transport_and_unlocks(library, monkeypatch):
    device, home = library
    url = f'http://127.0.0.1:{lab.PORT}'
    process = FakeProcess((json.dumps(dict(event='ready', url=url, session='authored-owned')) + '\n').encode())
    commands = fake_transport(monkeypatch, process, evidence=cleanup_evidence(original_ui_verified=True))
    task = quiet()
    callbacks = []
    def ready(value):
        callbacks.append(value)
        task.cancelled.set()
    with pytest.raises(Cancelled): lab.run_lab(device, home, task, on_ready=ready)
    assert callbacks == [url] and len(commands) == 1
    assert process.waits == [12] and process.stdin.saved == b'stop\n' and process.stdout.closed
    record = json.loads((device / 'canon-ui/session.json').read_text(encoding='utf-8'))
    assert record['status'] == 'stopped' and record['process_reaped'] and record['owned_guest_processes_reaped']
    assert not record['firmware_instructions_patched'] and not record['native_navigation_verified']
    with lab.GuestLock(device): pass


@pytest.mark.parametrize('failure', ['construct', 'start'])
def test_reader_startup_failure_still_reaps_owned_transport(library, monkeypatch, failure):
    device, home = library
    process = FakeProcess(b'')
    fake_transport(monkeypatch, process, evidence=cleanup_evidence())
    class BrokenThread:
        def __init__(self, *a, **k):
            if failure == 'construct': raise RuntimeError('authored reader failure')
        def start(self): raise RuntimeError('authored reader failure')
    monkeypatch.setattr(lab.threading, 'Thread', BrokenThread)
    with pytest.raises(RuntimeError, match='reader failure'): lab.run_menu_probe(device, home, quiet())
    assert process.waits == [12] and process.stdin.saved == b'stop\n' and process.stdout.closed


def test_foreign_ready_url_is_never_given_to_ui(library, monkeypatch):
    device, home = library
    process = FakeProcess(b'{"event":"ready","url":"http://example.invalid"}\n')
    fake_transport(monkeypatch, process, evidence=cleanup_evidence())
    with pytest.raises(RuntimeError, match='Unexpected Canon local URL'):
        lab.run_lab(device, home, quiet(), on_ready=lambda _: pytest.fail('Foreign URL reached callback'))
    assert process.waits == [12]


def test_verified_event_alone_cannot_replace_saved_evidence(library, monkeypatch):
    device, home = library
    output = b'{"event":"result","original_ui_verified":true,"native_navigation_verified":true}\n{"event":"stopped"}\n'
    process = FakeProcess(output)
    fake_transport(monkeypatch, process, evidence=cleanup_evidence())
    with pytest.raises(RuntimeError, match='Saved Canon evidence'):
        lab.run_menu_probe(device, home, quiet())
    record = json.loads((device / 'canon-ui/session.json').read_text(encoding='utf-8'))
    assert not record['original_ui_verified'] and not record['native_navigation_verified']


def test_success_requires_saved_menu_navigation_and_guest_cleanup(library, monkeypatch):
    device, home = library
    output = b'{"event":"result","original_ui_verified":true,"native_navigation_verified":true}\n{"event":"stopped"}\n'
    process = FakeProcess(output)
    fake_transport(monkeypatch, process, evidence=cleanup_evidence(original_ui_verified=True,
                    native_navigation_verified=True, original_reset_boot_verified=True))
    result = lab.run_menu_probe(device, home, quiet())
    assert result['original_ui_verified'] and result['native_navigation_verified']
    record = json.loads((device / 'canon-ui/session.json').read_text(encoding='utf-8'))
    assert record['owned_guest_processes_reaped'] and record['original_reset_boot_verified']
    assert record['status'] == 'stopped' and process.waits == [12]


def test_zero_exit_without_evidence_is_not_success(library, monkeypatch):
    device, home = library
    process = FakeProcess(b'{"event":"stopped"}\n')
    fake_transport(monkeypatch, process)
    with pytest.raises(RuntimeError, match='without verified menu evidence'):
        lab.run_menu_probe(device, home, quiet())
    record = json.loads((device / 'canon-ui/session.json').read_text(encoding='utf-8'))
    assert record['status'] == 'cleanup-incomplete' and not record['original_ui_verified']


def test_supervisor_oversized_line_stops_owned_transport(library, monkeypatch):
    device, home = library
    process = FakeProcess(b'x' * 5000 + b'\n')
    fake_transport(monkeypatch, process, evidence=cleanup_evidence())
    with pytest.raises(RuntimeError, match='exceeds its bound'):
        lab.run_menu_probe(device, home, quiet())
    assert process.waits == [12] and process.stdin.saved == b'stop\n'


def test_unreaped_transport_has_bounded_waits_and_persists_cleanup_failure(library, monkeypatch):
    device, home = library
    class Unreaped(FakeProcess):
        def wait(self, timeout=None):
            assert timeout is not None
            self.waits.append(timeout)
            raise subprocess.TimeoutExpired('authored unreaped transport', timeout)
        def terminate(self): pass
        def kill(self): pass
    process = Unreaped(b'{"event":"stopped"}\n')
    fake_transport(monkeypatch, process, evidence=cleanup_evidence())
    with pytest.raises((RuntimeError, subprocess.TimeoutExpired)):
        lab.run_menu_probe(device, home, quiet())
    assert process.waits == [12, 3, 3]
    record = json.loads((device / 'canon-ui/session.json').read_text(encoding='utf-8'))
    assert record['status'] == 'cleanup-incomplete' and not record['process_reaped']
    assert process.stdout.closed


@pytest.fixture
def runner():
    # Each test gets a fresh module; no shared `runner` or `interactive` import.
    name = 'authored_canon_ui_runner_' + uuid.uuid4().hex
    spec = importlib.util.spec_from_file_location(name, lab.RUNNER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_ppm_parser_accepts_only_exact_guest_extent(runner):
    rgb = bytes([12, 34, 56]) * (720 * 480)
    ppm = b'P6\n720 480\n255\n' + rgb
    assert runner.parse_ppm(ppm) == rgb
    for bad in (ppm[:-1], ppm + b'x', ppm.replace(b'720 480', b'480 720', 1),
                ppm.replace(b'255', b'254', 1), b'P6\n#comment\n720 480\n255\n' + rgb):
        with pytest.raises(ValueError): runner.parse_ppm(bad)


def test_png_encodes_original_rgb_rows_and_valid_chunk_crcs(runner):
    rgb = bytes([12, 34, 56]) * (720 * 480)
    image = runner.png(rgb)
    assert image[:8] == b'\x89PNG\r\n\x1a\n'
    offset = 8
    chunks = []
    while offset < len(image):
        length = struct.unpack_from('>I', image, offset)[0]
        kind = image[offset + 4:offset + 8]
        data = image[offset + 8:offset + 8 + length]
        crc = struct.unpack_from('>I', image, offset + 8 + length)[0]
        assert crc == zlib.crc32(kind + data) & 0xffffffff
        chunks.append((kind, data))
        offset += length + 12
    assert offset == len(image) and [kind for kind, _ in chunks] == [b'IHDR', b'IDAT', b'IEND']
    assert struct.unpack('>2I5B', chunks[0][1]) == (720, 480, 8, 2, 0, 0, 0)
    rows = zlib.decompress(chunks[1][1])
    assert rows == b''.join(b'\0' + rgb[i:i + 2160] for i in range(0, len(rgb), 2160))
    with pytest.raises(ValueError): runner.png(rgb[:-1])


class QMPStream:
    def __init__(self, lines):
        self.lines = list(lines)
        self.writes = []
        self.closed = False
    def readline(self, limit):
        return self.lines.pop(0)[:limit] if self.lines else b''
    def write(self, value): self.writes.append(value)
    def close(self): self.closed = True


class QMPSocket:
    def __init__(self, lines, fail_connect=False):
        self.stream = QMPStream(lines)
        self.closed = False
        self.fail_connect = fail_connect
    def settimeout(self, value): self.timeout = value
    def connect(self, path):
        if self.fail_connect: raise OSError('authored QMP connect failure')
    def makefile(self, *a, **k): return self.stream
    def close(self): self.closed = True


@pytest.mark.parametrize('fault', ['connect', 'greeting', 'capabilities'])
def test_qmp_constructor_fault_closes_owned_handles(runner, monkeypatch, fault):
    lines = [b'{"QMP":{}}\n', b'{"return":{}}\n']
    if fault == 'greeting': lines[0] = b'{"unexpected":{}}\n'
    if fault == 'capabilities': lines[1] = b'{"error":{"desc":"authored failure"}}\n'
    sock = QMPSocket(lines, fail_connect=fault == 'connect')
    monkeypatch.setattr(runner, 'socket', SimpleNamespace(AF_UNIX=1, socket=lambda *_: sock))
    with pytest.raises((ValueError, OSError)): runner.QMP('/authored/owned.sock')
    assert sock.closed
    if fault != 'connect': assert sock.stream.closed


def test_qmp_allowed_key_uses_original_mpu_event_and_rejects_arbitrary_input(runner, monkeypatch):
    sock = QMPSocket([b'{"QMP":{}}\n', b'{"return":{}}\n', b'{"return":{}}\n'])
    monkeypatch.setattr(runner, 'socket', SimpleNamespace(AF_UNIX=1, socket=lambda *_: sock))
    qmp = runner.QMP('/authored/owned.sock')
    qmp.key('spc')
    value = json.loads(sock.stream.writes[-1])
    assert value == {'execute': 'send-key', 'arguments': {'keys': [{'type': 'qcode', 'data': 'spc'}], 'hold-time': 80}}
    writes = len(sock.stream.writes)
    with pytest.raises(ValueError): qmp.key('shell;command')
    assert len(sock.stream.writes) == writes
    qmp.close()
    assert sock.closed and sock.stream.closed


@pytest.mark.parametrize('line', [b'', b'x' * 65537 + b'\n', b'not-json\n'], ids=['missing', 'oversized', 'invalid-json'])
def test_qmp_missing_oversized_or_invalid_response_rejected(runner, line):
    qmp = object.__new__(runner.QMP)
    qmp.stream = QMPStream([line])
    with pytest.raises(ValueError): qmp._read()


def test_runner_regular_rejects_wrong_size_hash_and_links(runner, tmp_path):
    path = tmp_path / 'authored.bin'
    path.write_bytes(b'authored')
    digest = hashlib.sha256(b'authored').hexdigest()
    assert runner.regular(path, expected=8, digest=digest) == digest
    for options in ({'expected': 9}, {'maximum': 7}, {'digest': '0' * 64}):
        with pytest.raises(ValueError): runner.regular(path, **options)
    alias = tmp_path / 'hardlink.bin'
    try: os.link(path, alias)
    except OSError: pytest.skip('Hard links unavailable on this host')
    with pytest.raises(ValueError, match='singly linked'): runner.regular(path)


def runner_configuration(runner, tmp_path, authored_roms, monkeypatch):
    monkeypatch.setattr(runner, 'ROM_FILES', dict(lab.ROM_FILES))
    binary = tmp_path / 'authored-qemu'
    binary.write_bytes(b'not executed authored binary')
    configuration = dict(revision=runner.REVISION, mode='probe', seconds=26, port=8793,
                         qemu=str(binary), qemu_sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),
                         roms=str(authored_roms[0].parent), destination=str(tmp_path / 'output'))
    # Simulate only the supervisor's platform guard. pathlib and the actual
    # host os/subprocess remain untouched, keeping full-suite imports isolated.
    monkeypatch.setattr(runner, 'os', SimpleNamespace(**{**vars(os), 'name': 'posix'}))
    monkeypatch.setattr(runner, 'signal', SimpleNamespace(SIGINT=2, SIGTERM=15, SIGKILL=9, signal=lambda *a: None))
    class SuppressedThread:
        def __init__(self, *a, **k): pass
        def start(self): pass
        def join(self, timeout=None): assert timeout is not None
        def is_alive(self): return False
    monkeypatch.setattr(runner, 'threading', SimpleNamespace(Event=runner.threading.Event, Thread=SuppressedThread))
    monkeypatch.setattr(runner, 'cards', lambda work: None)
    return configuration


@pytest.mark.parametrize('which', ['rom', 'qemu'])
def test_runner_rehashes_before_any_process_or_output_creation(runner, tmp_path, authored_roms, monkeypatch, which):
    configuration = runner_configuration(runner, tmp_path, authored_roms, monkeypatch)
    path = authored_roms[1] if which == 'rom' else Path(configuration['qemu'])
    path.write_bytes(b'x' * path.stat().st_size)
    monkeypatch.setattr(runner.subprocess, 'Popen', lambda *a, **k: pytest.fail('Tampered input must never execute'))
    with pytest.raises(ValueError, match='checksum mismatch'): runner.run(configuration)
    assert not Path(configuration['destination']).exists()


def test_runner_existing_output_refuses_overwrite_before_process(runner, tmp_path, authored_roms, monkeypatch):
    configuration = runner_configuration(runner, tmp_path, authored_roms, monkeypatch)
    out = Path(configuration['destination'])
    out.mkdir()
    marker = out / 'keep'
    marker.write_bytes(b'owned existing output')
    monkeypatch.setattr(runner.subprocess, 'Popen', lambda *a, **k: pytest.fail('Existing output must not start a process'))
    with pytest.raises(FileExistsError): runner.run(configuration)
    assert marker.read_bytes() == b'owned existing output'


def test_runner_qmp_constructor_failure_reaps_owned_process_before_temp_cleanup(runner, tmp_path, authored_roms, monkeypatch):
    configuration = runner_configuration(runner, tmp_path, authored_roms, monkeypatch)
    process = FakeProcess(b'')
    process.pid = 12345
    launched = []
    def launch(command, **options):
        work = Path(options['cwd'])
        assert options['start_new_session'] and work.exists()
        assert command[command.index('-M') + 1] == '70D,firmware=boot=0'
        assert command[command.index('-nic') + 1] == 'none'
        assert all('snapshot=on' in value for value in command if value.startswith('if='))
        assert tuple((work / 'roms/70D' / path.name).read_bytes() for path in authored_roms) == tuple(path.read_bytes() for path in authored_roms)
        (work / 'qmp.sock').touch()
        launched.append(work)
        return process
    monkeypatch.setattr(runner.subprocess, 'Popen', launch)
    killed = []
    monkeypatch.setattr(runner.os, 'killpg', lambda pid, sig: killed.append((pid, sig)), raising=False)
    monkeypatch.setattr(runner, 'QMP', lambda path: (_ for _ in ()).throw(OSError('authored QMP constructor failure')))
    assert runner.run(configuration) == 1
    evidence = json.loads((Path(configuration['destination']) / 'evidence.json').read_text(encoding='utf-8'))
    assert killed == [(process.pid, runner.signal.SIGTERM)] and process.waits == [3]
    assert launched and not launched[0].exists()
    assert evidence['process_reaped'] and not evidence['original_ui_verified']
    assert not evidence['native_navigation_verified'] and evidence['source_hashes_unchanged']
    assert 'QMP constructor failure' in evidence['error']


@pytest.mark.parametrize('which', ['rom', 'qemu'])
def test_runner_rechecks_copied_sources_and_binary_immediately_before_launch(runner, tmp_path, authored_roms, monkeypatch, which):
    configuration = runner_configuration(runner, tmp_path, authored_roms, monkeypatch)
    original_regular = runner.regular
    path = authored_roms[0] if which == 'rom' else Path(configuration['qemu'])
    calls = 0
    def replace_before_second_check(candidate, **options):
        nonlocal calls
        if Path(candidate) == path:
            calls += 1
            if calls == 2: path.write_bytes(b'x' * path.stat().st_size)
        return original_regular(candidate, **options)
    monkeypatch.setattr(runner, 'regular', replace_before_second_check)
    monkeypatch.setattr(runner.subprocess, 'Popen', lambda *a, **k: pytest.fail('Runtime checksum failure must not execute'))
    assert runner.run(configuration) == 1
    evidence = json.loads((Path(configuration['destination']) / 'evidence.json').read_text(encoding='utf-8'))
    assert calls >= 2 and evidence['process_reaped'] and not evidence['original_ui_verified']
    assert 'checksum mismatch' in evidence['error']


def test_runner_cancel_during_private_copy_never_starts_guest(runner, tmp_path, authored_roms, monkeypatch):
    configuration = runner_configuration(runner, tmp_path, authored_roms, monkeypatch)
    event = runner.threading.Event()
    class CancelInputThread:
        def __init__(self, *a, **k): pass
        def start(self): event.set()
        def join(self, timeout=None): assert timeout is not None
    monkeypatch.setattr(runner, 'threading', SimpleNamespace(Event=lambda: event, Thread=CancelInputThread))
    monkeypatch.setattr(runner, 'cards', lambda *a: pytest.fail('Cancellation must stop before cards'))
    monkeypatch.setattr(runner.subprocess, 'Popen', lambda *a, **k: pytest.fail('Cancelled copy must not start guest'))
    assert runner.run(configuration) == 0
    evidence = json.loads((Path(configuration['destination']) / 'evidence.json').read_text(encoding='utf-8'))
    assert evidence['cancelled'] and evidence['process_reaped'] and evidence['source_hashes_unchanged']
    assert not evidence['original_ui_verified'] and not evidence['native_navigation_verified']


def test_owned_stop_escalates_only_same_process_group_and_never_waits_unbounded(runner, monkeypatch):
    process = FakeProcess(b'')
    process.pid = 98765
    calls = []
    def wait(timeout=None):
        assert timeout is not None
        calls.append(timeout)
        if len(calls) == 1: raise subprocess.TimeoutExpired('authored guest', timeout)
        process.returncode = -9
        return -9
    process.wait = wait
    killed = []
    monkeypatch.setattr(runner, 'os', SimpleNamespace(killpg=lambda pid, sig: killed.append((pid, sig))))
    monkeypatch.setattr(runner, 'signal', SimpleNamespace(SIGTERM=15, SIGKILL=9))
    runner.stop_owned(process)
    assert calls == [3, 3]
    assert killed == [(process.pid, runner.signal.SIGTERM), (process.pid, runner.signal.SIGKILL)]
    runner.stop_owned(process)
    assert calls == [3, 3] and len(killed) == 2
