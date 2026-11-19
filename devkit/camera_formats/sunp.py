"""Read the reviewed mini Evo SUNP update without executing or repacking it.

Container field names cross-checked with mheistermann/spca-fun, commit
c1f2727499ad661d9b0d8879f37fe5e4b1561b4d, firmware-analysis/tools/sunp.py.
Implementation and validation are local. Other samples remain inspection-only.
"""
from __future__ import annotations

import array
import hashlib
import json
from pathlib import Path
import struct
import sys

from ..core import Task

FORMAT_IDS = ('sunp-burn',)
MAGIC = b'SUNP BURN FILE\0\0'
MAX_INPUT = 256 * 1024**2
PROFILE = {
    'size': 97328468,
    'sha256': '4f412d08688443212c2cf350586a8c01d43d467c580eb19389a82b40929a93ed',
    'public_key_spki_sha256': '7b2c17fb181b089a31ed14ea3de2dad40ee66234f6a09d28dd18e4a8d17d5091',
}


def _digest(source: Path, task: Task) -> str:
    digest = hashlib.sha256()
    with source.open('rb') as stream:
        while block := stream.read(1024**2):
            task.check()
            digest.update(block)
    return digest.hexdigest()


def identify(source: Path, task: Task) -> dict | None:
    with source.open('rb') as stream:
        if stream.read(16) != MAGIC:
            return None
    known = (source.stat().st_size == PROFILE['size']
             and _digest(source, task) == PROFILE['sha256'])
    return {
        'id': FORMAT_IDS[0], 'brand': 'Fujifilm' if known else 'Sunplus / iCatch',
        'model': 'instax mini Evo' if known else None,
        'version': '1.09' if known else None,
        'format': 'sunp-burn', 'architecture': 'mipsel' if known else None,
        'summary': ('mini Evo SUNP update: MIPS32 little-endian, ThreadX, two FAT16 resources'
                    if known else 'SUNP BURN update; unreviewed layout'),
        'extractable': known, 'key_required': False,
        'findings': ['SUNP BURN FILE header identified.'] + ([
            'Exact reviewed input SHA-256; ThreadX MIPS32_4Kx/GNU string and MIPS reset code.',
            'Resource records use XOR 0x7a; main code and FAT16 images are uncompressed.',
        ] if known else []),
        'blockers': ([] if known else ['This SUNP sample is inspection-only.']) + [
            'Physical debug-console access and native code execution are unverified.',
            'No matching complete QEMU board or verified camera boot is provided.',
        ],
    }


