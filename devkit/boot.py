"""Reviewed boot profiles for generic QEMU machines.

An instruction set match is a prerequisite, not proof that a device's kernel has
drivers for QEMU's virtual board. Profiles remain drafts until configured by the
user, and no proprietary update container is passed to QEMU as a kernel.
"""
from pathlib import Path
import os
import struct
import zlib

from .core import checked_child


BOARDS = {
    'arm64-virt': {
        'label': 'ARM64 · QEMU virt', 'binary': 'qemu-system-aarch64',
        'machine': 'virt', 'architecture': 'aarch64', 'default_cpu': 'cortex-a57',
        'cpus': ('cortex-a53', 'cortex-a57', 'cortex-a72', 'max'),
        'console': 'ttyAMA0', 'maximum_memory_mib': 8192,
    },
    'arm-virt': {
        'label': 'ARM32 · QEMU virt', 'binary': 'qemu-system-arm',
        'machine': 'virt', 'architecture': 'arm', 'default_cpu': 'cortex-a15',
        'cpus': ('cortex-a7', 'cortex-a15'),
        'console': 'ttyAMA0', 'maximum_memory_mib': 2048,
    },
    'x86_64-pc': {
        'label': 'x86 / x86-64 · QEMU PC', 'binary': 'qemu-system-x86_64',
        'machine': 'pc', 'architecture': 'x86_64', 'default_cpu': 'qemu64',
        'cpus': ('qemu64', 'max'),
        'console': 'ttyS0', 'maximum_memory_mib': 8192,
    },
    'riscv64-virt': {
        'label': 'RISC-V 64 · QEMU virt', 'binary': 'qemu-system-riscv64',
        'machine': 'virt', 'architecture': 'riscv64', 'default_cpu': 'rv64',
        'cpus': ('rv64',),
        'console': 'ttyS0', 'maximum_memory_mib': 8192,
    },
}

_FIELDS = {'schema', 'board', 'memory_mib', 'cpu', 'kernel', 'initrd', 'disk',
           'disk_format', 'cmdline', 'status', 'network'}
_ARCHES = {
    'aarch64': 'aarch64', 'arm64': 'aarch64', 'em_aarch64': 'aarch64',
    'arm': 'arm', 'arm32': 'arm', 'em_arm': 'arm',
    'x86_64': 'x86_64', 'amd64': 'x86_64', 'em_x86_64': 'x86_64',
    'x86': 'x86', 'i386': 'x86', 'em_386': 'x86',
    'riscv64': 'riscv64', 'risc-v64': 'riscv64', 'em_riscv': 'riscv64',
}
_DIRECT_FORMATS = {'linux-arm64-image', 'linux-arm-zimage',
                   'linux-x86-bzimage', 'linux-riscv-image', 'uimage'}


def _architecture(value):
    return _ARCHES.get(str(value).lower()) if value else None


