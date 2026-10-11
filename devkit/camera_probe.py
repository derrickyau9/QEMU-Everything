"""Time-limited, headless kernel experiments with recorded evidence.

A generic QEMU board is explicitly selected; a camera SoC is not inferred from
the CPU architecture. Observing serial text never marks original UI support.
"""
from pathlib import Path
import hashlib
import os
import stat
import struct
import subprocess
import threading
import time
import uuid

from .boot import BOARDS, build_command
from .core import Cancelled, GuestLock, Task, checked_child, read_json, write_json
from .inspection import inspect_firmware
from .library import is_camera

LOG_LIMIT = 1024**2
MAX_DTB_BYTES = 16 * 1024**2
MAX_KERNEL_BYTES = 2 * 1024**3
PROCESS_WAIT_SECONDS = 3


def _validate_dtb(path):
    """Validate bounded FDT v17 blocks/tokens, rather than only its magic."""
    metadata = path.stat()
    if not stat.S_ISREG(metadata.st_mode):
        raise ValueError('Select a regular flattened device tree file')
    if not 40 <= metadata.st_size <= MAX_DTB_BYTES:
        raise ValueError('Flattened device tree file exceeds the bounded size or is truncated')
    with path.open('rb') as stream:
        header = stream.read(40)
        if len(header) != 40:
            raise ValueError('Truncated flattened device tree header')
        magic, size, nodes, strings, reserve, version, compatible, _, string_size, node_size = struct.unpack('>10I', header)
        if (magic != 0xd00dfeed or not 40 <= size <= MAX_DTB_BYTES
                or size > path.stat().st_size or version < 17 or not 16 <= compatible <= 17
                or compatible > version or nodes % 4 or reserve % 8
                or nodes < 40 or strings < 40 or reserve < 40
                or node_size < 16 or nodes + node_size > size
                or strings + string_size > size
                or not (nodes + node_size <= strings or strings + string_size <= nodes)):
            raise ValueError('Invalid flattened device tree size, version or block bounds')
        stream.seek(0)
        data = stream.read(size)
        if len(data) != size:
            raise ValueError('Flattened device tree changed or was truncated')
    limit = min([offset for offset in (nodes, strings, size) if offset > reserve], default=size)
    cursor = reserve
    while cursor + 16 <= limit:
        entry = data[cursor:cursor + 16]
        cursor += 16
        if entry == bytes(16):
            break
    else:
        raise ValueError('Flattened device tree reservation map has no bounded terminator')
    if not (cursor <= nodes or nodes + node_size <= reserve) or not (cursor <= strings or strings + string_size <= reserve):
        raise ValueError('Flattened device tree reservation map overlaps another block')
    cursor, end, depth, roots = nodes, nodes + node_size, 0, 0
    while cursor + 4 <= end:
        token = struct.unpack_from('>I', data, cursor)[0]
        cursor += 4
        if token == 1:  # FDT_BEGIN_NODE
            terminator = data.find(b'\0', cursor, end)
            if terminator < 0 or depth >= 256:
                break
            if depth == 0:
                roots += 1
                if roots != 1 or terminator != cursor:
                    break
            cursor = (terminator + 4) & ~3
            depth += 1
        elif token == 2:  # FDT_END_NODE
            if not depth:
                break
            depth -= 1
        elif token == 3:  # FDT_PROP
            if not depth or cursor + 8 > end:
                break
            length, name = struct.unpack_from('>II', data, cursor)
            cursor += 8
            if (length > end - cursor or name >= string_size
                    or data.find(b'\0', strings + name, strings + string_size) < 0):
                break
            cursor = (cursor + length + 3) & ~3
        elif token == 4:  # FDT_NOP
            continue
        elif token == 9:  # FDT_END
            if depth == 0 and roots == 1 and not any(data[cursor:end]):
                return
            break
        else:
            break
    raise ValueError('Invalid or truncated flattened device tree structure')


def _snapshot(source, destination, report, task, maximum):
    """QEMU reads a private, hash-checked copy of the inspected input."""
    if report['size'] > maximum:
        raise ValueError('Probe input exceeds the bounded snapshot size')
    digest, copied = hashlib.sha256(), 0
    with source.open('rb') as original, destination.open('xb') as saved:
        for block in iter(lambda: original.read(1024**2), b''):
            task.check()
            copied += len(block)
            if copied > maximum:
                raise ValueError('Probe input grew beyond its snapshot size limit')
            digest.update(block)
            saved.write(block)
    if copied != report['size'] or digest.hexdigest() != report['sha256']:
        raise ValueError('Probe input changed after inspection; retry with an unchanged copy')


