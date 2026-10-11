import hashlib
import importlib.util
import io
import json
import struct
from pathlib import Path
from unittest.mock import Mock

import pytest

from devkit import sony_lab as lab
from devkit import insta360_lab as bridge
from devkit.core import GuestLock, Task, read_json, write_json


@pytest.fixture
def guest(tmp_path, monkeypatch):
    device, home = tmp_path / 'guest', tmp_path / 'home'
    values, inputs = {}, {}
    for name, filename, data in (('kernel', 'kernel.bin', b'original sony kernel'),
                                 ('rootfs', 'initrd.img', b'original sony initrd')):
        path = device / 'sony-lab-inputs' / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        inputs[name] = path
        values[name] = (filename.upper(), filename, len(data), hashlib.sha256(data).hexdigest())
    monkeypatch.setattr(lab.sony_components, 'INPUTS', values)
    profile = dict(lab.sony_components.PROFILES['a7iv-2.00'], inputs=values)
    monkeypatch.setattr(lab.sony_components, 'PROFILES', {'a7iv-2.00':profile})
    monkeypatch.setattr(lab.sony_components, 'prepare_components', lambda *args: inputs)
    write_json(home / 'sony-lab.json', dict(distro='Debian', manifest='/private/sony/build-manifest.json'))
    write_json(device / 'device.json', dict(sha256=lab.sony_components.SOURCE_SHA256))
    return device, home, inputs


def test_availability_checks_exact_source_and_config_without_unpacking(guest, monkeypatch):
    device, home, _ = guest
    monkeypatch.setattr(lab.sony_components, 'prepare_components', Mock(side_effect=AssertionError('must not unpack')))
    assert lab.lab_available(device, home)
    write_json(device / 'device.json', dict(sha256='different model/version'))
    assert not lab.lab_available(device, home)


@pytest.mark.parametrize('mode', ['shell', 'init'])
def test_command_mode_and_hash_checked_supervisors(guest, mode):
    device, home, _ = guest
    args = lab.build_lab_command(device, home, device / 'new session', Task(), 12, mode=mode)
    configuration = json.loads(args[-1])
    assert configuration['mode'] == mode
    assert configuration['seconds'] == 12
    assert len(configuration['supervisor_sha256']) == 64
    assert len(configuration['bridge_supervisor_sha256']) == 64
    assert args[-2].endswith('/scripts/boards/sony/run.py')


def test_component_mutation_rejected_before_owned_launch(guest, monkeypatch):
    device, home, inputs = guest
    inputs['kernel'].write_bytes(b'changed  sony kernel')
    launch = Mock()
    monkeypatch.setattr(lab, '_run_supervised', launch)
    with pytest.raises(ValueError, match='changed'):
        lab.run_lab(device, home, Task(lambda _: None), seconds=10)
    launch.assert_not_called()


def test_prepare_runs_before_session_lock_and_only_once(guest, monkeypatch):
    device, home, inputs = guest
    prepare_count = []
    def prepare(*args):
        with GuestLock(device):
            prepare_count.append(True)
        return inputs
    monkeypatch.setattr(lab.sony_components, 'prepare_components', prepare)
    monkeypatch.setattr(lab, '_run_supervised', lambda *args, **kwargs: device / 'evidence.json')
    assert lab.run_lab(device, home, Task(lambda _: None), seconds=10) == device / 'evidence.json'
    assert len(prepare_count) == 1


def test_input_path_must_be_prepared_guest_file(guest):
    device, home, inputs = guest
    outside = device.parent / 'kernel.bin'
    outside.write_bytes(inputs['kernel'].read_bytes())
    with pytest.raises(ValueError, match='outside'):
        lab.build_lab_command(device, home, device / 'run', Task(), 10,
                              inputs=dict(inputs, kernel=outside))


@pytest.mark.parametrize('mode,seconds', [('full', 10), ('shell', False), ('init', 121), ('shell', 4)])
def test_mode_and_duration_invalid_before_preparation(guest, monkeypatch, mode, seconds):
    device, home, _ = guest
    prepare = Mock()
    monkeypatch.setattr(lab.sony_components, 'prepare_components', prepare)
    with pytest.raises(ValueError):
        lab.run_lab(device, home, Task(lambda _: None), mode=mode, seconds=seconds)
    prepare.assert_not_called()


def test_shared_reader_failure_reaps_sony_owned_transport(guest, monkeypatch):
    device, home, _ = guest
    class Process:
        returncode = None
        stdin, stdout = io.BytesIO(), io.BytesIO()
        def poll(self): return self.returncode
        def wait(self, timeout):
            self.returncode = 0
        def terminate(self): self.returncode = -15
        def kill(self): self.returncode = -9
    process = Process()
    monkeypatch.setattr(bridge.subprocess, 'Popen', lambda *args, **kwargs: process)
    monkeypatch.setattr(bridge.threading.Thread, 'start', Mock(side_effect=RuntimeError('reader failed')))
    with pytest.raises(RuntimeError, match='reader failed'):
        lab.run_lab(device, home, Task(lambda _: None), seconds=10)
    assert process.returncode == 0
    assert process.stdin.closed and process.stdout.closed
    assert read_json(device / 'sony-lab-session.json')['process_reaped']


