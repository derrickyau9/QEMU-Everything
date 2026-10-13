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


@pytest.mark.parametrize('seconds', [0, 61, True, 1.5])
def test_rtos_invalid_duration_rejected_before_any_files(tmp_path, monkeypatch, seconds):
    module = rtos_helper(monkeypatch)
    with pytest.raises(ValueError, match='duration'):
        module.probe(tmp_path/'missing', tmp_path/'missing-manifest', tmp_path/'out', seconds)


def test_rtos_gate_idle_requires_explicit_latch_model(tmp_path, monkeypatch):
    module = rtos_helper(monkeypatch)
    with pytest.raises(ValueError, match='requires experimental latches'):
        module.probe(tmp_path/'missing', tmp_path/'missing-manifest', tmp_path/'out',
                     experimental_gate_idle=True)
    assert not (tmp_path/'out').exists()


def test_sric_failure_snapshot_has_exact_instruction_gate_and_preserves_gui(monkeypatch):
    module = rtos_helper(monkeypatch)
    with pytest.raises(ValueError, match='function bytes'):
        module.diagnostic_sric_failure_image(b'unknown input')
    image = bytearray(0x961f5c + 8)
    for offset in (0x961ed4, 0x961f5c):
        image[offset:offset+8] = bytes.fromhex('0dc0a0e17110ffe6')
    image[0x180000:0x180010] = bytes(range(16))
    original = bytes(image)
    execution, changes = module.diagnostic_sric_failure_image(original)
    assert original == bytes(image) and execution[:0x960000] == original[:0x960000]
    assert execution != original and len(changes) == 2
    assert all(c['semantic'].endswith('no ready/ACK is generated') for c in changes)
    with pytest.raises(ValueError, match='requires experimental'):
        module.probe(Path('absent'), Path('absent'), Path('absent'), diagnostic_skip_sric_wait=True)


@pytest.mark.parametrize('direct', [False, True])
def test_auxiliary_main_entry_uses_reviewed_original_functions_only(monkeypatch, direct):
    import struct
    module = rtos_helper(monkeypatch)
    with pytest.raises(ValueError, match='MainTask'):
        module.diagnostic_main_ui_image(b'unknown input', direct)
    image = bytearray(0xa78830)
    image[0xa7881c:0xa78824] = bytes.fromhex('1a0c00eb7420d4e5')
    image[0x180000:0x180010] = bytes(range(16))
    original = bytes(image)
    execution, changes = module.diagnostic_main_ui_image(original, direct)
    assert execution[:0xa7881c] == original[:0xa7881c]
    assert execution[0xa78824:] == original[0xa78824:]
    assert original == bytes(image) and len(changes) == 1
    if direct:
        for address, expected in ((0x53a7881c, 0x53182830), (0x53a78820, 0x53a788b4)):
            instruction = struct.unpack_from('<I', execution, address-0x53000000)[0]
            displacement = instruction & 0xffffff
            if displacement & 0x800000: displacement -= 0x1000000
            assert address+8+displacement*4 == expected
    with pytest.raises(ValueError, match='requires experimental'):
        module.probe(Path('absent'), Path('absent'), Path('absent'),
                     diagnostic_main_ui=not direct, diagnostic_gui_entry=direct)
    with pytest.raises(ValueError, match='Choose either'):
        module.probe(Path('absent'), Path('absent'), Path('absent'),
                     experimental_latches=True, experimental_memory_layout=True,
                     diagnostic_main_ui=True, diagnostic_gui_entry=True)


def test_original_domain_bridge_preserves_firmware_and_addresses_original_code(monkeypatch):
    import struct
    module = rtos_helper(monkeypatch)
    image = bytearray(0xa78830)
    image[0xa7881c:0xa78824] = bytes.fromhex('1a0c00eb7420d4e5')
    original = bytes(image)
    execution, changes = module.diagnostic_main_ui_image(original, True, True)
    assert execution[:0xa7881c] == original[:0xa7881c]
    assert execution[0xa78824:len(original)] == original[0xa78824:]
    scratch = 0x13d3000
    # Decode the authored PC-relative literal loads, not just expected opcodes.
    for offset, expected in ((4, 0x53a81480), (32, 0x53182830)):
        word = struct.unpack_from('<I', execution, scratch+offset)[0]
        literal = scratch+offset+8+(word&0xfff)
        assert struct.unpack_from('<I', execution, literal)[0] == expected
    assert len(changes) == 2 and changes[1]['original_bytes'] is None
    with pytest.raises(ValueError, match='requires direct'):
        module.probe(Path('absent'), Path('absent'), Path('absent'), diagnostic_domain_init=True)


