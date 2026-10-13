"""Bounded Sigma parser tests using authored data, never vendor firmware."""
import hashlib
import struct

import pytest

from devkit.camera_formats import identify_camera, sigma
from devkit.camera_research import unpack_firmware
from devkit.core import Cancelled, Task


def literal(data):
    return b''.join(bytes([(1 << len(data[n:n + 8])) - 1]) + data[n:n + 8]
                    for n in range(0, len(data), 8))


def fixture(tmp_path, monkeypatch):
    payloads = [bytes(range(32)), b'Sigma format test']
    packed = list(map(literal, payloads))
    rows, relative, address = [], 0, 0xc0000000
    for n, (plain, stream) in enumerate(zip(payloads, packed)):
        rows.append((len(plain), relative, address, sum(stream), 1 if n == 0 else 0x11, len(stream)))
        relative += len(stream)
        address += len(plain)
    data = bytearray(0x300) + b''.join(packed) + b'trailer'
    data[0x100:0x110] = sigma._DFI
    for n, row in enumerate(rows):
        struct.pack_into('<6I', data, 0x120 + n * 24, *row)
    size = 0x200 + relative
    checksum = sum(data[0x100:0x100 + size])
    header = f'LENGTH={size} w71c1 VER=V91 DVR=5.02 SUM={checksum} IPL PTBL DAT1\n\r'.encode()
    data[:0x100] = header.ljust(256, b' ')
    profile = {'version': '5.02', 'input_size': len(data), 'input_sha256': hashlib.sha256(data).hexdigest(),
               'outer_size': size, 'outer_sum': checksum, 'segments': tuple(rows),
               'main_size': sum(map(len, payloads)), 'load_address': 0xc0000000,
               'main_sha256': hashlib.sha256(b''.join(payloads)).hexdigest()}
    monkeypatch.setattr(sigma, 'PROFILE', profile)
    source = tmp_path / 'official.bin'
    source.write_bytes(data)
    return source, bytes(data), b''.join(payloads)


def test_framework_extracts_declared_main_and_preserves_source(tmp_path, monkeypatch):
    source, before, expected = fixture(tmp_path, monkeypatch)
    assert identify_camera(source)['id'] == 'sigma-fp-dfi'
    assert sigma.identify(source, Task())['extractable']
    output = unpack_firmware(source, tmp_path / 'output')
    assert (output / 'main.bin').read_bytes() == expected
    assert source.read_bytes() == before
    assert (output / 'extraction.json').is_file()
    with pytest.raises(FileExistsError):
        unpack_firmware(source, output)


def test_unknown_or_modified_revision_stays_inspection_only(tmp_path, monkeypatch):
    source, before, expected = fixture(tmp_path, monkeypatch)
    damaged = bytearray(before)
    damaged[-1] ^= 1
    source.write_bytes(damaged)
    assert not sigma.identify(source, Task())['extractable']
    with pytest.raises(ValueError, match='inspection-only'):
        unpack_firmware(source, tmp_path / 'output')
    assert not (tmp_path / 'output').exists()


def test_initial_zero_dictionary_and_overlapping_copy():
    assert sigma._decode(bytes.fromhex('000000'), 3, Task())[0] == bytes(3)
    # Literal A enters ring at FEE; following ref FEE/length6 copies itself.
    decoded, used = sigma._decode(bytes.fromhex('0141eef3'), 7, Task())
    assert decoded == b'A' * 7 and used == 4
    assert sigma._decode(bytes.fromhex('0141eeff'), 5, Task())[0] == b'A' * 5


@pytest.mark.parametrize('stream,expected', [(b'', 1), (b'\x01', 1), (b'\0\0', 3), (b'\1A', 2)])
def test_truncated_streams_fail(stream, expected):
    with pytest.raises(ValueError, match='Truncated'):
        sigma._decode(stream, expected, Task())


def test_output_limit_and_cancellation():
    with pytest.raises(ValueError, match='limit'):
        sigma._decode(b'', sigma.MAX_OUTPUT + 1, Task())
    task = Task()
    task.cancelled.set()
    with pytest.raises(Cancelled):
        sigma._decode(b'\1A', 1, task)


@pytest.mark.parametrize('kind,match', [('outer', 'outer byte SUM'), ('packed', 'packed section byte SUM'),
                                      ('overlap', 'contiguous'), ('bounds', 'exceeds the declared'),
                                      ('codec', 'codec')])
def test_layout_rejects_corruption_even_with_repaired_outer_sum(tmp_path, monkeypatch, kind, match):
    source, original, expected = fixture(tmp_path, monkeypatch)
    data = bytearray(original)
    if kind in ('outer', 'packed'):
        data[0x301] ^= 1
    elif kind == 'overlap':
        struct.pack_into('<I', data, 0x140, 0xc0000000)
    elif kind == 'bounds':
        struct.pack_into('<I', data, 0x134, 0x2000)
    elif kind == 'codec':
        struct.pack_into('<I', data, 0x130, 9)
    if kind != 'outer':
        size = sigma.PROFILE['outer_size']
        checksum = sum(data[0x100:0x100 + size])
        header = f'LENGTH={size} w71c1 VER=V91 DVR=5.02 SUM={checksum} IPL PTBL DAT1\n\r'.encode()
        data[:0x100] = header.ljust(256, b' ')
    with pytest.raises(ValueError, match=match):
        sigma._layout(bytes(data), Task())


def test_decode_failure_transaction_never_publishes_output(tmp_path, monkeypatch):
    source, before, expected = fixture(tmp_path, monkeypatch)
    monkeypatch.setitem(sigma.PROFILE, 'main_sha256', '0' * 64)
    with pytest.raises(ValueError, match='decoded MAIN'):
        unpack_firmware(source, tmp_path / 'output')
    assert not (tmp_path / 'output').exists()
    assert not list(tmp_path.glob('camera-unpack-*'))
    assert source.read_bytes() == before