def boot_template(inspection):
    """Suggest a draft only when a recognizable direct kernel is present.

    Disk image instruction sets cannot normally be determined from their
    container header. Their board is left blank for explicit selection. The
    importer may provide ``source_path`` for its staged, relative source path.
    """
    if not isinstance(inspection, dict):
        raise ValueError('Inspection must be an object')
    result = {'schema': 1, 'board': '', 'memory_mib': 1024, 'cpu': '',
              'kernel': '', 'initrd': '', 'disk': '', 'disk_format': 'raw',
              'cmdline': '', 'status': 'draft', 'network': False}
    kind = inspection.get('format')
    path = inspection.get('source_path') or inspection.get('filename') or inspection.get('source_name') or ''
    # An unsafe/unknown source still gets a useful editable blank profile.
    if not isinstance(path, str) or not path:
        return result
    try:
        checked_child(Path.cwd(), path)
    except ValueError:
        return result
    architecture = _architecture(inspection.get('architecture'))
    candidates = inspection.get('candidates', [])
    if not architecture and isinstance(candidates, list):
        for candidate in candidates:
            if isinstance(candidate, dict) and candidate.get('kind') == 'kernel' and candidate.get('offset', 0) == 0:
                architecture = _architecture(candidate.get('architecture'))
                break
    # Inspection uses blockers for both malformed inputs and the honest warning
    # that a matching instruction set does not establish board driver support.
    # A structurally valid boot candidate can still get an unverified draft.
    candidate = inspection.get('status') == 'boot-candidate'
    if kind in _DIRECT_FORMATS and inspection.get('status') != 'inspect-only' and (candidate or not inspection.get('blockers')):
        board = {'aarch64': 'arm64-virt', 'arm': 'arm-virt',
                 'x86_64': 'x86_64-pc', 'x86': 'x86_64-pc',
                 'riscv64': 'riscv64-virt'}.get(architecture)
        if board:
            spec = BOARDS[board]
            result.update(board=board, cpu=spec['default_cpu'], kernel=path,
                          cmdline=f'console={spec["console"]},115200')
    elif kind in ('raw-disk', 'qcow2') and inspection.get('status') != 'inspect-only' and (candidate or not inspection.get('blockers')):
        result.update(disk=path, disk_format='qcow2' if kind == 'qcow2' else 'raw')
    return result


def _component(value, device, field):
    if value in (None, ''):
        return '', None
    if not isinstance(value, str) or len(value) > 512:
        raise ValueError(f'{field} must be a relative file path')
    path = checked_child(device, value)
    if not path.is_file():
        raise ValueError(f'{field} does not exist in this firmware workspace: {value}')
    if path.stat().st_size == 0:
        raise ValueError(f'{field} is empty: {value}')
    # QEMU -drive is a comma-separated option grammar, not a shell argument.
    if ',' in str(path):
        raise ValueError('Choose a firmware workspace and filenames without commas')
    return path.relative_to(device.resolve()).as_posix(), path


def _kernel_architecture(path):
    """Check the supplied file, rather than trusting inspection metadata."""
    with path.open('rb') as stream:
        head = stream.read(4096)
        if len(head) >= 64 and head[56:60] == b'ARM\x64':
            image_size = int.from_bytes(head[16:24], 'little')
            if image_size and not 64 <= image_size < 2**48:
                raise ValueError('Invalid ARM64 Image memory footprint')
            return 'aarch64'
        if len(head) >= 48 and head[36:40] == b'\x18\x28\x6f\x01':
            start, end = struct.unpack_from('<II', head, 40)
            if not 48 <= end - start <= path.stat().st_size:
                raise ValueError('Invalid ARM zImage payload length')
            return 'arm'
        if len(head) >= 0x238 and head[510:512] == b'\x55\xaa' and head[0x202:0x206] == b'HdrS':
            protocol = int.from_bytes(head[0x206:0x208], 'little')
            setup_sectors = head[0x1f1] or 4
            protected_size = int.from_bytes(head[0x1f4:0x1f8], 'little') * 16
            if protocol < 0x200 or protected_size <= 0 or (setup_sectors + 1) * 512 + protected_size > path.stat().st_size:
                raise ValueError('Invalid or truncated x86 bzImage payload')
            return 'x86_64' if protocol >= 0x20c and int.from_bytes(head[0x236:0x238], 'little') & 1 else 'x86'
        if len(head) >= 64 and (head[48:56] == b'RISCV\0\0\0' or head[56:60] == b'RSC\x05'):
            # The common RISC-V Image header does not encode the XLEN. An explicit
            # 64-bit board selection is still required; no automatic ELF guess.
            image_size = int.from_bytes(head[16:24], 'little')
            if not 64 <= image_size < 2**48:
                raise ValueError('RISC-V Image requires a valid nonzero image_size')
            if int.from_bytes(head[24:32], 'little') & 1:
                raise ValueError('Big-endian RISC-V Images are not supported by this profile')
            return 'riscv64'
        if head[:4] == b'\x27\x05\x19\x56' and len(head) >= 64:
            header = bytearray(head[:64])
            expected_header_crc = struct.unpack_from('>I', header, 4)[0]
            header[4:8] = b'\0' * 4
            if zlib.crc32(header) != expected_header_crc:
                raise ValueError('Invalid uImage header CRC')
            size, expected_data_crc = struct.unpack_from('>I', head, 12)[0], struct.unpack_from('>I', head, 24)[0]
            if head[28] != 5 or head[30] != 2:
                raise ValueError('uImage must contain a Linux kernel')
            if head[31] != 0:
                raise ValueError('Extract the compressed uImage kernel before configuring boot')
            if size <= 0 or size + 64 != path.stat().st_size:
                raise ValueError('Invalid uImage payload length')
            stream.seek(64)
            crc = 0
            while chunk := stream.read(1024 * 1024):
                crc = zlib.crc32(chunk, crc)
            if crc != expected_data_crc:
                raise ValueError('Invalid uImage payload CRC')
            architecture = {2: 'arm', 3: 'x86', 22: 'aarch64', 24: 'riscv64'}.get(head[29])
            if architecture:
                return architecture
            raise ValueError('The uImage architecture is not supported')
    raise ValueError('Select an extracted Linux Image, zImage, bzImage or uncompressed uImage; update containers and arbitrary ELF programs are not direct kernels')