def _supervisor():
    spec = importlib.util.spec_from_file_location('sony_supervisor_test', lab.OVERLAY / 'run.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def development_dtb(supervisor, *, duplicate=False):
    strings = b'linux,initrd-start\0linux,initrd-end\0'
    def word(value): return struct.pack('>I', value)
    def node(name):
        data = name + b'\0'
        return word(1) + data + b'\0' * ((-len(data)) % 4)
    def prop(name_offset, value):
        return word(3) + word(8) + word(name_offset) + struct.pack('>Q', value)
    start = prop(0, supervisor.INITRD_START)
    end = prop(len(b'linux,initrd-start\0'), supervisor.BASE_INITRD_END)
    tree = node(b'') + node(b'chosen') + start + end + (end if duplicate else b'') + word(2)*2 + word(9)
    strings_offset = 56 + len(tree)
    return (struct.pack('>10I', 0xd00dfeed, strings_offset+len(strings), 56, strings_offset, 40, 17, 16,
                        0, len(strings), len(tree)) + b'\0'*16 + tree + strings)


@pytest.mark.parametrize('mode', ['shell', 'init'])
def test_loader_handoff_fixed_and_kernel_unchanged(tmp_path, mode):
    supervisor = _supervisor()
    dtb = tmp_path/'development.dtb'; dtb.write_bytes(development_dtb(supervisor))
    manifest = dict(binary='/private/qemu', dtb=str(dtb))
    inputs = dict(kernel='/private/original-kernel', rootfs='/private/original-initrd')
    command = supervisor.make_command(manifest, inputs, tmp_path, '/tmp/owned-qmp', dict(mode=mode))
    assert command[command.index('-cpu') + 1] == 'cortex-a35,cntfrq=2000000'
    assert command[command.index('-kernel') + 1] == '/private/original-kernel'
    assert any('addr=0x400002000' in arg for arg in command)
    assert any('addr=0x418000000' in arg for arg in command)
    parameters = (tmp_path / 'kemco-parameters.bin').read_bytes()
    assert parameters.startswith(b'kemco') and parameters.endswith(b'\0')
    assert (b'init=/bin/sh' in parameters) == (mode == 'shell')
    assert b'initcall_blacklist=__boottime_sony_usb_init' in parameters
    assert command[command.index('-nic') + 1] == 'none'
    assert command[-1].startswith('unix:/tmp/owned-qmp,')


def test_shared_supervisor_hash_changes_fail_before_import():
    supervisor = _supervisor()
    with pytest.raises(ValueError, match='Common board supervisor changed'):
        supervisor.run(dict(profile='a7iv-2.00', bridge_supervisor_sha256='wrong'))


def test_a7rv_dtb_changes_only_exact_chosen_end(tmp_path):
    supervisor = _supervisor()
    original = development_dtb(supervisor)
    end = supervisor.INITRD_START + supervisor.PROFILES['a7rv-4.00']['expected']['rootfs'][0]
    updated = supervisor.patch_initrd_end(original, end)
    assert len(updated) == len(original)
    assert struct.pack('>Q', end) in updated
    assert len([i for i,(a,b) in enumerate(zip(original, updated)) if a != b]) <= 4
    dtb = tmp_path/'development.dtb'; dtb.write_bytes(original)
    manifest = dict(binary='/private/qemu', dtb=str(dtb))
    supervisor.make_command(manifest, dict(kernel='kernel',rootfs='rootfs'), tmp_path, '/qmp',
                            dict(mode='shell',profile='a7rv-4.00'))
    assert dtb.read_bytes() == updated
    assert manifest['dtb_changes'] == [{'path':'/chosen/linux,initrd-end',
                                      'before':'0x4184cc000','after':'0x4184fa000'}]


@pytest.mark.parametrize('mutation', ['duplicate', 'wrong-start', 'wrong-end', 'string-bounds', 'truncated'])
def test_dtb_patch_rejects_missing_exact_property_or_invalid_bounds(mutation):
    supervisor = _supervisor()
    data = bytearray(development_dtb(supervisor, duplicate=mutation=='duplicate'))
    if mutation == 'wrong-start':
        pos = data.find(struct.pack('>Q',supervisor.INITRD_START)); data[pos+7] ^= 1
    if mutation == 'wrong-end':
        pos = data.find(struct.pack('>Q',supervisor.BASE_INITRD_END)); data[pos+7] ^= 1
    if mutation == 'string-bounds': struct.pack_into('>I',data,12,len(data)+1)
    if mutation == 'truncated': data.pop()
    with pytest.raises(ValueError): supervisor.patch_initrd_end(data,supervisor.BASE_INITRD_END)


def test_dtb_patch_never_writes_original_build_file(tmp_path):
    supervisor = _supervisor()
    original = tmp_path/'original.dtb'; original.write_bytes(development_dtb(supervisor))
    destination = tmp_path/'frozen'; destination.mkdir()
    with pytest.raises(ValueError, match='frozen temporary copy'):
        supervisor.make_command(dict(binary='qemu',dtb=str(original)), {}, destination, '/qmp', dict(mode='shell'))
    assert original.read_bytes() == development_dtb(supervisor)
