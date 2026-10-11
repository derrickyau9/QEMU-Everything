#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Checked Sony A7 IV startup using the common owned board supervisor."""
from pathlib import Path
import hashlib
import importlib.util
import json
import os
import stat
import struct
import sys

EXPECTED = {
    'kernel': (6955016, '5aa8ccf608b7e697b7ddb3fc78b00ebcb666236596e390c7d4517bcea72f474e'),
    'rootfs': (5029888, '5bd6f0313198b7ddc3001faf07111ddf380f8e6fcfa3e76a033368ad311ae171'),
}
PROFILES = {
    'a7iv-2.00': {'model': 'ILCE-7M4', 'version': '2.00', 'kernel_version': '4.9.186-scel+',
                  'source_sha256': '394ac0daaeb9ce3f6ca0273a18c6af26505770e3b470f70d1d9f062e257bbe88',
                  'expected': EXPECTED},
    'a7rv-4.00': {'model': 'ILCE-7RM5', 'version': '4.00', 'kernel_version': '4.9.337-scel',
                  'source_sha256': '51e1176a255a9a4b4782dfbcfa6c5f332b71173bdfe9ed2bcf4fca6bcbc6c2e2',
                  'expected': {
                      'kernel': (6610952, 'bdb129ad6806e3e33f1a1b9c094c0c97c4940bdea7aa9c09cef18f02e4012e66'),
                      'rootfs': (5218304, '8a1ac5c8494fe6b4b8b46d46f88d11272fe42091bc4ce28393dc9eae6a0e649d')}},
}
INITRD_START = 0x418000000
BASE_INITRD_END = INITRD_START + EXPECTED['rootfs'][0]
ARCHIVE_SHA256 = '821b545b92f165e57dddccac5077d76d4d436a226595b8813ad59306bbfd0746'
APPEND = ('earlycon=pl011,0xf1000000 console=ttyAMA0,460800 loglevel=8 ignore_loglevel '
          'nosmp panic=-1 root=/dev/ram0 initcall_blacklist=__boottime_sony_usb_init')


def digest(path):
    sha = hashlib.sha256()
    descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NONBLOCK', 0) |
                         getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0))
    with os.fdopen(descriptor, 'rb') as stream:
        metadata = os.fstat(stream.fileno())
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > 128 * 1024**2:
            raise ValueError('Sony hash input must be a bounded regular file')
        copied = 0
        for block in iter(lambda: stream.read(1024**2), b''):
            copied += len(block)
            if copied > 128 * 1024**2:
                raise ValueError('Sony hash input grew beyond its bound')
            sha.update(block)
    return sha.hexdigest()


def validate(configuration):
    selected = PROFILES.get(configuration.get('profile'))
    if selected is None: raise ValueError('Sony model/version profile is not allowlisted')
    if configuration.get('mode') not in ('shell', 'init'):
        raise ValueError('Sony lab mode must be shell or init')
    if digest(__file__) != configuration.get('supervisor_sha256'):
        raise ValueError('Sony board supervisor changed before launch')
    path = Path(configuration['manifest'])
    manifest = json.loads(path.read_text())
    if manifest.get('qemu_version') != '10.2.4' or manifest.get('archive_sha256') != ARCHIVE_SHA256 or manifest.get('machine') != 'sony-cxd90057-lab':
        raise ValueError('Requires the reviewed Sony CXD90057 QEMU 10.2.4 build')
    for key in ('binary', 'dtb'):
        if digest(manifest[key]) != manifest.get(key + '_sha256'):
            raise ValueError('Sony build artifact changed: ' + key)
    overlay = Path(__file__).resolve().parent
    for name in ('build.py', 'cxd90057-mailbox.c.inc', 'cxd90057-lab.dts'):
        if digest(overlay / name) != manifest['sources'].get(name):
            raise ValueError('Sony board source changed; rebuild before launch: ' + name)
    generated = path.parent / 'qemu-10.2.4/hw/arm/virt.c'
    if digest(generated) != manifest.get('generated_virt_sha256'):
        raise ValueError('Compiled Sony virt source no longer matches the build manifest')
    for name, (size, sha) in selected['expected'].items():
        source = Path(configuration['artifacts'][name])
        if source.stat().st_size != size or digest(source) != sha:
            raise ValueError('Original Sony component changed: ' + name)
    # Normalize the evidence field used by the shared supervisor.
    manifest['overlay_sha256'] = dict(manifest['sources'], **{
        'run.py': configuration['supervisor_sha256'],
        '../insta360/run.py': configuration['bridge_supervisor_sha256']})
    return manifest


