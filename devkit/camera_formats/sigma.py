"""Offline Sigma fp 5.02 MAIN extraction, checked against its original IPL.

The two DFI streams were independently replayed through the original ARM
decoder. No vendor firmware, keys, instructions or executable helpers ship here.
Other revisions remain inspection-only until their layouts are verified.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import re
import struct

from ..core import Task

FORMAT_IDS = ('sigma-fp-dfi',)
MAX_INPUT = 64 * 1024**2
MAX_OUTPUT = 128 * 1024**2
_PREFIX = re.compile(rb'LENGTH=(\d+) w71c1 VER=V91 DVR=([0-9.]+) SUM=(\d+) IPL PTBL DAT1\n\r')
_DFI = bytes.fromhex('40444649010000000000004001000000')
PROFILE = {
    'version': '5.02', 'input_size': 25692160,
    'input_sha256': 'c9fa76f32f523bb2dc9a3fbda39418c31573cab6a1aa5c56d28c371862066ed8',
    'outer_size': 24503296, 'outer_sum': 2768501654,
    'main_size': 49481728, 'load_address': 0xc0000000,
    'main_sha256': 'acead79a338de2d8d7485d036b0a020b8eebcbad29d05741540130c6acb75bfb',
    'segments': (
        (49245696, 0, 0xc0000000, 0xa434830a, 1, 24382976),
        (236032, 24382976, 0xc2ef6e00, 0xcf6567, 0x11, 119808),
    ),
}


def _header(head: bytes):
    match = _PREFIX.match(head[:256])
    if not match or head[0x100:0x110] != _DFI:
        return None
    length, version, checksum = match.groups()
    return int(length), version.decode('ascii'), int(checksum)


def _hash(source: Path, task: Task) -> str:
    digest = hashlib.sha256()
    with source.open('rb') as stream:
        while block := stream.read(1024**2):
            task.check()
            digest.update(block)
    return digest.hexdigest()


def identify(source: Path, task: Task) -> dict | None:
    task.check()
    with source.open('rb') as stream:
        header = _header(stream.read(0x300))
    if header is None:
        return None
    size, version, checksum = header
    known = (source.stat().st_size == PROFILE['input_size'] and version == PROFILE['version']
             and size == PROFILE['outer_size'] and checksum == PROFILE['outer_sum']
             and _hash(source, task) == PROFILE['input_sha256'])
    return {
        'id': FORMAT_IDS[0], 'brand': 'Sigma', 'model': 'fp', 'version': version,
        'format': 'sigma-fp-dfi', 'summary': 'Sigma fp DFI update with ARM MAIN component',
        'extractable': known, 'key_required': False,
        'findings': ['ASCII update prefix and DFI directory identified.',
                     'Exact fp 5.02 input and native decoder contract verified.' if known else
                     'This input is not the exact reviewed fp 5.02 package.'],
        'blockers': ([] if known else ['This revision/layout is inspection-only.']) +
                    ['Original RTOS startup, peripherals and camera UI require separate verification.'],
    }


def _layout(data: bytes, task: Task) -> list[tuple[int, ...]]:
    header = _header(data[:0x300])
    if header is None:
        raise ValueError('Unsupported Sigma fp header')
    outer, version, checksum = header
    if not 0x200 <= outer <= len(data) - 0x100 or checksum > 0xffffffff:
        raise ValueError('Sigma declared container extent is invalid')
    task.check()
    if sum(memoryview(data)[0x100:0x100 + outer]) & 0xffffffff != checksum:
        raise ValueError('Sigma outer byte SUM mismatch')
    rows, packed_end, output_end = [], 0, PROFILE['load_address']
    for index in range(2):
        row = struct.unpack_from('<6I', data, 0x120 + index * 24)
        length, relative, address, stored_sum, codec, packed = row
        if not 0 < length <= MAX_OUTPUT or not 0 < packed <= MAX_INPUT:
            raise ValueError('Sigma DFI section size exceeds limits')
        if relative != packed_end or address != output_end or address + length > 0x100000000:
            raise ValueError('Sigma DFI sections are not contiguous or overflow their address range')
        if 0x300 + relative + packed > 0x100 + outer:
            raise ValueError('Sigma packed section exceeds the declared container')
        if codec not in (1, 0x11):
            raise ValueError('Unsupported Sigma DFI codec')
        task.check()
        if sum(memoryview(data)[0x300 + relative:0x300 + relative + packed]) & 0xffffffff != stored_sum:
            raise ValueError('Sigma packed section byte SUM mismatch')
        packed_end += packed
        output_end += length
        rows.append(row)
    if output_end - PROFILE['load_address'] > MAX_OUTPUT:
        raise ValueError('Sigma total decoded output exceeds limit')
    if tuple(rows) != tuple(PROFILE['segments']):
        raise ValueError('Sigma DFI directory differs from the reviewed profile')
    return rows


def _decode(stream: bytes, expected: int, task: Task) -> tuple[bytes, int]:
    if not 0 < expected <= MAX_OUTPUT:
        raise ValueError('Sigma decoded section exceeds limit')
    dictionary = bytearray(4096)
    cursor, offset, groups = 0xfee, 0, 0
    output = bytearray()
    while len(output) < expected:
        if groups % 4096 == 0:
            task.check()
        groups += 1
        if offset >= len(stream):
            raise ValueError('Truncated Sigma LZSS control')
        control = stream[offset]
        offset += 1
        for bit in range(8):
            if len(output) == expected:
                break
            if control & (1 << bit):
                if offset >= len(stream):
                    raise ValueError('Truncated Sigma LZSS literal')
                value = stream[offset]
                offset += 1
                output.append(value)
                dictionary[cursor] = value
                cursor = (cursor + 1) & 4095
            else:
                if offset + 2 > len(stream):
                    raise ValueError('Truncated Sigma LZSS reference')
                first, second = stream[offset:offset + 2]
                offset += 2
                source = first | ((second & 0xf0) << 4)
                # The original decoder also expands packed block padding.
                # Extract only the DFI-declared address range, validated
                # byte-for-byte against the original IPL decoder output.
                count = min((second & 15) + 3, expected - len(output))
                for index in range(count):
                    value = dictionary[(source + index) & 4095]
                    output.append(value)
                    dictionary[cursor] = value
                    cursor = (cursor + 1) & 4095
    task.check()
    return bytes(output), offset


def extract(source: Path, destination: Path, task: Task) -> dict:
    task.check()
    if source.stat().st_size != PROFILE['input_size'] or source.stat().st_size > MAX_INPUT:
        raise ValueError('Sigma input size is not the reviewed fp 5.02 package')
    data = source.read_bytes()
    if hashlib.sha256(data).hexdigest() != PROFILE['input_sha256']:
        raise ValueError('Sigma input SHA-256 differs from the reviewed fp 5.02 package')
    rows = _layout(data, task)
    digest, total, components = hashlib.sha256(), 0, []
    with (destination / 'main.bin').open('xb') as sink:
        for index, row in enumerate(rows):
            length, relative, address, stored_sum, codec, packed = row
            task.report(f'Decoding Sigma fp MAIN section {index + 1}/2 / 解码适马主程序…')
            decoded, consumed = _decode(data[0x300 + relative:0x300 + relative + packed], length, task)
            sink.write(decoded)
            digest.update(decoded)
            total += len(decoded)
            components.append({'address': address, 'size': length, 'codec': codec,
                               'sha256': hashlib.sha256(decoded).hexdigest(),
                               'packed_bytes': packed, 'consumed_packed_bytes': consumed})
    if total != PROFILE['main_size'] or digest.hexdigest() != PROFILE['main_sha256']:
        raise ValueError('Sigma decoded MAIN SHA-256/size mismatch')
    return {
        'method': 'sigma-fp-502-dfi-lzss',
        'checks': ['Exact official input SHA-256 and length.',
                   'ASCII outer extent and byte SUM; bounded contiguous DFI records and packed byte SUMs.',
                   'Zero-initialized 4 KiB LZSS history; both codec 1 and 0x11 independently verified through original IPL.',
                   'Combined declared MAIN range SHA-256 and size.'],
        'notes': ['Only MAIN is extracted; IPL, partition table and DAT1 tails are not installed or executed.',
                  'Original decoder expands additional packed padding outside the declared MAIN ranges; it is excluded.',
                  'Original firmware remains unchanged. This extraction does not establish successful UI or camera boot.',
                  'Native memory/function cross-checks credited to ijigen/fpSup; decoder independently derived from the supplied IPL.'],
        'decoded_size': total, 'sha256': digest.hexdigest(),
        'load_address': PROFILE['load_address'], 'components': components,
    }