def _validate_disk(path, disk_format):
    with path.open('rb') as stream:
        header = stream.read(104)
    if disk_format == 'raw':
        if header[:4] == b'QFI\xfb':
            raise ValueError('This is qcow2, not a raw disk image')
        return
    if len(header) < 72 or header[:4] != b'QFI\xfb':
        raise ValueError('This is not a qcow2 disk image')
    version = struct.unpack_from('>I', header, 4)[0]
    if version not in (2, 3):
        raise ValueError('Unsupported qcow2 version')
    backing_offset = struct.unpack_from('>Q', header, 8)[0]
    backing_size = struct.unpack_from('>I', header, 16)[0]
    crypt_method = struct.unpack_from('>I', header, 32)[0]
    cluster_bits = struct.unpack_from('>I', header, 20)[0]
    if backing_offset or backing_size:
        raise ValueError('qcow2 backing files are not allowed; import a self-contained image')
    if crypt_method:
        raise ValueError('Encrypted qcow2 images are not supported')
    if not 9 <= cluster_bits <= 21:
        raise ValueError('Invalid qcow2 cluster size')
    if version == 3:
        if len(header) < 104:
            raise ValueError('Truncated qcow2 v3 header')
        incompatible = struct.unpack_from('>Q', header, 72)[0]
        # Bit 2 refers to an external data file, which could reach a host path.
        # Only the documented dirty/corrupt flags and compression flag are known.
        if incompatible & ~0b1011:
            raise ValueError('qcow2 external data files or unknown features are not allowed')


