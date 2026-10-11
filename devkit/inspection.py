"""Bounded, read-only identification of user supplied firmware.

Signatures establish an image *format*, never successful board emulation.  No
archive is expanded and no binary from an imported image is executed here.
Layout references: Linux's arm64/arm/x86 boot protocols, AOSP bootimg.h,
U-Boot include/image.h, and QEMU's qcow2 interoperability specification.
Sony's UFU chunk layout is documented by ma1co/fwtool.py (sony/dat.py).
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import stat
import struct
from typing import BinaryIO
import zlib

from .core import Task


HASH_CHUNK = 1024 * 1024
SCAN_LIMIT = 8 * 1024 * 1024
MAX_CANDIDATES = 64
SONY_DAT_MAGIC = b'\x89UFU\r\n\x1a\n'
ELF_ARCHITECTURES = {3: 'x86', 8: 'mips', 20: 'ppc', 21: 'ppc64', 40: 'arm',
                     62: 'x86_64', 183: 'arm64', 243: 'riscv64'}


def _read(file: BinaryIO, offset: int, length: int, size: int) -> bytes:
    """Read a small, explicit region, rejecting offsets outside this file."""
    if offset < 0 or length < 0 or length > SCAN_LIMIT or offset + length > size:
        return b''
    file.seek(offset)
    return file.read(length)


def _text(data: bytes) -> str:
    return data.split(b'\0', 1)[0].decode('ascii', 'replace').strip()


def _candidate(report: dict, kind: str, offset: int, size: int,
               architecture: str | None, evidence: str) -> None:
    if size > 0 and offset >= 0 and offset + size <= report['size']:
        if len(report['candidates']) < MAX_CANDIDATES:
            report['candidates'].append({'kind': kind, 'offset': offset, 'size': size,
                                         'architecture': architecture, 'evidence': evidence})


def _boot(report: dict, kind: str = 'kernel') -> None:
    report['status'] = 'boot-candidate'
    _candidate(report, kind, 0, report['size'], report['architecture'],
               'Recognized top-level image; target machine compatibility is unverified.')
    report['blockers'].append('Select a compatible QEMU board and supply its required boot components; boot has not been verified.')


def _kernel_header(header: bytes, available: int) -> tuple[str, str, int] | None:
    if len(header) >= 64 and header[56:60] == b'ARM\x64':
        image_size = struct.unpack_from('<Q', header, 16)[0]
        # This is the RAM footprint (including BSS), not an extraction length.
        # It can legitimately exceed the file length; zero predates Linux 3.17.
        if image_size == 0 or 64 <= image_size < 2**48:
            return 'linux-arm64-image', 'arm64', available
    if len(header) >= 48 and header[36:40] == b'\x18\x28\x6f\x01':
        start, end = struct.unpack_from('<II', header, 40)
        length = end - start
        if 48 <= length <= available:
            return 'linux-arm-zimage', 'arm', length
    if len(header) >= 0x238 and header[0x1fe:0x200] == b'\x55\xaa' and header[0x202:0x206] == b'HdrS':
        protocol = struct.unpack_from('<H', header, 0x206)[0]
        setup_sectors = header[0x1f1] or 4
        protected_size = struct.unpack_from('<I', header, 0x1f4)[0] * 16
        total = (setup_sectors + 1) * 512 + protected_size
        if protocol >= 0x200 and protected_size > 0 and total <= available:
            xloadflags = struct.unpack_from('<H', header, 0x236)[0] if protocol >= 0x20c else 0
            return 'linux-x86-bzimage', 'x86_64' if xloadflags & 1 else 'x86', total
    return None


def _elf(header: bytes, available: int) -> str | None:
    if len(header) < 52 or not header.startswith(b'\x7fELF') or header[4] not in (1, 2) or header[5] not in (1, 2) or header[6] != 1:
        return None
    width = 64 if header[4] == 2 else 52
    if len(header) < width or available < width:
        return None
    endian = '<' if header[5] == 1 else '>'
    ehsize = struct.unpack_from(endian + 'H', header, 52 if width == 64 else 40)[0]
    if ehsize != width:
        return None
    machine = struct.unpack_from(endian + 'H', header, 18)[0]
    arch = ELF_ARCHITECTURES.get(machine, f'elf-machine-{machine}')
    if machine == 243 and width == 52:
        arch = 'riscv32'
    return arch


def _android(file: BinaryIO, header: bytes, report: dict) -> None:
    report['format'] = 'android-boot'
    report['summary'] = 'Android boot image container; components require board matching.'
    if len(header) < 48:
        report['blockers'].append('Truncated Android boot header.')
        return
    version = struct.unpack_from('<I', header, 40)[0]
    report['findings'].append(f'Android boot header version {version}.')
    if version not in range(5):
        report['blockers'].append('Unsupported Android boot header version; no components were inferred.')
        return
    required = [1632, 1648, 1660, 1580, 1584][version]
    if len(header) < required:
        report['blockers'].append('Truncated Android boot header.')
        return
    kernel_size = struct.unpack_from('<I', header, 8)[0]
    if version < 3:
        ramdisk_size = struct.unpack_from('<I', header, 16)[0]
        page = struct.unpack_from('<I', header, 36)[0]
        second_size = struct.unpack_from('<I', header, 24)[0]
        if page < required or page > 65536 or page & (page - 1):
            report['blockers'].append('Invalid Android boot page size.')
            return
    else:
        ramdisk_size = struct.unpack_from('<I', header, 12)[0]
        page, second_size = 4096, 0
        header_size = struct.unpack_from('<I', header, 20)[0]
        if header_size < required or header_size > page:
            report['blockers'].append('Invalid Android boot header size.')
            return
    align = lambda number: (number + page - 1) // page * page
    kernel_offset = page
    ramdisk_offset = page + align(kernel_size)
    end = ramdisk_offset + align(ramdisk_size) + align(second_size)
    if kernel_size == 0 or end > report['size']:
        report['blockers'].append('Android boot payload sizes extend outside the file or contain no kernel.')
        return
    kernel = _read(file, kernel_offset, min(kernel_size, 4096), report['size'])
    identified = _kernel_header(kernel, kernel_size)
    architecture = identified[1] if identified else _elf(kernel, kernel_size)
    report['architecture'] = architecture
    _candidate(report, 'kernel', kernel_offset, kernel_size, architecture, 'Android header kernel range.')
    _candidate(report, 'initrd', ramdisk_offset, ramdisk_size, None, 'Android header ramdisk range.')
    if second_size:
        _candidate(report, 'second-stage', ramdisk_offset + align(ramdisk_size), second_size, None, 'Android header second-stage range.')
    if version in (1, 2):
        dtbo_size, dtbo_offset = struct.unpack_from('<IQ', header, 1632)
        if dtbo_size:
            if dtbo_offset < end or dtbo_offset + dtbo_size > report['size']:
                report['blockers'].append('Recovery DTBO range is invalid; do not extract components automatically.')
                report['candidates'].clear()
                return
            _candidate(report, 'dtbo', dtbo_offset, dtbo_size, None, 'Android recovery DTBO header range.')
            end = dtbo_offset + align(dtbo_size)
        if version == 2:
            dtb_size = struct.unpack_from('<I', header, 1648)[0]
            if end + dtb_size > report['size']:
                report['blockers'].append('Android DTB extends outside the file.')
                report['candidates'].clear()
                return
            _candidate(report, 'dtb', end, dtb_size, None, 'Android v2 DTB header range.')
    if version >= 3:
        report['blockers'].append('Android v3/v4 also requires the matching vendor_boot image and vendor hardware support.')
    report['blockers'].append('A vendor Android kernel does not automatically support QEMU virt; inspect its device tree and drivers.')


def _cim(header: bytes, report: dict) -> None:
    report['format'] = 'hasselblad-cim'
    report['summary'] = 'Hasselblad VHABCIM update container.'
    if len(header) < 0x80:
        report['blockers'].append('Truncated VHABCIM public header.')
        return
    # Use the existing parser's documented public layout, without calling its
    # whole-file/decryption APIs or pretending a short probe is the full file.
    parts = header[:0x3c].split(b'\r\n')
    if len(parts) > 1:
        report['findings'].append(f'Public product identifier: {_text(parts[1])}.')
    marker, stored = struct.unpack_from('>II', header, 0x40)
    labels = {0x41886a34: 'encrypted', 0x0efc59fb: 'plaintext'}
    report['findings'].append(f'Public payload marker: {labels.get(marker, "unknown")} (0x{marker:08x}).')
    if stored != report['size']:
        report['blockers'].append(f'VHABCIM declared size {stored} does not match the file size.')
    if marker not in labels:
        report['blockers'].append('Unknown VHABCIM payload marker.')
    report['blockers'].append('Use a verified Hasselblad adapter for UI execution; the update container is not a QEMU boot image.')


def _sony_dat(file: BinaryIO, report: dict) -> None:
    report['format'] = 'sony-dat'
    report['summary'] = 'Sony UFU update container; payload has not been decrypted.'
    offset, chunks, dend = 8, [], False
    for _ in range(128):
        raw = _read(file, offset, 8, report['size'])
        if len(raw) != 8:
            report['blockers'].append('Sony DAT chunk header is truncated or missing DEND.')
            break
        length, tag = struct.unpack('>I4s', raw)
        start = offset + 8
        if start + length > report['size']:
            report['blockers'].append('Sony DAT chunk extends outside the file.')
            break
        if not all(32 <= byte < 127 for byte in tag):
            report['blockers'].append('Sony DAT chunk type contains invalid bytes.')
            break
        chunks.append(f'{tag.decode("ascii")} ({length} bytes)')
        if tag == b'FDAT':
            _candidate(report, 'firmware-payload', start, length, None, 'Sony UFU FDAT chunk; encryption/board compatibility is unknown.')
        if tag == b'DEND':
            dend = True
            if length != 4:
                report['blockers'].append('Sony DAT DEND CRC field has an invalid size.')
            if start + length != report['size']:
                report['findings'].append('Trailing bytes follow the Sony DAT DEND chunk.')
            break
        offset = start + length
    if not dend and not report['blockers']:
        report['blockers'].append('Sony DAT inspection chunk limit reached; full structure is unverified.')
    report['findings'].append('Public chunks: ' + ', '.join(chunks[:12]) + (', …' if len(chunks) > 12 else ''))
    report['findings'].append('DAT container CRC and FDAT encryption were not checked.')
    report['blockers'].append('Sony update extraction and a matching SoC/board adapter are required; identification is not Sony boot support.')


def _uimage(file: BinaryIO, header: bytes, report: dict, task: Task) -> None:
    report['format'] = 'uimage'
    report['summary'] = 'U-Boot legacy image.'
    if len(header) < 64:
        report['blockers'].append('Truncated uImage header.')
        return
    _, hcrc, _, length, load, entry, dcrc, os_id, arch_id, type_id, compression, name = struct.unpack('>7I4B32s', header[:64])
    report['architecture'] = {2: 'arm', 3: 'x86', 22: 'arm64', 24: 'x86_64'}.get(arch_id)
    report['findings'].extend([f'uImage name: {_text(name)}.', f'Load address 0x{load:x}; entry 0x{entry:x}; OS {os_id}, architecture {arch_id}, type {type_id}, compression {compression}.'])
    crc_header = header[:4] + bytes(4) + header[8:64]
    if zlib.crc32(crc_header) != hcrc:
        report['blockers'].append('uImage header CRC32 does not match.')
        return
    if length == 0 or 64 + length > report['size']:
        report['blockers'].append('uImage payload is empty or extends outside the file.')
        return
    file.seek(64)
    crc, remaining = 0, length
    while remaining:
        task.check()
        data = file.read(min(HASH_CHUNK, remaining))
        if not data:
            report['blockers'].append('uImage payload changed or was truncated while being read.')
            return
        crc = zlib.crc32(data, crc)
        remaining -= len(data)
    if crc != dcrc:
        report['blockers'].append('uImage payload CRC32 does not match.')
        return
    report['findings'].append('uImage header and payload CRC32 both match.')
    kind = 'kernel' if type_id in (2, 14) else 'initrd' if type_id == 3 else 'payload'
    _candidate(report, kind, 64, length, report['architecture'], 'uImage size and CRC32 verified; payload may be compressed.')
    if os_id == 5 and type_id in (2, 14) and compression == 0 and report['architecture'] in ('arm', 'arm64', 'x86', 'x86_64'):
        report['status'] = 'boot-candidate'
        report['blockers'].append('QEMU board, load address and vendor driver compatibility remain unverified.')
    else:
        report['blockers'].append('This uImage type, architecture or compression requires an adapter before direct boot.')


def _qcow(header: bytes, report: dict) -> None:
    report['format'] = 'qcow2'
    report['summary'] = 'QEMU qcow2 disk image; guest architecture is unspecified.'
    if len(header) < 72:
        report['blockers'].append('Truncated qcow2 header.')
        return
    version = struct.unpack_from('>I', header, 4)[0]
    backing_offset = struct.unpack_from('>Q', header, 8)[0]
    backing_size, cluster_bits = struct.unpack_from('>II', header, 16)
    virtual_size = struct.unpack_from('>Q', header, 24)[0]
    crypt = struct.unpack_from('>I', header, 32)[0]
    if version not in (2, 3) or not 9 <= cluster_bits <= 21 or not 0 < virtual_size < 2**63:
        report['blockers'].append('Unsupported or invalid qcow2 version, cluster size or virtual size.')
        return
    if version == 3:
        if len(header) < 104:
            report['blockers'].append('Truncated qcow2 v3 header.')
            return
        header_length = struct.unpack_from('>I', header, 100)[0]
        if header_length < 104 or header_length > report['size'] or header_length > 1 << cluster_bits:
            report['blockers'].append('Invalid qcow2 v3 header length.')
        incompatible = struct.unpack_from('>Q', header, 72)[0]
        if incompatible & 4:
            report['blockers'].append('qcow2 references an external data file; automatic boot is disabled.')
        if incompatible & ~0x1f:
            report['blockers'].append('Unknown qcow2 incompatible feature flags.')
    report['findings'].append(f'qcow2 version {version}; virtual disk size {virtual_size} bytes.')
    if backing_offset or backing_size:
        report['blockers'].append('qcow2 references a backing file; import a flattened standalone image to avoid external host paths.')
    if crypt:
        report['blockers'].append('Encrypted qcow2 requires credentials and is not automatically bootable.')
    report['findings'].append('Only the qcow2 header was inspected; allocation tables and bootloader were not validated.')
    if not report['blockers']:
        _boot(report, 'disk')


def _disk(file: BinaryIO, header: bytes, report: dict) -> bool:
    # Check both common logical sector sizes; a GPT magic alone is insufficient.
    for sector in (512, 4096):
        gpt = _read(file, sector, min(sector, report['size'] - sector), report['size']) if report['size'] > sector else b''
        if not gpt.startswith(b'EFI PART'):
            continue
        report['format'], report['summary'] = 'raw-disk', 'Raw disk with a GPT partition table.'
        report['findings'].append(f'GPT signature at logical sector 1 ({sector}-byte sectors).')
        if len(gpt) < 92:
            report['blockers'].append('Truncated GPT header.')
            return True
        revision, length, crc = struct.unpack_from('<III', gpt, 8)
        if revision != 0x10000 or not 92 <= length <= sector or length > len(gpt):
            report['blockers'].append('Invalid GPT revision or header length.')
            return True
        if zlib.crc32(gpt[:16] + bytes(4) + gpt[20:length]) != crc:
            report['blockers'].append('GPT header CRC32 does not match.')
            return True
        current, backup, first, last = struct.unpack_from('<4Q', gpt, 24)
        table_lba, count, entry_size = struct.unpack_from('<QII', gpt, 72)
        sectors = report['size'] // sector
        if current != 1 or not 1 < backup < sectors or not 1 < first <= last < sectors or table_lba < 2 or count == 0 or entry_size < 128 or entry_size % 128 or table_lba * sector + count * entry_size > report['size']:
            report['blockers'].append('GPT sector or partition entry ranges are invalid.')
            return True
        report['findings'].append(f'GPT header CRC32 matches; {count} partition entries (table CRC not checked).')
        _boot(report, 'disk')
        return True
    if len(header) < 512 or header[510:512] != b'\x55\xaa':
        return False
    entries = [struct.unpack_from('<B3sB3sII', header, 446 + index * 16) for index in range(4)]
    present = [entry for entry in entries if entry[2] and entry[5]]
    if not present:
        return False  # A sector footer alone is not a disk image signature.
    report['format'], report['summary'] = 'raw-disk', 'Raw disk with an MBR partition table.'
    if any(entry[0] not in (0, 0x80) or entry[4] == 0 or (entry[4] + entry[5]) * 512 > report['size'] for entry in present):
        report['blockers'].append('MBR partition entries extend outside the file or contain invalid status/start fields.')
    elif any(entry[2] == 0xee for entry in present):
        report['blockers'].append('Protective MBR was found without a valid GPT header.')
    else:
        report['findings'].append(f'MBR signature and {len(present)} in-file partition ranges found; bootloader is unverified.')
        _boot(report, 'disk')
    return True


def _embedded(probe: bytes, report: dict, task: Task) -> None:
    """Find only a bounded set of structural leads, never promote to boot support."""
    found = []
    for signature, back in ((b'ARM\x64', 56), (b'\x18\x28\x6f\x01', 36), (b'\x7fELF', 0)):
        offset = probe.find(signature)
        while offset >= 0 and len(found) < MAX_CANDIDATES:
            task.check()
            start = offset - back
            if start > 0:
                part = probe[start:start + 4096]
                available = report['size'] - start
                kernel = _kernel_header(part, available) if back else None
                arch = kernel[1] if kernel else _elf(part, available) if not back else None
                if kernel:
                    # ARM64 image_size is a RAM footprint, not a file boundary.
                    length = kernel[2]
                    if kernel[0] == 'linux-arm64-image':
                        length = 64
                    found.append(('kernel-header', start, length, arch))
                elif arch:
                    found.append(('elf-header', start, 64 if part[4] == 2 else 52, arch))
            offset = probe.find(signature, offset + len(signature))
    for kind, offset, length, arch in sorted(found, key=lambda item: item[1]):
        _candidate(report, kind, offset, length, arch, 'Embedded structural signature within the bounded probe; not an authenticated extraction boundary.')
    if found:
        report['findings'].append(f'Embedded structural leads found in the first {len(probe)} bytes; these are not proof of board compatibility.')


def inspect_firmware(source: Path, task: Task | None = None) -> dict:
    """Identify a regular file, returning a JSON report without modifying it.

    The full file is streamed for SHA-256.  Identification reads at most an
    8 MiB prefix, small explicit headers and (for uImage) a streamed payload CRC.
    Unknown/corrupt formats return useful reports rather than rejecting import.
    Cancellation and ordinary filesystem errors propagate to the caller.
    """
    source, task = Path(source), task or Task()
    task.check()
    with source.open('rb') as file:
        before = os.fstat(file.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise ValueError('Firmware inspection requires a regular file.')
        size, digest, hashed = before.st_size, hashlib.sha256(), 0
        task.report(f'Inspecting {source.name} ({size:,} bytes)…')
        while True:
            task.check()
            chunk = file.read(HASH_CHUNK)
            if not chunk:
                break
            digest.update(chunk)
            hashed += len(chunk)
        after = os.fstat(file.fileno())
        if hashed != size or after.st_size != size or after.st_mtime_ns != before.st_mtime_ns:
            raise ValueError('Firmware file changed while being inspected; retry with an unchanged copy.')
        report = {'schema_version': 1, 'filename': source.name, 'size': size,
                  'sha256': digest.hexdigest(), 'format': 'unknown', 'architecture': None,
                  'summary': 'Unrecognized firmware or binary; manual platform investigation is required.',
                  'findings': [], 'candidates': [], 'status': 'inspect-only', 'blockers': []}
        probe = _read(file, 0, min(size, SCAN_LIMIT), size)
        header = probe[:65536]
        if header.startswith(b'VHABCIM\r\n'):
            _cim(header, report)
        elif header.startswith(SONY_DAT_MAGIC):
            _sony_dat(file, report)
        elif header.startswith(b'UDTRFIRM'):
            report['format'], report['summary'] = 'sony-fdat', 'Sony plaintext FDAT container signature; board support is unverified.'
            report['blockers'].append('Sony FDAT component extraction and a matching SoC adapter are required.')
        elif header.startswith(b'ANDROID!'):
            _android(file, header, report)
        elif header.startswith(b'VNDRBOOT'):
            report['format'], report['summary'] = 'android-vendor-boot', 'Android vendor boot container; requires a matching Android boot image.'
            report['blockers'].append('Vendor ramdisk/DTB extraction and vendor hardware support are required.')
        elif header.startswith(b'\x27\x05\x19\x56'):
            _uimage(file, header, report, task)
        elif header.startswith(b'QFI\xfb'):
            _qcow(header, report)
        elif (kernel := _kernel_header(header, size)):
            report['format'], report['architecture'], _ = kernel
            report['summary'] = f'Linux kernel image for {kernel[1]}; QEMU board compatibility is unverified.'
            _boot(report)
        elif header.startswith(b'\x7fELF'):
            report['format'], report['summary'] = 'elf', 'ELF binary; a userspace executable is not necessarily firmware or a kernel.'
            report['architecture'] = _elf(header, size)
            if report['architecture']:
                report['findings'].append(f'ELF e_machine identifies {report["architecture"]}.')
            else:
                report['blockers'].append('ELF identification/header fields are invalid or truncated.')
            report['blockers'].append('Determine ELF purpose and board/load protocol before attempting system emulation.')
        elif header.startswith((b'PK\x03\x04', b'PK\x05\x06', b'PK\x07\x08')):
            report['format'], report['summary'] = 'zip', 'ZIP archive signature; contents have not been extracted or validated.'
        elif header.startswith(b'\x1f\x8b\x08'):
            report['format'], report['summary'] = 'gzip', 'gzip compressed data; decompressed content is unknown.'
        elif header.startswith(b'\xfd7zXZ\x00'):
            report['format'], report['summary'] = 'xz', 'XZ compressed data; decompressed content is unknown.'
        elif header.startswith(b'BZh') and len(header) >= 4 and header[3] in b'123456789':
            report['format'], report['summary'] = 'bzip2', 'bzip2 compressed data; decompressed content is unknown.'
        elif len(header) >= 265 and header[257:263] in (b'ustar\x00', b'ustar '):
            report['format'], report['summary'] = 'tar', 'tar archive signature; contents have not been extracted or validated.'
        else:
            _disk(file, header, report)
        if report['format'] in ('unknown', 'sony-fdat', 'elf'):
            _embedded(probe, report, task)
        if report['format'] in ('zip', 'gzip', 'xz', 'bzip2', 'tar'):
            report['blockers'].append('Expand with a bounded format-specific adapter, then identify kernel/rootfs and target board.')
        if report['format'] == 'unknown':
            report['blockers'].append('No supported top-level signature found; specify packaging, CPU, memory map and peripheral requirements.')
        report['findings'].append(f'Identification prefix is limited to {min(size, SCAN_LIMIT):,} bytes; SHA-256 covers all {size:,} bytes.')
        task.check()
        final = os.fstat(file.fileno())
        if final.st_size != size or final.st_mtime_ns != before.st_mtime_ns:
            raise ValueError('Firmware file changed while being inspected; retry with an unchanged copy.')
        return report