def _layout(data: bytes) -> dict:
    if not 0x240 <= len(data) <= MAX_INPUT or len(data) % 4 or data[:16] != MAGIC:
        raise ValueError('Invalid SUNP input size/header')
    end, hdr1, hdr2, main, reserved, hdr3 = struct.unpack_from('<6I', data, 16)
    if end != len(data) - 0x504 or reserved != 0:
        raise ValueError('SUNP declared size/reserved field mismatch')
    if not 0x200 < hdr1 < hdr2 < main < hdr3 < end:
        raise ValueError('SUNP section offsets are not ordered/in bounds')
    words = array.array('I', data)
    if sys.byteorder != 'little':
        words.byteswap()
    if sum(words) & 0xffffffff:
        raise ValueError('SUNP additive 32-bit checksum mismatch')
    if sum(words[:end // 4]) & 0xffffffff:
        raise ValueError('SUNP declared-container checksum mismatch')
    for number, offset, limit in [(1, hdr1, hdr2), (2, hdr2, main), (3, hdr3, end)]:
        if offset + 0x20 > limit or data[offset:offset + 16] != f'SUNP BURN HDR {number}\0'.encode():
            raise ValueError(f'Invalid SUNP header {number}')
    count = struct.unpack_from('<I', data, hdr2 + 16)[0]
    if not 1 <= count <= 16 or hdr2 + 0x20 + 0x80 * count > main:
        raise ValueError('SUNP resource table exceeds bounds')
    resources = []
    expected = hdr2 + 0x20 + 0x80 * count
    for index in range(count):
        record = bytes(value ^ 0x7a for value in data[hdr2 + 0x20 + 128 * index:hdr2 + 0xa0 + 128 * index])
        offset, length = struct.unpack_from('<II', record, 0x70)
        if offset != expected or length < 512 or offset + length > main:
            raise ValueError('SUNP resource range is not contiguous/in bounds')
        boot = data[offset:offset + 512]
        if boot[510:512] != b'\x55\xaa' or boot[54:62] != b'FAT16   ':
            raise ValueError('SUNP resource is not the expected FAT16 image')
        sector_size = struct.unpack_from('<H', boot, 11)[0]
        sectors = struct.unpack_from('<H', boot, 19)[0] or struct.unpack_from('<I', boot, 32)[0]
        if sector_size not in (512, 1024, 2048, 4096) or sectors * sector_size != length:
            raise ValueError('SUNP FAT16 extent differs from directory')
        resources.append({'index': index, 'offset': offset, 'size': length,
                          'label': record[:0x70].split(b'\0')[0].decode('ascii', errors='replace'),
                          'path': f'resource-{index}.fat16.img'})
        expected = offset + length
    if expected != main:
        raise ValueError('SUNP resource end does not match main code')
    backup_count = struct.unpack_from('<I', data, hdr3 + 16)[0]
    if not 0 <= backup_count <= 4096 or hdr3 + 0x20 + 0x70 * backup_count != end:
        raise ValueError('SUNP preservation-list extent mismatch')
    preserve = [data[hdr3 + 0x20 + 112 * i:hdr3 + 0x90 + 112 * i].split(b'\0')[0].decode('ascii', errors='replace')
                for i in range(backup_count)]
    return {'header1_offset': hdr1, 'header2_offset': hdr2, 'main_offset': main,
            'main_size': hdr3 - main, 'header3_offset': hdr3, 'declared_end': end,
            'trailer_offset': end, 'trailer_size': len(data) - end,
            'checksum_le32': struct.unpack_from('<I', data, len(data) - 4)[0],
            'checksum_type': 'sum of all little-endian uint32 words modulo 2^32 equals zero',
            'resources': resources, 'preserved_paths': preserve}


def _analysis_elf(code: bytes, base: int = 0x80000000) -> bytes:
    """Synthetic ELF for disassemblers, with only the observed file-backed range.

    No claim is made about BSS extent or compatibility with Linux user-mode QEMU.
    """
    ident = b'\x7fELF\x01\x01\x01' + bytes(9)
    header = struct.pack('<16sHHIIIIIHHHHHH', ident, 2, 8, 1, base,
                         52, 0, 0x50001000, 52, 32, 1, 0, 0, 0)
    segment = struct.pack('<8I', 1, 0x1000, base, base & 0x1fffffff,
                          len(code), len(code), 7, 0x1000)
    return (header + segment).ljust(0x1000, b'\0') + code


def _verify_signature(data: bytes, layout: dict) -> dict:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding, rsa, utils

    resource = layout['resources'][0]
    image = data[resource['offset']:resource['offset'] + resource['size']]
    marker, footer = b'-----BEGIN PUBLIC KEY-----', b'-----END PUBLIC KEY-----'
    start = image.find(marker)
    stop = image.find(footer, start) if start >= 0 else -1
    if start < 0 or stop < 0 or stop - start > 4096:
        raise ValueError('SUNP public key not found in resource image')
    key = serialization.load_pem_public_key(image[start:stop + len(footer)])
    fingerprint = hashlib.sha256(key.public_bytes(serialization.Encoding.DER,
                                serialization.PublicFormat.SubjectPublicKeyInfo)).hexdigest()
    if not isinstance(key, rsa.RSAPublicKey) or key.key_size != 2048 or fingerprint != PROFILE['public_key_spki_sha256']:
        raise ValueError('SUNP public key differs from reviewed key')
    end = layout['declared_end']
    digest = hashlib.sha256(data[:end])
    digest.update(data[end + 256:-4])
    try:
        key.verify(data[end:end + 256], digest.digest(), padding.PKCS1v15(),
                   utils.Prehashed(hashes.SHA256()))
    except InvalidSignature as error:
        raise ValueError('SUNP RSA/SHA-256 signature mismatch') from error
    return {'algorithm': 'RSA-2048 PKCS#1 v1.5 with SHA-256',
            'public_key_spki_sha256': fingerprint, 'verified': True,
            'signature_offset': end, 'signature_size': 256,
            'signed_ranges': [[0, end], [end + 256, len(data) - 4]],
            'signed_digest': digest.hexdigest(),
            'scope': 'Offline verification against the reviewed embedded public key; hardware enforcement is not tested.'}


def extract(source: Path, destination: Path, task: Task) -> dict:
    if source.stat().st_size != PROFILE['size']:
        raise ValueError('SUNP sample size is not reviewed')
    data = source.read_bytes()
    if hashlib.sha256(data).hexdigest() != PROFILE['sha256']:
        raise ValueError('SUNP sample SHA-256 is not reviewed')
    task.check()
    layout = _layout(data)
    layout['signature'] = _verify_signature(data, layout)
    segments = [
        ('container-header.bin', 0, 0x200),
        ('updater.bin', 0x200, layout['header1_offset'] - 0x200),
        ('burn-header-1.bin', layout['header1_offset'], layout['header2_offset'] - layout['header1_offset']),
        ('burn-header-2.bin', layout['header2_offset'], 0x20 + 128 * len(layout['resources'])),
        ('main.bin', layout['main_offset'], layout['main_size']),
        ('burn-header-3.bin', layout['header3_offset'], layout['declared_end'] - layout['header3_offset']),
        ('signature-trailer.bin', layout['declared_end'], len(data) - layout['declared_end']),
    ] + [(r['path'], r['offset'], r['size']) for r in layout['resources']]
    manifest = []
    for name, offset, size in segments:
        task.check()
        payload = data[offset:offset + size]
        with (destination / name).open('xb') as stream:
            stream.write(payload)
        manifest.append({'path': name, 'offset': offset, 'size': size,
                         'sha256': hashlib.sha256(payload).hexdigest()})
    code = data[layout['main_offset']:layout['header3_offset']]
    (destination / 'main.analysis.elf').write_bytes(_analysis_elf(code))
    layout.update({'source_sha256': PROFILE['sha256'], 'segments': manifest,
                   'main_load_address': '0x80000000',
                   'analysis_elf': 'Synthetic MIPS32/o32 analysis wrapper; BSS unknown; not a Linux executable.',
                   'preserved_paths_note': 'Updater backup/restore list, not files supplied by this update.'})
    (destination / 'sunp-layout.json').write_text(json.dumps(layout, indent=2) + '\n', encoding='utf-8')
    return {'method': 'sunp-reviewed-mini-evo', 'checks': [
        'Exact reviewed source SHA-256 and size.',
        'Ordered section extents, all three header magics, bounded resource and preservation tables.',
        'Whole-file little-endian uint32 additive checksum is zero.',
        'Declared SUNP container has an independent zero uint32 additive sum.',
        'RSA-2048 PKCS#1 v1.5/SHA-256 signature verified using the reviewed embedded public key.',
        'XOR-decoded resource extents match FAT16 boot-sector sizes exactly.',
    ], 'notes': [
        'Offline signature validity does not demonstrate hardware enforcement or acceptance of modified firmware.',
        'main.analysis.elf is generated for static analysis only; main.bin is byte-exact firmware.',
        'The preservation list does not provide device-specific calibration or user data.',
        'Container structure reference: https://github.com/mheistermann/spca-fun/blob/c1f2727499ad661d9b0d8879f37fe5e4b1561b4d/firmware-analysis/tools/sunp.py',
    ]}