def probe_boot(device, kernel, board, qemu_directory, task=None, *, seconds=10, dtb=''):
    task = task or Task()
    task.check()
    device = Path(device).resolve()
    if type(seconds) is not int or not 1 <= seconds <= 60:
        raise ValueError('Probe duration must be 1..60 seconds')
    if not isinstance(board, str) or board not in BOARDS:
        raise ValueError('Select a supported QEMU board explicitly')
    source = read_json(device / 'device.json')
    if not isinstance(source, dict) or not source.get('sha256') or is_camera(source):
        raise ValueError('Select an imported generic firmware workspace')
    profile = {'schema': 1, 'board': board, 'memory_mib': 512,
               'cpu': BOARDS[board]['default_cpu'], 'kernel': kernel,
               'initrd': '', 'disk': '', 'disk_format': 'raw',
               'cmdline': f"console={BOARDS[board]['console']},115200 earlycon panic=-1",
               'network': False, 'status': 'draft'}
    if not isinstance(kernel, str) or not kernel:
        raise ValueError('Select a relative kernel file path')
    kernel_path = checked_child(device, kernel)
    metadata = kernel_path.stat()
    if not stat.S_ISREG(metadata.st_mode) or not 1 <= metadata.st_size <= MAX_KERNEL_BYTES:
        raise ValueError('Kernel must be a regular file within the probe size limit')
    command = build_command(profile, device, qemu_directory) + ['-display', 'none']
    kernel_report = inspect_firmware(kernel_path, task)
    dtb_report = None
    if dtb:
        path = checked_child(device, dtb)
        _validate_dtb(path)
        dtb_report = inspect_firmware(path, task)
    with GuestLock(device):
        root = checked_child(device, 'support/boot-probes/' + uuid.uuid4().hex)
        root.mkdir(parents=True)
        buffers, truncated = {'serial': bytearray(), 'stderr': bytearray()}, {'serial': False, 'stderr': False}
        reader_errors = {}

        def drain(stream, key):
            try:
                with stream:
                    while block := stream.read(4096):
                        room = max(0, LOG_LIMIT - len(buffers[key]))
                        buffers[key].extend(block[:room])
                        if len(block) > room:
                            truncated[key] = True
            except (OSError, ValueError) as error:
                reader_errors[key] = str(error)

        flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        started = time.monotonic()
        process, threads, managed_streams, snapshots = None, [], [], [root / 'kernel-input.bin']
        timed_out, cancelled, failure = False, False, None
        try:
            _snapshot(checked_child(device, kernel), snapshots[0], kernel_report, task, MAX_KERNEL_BYTES)
            command[command.index('-kernel') + 1] = str(snapshots[0])
            if dtb_report:
                snapshots.append(root / 'dtb-input.bin')
                _snapshot(checked_child(device, dtb), snapshots[-1], dtb_report, task, MAX_DTB_BYTES)
                command += ['-dtb', str(snapshots[-1])]
            task.check()
            task.report(f'Kernel probe / 内核启动试验: {board}, {seconds}s')
            started = time.monotonic()
            process = subprocess.Popen(command, cwd=device, stdin=subprocess.DEVNULL,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       creationflags=flags)
            # Include thread construction/start in the process ownership scope.
            for stream, key in ((process.stdout, 'serial'), (process.stderr, 'stderr')):
                thread = threading.Thread(target=drain, args=(stream, key), daemon=True)
                thread.start()
                threads.append(thread)
                managed_streams.append(stream)
            while process.poll() is None:
                task.check()
                if time.monotonic() - started >= seconds:
                    timed_out = True
                    break
                time.sleep(.05)
        except BaseException as error:
            cancelled = isinstance(error, (Cancelled, KeyboardInterrupt)) or task.cancelled.is_set()
            failure = f'{type(error).__name__}: {error}'
            raise
        finally:
            cleanup_errors = []
            if process is not None and process.poll() is None:
                try:
                    process.terminate()
                except OSError as error:
                    if process.poll() is None:
                        cleanup_errors.append(str(error))
                try:
                    process.wait(timeout=PROCESS_WAIT_SECONDS)
                except subprocess.TimeoutExpired:
                    try:
                        process.kill()
                        process.wait(timeout=PROCESS_WAIT_SECONDS)
                    except (OSError, subprocess.TimeoutExpired) as error:
                        cleanup_errors.append(str(error))
            for thread in threads:
                thread.join(timeout=1)
            # A failure before a reader starts must still release that pipe.
            if process is not None:
                for stream in (process.stdout, process.stderr):
                    if stream is not None and stream not in managed_streams:
                        stream.close()
            serial = bytes(buffers['serial']).decode(errors='replace')
            result = {'schema_version': 1, 'source_sha256': source['sha256'],
                      'kernel_sha256': kernel_report['sha256'], 'kernel': kernel,
                      'input_policy': 'QEMU reads hash-checked private snapshots; temporary kernel/DTB copies are removed after cleanup.',
                      'dtb_sha256': dtb_report['sha256'] if dtb_report else None,
                      'board': board, 'command': command, 'seconds_limit': seconds,
                      'elapsed_seconds': round(time.monotonic() - started, 3),
                      'exit_code': process.returncode if process is not None else None,
                      'process_started': process is not None,
                      'process_reaped': process is None or process.poll() is not None,
                      'error': failure, 'cleanup_errors': cleanup_errors,
                      'log_reader_errors': reader_errors,
                      'log_drains_finished': all(not thread.is_alive() for thread in threads),
                      'timed_out': timed_out,
                      'cancelled': cancelled, 'logs_truncated': truncated,
                      'serial_bytes': len(buffers['serial']),
                      'kernel_console_observed': 'Linux version ' in serial,
                      'kernel_panic_observed': 'Kernel panic' in serial,
                      'kernel_boot_verified': False, 'userspace_boot_verified': False,
                      'original_ui_verified': False,
                      'interpretation': 'This tests a selected generic board, not an emulated camera. Inspect logs and implement the actual board/peripherals.'}
            try:
                for key, data in buffers.items():
                    (root / (key + '.log')).write_bytes(data)
                write_json(root / 'result.json', result)
            finally:
                # Keep evidence, not additional proprietary kernel copies.
                for snapshot in snapshots:
                    snapshot.unlink(missing_ok=True)
            if cleanup_errors and failure is None:
                raise RuntimeError('Could not reap the owned QEMU process; inspect probe evidence')
        task.report(f'Probe evidence / 试验记录: {root}')
        return root