def patch_initrd_end(data, end):
    """Change one exact /chosen property in a checked development DTB copy."""
    if len(data) < 40 or len(data) > 1024**2:
        raise ValueError('Invalid bounded development DTB')
    magic, total, offset, strings, reserve, version, compatible, _, string_size, structure_size = struct.unpack_from('>10I', data)
    if (magic != 0xd00dfeed or total != len(data) or version != 17 or compatible > 17 or
            offset % 4 or reserve % 8 or min(offset, strings, reserve) < 40 or
            not 4 <= structure_size <= total or not 1 <= string_size <= total or
            offset + structure_size > total or strings + string_size > total or
            not (offset + structure_size <= strings or strings + string_size <= offset)):
        raise ValueError('Invalid development DTB header/blocks')
    limit = offset + structure_size
    stack, matches, starts, nodes = [], [], [], set()
    cursor, finished, root_seen = offset, False, False
    while cursor < limit:
        if cursor + 4 > limit: raise ValueError('Truncated development DTB token')
        token = struct.unpack_from('>I', data, cursor)[0]; cursor += 4
        if token == 1:
            finish = data.find(b'\0', cursor, limit)
            if finish < 0: raise ValueError('Unterminated development DTB node')
            name = bytes(data[cursor:finish])
            if b'/' in name: raise ValueError('Invalid development DTB node name')
            if not stack:
                if root_seen or name: raise ValueError('Invalid development DTB root')
                root_seen = True
            stack.append(name)
            node_path = tuple(stack)
            if node_path in nodes: raise ValueError('Duplicate development DTB node path')
            nodes.add(node_path)
            cursor = (finish + 4) & ~3
        elif token == 2:
            if not stack: raise ValueError('Unbalanced development DTB node')
            stack.pop()
        elif token == 3:
            if not stack or cursor + 8 > limit: raise ValueError('Invalid development DTB property')
            size, name_offset = struct.unpack_from('>II', data, cursor); cursor += 8
            if cursor + size > limit or name_offset >= string_size:
                raise ValueError('Development DTB property escapes its block')
            finish = data.find(b'\0', strings + name_offset, strings + string_size)
            if finish < 0: raise ValueError('Unterminated development DTB property name')
            name = data[strings + name_offset:finish]
            if stack == [b'', b'chosen'] and name in (b'linux,initrd-start', b'linux,initrd-end'):
                if size != 8: raise ValueError('Development DTB initrd address is not 64-bit')
                (starts if name == b'linux,initrd-start' else matches).append((cursor, struct.unpack_from('>Q', data, cursor)[0]))
            cursor = (cursor + size + 3) & ~3
        elif token == 4:
            pass
        elif token == 9:
            if stack or not root_seen: raise ValueError('Unbalanced development DTB end')
            finished = True
            if any(data[cursor:limit]): raise ValueError('Unexpected data after development DTB end')
            break
        else:
            raise ValueError('Unknown development DTB token')
        if cursor > limit: raise ValueError('Development DTB padding escapes its block')
    if (not finished or len(matches) != 1 or len(starts) != 1 or
            starts[0][1] != INITRD_START or matches[0][1] != BASE_INITRD_END):
        raise ValueError('Development DTB lacks the exact original chosen initrd range')
    if not isinstance(end, int) or isinstance(end, bool) or not INITRD_START < end < INITRD_START + 128*1024**2:
        raise ValueError('Development DTB initrd end is outside its bound')
    result = bytearray(data)
    struct.pack_into('>Q', result, matches[0][0], end)
    return bytes(result)