def validate_profile(profile, device):
    """Return a normalized configuration, or a useful explanation of blockers.

    Validated means inputs are well formed and architecture compatible. It does
    not mean the kernel has booted or its original peripherals are emulated.
    """
    if not isinstance(profile, dict):
        raise ValueError('Boot profile must be an object')
    if unknown := set(profile) - _FIELDS:
        raise ValueError('Unknown boot profile fields: ' + ', '.join(sorted(unknown)))
    if type(profile.get('schema')) is not int or profile['schema'] != 1:
        raise ValueError('Unsupported boot profile schema; expected 1')
    board = profile.get('board')
    if not isinstance(board, str) or board not in BOARDS:
        raise ValueError('Choose a supported QEMU board explicitly before starting')
    spec = BOARDS[board]
    memory = profile.get('memory_mib', 1024)
    if type(memory) is not int or not 128 <= memory <= spec['maximum_memory_mib']:
        raise ValueError(f'memory_mib must be between 128 and {spec["maximum_memory_mib"]}')
    cpu = profile.get('cpu') or spec['default_cpu']
    if not isinstance(cpu, str) or cpu not in spec['cpus']:
        raise ValueError(f'CPU is not supported for {board}; choose ' + ', '.join(spec['cpus']))
    status = profile.get('status', 'draft')
    if status not in ('draft', 'configured', 'verified'):
        raise ValueError('Unknown boot profile status')
    network = profile.get('network', False)
    if network is not False:
        raise ValueError('Generic firmware networking is disabled in this version; network must be false')
    cmdline = profile.get('cmdline', '')
    if not isinstance(cmdline, str) or len(cmdline) > 4096 or any(ord(c) < 32 for c in cmdline):
        raise ValueError('Kernel command line must be a single line of at most 4096 characters')
    disk_format = profile.get('disk_format', 'raw')
    if disk_format not in ('raw', 'qcow2'):
        raise ValueError('disk_format must be raw or qcow2')
    device = Path(device).resolve()
    paths, resolved = {}, {}
    for field in ('kernel', 'initrd', 'disk'):
        paths[field], resolved[field] = _component(profile.get(field), device, field)
    if not resolved['kernel'] and not resolved['disk']:
        raise ValueError('Configure a direct kernel or bootable disk first')
    if not resolved['kernel'] and board != 'x86_64-pc':
        raise ValueError('This virt board requires a direct kernel; disk-only boot needs a separate firmware adapter')
    if resolved['initrd'] and not resolved['kernel']:
        raise ValueError('An initrd requires a direct kernel')
    if cmdline and not resolved['kernel']:
        raise ValueError('Kernel command line requires a direct kernel')
    if resolved['kernel']:
        architecture = _kernel_architecture(resolved['kernel'])
        if architecture != spec['architecture'] and not (board == 'x86_64-pc' and architecture == 'x86'):
            raise ValueError(f'Kernel architecture {architecture} does not match {board}')
    if resolved['disk']:
        _validate_disk(resolved['disk'], disk_format)
    return {'schema': 1, 'board': board, 'memory_mib': memory, 'cpu': cpu,
            **paths, 'disk_format': disk_format, 'cmdline': cmdline,
            'status': 'configured', 'network': network}


def build_command(profile, device, qemu_directory, *, console=True):
    """Build an argument vector, never a shell command or user-supplied flags."""
    profile = validate_profile(profile, device)
    spec = BOARDS[profile['board']]
    binary = Path(qemu_directory) / (spec['binary'] + ('.exe' if os.name == 'nt' else ''))
    if not binary.is_file():
        raise FileNotFoundError(f'QEMU runtime is missing {binary.name}')
    args = [str(binary), '-machine', spec['machine'], '-cpu', profile['cpu'],
            '-accel', 'tcg,thread=multi', '-m', str(profile['memory_mib']),
            '-smp', '2', '-no-reboot', '-monitor', 'none',
            '-serial', 'stdio' if console else 'none',
            '-nic', 'none']
    if profile['board'] != 'x86_64-pc':
        args += ['-device', 'virtio-gpu-pci', '-device', 'qemu-xhci',
                 '-device', 'usb-kbd', '-device', 'usb-tablet']
    for field, option in (('kernel', '-kernel'), ('initrd', '-initrd')):
        if profile[field]:
            args += [option, str(checked_child(device, profile[field]))]
    if profile['kernel'] and profile['cmdline']:
        args += ['-append', profile['cmdline']]
    if profile['disk']:
        path = checked_child(device, profile['disk'])
        interface = 'ide' if profile['board'] == 'x86_64-pc' else 'virtio'
        args += ['-snapshot', '-drive', f'file={path},format={profile["disk_format"]},if={interface}']
    return args
