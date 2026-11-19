"""Authored SUNP fixture: bounds, signatures and transactional extraction."""
import hashlib
import io
import struct

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from elftools.elf.elffile import ELFFile

from devkit.camera_formats import sunp
from devkit.camera_research import unpack_firmware
from devkit.core import Task
from devkit.inspection import inspect_firmware


def sums(data):
    end = struct.unpack_from('<I', data, 16)[0]
    struct.pack_into('<I', data, 0x380, 0)
    struct.pack_into('<I', data, 0x380, -sum(struct.unpack(f'<{end//4}I', data[:end])) & 0xffffffff)
    struct.pack_into('<I', data, len(data)-4, 0)
    struct.pack_into('<I', data, len(data)-4, -sum(struct.unpack(f'<{len(data)//4}I', data)) & 0xffffffff)


@pytest.fixture
def sample(tmp_path, monkeypatch):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    der = key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    hdr1, hdr2, resource, main, hdr3, end = 0x300, 0x400, 0x4a0, 0xca0, 0xda0, 0xe30
    data = bytearray(end + 0x504)
    data[:16] = sunp.MAGIC
    struct.pack_into('<6I', data, 16, end, hdr1, hdr2, main, 0, hdr3)
    for n, off in [(1, hdr1), (2, hdr2), (3, hdr3)]:
        data[off:off+16] = f'SUNP BURN HDR {n}\0'.encode()
    struct.pack_into('<I', data, hdr2+16, 1)
    record = bytearray(128)
    record[:9] = b'test-fat\0'
    struct.pack_into('<II', record, 0x70, resource, 0x800)
    data[hdr2+0x20:resource] = bytes(x ^ 0x7a for x in record)
    data[resource+54:resource+62] = b'FAT16   '
    data[resource+510:resource+512] = b'\x55\xaa'
    struct.pack_into('<H', data, resource+11, 512)
    struct.pack_into('<H', data, resource+19, 4)
    data[resource+512:resource+512+len(pem)] = pem
    data[main:main+16] = bytes.fromhex('4f44023c4d4f42340800e00300000000')
    struct.pack_into('<I', data, hdr3+16, 1)
    data[hdr3+32:hdr3+44] = b'A:\\TEST.BIN\0'
    sums(data)
    data[end:end+256] = key.sign(bytes(data[:end] + data[end+256:-4]), padding.PKCS1v15(), hashes.SHA256())
    sums(data)
    monkeypatch.setattr(sunp, 'PROFILE', {'size': len(data), 'sha256': hashlib.sha256(data).hexdigest(),
                        'public_key_spki_sha256': hashlib.sha256(der).hexdigest()})
    path = tmp_path / 'sample.dat'
    path.write_bytes(data)
    return path, data


def test_extract_preserves_input_and_components(sample, tmp_path):
    source, data = sample
    assert inspect_firmware(source)['architecture'] == 'mipsel'
    result = unpack_firmware(source, tmp_path / 'out')
    assert source.read_bytes() == data
    assert (result / 'main.bin').read_bytes() == data[0xca0:0xda0]
    assert (result / 'resource-0.fat16.img').read_bytes() == data[0x4a0:0xca0]
    elf = ELFFile(io.BytesIO((result / 'main.analysis.elf').read_bytes()))
    assert elf.header.e_machine == 'EM_MIPS' and elf.little_endian
    assert elf.header.e_entry == 0x80000000
    assert elf.get_segment(0).data() == data[0xca0:0xda0]
    with pytest.raises(FileExistsError):
        unpack_firmware(source, result)


@pytest.mark.parametrize('kind', ['count', 'resource', 'fat', 'backup', 'order', 'magic'])
def test_bounds_rejected_after_repairing_checksums(sample, kind):
    _, original = sample
    data = bytearray(original)
    if kind == 'count':
        struct.pack_into('<I', data, 0x410, 0xffffffff)
    elif kind == 'resource':
        data[0x490] ^= 4
    elif kind == 'fat':
        struct.pack_into('<H', data, 0x4a0+19, 5)
    elif kind == 'backup':
        struct.pack_into('<I', data, 0xdb0, 1000)
    elif kind == 'order':
        struct.pack_into('<I', data, 28, 0x300)
    else:
        data[0x400] ^= 1
    sums(data)
    with pytest.raises(ValueError):
        sunp._layout(bytes(data))


def test_signature_rejects_mutation_even_with_valid_additive_checksums(sample):
    _, original = sample
    data = bytearray(original)
    data[0xca1] ^= 1
    sums(data)
    layout = sunp._layout(bytes(data))
    with pytest.raises(ValueError, match='signature mismatch'):
        sunp._verify_signature(bytes(data), layout)


def test_changed_sample_stays_inspection_only(sample, tmp_path):
    source, data = sample
    data[-1] ^= 1
    source.write_bytes(data)
    assert not sunp.identify(source, Task())['extractable']
    with pytest.raises(ValueError, match='inspection-only'):
        unpack_firmware(source, tmp_path / 'out')
    assert not (tmp_path / 'out').exists()


def test_checksum_and_truncation(sample):
    _, original = sample
    for data in [original[:-4], original[:-1], original[:100]]:
        with pytest.raises(ValueError):
            sunp._layout(bytes(data))
    original[0xca0] ^= 1
    with pytest.raises(ValueError, match='checksum'):
        sunp._layout(bytes(original))
