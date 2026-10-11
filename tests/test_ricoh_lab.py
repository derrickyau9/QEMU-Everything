"""Ricoh runtime provenance, real-oracle evidence and process-boundary tests."""
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import importlib.util
from types import SimpleNamespace

import pytest

from devkit import ricoh_lab as lab
from devkit.core import Cancelled, Task, write_json


def rtos_helper(monkeypatch):
    path = Path(__file__).parents[1] / 'scripts/boards/ricoh/rtos_probe.py'
    spec = importlib.util.spec_from_file_location('ricoh_rtos_probe_test', path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    # Input-validation tests are host-independent; no Linux process may start.
    monkeypatch.setattr(module, 'os', SimpleNamespace(name='posix'))
    monkeypatch.setattr(module.subprocess, 'Popen', lambda *a, **k: pytest.fail('Invalid RTOS inputs must never launch'))
    return module


@pytest.mark.parametrize('seconds', [0, 16, True, 1.5])
def test_rtos_invalid_duration_rejected_before_any_files(tmp_path, monkeypatch, seconds):
    module = rtos_helper(monkeypatch)
    with pytest.raises(ValueError, match='duration'):
        module.probe(tmp_path/'missing', tmp_path/'missing-manifest', tmp_path/'out', seconds)


def test_rtos_unknown_board_rejected_before_execution(tmp_path, monkeypatch):
    module = rtos_helper(monkeypatch)
    payload, manifest = tmp_path/'payload.bin', tmp_path/'manifest.json'
    payload.write_bytes(b'Authored input'); manifest.write_text('{"machine":"virt"}')
    with pytest.raises(ValueError, match='reviewed separate'):
        module.probe(payload, manifest, tmp_path/'out')
    assert not (tmp_path/'out').exists()


def test_rtos_unknown_payload_rejected_before_output_creation(tmp_path, monkeypatch):
    module = rtos_helper(monkeypatch)
    payload, manifest, binary = tmp_path/'payload.bin', tmp_path/'manifest.json', tmp_path/'authored-qemu-marker'
    payload.write_bytes(b'Authored wrong payload'); binary.write_bytes(b'Authored marker, not executable')
    manifest.write_text(json.dumps({'machine':'ricoh-rtos-lab','qemu_version':'10.2.4',
        'upstream_archive_sha256':module.ARCHIVE_SHA256,
        'sources':{'ricoh-rtos-lab.c':module.BOARD_SHA256},
        'binary':str(binary),'binary_sha256':hashlib.sha256(binary.read_bytes()).hexdigest()}))
    with pytest.raises(ValueError, match='exact unpacked official'):
        module.probe(payload, manifest, tmp_path/'out')
    assert not (tmp_path/'out').exists()


def fixture_lab(tmp_path, monkeypatch):
    device, home = tmp_path / 'guest with spaces', tmp_path / 'home'
    device.mkdir(); home.mkdir(); (device / 'source').mkdir(); (device / 'extracted').mkdir()
    original = b'Original, authored test container'
    kernel = b'Authored test kernel'; rootfs = b'Authored test rootfs'
    digest = lambda b: hashlib.sha256(b).hexdigest()
    source_sha = digest(original)
    profile = {'model': 'Fixture', 'firmware': '1.0', 'source_size': len(original),
               'kernel_version': '4.4.15', 'rootfs_type': 'cpio',
               'kernel': ('kernel.zImage', len(kernel), digest(kernel)),
               'rootfs': ('ramdisk.cpio.gz', len(rootfs), digest(rootfs))}
    monkeypatch.setattr(lab, 'PROFILES', {source_sha: profile})
    (device / 'source/firmware.bin').write_bytes(original)
    (device / 'extracted/kernel.zImage').write_bytes(kernel)
    (device / 'extracted/ramdisk.cpio.gz').write_bytes(rootfs)
    write_json(device / 'device.json', {'adapter': 'generic', 'sha256': source_sha})
    write_json(device / 'extracted/extraction.json', {
        'camera': {'id': 'ricoh-gr'}, 'source_sha256': source_sha,
        'artifacts': [{'path': name, 'size': size, 'sha256': sha}
                      for name, size, sha in (profile['kernel'], profile['rootfs'])]})
    manifest = home / 'build-manifest.json'
    build = {'qemu_version': '10.2.4', 'machine': 'ricoh-milbeaut-lab',
             'upstream_archive_sha256': lab.ARCHIVE_SHA256,
             'sources': {'ricoh-milbeaut-lab.c': lab.BOARD_SHA256, 'ricoh-milbeaut-lab.dts': lab.DTS_SHA256},
             'dtb': '/local/ricoh.dtb', 'dtb_sha256': lab.DTB_SHA256,
             'binary': '/local/qemu-system-arm', 'binary_sha256': '1' * 64}
    write_json(manifest, build)
    write_json(home / 'ricoh-lab.json', {'manifest': str(manifest), 'distro': 'Debian'})
    return device, home, profile, build


def test_eligibility_does_not_hash_or_execute(tmp_path, monkeypatch):
    device, home, _, _ = fixture_lab(tmp_path, monkeypatch)
    monkeypatch.setattr(lab, '_hash', lambda *a: pytest.fail('No large input hashing in eligibility'))
    monkeypatch.setattr(lab.subprocess, 'Popen', lambda *a, **k: pytest.fail('No process in eligibility'))
    assert lab.lab_available(device, home)
    assert not lab.lab_available(tmp_path / 'missing', home)


def test_source_tampering_rejected_before_qemu(tmp_path, monkeypatch):
    device, home, _, _ = fixture_lab(tmp_path, monkeypatch)
    source = device / 'source/firmware.bin'; source.write_bytes(b'x' * source.stat().st_size)
    monkeypatch.setattr(lab.subprocess, 'Popen', lambda *a, **k: pytest.fail('No process for altered input'))
    with pytest.raises(ValueError, match='original firmware SHA-256'):
        lab.run_lab(device, home)


def test_component_tampering_rejected_during_snapshot(tmp_path, monkeypatch):
    device, home, _, _ = fixture_lab(tmp_path, monkeypatch)
    rootfs = device / 'extracted/ramdisk.cpio.gz'; rootfs.write_bytes(b'x' * rootfs.stat().st_size)
    monkeypatch.setattr(lab.subprocess, 'Popen', lambda *a, **k: pytest.fail('No process for altered rootfs'))
    with pytest.raises(ValueError, match='changed while creating'):
        lab.run_lab(device, home)


def test_unknown_source_and_wrong_artifact_metadata_rejected(tmp_path, monkeypatch):
    device, home, _, _ = fixture_lab(tmp_path, monkeypatch)
    path = device / 'extracted/extraction.json'; data = json.loads(path.read_text())
    data['source_sha256'] = 'f' * 64; write_json(path, data)
    assert not lab.lab_available(device, home)
    data['source_sha256'] = next(iter(lab.PROFILES)); data['artifacts'][0]['sha256'] = '0' * 64
    write_json(path, data)
    assert not lab.lab_available(device, home)


@pytest.mark.parametrize('change', ['board', 'dtb', 'version', 'distro'])
def test_unreviewed_build_rejected(tmp_path, monkeypatch, change):
    device, home, _, build = fixture_lab(tmp_path, monkeypatch)
    if change == 'distro':
        config = json.loads((home / 'ricoh-lab.json').read_text()); config['distro'] = 'Debian --root'
        write_json(home / 'ricoh-lab.json', config)
    else:
        if change == 'board': build['sources']['ricoh-milbeaut-lab.c'] = '0' * 64
        elif change == 'dtb': build['dtb_sha256'] = '0' * 64
        else: build['qemu_version'] = '9.0.0'
        write_json(home / 'build-manifest.json', build)
    assert not lab.lab_available(device, home)


def test_path_traversal_and_windows_reparse_are_rejected(tmp_path, monkeypatch):
    device, _, _, _ = fixture_lab(tmp_path, monkeypatch)
    with pytest.raises(ValueError): lab._inside(device, '../outside')
    from types import SimpleNamespace
    original = Path.lstat
    def reparse(self, *args, **kwargs):
        if self == device: return SimpleNamespace(st_mode=0o40755, st_file_attributes=0x400)
        return original(self, *args, **kwargs)
    monkeypatch.setattr(Path, 'lstat', reparse)
    with pytest.raises(ValueError, match='junction'):
        lab._inside(device, 'extracted/kernel.zImage')


def test_config_reparse_rejected_before_json_read(tmp_path, monkeypatch):
    _, home, _, _ = fixture_lab(tmp_path, monkeypatch)
    from types import SimpleNamespace
    original = Path.lstat
    def reparse(self, *args, **kwargs):
        if self == home / 'ricoh-lab.json':
            return SimpleNamespace(st_mode=0o100644, st_file_attributes=0x400)
        return original(self, *args, **kwargs)
    monkeypatch.setattr(Path, 'lstat', reparse)
    monkeypatch.setattr(lab, 'read_json', lambda *a: pytest.fail('Reparse config must not be read'))
    with pytest.raises(ValueError, match='junction'): lab._config(home)


def test_echoed_commands_are_not_boot_evidence():
    profile = {'kernel_version': '4.4.15'}; nonce = 'authorednonce'
    serial = b'Linux version 4.4.15\nconsole [ttyUSI1] enabled\n' + lab._oracle(nonce, 'shell').encode()
    result = lab.evaluate_console(serial, profile, nonce, 'shell')
    assert result['kernel_booted']
    assert not result['userspace_booted']
    assert not result['original_ui_verified']


def test_complete_real_output_required_and_panic_overrides():
    profile = {'kernel_version': '5.15.190'}; nonce = 'xyz'
    serial = ("Linux version 5.15.190\nconsole [ttyUSI1] enabled\n"
              "RICOH_ORACLE_START_xyz\nLinux rdsc 5.15.190 #1 armv7l GNU/Linux\n"
              "uid=0(root) gid=0(root)\nRICOH_ORACLE_END_xyz\n").encode()
    result = lab.evaluate_console(serial, profile, nonce, 'shell')
    assert result['kernel_booted'] and result['userspace_booted']
    assert not result['original_ui_verified']
    assert not lab.evaluate_console(serial.replace(b'END_xyz', b'END_other'), profile, nonce, 'shell')['userspace_booted']
    assert not lab.evaluate_console(serial + b'Kernel panic - failed init\n', profile, nonce, 'shell')['userspace_booted']


def test_original_init_attempt_is_not_success_without_child_output():
    profile = {'kernel_version': '5.15.190'}
    serial = b'Linux version 5.15.190\nconsole [ttyUSI1] enabled\nRun /sbin/finit as init process\n'
    assert not lab.evaluate_console(serial, profile, 'x', 'init')['userspace_booted']
    result = lab.evaluate_console(serial + b'udevd[75]: starting version 3.2.10\n', profile, 'x', 'init')
    assert result['original_init_started'] and result['userspace_booted']
    assert not result['shell_oracle_complete'] and not result['original_ui_verified']


def test_old_original_init_requires_actual_named_child_entropy_log():
    profile = {'kernel_version': '4.4.15'}
    serial = b'Linux version 4.4.15\nconsole [ttyUSI1] enabled\n'
    child = b'[    1.590885] random: dbus-daemon urandom read with 0 bits of entropy available\n'
    result = lab.evaluate_console(serial + child, profile, 'x', 'init')
    assert result['original_init_started'] and result['userspace_booted']
    assert not result['shell_oracle_complete'] and not result['original_ui_verified']
    assert not lab.evaluate_console(serial + b'dbus-daemon --system\n', profile, 'x', 'init')['userspace_booted']
    assert not lab.evaluate_console(serial + child, profile, 'x', 'shell')['userspace_booted']


@pytest.mark.parametrize('seconds,mode', [(0, 'shell'), (121, 'shell'), (True, 'shell'), (15, 'ui')])
def test_invalid_probe_controls_rejected(seconds, mode, tmp_path):
    with pytest.raises(ValueError): lab.run_lab(tmp_path, tmp_path, seconds=seconds, mode=mode)


def test_cancellation_before_launch(tmp_path, monkeypatch):
    device, home, _, _ = fixture_lab(tmp_path, monkeypatch)
    task = Task(lambda _: None); task.cancelled.set()
    monkeypatch.setattr(lab.subprocess, 'Popen', lambda *a, **k: pytest.fail('Cancelled task must not launch'))
    with pytest.raises(Cancelled): lab.run_lab(device, home, task)


def test_owned_supervisor_arguments_and_result_provenance(tmp_path, monkeypatch):
    device, home, profile, _ = fixture_lab(tmp_path, monkeypatch)
    seen = []
    class Owned:
        def __init__(self, command, **kwargs):
            assert 'shell' not in kwargs
            seen.append(command); config = json.loads(command[-1]); self.returncode = None
            # Inputs passed to QEMU's supervisor are new owned snapshots.
            assert all('/ricoh-lab-runs/' in a['path'] for a in config['inputs'])
            assert config['inputs'][0]['sha256'] == profile['kernel'][2]
            from re import search
            nonce = search(r"START_([a-f0-9]+)'", config['script'])[1]
            serial = (f"Linux version 4.4.15\nconsole [ttyUSI1] enabled\nRICOH_ORACLE_START_{nonce}\n"
                      f"Linux rdsc 4.4.15 #6 armv7l GNU/Linux\nuid=0(root) gid=0(root)\nRICOH_ORACLE_END_{nonce}\n").encode()
            # Convert only fixture log arguments back from WSL paths.
            def host(path):
                if os.name == 'nt': return Path(path[5].upper() + ':/' + path[7:])
                return Path(path)
            host(config['serial_log']).write_bytes(serial); host(config['stderr_log']).write_bytes(b'')
            result = {'serial_sha256': hashlib.sha256(serial).hexdigest(), 'process_reaped': True, 'qemu_exit_code': 0}
            self.stdout = io.BytesIO((json.dumps({'event': 'finished', 'result': result}) + '\n').encode())
            self.stdin = io.BytesIO()
        def poll(self): return self.returncode
        def wait(self, **kwargs): self.returncode = 0; return 0
    monkeypatch.setattr(lab.subprocess, 'Popen', Owned)
    result = lab.run_lab(device, home, Task(lambda _: None), seconds=8)
    assert result['userspace_booted'] and not result['original_ui_verified']
    assert result['assisted_startup'] and not result['camera_access']
    assert result['transport_reaped']
    assert 'GICC_CTLR' in ' '.join(result['interventions'])
    assert json.loads((device / 'ricoh-lab-last.json').read_text())['status'] == 'verified-linux'
    assert seen[0][-2] == lab.SUPERVISOR


def test_reader_start_failure_stops_owned_supervisor(tmp_path, monkeypatch):
    device, home, _, _ = fixture_lab(tmp_path, monkeypatch)
    seen = []
    class Input(io.BytesIO):
        def write(self, value): seen.append(value); return super().write(value)
    class Owned:
        returncode = None
        stdin = Input(); stdout = io.BytesIO()
        def poll(self): return self.returncode
        def wait(self, **kwargs): self.returncode = 0; return 0
    process = Owned()
    monkeypatch.setattr(lab.subprocess, 'Popen', lambda *a, **k: process)
    def fail(self): raise RuntimeError('reader startup failed')
    monkeypatch.setattr(lab.threading.Thread, 'start', fail)
    with pytest.raises(RuntimeError, match='reader startup failed'):
        lab.run_lab(device, home, Task(lambda _: None), seconds=8)
    assert b'stop\n' in seen and process.returncode == 0
    record = json.loads((device / 'ricoh-lab-last.json').read_text())
    assert record['status'] == 'failed' and record['transport_reaped']


@pytest.mark.skipif(os.name == 'nt', reason='Supervisor and QEMU run inside Linux/WSL')
def test_supervisor_freezes_inputs_and_reaps_only_owned_process(tmp_path):
    import signal
    import sys
    import time
    receipt = tmp_path / 'receipt.json'
    binary = tmp_path / 'authored-qemu-fixture'
    binary.write_text("#!/usr/bin/env python3\nimport json,sys,time\nfrom pathlib import Path\n"
                      "a=sys.argv\np=lambda k:Path(a[a.index(k)+1])\n"
                      f"Path({str(receipt)!r}).write_text(json.dumps(dict(kernel=str(p('-kernel')),"
                      "rootfs=str(p('-initrd')),dtb=str(p('-dtb')),kernel_bytes=p('-kernel').read_text(),"
                      "rootfs_bytes=p('-initrd').read_text(),dtb_bytes=p('-dtb').read_text())))\n"
                      "print('authored console fixture',flush=True)\nwhile True:time.sleep(.1)\n")
    binary.chmod(0o755)
    inputs = []
    for name, content in (('kernel.bin', b'kernel'), ('rootfs.bin', b'rootfs')):
        path = tmp_path / name; path.write_bytes(content)
        inputs.append({'path': str(path), 'size': len(content), 'sha256': hashlib.sha256(content).hexdigest()})
    dtb = tmp_path / 'dev.dtb'; dtb.write_bytes(b'development dtb')
    digest = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
    build = {'binary': str(binary), 'binary_sha256': digest(binary), 'dtb': str(dtb), 'dtb_sha256': digest(dtb)}
    manifest = tmp_path / 'manifest.json'; write_json(manifest, build)
    controls = dict(manifest=str(manifest), build=build, inputs=inputs, cmdline='authored', seconds=4,
                    script='', serial_log=str(tmp_path / 'serial.log'), stderr_log=str(tmp_path / 'stderr.log'))
    outsider = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
    process = subprocess.Popen([sys.executable, '-u', '-c', lab.SUPERVISOR, json.dumps(controls)],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        started = json.loads(process.stdout.readline())
        assert started['event'] == 'started'; owned_pid = started['pid']
        deadline = time.monotonic() + 3
        while not receipt.exists() and time.monotonic() < deadline: time.sleep(.02)
        observed = json.loads(receipt.read_text())
        assert (observed['kernel_bytes'], observed['rootfs_bytes'], observed['dtb_bytes']) == ('kernel', 'rootfs', 'development dtb')
        assert observed['kernel'] != inputs[0]['path'] and observed['rootfs'] != inputs[1]['path'] and observed['dtb'] != str(dtb)
        # Changing source copies cannot alter the Linux-local running snapshots.
        for item in inputs: Path(item['path']).write_bytes(b'changed source')
        dtb.write_bytes(b'changed dtb')
        assert Path(observed['kernel']).read_bytes() == b'kernel'
        assert Path(observed['rootfs']).read_bytes() == b'rootfs'
        assert Path(observed['dtb']).read_bytes() == b'development dtb'
        process.stdin.close(); process.stdin = None
        stdout, stderr = process.communicate(timeout=8)
        assert process.returncode == 0, stderr.decode()
        finished = json.loads(stdout.splitlines()[-1])['result']
        assert finished['process_reaped'] and finished['cancelled']
        with pytest.raises(ProcessLookupError): os.kill(owned_pid, 0)
        assert outsider.poll() is None
    finally:
        if process.poll() is None: process.kill(); process.wait()
        outsider.terminate(); outsider.wait()
