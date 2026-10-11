#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Owned, bounded Linux supervisor for the experimental H22 board."""
from pathlib import Path
import hashlib
import json
import os
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
import uuid

ARCHIVE_SHA256 = '821b545b92f165e57dddccac5077d76d4d436a226595b8813ad59306bbfd0746'
EXPECTED = {
    'kernel.bin': (7788552, 'f1dd76309a3d7e6d0f6cf9d4b4b9b3c2ca55384afbbd97ebb5995a70dd6f3295'),
    'rootfs.squashfs': (15708160, '31aa64fd800d78288fe28c8ae80f6a7b4a7c5a523b1f2dd3417d7a4b1056a89b'),
}


def digest(path):
    sha = hashlib.sha256()
    descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NONBLOCK', 0) |
                         getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0))
    with os.fdopen(descriptor, 'rb') as stream:
        metadata = os.fstat(stream.fileno())
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > 128 * 1024**2:
            raise ValueError('Board hash input must be a bounded regular file')
        count = 0
        for block in iter(lambda: stream.read(1024**2), b''):
            count += len(block)
            if count > 128 * 1024**2:
                raise ValueError('Board hash input grew beyond its bound')
            sha.update(block)
    return sha.hexdigest()


def frozen_copy(source, destination, size, expected_sha):
    """Copy a bounded regular input and hash those exact bytes before use."""
    if not isinstance(size, int) or not 0 < size <= 128 * 1024**2:
        raise ValueError('Invalid frozen board input size')
    descriptor = os.open(source, os.O_RDONLY | getattr(os, 'O_NONBLOCK', 0) |
                         getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0))
    sha = hashlib.sha256(); copied = 0
    with os.fdopen(descriptor, 'rb') as input_file, Path(destination).open('xb') as output_file:
        before = os.fstat(input_file.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size != size:
            raise ValueError('Board input size/type changed before frozen snapshot')
        while block := input_file.read(1024**2):
            copied += len(block)
            if copied > size: raise ValueError('Board input grew during frozen snapshot')
            sha.update(block); output_file.write(block)
        after = os.fstat(input_file.fileno())
    identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
    if (copied != size or sha.hexdigest() != expected_sha or
            identity(before) != identity(after) or identity(after) != identity(Path(source).stat())):
        raise ValueError('Board input changed while taking its frozen snapshot')
    return str(destination)


def emit(event, **values):
    print(json.dumps(dict(event=event, **values)), flush=True)


def validate(configuration):
    manifest = json.loads(Path(configuration['manifest']).read_text())
    if manifest.get('qemu_version') != '10.2.4' or manifest.get('upstream_archive_sha256') != ARCHIVE_SHA256:
        raise ValueError('Requires the reviewed QEMU 10.2.4 H22 build')
    for key in ('binary', 'dtb'):
        if not Path(manifest[key]).is_file() or digest(manifest[key]) != manifest[key + '_sha256']:
            raise ValueError('H22 build artifact changed: ' + key)
    overlay = Path(__file__).resolve().parent
    # Content pins allow a packaged workbench to carry the identical GPL
    # overlay at another path than the source checkout used for the build.
    for name in ('build.py', 'run.py', 'insta360-h22-lab.c', 'minimal-linux.dts'):
        if digest(overlay / name) != manifest['overlay_sha256'].get(name):
            raise ValueError('H22 board source changed; rebuild before launch: ' + name)
    copied = Path(manifest['source_directory']) / 'hw/arm/insta360-h22-lab.c'
    if digest(copied) != manifest['overlay_sha256']['insta360-h22-lab.c']:
        raise ValueError('Compiled H22 source no longer matches the board overlay')
    for name, (size, sha) in EXPECTED.items():
        path = Path(configuration['artifacts'][name])
        if path.stat().st_size != size or digest(path) != sha:
            raise ValueError('Original X3 firmware artifact changed: ' + name)
    return manifest


def make_command(manifest, snapshots, temporary, monitor, configuration):
    return [manifest['binary'], '-M', 'insta360-h22-lab', '-m', '128',
            '-display', 'none', '-monitor', 'none', '-serial', 'stdio',
            '-nic', 'none', '-kernel', snapshots['kernel.bin'],
            '-dtb', manifest['dtb'], '-initrd', snapshots['rootfs.squashfs'],
            '-append', 'console=ttyS0,115200 ignore_loglevel nokaslr maxcpus=1 '
                       'panic=-1 root=/dev/ram0 rootfstype=squashfs ro init=/bin/sh',
            '-qmp', 'unix:' + monitor + ',server=on,wait=off']


PROFILE = {
    'interventions': ['Linux-only device tree and single CPU',
                      'GIC distributor enabled after reset (missing RTOS handoff)',
                      'Original SquashFS passed as readonly RAM disk',
                      'init=/bin/sh bypasses RTOS-dependent service startup'],
    'kernel_banner': b'Linux version 4.9.76',
    'rootfs_banner': b'VFS: Mounted root (squashfs filesystem) readonly',
    'native_version': b'4.9.76',
    'proof_prefix': 'QEMU_X3_PROOF_',
    'console_label': 'Original X3 BusyBox console reached; checking native commands',
}


def main(configuration, *, expected=None, validator=None, command_builder=None, profile=None):
    # Sony's separate board wrapper reuses only this process supervisor. Each
    # profile supplies its own exact artifacts, checked build and command vector.
    expected = EXPECTED if expected is None else expected
    validator = validate if validator is None else validator
    command_builder = make_command if command_builder is None else command_builder
    profile = PROFILE if profile is None else profile
    child = None
    readers = []
    stopping = threading.Event()
    result = {'assisted_startup': True, 'firmware_file_modified': False,
              'original_ui_verified': False, 'natural_power_on_verified': False,
              'camera_access': False, 'peripherals_stubbed': True,
              'interventions': profile['interventions'],
              'kernel_booted': False, 'rootfs_mounted': False, 'userspace_booted': False,
              'serial_command_verified': False}
    result.update(profile.get('evidence', {}))
    errors = []
    try:
        manifest = validator(configuration)
        seconds = configuration['seconds']
        if not isinstance(seconds, int) or isinstance(seconds, bool) or not 5 <= seconds <= 120:
            raise ValueError('Board run duration must be 5..120 seconds')
        destination = Path(configuration['destination'])
        destination.mkdir(parents=True, exist_ok=False)
        def stop_input():
            for line in sys.stdin:
                if line.strip() == 'stop':
                    break
            stopping.set()
        listener = threading.Thread(target=stop_input, daemon=True)
        listener.start()
        with tempfile.TemporaryDirectory(prefix='qemu-x3-') as temporary:
            monitor = str(Path(temporary) / 'qmp')
            snapshots = {}
            original_dtb = Path(manifest['dtb'])
            original_dtb_sha = manifest['dtb_sha256']
            dtb_size = original_dtb.stat().st_size
            if dtb_size > 1024**2: raise ValueError('Development DTB exceeds its size bound')
            manifest = dict(manifest)
            manifest['dtb'] = frozen_copy(original_dtb, Path(temporary) / 'development.dtb', dtb_size, original_dtb_sha)
            for name, (size, expected_sha) in expected.items():
                source = Path(configuration['artifacts'][name])
                snapshot = Path(temporary) / name
                snapshots[name] = frozen_copy(source, snapshot, size, expected_sha)
            command = command_builder(manifest, snapshots, temporary, monitor, configuration)
            result.update(command=command, qemu_sha256=manifest['binary_sha256'],
                          dtb_sha256=manifest['dtb_sha256'], overlay_sha256=manifest['overlay_sha256'],
                          original_development_dtb_sha256=original_dtb_sha,
                          dtb_snapshot_frozen=True,
                          development_dtb_changes=manifest.get('dtb_changes', []),
                          firmware_sha256={name: fingerprint[1] for name, fingerprint in expected.items()},
                          startup_mode=configuration.get('mode', 'shell'))
            child = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, start_new_session=True)
            result['owned_qemu_pid'] = child.pid
            emit('started', pid=child.pid)
            output = bytearray()
            def read_stream(stream, name, limit):
                try:
                    with (destination / name).open('xb') as log:
                        total = 0
                        while block := os.read(stream.fileno(), 4096):
                            total += len(block)
                            if total > limit:
                                errors.append('H22 log limit exceeded: ' + name)
                                stopping.set()
                                break
                            log.write(block); log.flush()
                            if name == 'serial.log':
                                output.extend(block)
                except Exception as error:
                    errors.append(str(error)); stopping.set()
                finally:
                    stream.close()
            for stream, name in ((child.stdout, 'serial.log'), (child.stderr, 'emulator.log')):
                reader = threading.Thread(target=read_stream, args=(stream, name, 16 * 1024**2), daemon=True)
                reader.start(); readers.append(reader)
            start = time.monotonic()
            sent = False
            proof = profile['proof_prefix'] + uuid.uuid4().hex
            while child.poll() is None and not stopping.wait(.1) and time.monotonic() - start < seconds:
                if not sent and b'/ # ' in output:
                    # A line containing only the random token proves command execution;
                    # the echoed shell command contains additional characters.
                    commands = (profile.get('native_commands',
                                'uname -a; /bin/busybox id; mount -t proc proc /proc; cat /proc/mounts; ls /; ') +
                                'printf "\\n' + proof + '\\n"\n')
                    child.stdin.write(commands.encode('ascii')); child.stdin.flush()
                    sent = True
                    emit('console', message=profile['console_label'])
            if child.poll() is not None:
                errors.append('QEMU exited before the bounded session finished: ' + str(child.returncode))
            try:
                with socket.socket(socket.AF_UNIX) as connection:
                    connection.settimeout(2); connection.connect(monitor)
                    transport = connection.makefile('rwb', buffering=0)
                    transport.readline()
                    def qmp(name, arguments=None):
                        transport.write(json.dumps({'execute': name, **({'arguments': arguments} if arguments else {})}).encode() + b'\n')
                        while True:
                            reply = json.loads(transport.readline())
                            if 'return' in reply:
                                return reply['return']
                            if 'error' in reply:
                                raise RuntimeError(reply['error'])
                    qmp('qmp_capabilities'); qmp('stop')
                    result['registers'] = qmp('human-monitor-command', {'command-line': 'info registers'})
                    qmp('quit'); transport.close()
            except (OSError, ValueError, RuntimeError):
                pass
            result['proof_token'] = proof
            result['bounded_seconds'] = seconds
            result['kernel_booted'] = profile['kernel_banner'] in output
            result['rootfs_mounted'] = profile['rootfs_banner'] in output
            result['userspace_booted'] = b'/ # ' in output
            result['serial_command_verified'] = proof.encode() in [line.strip() for line in output.splitlines()]
            result['native_identity_verified'] = profile.get('identity_banner', b'uid=0(root)') in output and any(line.startswith(b'Linux ') and profile['native_version'] in line for line in output.splitlines())
            result['original_init_requested'] = configuration.get('mode', 'shell') == 'init'
            result['kernel_oops_observed'] = b'Internal error:' in output
            result['kernel_panic_observed'] = b'Kernel panic' in output
            result['guest_exception_observed'] = b'Entering exception monitor.' in output or b'Unhandled fault:' in output
            result['original_init_progress_verified'] = configuration.get('mode') == 'init' and bool(profile.get('init_progress_banners')) and all(banner in output for banner in profile.get('init_progress_banners', ()))
            result['source_sha256_verified_after'] = all(digest(configuration['artifacts'][name]) == sha for name, (_, sha) in expected.items())
            result['development_dtb_sha256_verified_after'] = digest(original_dtb) == original_dtb_sha
            if not result['development_dtb_sha256_verified_after']:
                errors.append('Development DTB source changed during the lab session')
            if not result['source_sha256_verified_after']:
                errors.append('Original X3 inputs changed during the lab session')
    except Exception as error:
        errors.append(str(error)); emit('error', message=str(error))
    finally:
        if child and child.poll() is None:
            try:
                os.killpg(child.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                child.wait(timeout=3)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(child.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                try:
                    child.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    errors.append('Owned QEMU did not exit after SIGKILL')
        for reader in readers:
            reader.join(timeout=1)
        if child:
            # A started reader owns its buffered stream; avoid closing a live
            # reader's lock if an OS kill exceptionally fails to reap QEMU.
            for stream in (child.stdin, child.stdout, child.stderr):
                if stream in (child.stdout, child.stderr) and any(reader.is_alive() for reader in readers):
                    continue
                if stream and not stream.closed:
                    stream.close()
        result.update(errors=errors, process_reaped=child is None or child.poll() is not None,
                      exit_code=child.returncode if child else None)
        destination = Path(configuration['destination'])
        if destination.is_dir():
            (destination / 'evidence.json').write_text(json.dumps(result, indent=2) + '\n')
        emit('stopped', evidence=str(destination / 'evidence.json'), errors=errors)
    return 1 if errors else 0


if __name__ == '__main__':
    sys.exit(main(json.loads(sys.argv[1])))