def make_command(manifest, snapshots, temporary, monitor, configuration):
    selected = PROFILES[configuration.get('profile', 'a7iv-2.00')]
    dtb = Path(manifest['dtb'])
    if dtb.parent.resolve() != Path(temporary).resolve():
        raise ValueError('Sony development DTB must be a frozen temporary copy')
    end = INITRD_START + selected['expected']['rootfs'][0]
    patched = patch_initrd_end(dtb.read_bytes(), end)
    dtb.write_bytes(patched)
    manifest['dtb_sha256'] = hashlib.sha256(patched).hexdigest()
    manifest['dtb_changes'] = ([] if end == BASE_INITRD_END else [
        {'path': '/chosen/linux,initrd-end', 'before': hex(BASE_INITRD_END), 'after': hex(end)}])
    parameters = Path(temporary) / 'kemco-parameters.bin'
    append = APPEND + (' init=/bin/sh' if configuration['mode'] == 'shell' else '')
    parameters.write_bytes(b'kemco' + append.encode('ascii') + b'\0')
    def loader(path, address):
        return 'loader,file=' + str(path).replace(',', ',,') + ',addr=' + address + ',force-raw=on'
    return [manifest['binary'], '-machine', 'sony-cxd90057-lab,gic-version=2,highmem-mmio=off,highmem-ecam=off',
            '-cpu', 'cortex-a35,cntfrq=2000000', '-smp', '1', '-m', '512M', '-accel', 'tcg,thread=single',
            '-nodefaults', '-display', 'none', '-monitor', 'none', '-serial', 'stdio',
            '-nic', 'none', '-no-reboot', '-kernel', snapshots['kernel'],
            '-device', loader(manifest['dtb'], '0x400002000'),
            '-device', loader(parameters, '0x400001000'),
            '-device', loader(snapshots['rootfs'], '0x418000000'),
            '-qmp', 'unix:' + monitor + ',server=on,wait=off']


def run(configuration):
    selected = PROFILES.get(configuration.get('profile'))
    if selected is None: raise ValueError('Sony model/version profile is not allowlisted')
    bridge = Path(__file__).resolve().parents[1] / 'insta360/run.py'
    if digest(bridge) != configuration.get('bridge_supervisor_sha256'):
        raise ValueError('Common board supervisor changed before launch')
    spec = importlib.util.spec_from_file_location('qemu_board_supervisor', bridge)
    supervisor = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(supervisor)
    profile = {'interventions': ['Linux development DTB and single Cortex-A35 core',
                                'CXD90057 UART/GIC and board register models with unsupported peripheral stubs',
                                'Development DTB reserves a 2 MiB noncacheable DMA instruction pool',
                                'Bootloader kemco parameters, DTB and original ext2 initrd placed by raw loaders',
                                'Blacklisted __boottime_sony_usb_init: USB board DTS is not yet implemented'],
               'kernel_banner': ('Linux version ' + selected['kernel_version']).encode('ascii'),
               'rootfs_banner': b'VFS: Mounted root (ext2 filesystem)',
               'native_version': selected['kernel_version'].encode('ascii'),
               'identity_banner': b'Uid:\t0\t0\t0\t0',
               'init_progress_banners': (b'dma330_probe', b'Modules linked in: crypt_emmc'),
               'native_commands': 'uname -a; mount -t proc proc /proc; cat /proc/self/status; cat /proc/mounts; ls /; ',
               'proof_prefix': 'QEMU_SONY_PROOF_',
               'console_label': 'Original ' + selected['model'] + ' BusyBox console reached; checking native commands',
               'evidence': {'profile': configuration['profile'], 'model': selected['model'],
                            'firmware_version': selected['version'], 'source_firmware_sha256': selected['source_sha256']}}
    if configuration['profile'] == 'a7rv-4.00':
        profile['interventions'].append('Development DTB chosen initrd-end adjusted for the exact A7R V initrd size')
    if configuration['mode'] == 'shell':
        profile['interventions'].append('init=/bin/sh bypasses camera service startup')
    return supervisor.main(configuration, expected=selected['expected'], validator=validate,
                           command_builder=make_command, profile=profile)


if __name__ == '__main__':
    try:
        sys.exit(run(json.loads(sys.argv[1])))
    except Exception as error:
        print(json.dumps({'event': 'error', 'message': str(error)}), flush=True)
        sys.exit(1)