def test_unavailable_settings_storage_returns_failure_in_owned_snapshot_only(monkeypatch):
    module = rtos_helper(monkeypatch)
    with pytest.raises(ValueError, match='missing-file'):
        module.diagnostic_empty_domain_image(b'unknown input')
    image = bytearray(0xa7f390)
    for offset in (0xa7f374, 0xa7ec70):
        image[offset:offset+8] = bytes.fromhex('0dc0a0e13c20a0e3')
    original = bytes(image)
    execution, changes = module.diagnostic_empty_domain_image(original)
    assert original == bytes(image)
    assert execution[:0xa7ec70] == original[:0xa7ec70]
    assert execution[0xa7ec78:0xa7f374] == original[0xa7ec78:0xa7f374]
    assert execution[0xa7f37c:] == original[0xa7f37c:]
    assert len(changes) == 2 and 'NULL (missing file)' in changes[0]['semantic']
    with pytest.raises(ValueError, match='requires original domain'):
        module.probe(Path('absent'), Path('absent'), Path('absent'),
                     diagnostic_empty_domain_storage=True)


def test_null_filesystem_guard_keeps_nonnull_original_prologue(monkeypatch):
    import struct
    module = rtos_helper(monkeypatch)
    with pytest.raises(ValueError, match='filesystem method bytes'):
        module.diagnostic_null_filesystem_image(b'unknown input')
    image = bytearray(0x83b260)
    image[0x83b22c:0x83b234] = bytes.fromhex('c83801e30dc0a0e1')
    image[0x83a3c4:0x83a3cc] = bytes.fromhex('0dc0a0e1f0df2de9')
    original = bytes(image)
    execution, changes = module.diagnostic_null_filesystem_image(original)
    assert original == bytes(image)
    assert execution[:0x83a3c4] == original[:0x83a3c4]
    assert execution[0x83a3c8:0x83b22c] == original[0x83a3c8:0x83b22c]
    assert execution[0x83b230:len(original)] == original[0x83b230:]
    for offset, scratch in ((0x83b22c, 0x13d3100), (0x83a3c4, 0x13d3200)):
        words = struct.unpack_from('<5I', execution, scratch)
        assert words[:2] == (0xe3500000, 0x012fff1e)  # NULL-only return
        assert execution[scratch+8:scratch+16] == original[offset:offset+8]
        displacement = words[4]&0xffffff
        if displacement&0x800000: displacement -= 0x1000000
        assert 0x53000000+scratch+16+8+displacement*4 == 0x53000000+offset+8
    assert len(changes) == 4 and 'nonnull' in changes[0]['semantic']


@pytest.mark.skipif(os.name != 'posix' or not os.environ.get('RICOH_RTOS_TEST_MANIFEST'),
                    reason='Requires explicitly selected, locally built Ricoh RTOS QEMU')
