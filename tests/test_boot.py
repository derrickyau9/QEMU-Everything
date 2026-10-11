import os
import struct
import zlib

import pytest

from devkit.boot import BOARDS, boot_template, build_command, validate_profile


def linux_image(architecture):
    image = bytearray(4096)
    if architecture == 'aarch64':
        image[56:60] = b'ARM\x64'
    elif architecture == 'arm':
        image[36:40] = b'\x18\x28\x6f\x01'
        struct.pack_into('<II', image, 40, 0, len(image))
    elif architecture == 'x86_64':
        image[510:512] = b'\x55\xaa'
        image[0x202:0x206] = b'HdrS'
        struct.pack_into('<H', image, 0x206, 0x20c)
        struct.pack_into('<I', image, 0x1f4, (len(image) - 5 * 512) // 16)
        image[0x236:0x238] = b'\x01\x00'
    elif architecture == 'riscv64':
        struct.pack_into('<Q', image, 16, len(image))
        image[48:56] = b'RISCV\0\0\0'
        image[56:60] = b'RSC\x05'
    return bytes(image)


def profile(tmp_path, board='arm64-virt'):
    (tmp_path / 'Image').write_bytes(linux_image(BOARDS[board]['architecture']))
    return {'schema': 1, 'board': board, 'memory_mib': 1024,
            'kernel': 'Image', 'cmdline': 'console=ttyAMA0,115200',
            'status': 'draft'}


def qcow2(*, backing=False, features=0):
    head = bytearray(104)
    head[:4] = b'QFI\xfb'
    struct.pack_into('>I', head, 4, 3)
    if backing:
        struct.pack_into('>QI', head, 8, 104, 12)
    struct.pack_into('>I', head, 20, 16)
    struct.pack_into('>Q', head, 24, 16 * 1024 * 1024)
    struct.pack_into('>Q', head, 72, features)
    struct.pack_into('>I', head, 100, 104)
    return bytes(head)


def test_proprietary_or_unknown_kernel_does_not_get_a_machine_guess():
    for kind in ('sony-dat', 'hasselblad-cim', 'android-boot', 'elf', 'unknown'):
        draft = boot_template({'format': kind, 'filename': 'firmware.bin',
                               'architecture': 'aarch64'})
        assert draft['board'] == '' and draft['kernel'] == ''
        assert draft['status'] == 'draft'


def test_known_kernel_template_is_only_a_draft():
    draft = boot_template({'format': 'linux-arm64-image', 'architecture': 'arm64',
                           'status': 'boot-candidate', 'source_path': 'source/Image'})
    assert draft['board'] == 'arm64-virt'
    assert draft['kernel'] == 'source/Image'
    assert draft['status'] == 'draft'
    assert draft['network'] is False


def test_valid_unverified_kernel_still_gets_a_draft_suggestion():
    draft = boot_template({'format': 'linux-arm64-image', 'architecture': 'aarch64',
                           'status': 'boot-candidate', 'filename': 'Image',
                           'blockers': ['Board drivers and boot have not been verified']})
    assert draft['board'] == 'arm64-virt'
    assert draft['kernel'] == 'Image'
    assert draft['status'] == 'draft'


def test_disk_template_requires_board_selection():
    draft = boot_template({'format': 'qcow2', 'filename': 'disk.qcow2'})
    assert draft['disk_format'] == 'qcow2' and draft['disk'] == 'disk.qcow2'
    assert draft['board'] == ''


def test_valid_unverified_disk_keeps_its_component_but_no_board_guess():
    draft = boot_template({'format': 'qcow2', 'filename': 'disk.qcow2',
                           'status': 'boot-candidate',
                           'blockers': ['Compatible board and boot have not been verified']})
    assert draft['disk'] == 'disk.qcow2'
    assert draft['board'] == ''
    assert draft['status'] == 'draft'


def test_blocked_inspection_does_not_populate_kernel():
    draft = boot_template({'format': 'linux-arm64-image', 'architecture': 'aarch64',
                           'filename': 'Image', 'blockers': ['truncated image']})
    assert draft['board'] == '' and draft['kernel'] == ''


@pytest.mark.parametrize('board', list(BOARDS))
def test_each_board_builds_a_direct_boot_command(tmp_path, board):
    p = profile(tmp_path, board)
    runtime = tmp_path / 'qemu'
    runtime.mkdir()
    binary = BOARDS[board]['binary'] + ('.exe' if os.name == 'nt' else '')
    (runtime / binary).touch()
    args = build_command(p, tmp_path, runtime)
    assert args[0] == str(runtime / binary)
    assert args[args.index('-kernel') + 1] == str(tmp_path / 'Image')
    assert args[args.index('-nic') + 1] == 'none'
    assert args[args.index('-serial') + 1] == 'stdio'
    assert args.count('-serial') == 1 and '-nographic' not in args
    assert p['status'] == 'draft'  # validation never mutates the caller's profile


@pytest.mark.parametrize('path', ['../outside', '/etc/passwd', 'C:/outside',
                                'file,format=raw', 'CON', 'source/../Image'])
def test_profile_paths_cannot_reach_outside_the_workspace(tmp_path, path):
    p = profile(tmp_path)
    p['kernel'] = path
    with pytest.raises(ValueError):
        validate_profile(p, tmp_path)


def test_symlink_to_host_file_is_rejected(tmp_path):
    outside = tmp_path.parent / (tmp_path.name + '-outside')
    outside.write_bytes(linux_image('aarch64'))
    try:
        (tmp_path / 'escape').symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip('This Windows account cannot create symlinks')
    p = profile(tmp_path)
    p['kernel'] = 'escape'
    with pytest.raises(ValueError, match='escapes'):
        validate_profile(p, tmp_path)


@pytest.mark.parametrize('extra', [{'args': ['-drive', 'file=host']},
                                  {'machine': 'virt'}, {'bios': '../../secret'}])
def test_arbitrary_qemu_flags_are_rejected(tmp_path, extra):
    p = profile(tmp_path)
    p.update(extra)
    with pytest.raises(ValueError, match='Unknown boot profile fields'):
        validate_profile(p, tmp_path)


def test_kernel_architecture_is_checked_from_file(tmp_path):
    p = profile(tmp_path)
    (tmp_path / 'Image').write_bytes(linux_image('arm'))
    with pytest.raises(ValueError, match='architecture arm does not match'):
        validate_profile(p, tmp_path)


def test_camera_update_container_cannot_be_direct_kernel(tmp_path):
    p = profile(tmp_path)
    (tmp_path / 'Image').write_bytes(b'VHABCIM\r\n' + b'\0' * 4096)
    with pytest.raises(ValueError, match='update containers'):
        validate_profile(p, tmp_path)


@pytest.mark.parametrize('board,offset', [('arm-virt', 44), ('x86_64-pc', 0x1f4),
                                         ('riscv64-virt', 16)])
def test_invalid_direct_kernel_lengths_are_rejected(tmp_path, board, offset):
    p = profile(tmp_path, board)
    invalid = bytearray((tmp_path / 'Image').read_bytes())
    invalid[offset:offset + 4] = b'\0' * 4
    (tmp_path / 'Image').write_bytes(invalid)
    with pytest.raises(ValueError):
        validate_profile(p, tmp_path)


@pytest.mark.parametrize('field,value', [('cpu', 'max,migratable=off'),
                                         ('memory_mib', 16), ('memory_mib', 65536),
                                         ('memory_mib', True), ('network', 'yes'),
                                         ('network', True),
                                         ('cmdline', 'console=ttyS0\n-host-option')])
def test_resource_limits_and_option_grammar(tmp_path, field, value):
    p = profile(tmp_path)
    p[field] = value
    with pytest.raises(ValueError):
        validate_profile(p, tmp_path)


def test_disk_only_virt_board_requires_kernel(tmp_path):
    (tmp_path / 'disk.img').write_bytes(b'\0' * 1024)
    with pytest.raises(ValueError, match='requires a direct kernel'):
        validate_profile({'schema': 1, 'board': 'arm64-virt', 'disk': 'disk.img'}, tmp_path)


def test_disk_uses_temporary_snapshot_and_explicit_format(tmp_path):
    (tmp_path / 'disk.img').write_bytes(b'\0' * 1024)
    (tmp_path / ('qemu-system-x86_64' + ('.exe' if os.name == 'nt' else ''))).touch()
    args = build_command({'schema': 1, 'board': 'x86_64-pc', 'disk': 'disk.img'}, tmp_path, tmp_path)
    assert '-snapshot' in args
    assert 'format=raw,if=ide' in args[args.index('-drive') + 1]
    assert '-kernel' not in args


@pytest.mark.parametrize('data', [qcow2(backing=True), qcow2(features=4), qcow2(features=16)])
def test_qcow2_cannot_open_backing_or_external_host_files(tmp_path, data):
    (tmp_path / 'disk.qcow2').write_bytes(data)
    with pytest.raises(ValueError):
        validate_profile({'schema': 1, 'board': 'x86_64-pc',
                          'disk': 'disk.qcow2', 'disk_format': 'qcow2'}, tmp_path)


def test_qcow2_must_not_be_opened_as_raw(tmp_path):
    (tmp_path / 'disk.img').write_bytes(qcow2())
    with pytest.raises(ValueError, match='not a raw'):
        validate_profile({'schema': 1, 'board': 'x86_64-pc', 'disk': 'disk.img'}, tmp_path)


def test_uncompressed_uimage_checks_both_crcs(tmp_path):
    payload = linux_image('arm')
    header = bytearray(64)
    header[:4] = b'\x27\x05\x19\x56'
    struct.pack_into('>I', header, 12, len(payload))
    struct.pack_into('>I', header, 24, zlib.crc32(payload))
    header[28:32] = bytes([5, 2, 2, 0])
    struct.pack_into('>I', header, 4, zlib.crc32(header))
    (tmp_path / 'uImage').write_bytes(header + payload)
    p = {'schema': 1, 'board': 'arm-virt', 'kernel': 'uImage'}
    assert validate_profile(p, tmp_path)['status'] == 'configured'
    (tmp_path / 'uImage').write_bytes(header + payload[:-1] + b'x')
    with pytest.raises(ValueError, match='payload CRC'):
        validate_profile(p, tmp_path)


def test_missing_qemu_binary_has_actionable_error(tmp_path):
    p = profile(tmp_path)
    with pytest.raises(FileNotFoundError, match='qemu-system-aarch64'):
        build_command(p, tmp_path, tmp_path / 'missing-runtime')
