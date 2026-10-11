import hashlib
import io
import json
import struct
import zlib

import pytest

from devkit.core import Cancelled, Task
from devkit.inspection import HASH_CHUNK, SCAN_LIMIT, SONY_DAT_MAGIC, inspect_firmware


def inspect(tmp_path, data, name='firmware.bin'):
    source = tmp_path / name
    source.write_bytes(data)
    original = hashlib.sha256(data).hexdigest()
    result = inspect_firmware(source)
    assert hashlib.sha256(source.read_bytes()).hexdigest() == original
    assert result['sha256'] == original
    assert result['size'] == len(data)
    json.dumps(result)
    for part in result['candidates']:
        assert 0 <= part['offset'] < part['offset'] + part['size'] <= len(data)
    return result


def arm64(size=4096, footprint=None):
    data = bytearray(size)
    struct.pack_into('<Q', data, 16, size if footprint is None else footprint)
    data[56:60] = b'ARM\x64'
    return data


def elf(machine=183, bits=64, endian='<'):
    header = bytearray(64 if bits == 64 else 52)
    header[:7] = b'\x7fELF' + bytes((2 if bits == 64 else 1, 1 if endian == '<' else 2, 1))
    struct.pack_into(endian + 'H', header, 18, machine)
    struct.pack_into(endian + 'H', header, 52 if bits == 64 else 40, len(header))
    return header


@pytest.mark.parametrize('data', [b'', b'garbage', b'QFI\xfb', b'ANDROID!', b'VHABCIM\r\n', b'\x27\x05\x19\x56', b'\x7fELF'])
def test_truncated_and_unknown_images_return_reports(tmp_path, data):
    report = inspect(tmp_path, data)
    assert report['status'] == 'inspect-only'
    assert report['blockers']
    assert report['candidates'] == []


def test_linux_arm64_ram_footprint_is_not_a_file_size(tmp_path):
    report = inspect(tmp_path, arm64(4096, footprint=8192))
    assert report['format'] == 'linux-arm64-image'
    assert report['architecture'] == 'arm64'
    assert report['status'] == 'boot-candidate'
    assert report['candidates'][0]['size'] == 4096
    assert 'not been verified' in report['blockers'][0]


def test_linux_arm_zimage_bounds(tmp_path):
    data = bytearray(2048)
    struct.pack_into('<III', data, 36, 0x016f2818, 0x8000, 0x8800)
    report = inspect(tmp_path, data)
    assert (report['format'], report['architecture'], report['status']) == ('linux-arm-zimage', 'arm', 'boot-candidate')
    struct.pack_into('<I', data, 44, 0x80000000)
    assert inspect(tmp_path, data)['status'] == 'inspect-only'


def test_x86_bzimage_identification_precedes_mbr(tmp_path):
    data = bytearray(4096)
    data[0x1fe:0x200], data[0x202:0x206] = b'\x55\xaa', b'HdrS'
    struct.pack_into('<I', data, 0x1f4, 80)  # 1280 protected bytes + 2560 setup
    struct.pack_into('<H', data, 0x206, 0x20c)
    struct.pack_into('<H', data, 0x236, 1)
    report = inspect(tmp_path, data)
    assert (report['format'], report['architecture']) == ('linux-x86-bzimage', 'x86_64')
    struct.pack_into('<I', data, 0x1f4, 0xffffffff)
    assert inspect(tmp_path, data)['status'] == 'inspect-only'


@pytest.mark.parametrize('machine,bits,endian,expected', [(183, 64, '<', 'arm64'), (40, 32, '>', 'arm'), (62, 64, '<', 'x86_64'), (243, 32, '<', 'riscv32')])
def test_elf_architecture_does_not_prove_it_is_a_kernel(tmp_path, machine, bits, endian, expected):
    report = inspect(tmp_path, elf(machine, bits, endian))
    assert report['architecture'] == expected
    assert report['format'] == 'elf'
    assert report['status'] == 'inspect-only'


def test_embedded_signature_is_a_lead_not_a_boot_image(tmp_path):
    report = inspect(tmp_path, b'x' * 128 + arm64(4096, footprint=8192))
    assert report['format'] == 'unknown'
    assert report['status'] == 'inspect-only'
    assert report['candidates'][0]['offset'] == 128
    assert report['candidates'][0]['size'] == 64
    assert report['candidates'][0]['kind'] == 'kernel-header'