def test_rtos_actual_board_register_isolation(tmp_path):
    """An authored ARM program catches overlapping windows and false ready bits.

    No camera firmware is needed or executed. Firmware/UI boot is validated
    separately by rtos_probe, never inferred from this peripheral regression.
    """
    path = Path(__file__).parents[1] / 'scripts/boards/ricoh/rtos_probe.py'
    spec = importlib.util.spec_from_file_location('ricoh_rtos_test_board', path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    manifest = json.loads(Path(os.environ['RICOH_RTOS_TEST_MANIFEST']).read_text())
    binary = Path(manifest['binary'])
    assert manifest['sources']['ricoh-rtos-lab.c'] == module.BOARD_SHA256
    assert hashlib.sha256(binary.read_bytes()).hexdigest() == manifest['binary_sha256']
    assembly = tmp_path/'authored.S'
    assembly.write_text('''
.syntax unified
.arm
.global _start
_start:
    mrc p15, 0, r0, c0, c0, 5
    ands r0, r0, #3
    bne idle
    ldr r0, =0x1e0000d0
    mov r1, #0
    str r1, [r0]
    ldr r1, =6749
    str r1, [r0, #8]
    ldr r2, [r0, #8]
    cmp r1, r2
    bne fail
    ldr r0, =0x1dffb000
    mov r1, #1
    str r1, [r0, #0x108]
    ldr r2, [r0, #0x108]
    cmp r2, #1
    bne fail
    ldr r1, =0x00010001
    str r1, [r0, #8]
    ldr r2, [r0, #8]
    cmp r2, #1
    bne fail
    ldr r1, =0x00010000
    str r1, [r0, #8]
    ldr r2, [r0, #8]
    cmp r2, #0
    bne fail
    ldr r0, =0x1d021000
    mov r1, #0
    str r1, [r0, #0xc]
    ldr r2, [r0, #0x38]
    cmp r2, #0
    bne fail
    mov r1, #3
    str r1, [r0, #0x54]
    ldr r2, [r0, #0x54]
    cmp r2, #1
    bne fail
    mov r1, #2
    str r1, [r0, #0x54]
    ldr r2, [r0, #0x54]
    cmp r2, #0
    bne fail
    ldr r0, =0x28000000
    mov r1, #0
    str r1, [r0, #0xc]
    ldr r2, [r0, #0x38]
    cmp r2, #1
    bne fail
    ldr r0, =0x1d022000
    mov r1, #1
    str r1, [r0, #0x118]
    ldr r1, =0x00010001
    str r1, [r0, #0x18]
    ldr r2, [r0, #0x18]
    cmp r2, #1
    bne fail
    ldr r0, =0x1e003400
    mov r1, #255
    str r1, [r0, #0x14]
    mov r1, #1
    str r1, [r0, #0xc]
    ldr r3, =1000000
start_poll:
    ldr r2, [r0, #4]
    cmp r2, #0x81
    beq start_done
    subs r3, r3, #1
    bne start_poll
    b fail
start_done:
    mov r1, #0x65
    str r1, [r0]
    mov r1, #0x80
    str r1, [r0, #0xc]
    ldr r3, =1000000
nack_poll:
    ldr r2, [r0, #4]
    cmp r2, #0x89
    beq nack_done
    subs r3, r3, #1
    bne nack_poll
    b fail
nack_done:
    mov r1, #2
    str r1, [r0, #0xc]
    ldr r2, [r0, #8]
    tst r2, #1
    bne fail
    ldr r0, =0x1de00000
    mov r1, #84
    strh r1, [r0, #4]
    mov r0, #4
    ldr r1, =passed
    svc 0x123456
    mov r0, #0x18
    ldr r1, =0x20026
    svc 0x123456
fail:
    mov r0, #4
    ldr r1, =failed
    svc 0x123456
idle:
    wfi
    b idle
passed: .asciz "RICOH AUTHORED DEVICE CHECK PASSED\\n"
failed: .asciz "RICOH AUTHORED DEVICE CHECK FAILED\\n"
''')
    obj, elf, image = (tmp_path/name for name in ('authored.o','authored.elf','authored.bin'))
    subprocess.run(['arm-linux-gnueabihf-as','-march=armv7-a',str(assembly),'-o',str(obj)],check=True)
    subprocess.run(['arm-linux-gnueabihf-ld','-Ttext=0x53000000',str(obj),'-o',str(elf)],check=True)
    subprocess.run(['arm-linux-gnueabihf-objcopy','-O','binary',str(elf),str(image)],check=True)
    command = [str(binary),'-M','ricoh-rtos-lab,experimental-latches=on,experimental-gate-idle=on,experimental-memory-layout=on',
               '-m','2048','-display','none','-monitor','none','-serial',f'file:{tmp_path/"serial.log"}',
               '-nic','none','-semihosting-config','enable=on,target=native',
               '-device',f'loader,file={image},addr=0x53000000,force-raw=on']
    for cpu in range(3): command += ['-device',f'loader,addr=0x53000000,cpu-num={cpu}']
    result = subprocess.run(command, capture_output=True, timeout=8)
    assert result.returncode == 0
    assert b'RICOH AUTHORED DEVICE CHECK PASSED' in result.stderr
    assert b'FAILED' not in result.stderr
    assert (tmp_path/'serial.log').read_bytes() == b'T'


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