def test_scan_is_bounded_but_hash_is_complete(tmp_path):
    data = b'x' * SCAN_LIMIT + arm64()
    report = inspect(tmp_path, data)
    assert report['candidates'] == []
    assert report['sha256'] == hashlib.sha256(data).hexdigest()
    assert str(SCAN_LIMIT) in report['findings'][-1].replace(',', '')


def android_boot(version=0):
    page = 4096
    kernel, ramdisk = arm64(), b'ramdisk'
    data = bytearray(page * 3)
    data[:8] = b'ANDROID!'
    struct.pack_into('<I', data, 8, len(kernel))
    struct.pack_into('<I', data, 40, version)
    if version < 3:
        struct.pack_into('<I', data, 16, len(ramdisk))
        struct.pack_into('<I', data, 36, page)
    else:
        struct.pack_into('<I', data, 12, len(ramdisk))
        struct.pack_into('<I', data, 20, 1580 if version == 3 else 1584)
    data[page:page * 2] = kernel
    data[page * 2:page * 2 + len(ramdisk)] = ramdisk
    return data


@pytest.mark.parametrize('version', range(5))
def test_android_component_ranges(tmp_path, version):
    report = inspect(tmp_path, android_boot(version))
    assert report['format'] == 'android-boot'
    assert report['architecture'] == 'arm64'
    assert report['status'] == 'inspect-only'
    assert [part['kind'] for part in report['candidates']] == ['kernel', 'initrd']
    assert report['candidates'][0]['offset'] == 4096


@pytest.mark.parametrize('offset,value', [(8, 0xffffffff), (36, 3), (40, 900)])
def test_android_bad_sizes_never_create_out_of_bounds_components(tmp_path, offset, value):
    data = android_boot()
    struct.pack_into('<I', data, offset, value)
    report = inspect(tmp_path, data)
    assert report['status'] == 'inspect-only'
    assert report['candidates'] == []


def test_hasselblad_public_header_without_decryption(tmp_path):
    data = bytearray(4096)
    data[:len(b'VHABCIM\r\n2102281_PVF\r\n')] = b'VHABCIM\r\n2102281_PVF\r\n'
    struct.pack_into('>II', data, 0x40, 0x41886a34, len(data))
    report = inspect(tmp_path, data, 'update.cim')
    assert report['format'] == 'hasselblad-cim'
    assert report['status'] == 'inspect-only'
    assert any('encrypted' in finding for finding in report['findings'])
    assert report['candidates'] == []


def test_sony_dat_jumps_over_payload_without_decrypting(tmp_path):
    def chunk(tag, data):
        return struct.pack('>I4s', len(data), tag) + data
    raw = SONY_DAT_MAGIC + chunk(b'DATV', b'\x01\x00\x00\x00') + chunk(b'FDAT', b'x' * 4096) + chunk(b'DEND', bytes(4))
    report = inspect(tmp_path, raw, 'BODYDATA.DAT')
    assert report['format'] == 'sony-dat'
    assert report['candidates'] == [{'kind': 'firmware-payload', 'offset': 28, 'size': 4096, 'architecture': None,
                                      'evidence': 'Sony UFU FDAT chunk; encryption/board compatibility is unknown.'}]
    assert report['status'] == 'inspect-only'
    malformed = SONY_DAT_MAGIC + struct.pack('>I4s', 0xffffffff, b'FDAT')
    assert inspect(tmp_path, malformed)['candidates'] == []


def uimage(payload=b'kernel' * 64, arch=22, comp=0):
    header = bytearray(struct.pack('>7I4B32s', 0x27051956, 0, 0, len(payload), 0x40080000, 0x40080000,
                                   zlib.crc32(payload), 5, arch, 2, comp, b'test kernel'))
    struct.pack_into('>I', header, 4, zlib.crc32(header))
    return header + payload


def test_uimage_checks_both_crcs(tmp_path):
    raw = uimage()
    report = inspect(tmp_path, raw)
    assert (report['format'], report['architecture'], report['status']) == ('uimage', 'arm64', 'boot-candidate')
    assert report['candidates'][0]['offset'] == 64
    raw[-1] ^= 1
    assert inspect(tmp_path, raw)['candidates'] == []
    raw[32] ^= 1
    assert inspect(tmp_path, raw)['candidates'] == []


def test_compressed_uimage_needs_adapter(tmp_path):
    report = inspect(tmp_path, uimage(comp=1))
    assert report['status'] == 'inspect-only'
    assert report['candidates'][0]['kind'] == 'kernel'


def qcow(backing=False, external=False):
    header = bytearray(512)
    header[:4] = b'QFI\xfb'
    struct.pack_into('>I', header, 4, 3)
    struct.pack_into('>I', header, 20, 16)
    struct.pack_into('>Q', header, 24, 1024**3)
    struct.pack_into('>I', header, 100, 104)
    if backing:
        struct.pack_into('>QI', header, 8, 104, 10)
    if external:
        struct.pack_into('>Q', header, 72, 4)
    return header


def test_qcow_standalone_vs_external_references(tmp_path):
    assert inspect(tmp_path, qcow())['status'] == 'boot-candidate'
    for raw in (qcow(backing=True), qcow(external=True)):
        report = inspect(tmp_path, raw)
        assert report['format'] == 'qcow2'
        assert report['status'] == 'inspect-only'
        assert report['candidates'] == []


def test_mbr_partition_ranges_must_fit(tmp_path):
    raw = bytearray(4096)
    raw[510:512] = b'\x55\xaa'
    # Signature alone must not be enough.
    assert inspect(tmp_path, raw)['status'] == 'inspect-only'
    struct.pack_into('<B3sB3sII', raw, 446, 0x80, bytes(3), 0x83, bytes(3), 1, 7)
    assert inspect(tmp_path, raw)['status'] == 'boot-candidate'
    struct.pack_into('<I', raw, 458, 999)
    report = inspect(tmp_path, raw)
    assert report['format'] == 'raw-disk'
    assert report['status'] == 'inspect-only'


def test_gpt_requires_header_crc_and_in_file_ranges(tmp_path):
    raw = bytearray(64 * 512)
    header = bytearray(512)
    header[:8] = b'EFI PART'
    struct.pack_into('<III', header, 8, 0x10000, 92, 0)
    struct.pack_into('<4Q', header, 24, 1, 63, 34, 62)
    struct.pack_into('<QII', header, 72, 2, 128, 128)
    struct.pack_into('<I', header, 16, zlib.crc32(header[:92]))
    raw[512:1024] = header
    assert inspect(tmp_path, raw)['status'] == 'boot-candidate'
    raw[512 + 56] ^= 1
    report = inspect(tmp_path, raw)
    assert report['status'] == 'inspect-only'
    assert report['candidates'] == []


@pytest.mark.parametrize('magic,format_id', [(b'PK\x03\x04', 'zip'), (b'\x1f\x8b\x08', 'gzip'), (b'\xfd7zXZ\x00', 'xz'), (b'BZh9', 'bzip2')])
def test_compression_signatures_do_not_expand_data(tmp_path, magic, format_id):
    report = inspect(tmp_path, magic + bytes(4096))
    assert report['format'] == format_id
    assert report['status'] == 'inspect-only'
    assert report['candidates'] == []


def test_task_cancel_interrupts_hash(tmp_path):
    source = tmp_path / 'image.bin'
    source.write_bytes(b'x' * (HASH_CHUNK * 2))
    task = Task()
    task.cancelled.set()
    with pytest.raises(Cancelled):
        inspect_firmware(source, task)


def test_input_mutation_during_read_is_rejected(tmp_path, monkeypatch):
    source = tmp_path / 'image.bin'
    source.write_bytes(b'x' * (HASH_CHUNK * 2))
    task = Task()
    original_check = task.check
    calls = 0
    def check():
        nonlocal calls
        calls += 1
        if calls == 3:
            with source.open('ab') as file:
                file.write(b'changed')
        original_check()
    monkeypatch.setattr(task, 'check', check)
    with pytest.raises(ValueError, match='changed'):
        inspect_firmware(source, task)
