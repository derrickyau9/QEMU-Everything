"""Bounded, offline decoding of the publicly documented Ricoh GR frame format.

Format research: yeahnope/gr_unpack and radium-wang's GR IV research.
This implementation is original; it does not import either project's code.
Only GR III, IIIx and IV containers are recognized, not legacy Ricoh/Pentax.
"""
from __future__ import annotations

import hashlib
import mmap
import struct
import zlib
from pathlib import Path

from ..core import Task

MAX_OUTPUT = 2 * 1024**3
MAX_INPUT = 2 * 1024**3
_CHUNK = 64 * 1024
_HISTORY = 8192
_MODELS = {"GR III", "GR IIIx", "GR IV"}
FORMAT_IDS = ("ricoh-gr",)


def _header(source: Path) -> tuple[bytes, str] | None:
    with source.open("rb") as stream:
        header = stream.read(128)
    if len(header) != 128 or header[:8] != b"RICOH\0\0\0":
        return None
    # GR IIIx places the company string at 0x10, directly after the model's
    # NUL terminator. The GR IV model field is wider; do not require padding.
    model_bytes, separator, _ = header[8:20].partition(b"\0")
    try:
        model = model_bytes.decode("ascii")
    except UnicodeDecodeError:
        return None
    if model not in _MODELS or not separator:
        return None
    return header, model


def identify(source: Path, task: Task) -> dict | None:
    task.check()
    result = _header(source)
    if result is None:
        return None
    header, model = result
    version = ".".join(str(component) for component in header[0x38:0x3C])
    return {
        "id": "ricoh-gr", "brand": "Ricoh", "model": model,
        "format": "ricoh-gr-update", "extractable": True,
        "summary": f"{model} compressed update container; header version {version}",
        "findings": [
            "Recognized 128-byte Ricoh GR header and documented literal/back-reference frame format.",
            "Extraction validates the complete container and decoded payload word checksums.",
        ],
        "blockers": [
            "Extraction does not establish a bootable machine or complete camera emulation.",
            "Camera-specific RTOS/SoC, display and IPC adaptation may be required.",
        ],
    }


def _word_sum(source: Path, task: Task) -> int:
    if source.stat().st_size % 4:
        raise ValueError("Ricoh container length is not aligned to 32-bit words")
    total = 0
    with source.open("rb") as stream:
        while data := stream.read(1024**2):
            task.check()
            total = (total + sum(word[0] for word in struct.iter_unpack("<I", data))) & 0xFFFFFFFF
    return total


class _Output:
    """Write incrementally, retaining only the back-reference window in memory."""

    def __init__(self, stream, task: Task):
        self.stream, self.task = stream, task
        self.window = bytearray(_HISTORY)
        self.cursor = self.size = self.total = 0
        self.remainder = b""
        self.digest = hashlib.sha256()

    def emit(self, data: bytes | bytearray):
        self.task.check()
        length = len(data)
        if self.size + length > MAX_OUTPUT:
            raise ValueError("Ricoh decoded output exceeds the 2 GiB limit")
        self.stream.write(data)
        self.digest.update(data)
        words = self.remainder + data
        aligned = len(words) & ~3
        self.total = (self.total + sum(word[0] for word in struct.iter_unpack("<I", words[:aligned]))) & 0xFFFFFFFF
        self.remainder = bytes(words[aligned:])
        if length >= _HISTORY:
            self.window[:] = data[-_HISTORY:]
            self.cursor = 0
        elif length:
            first = min(length, _HISTORY - self.cursor)
            self.window[self.cursor:self.cursor + first] = data[:first]
            self.window[:length - first] = data[first:]
            self.cursor = (self.cursor + length) % _HISTORY
        self.size += length

    def copy(self, distance: int, length: int):
        if not 0 < distance <= min(self.size, 8191):
            raise ValueError("Ricoh invalid back-reference distance")
        if self.size + length > MAX_OUTPUT:
            raise ValueError("Ricoh decoded output exceeds the 2 GiB limit")
        while length:
            self.task.check()
            start = (self.cursor - distance) % _HISTORY
            first = min(distance, _HISTORY - start)
            pattern = bytes(self.window[start:start + first]) + bytes(self.window[:distance - first])
            chunk_length = min(length, _CHUNK)
            self.emit((pattern * ((chunk_length + distance - 1) // distance))[:chunk_length])
            length -= chunk_length


def _decode(stream, output: _Output, input_size: int, task: Task) -> tuple[int, int]:
    stream.seek(128)
    frames = 0
    while True:
        task.check()
        prefix_data = stream.read(2)
        if len(prefix_data) != 2:
            raise ValueError("Ricoh frame terminator is missing or truncated")
        prefix = int.from_bytes(prefix_data, "big")
        if prefix == 0:
            break
        size = prefix & 0x7FFF
        if not size or stream.tell() + size > input_size - 4:
            raise ValueError("Ricoh frame extent exceeds the input or checksum trailer")
        frame = stream.read(size)
        if len(frame) != size:
            raise ValueError("Ricoh truncated frame")
        frames += 1
        if prefix & 0x8000:
            output.emit(frame)
            continue
        pos = 0
        while pos < size:
            if pos + 2 > size:
                raise ValueError("Ricoh truncated compression block flags")
            flags = int.from_bytes(frame[pos:pos + 2], "big")
            pos += 2
            literals = bytearray()
            for bit in range(15, -1, -1):
                if pos == size:
                    break
                if not flags & (1 << bit):
                    literals.append(frame[pos])
                    pos += 1
                    continue
                if literals:
                    output.emit(literals)
                    literals.clear()
                if pos + 2 > size:
                    raise ValueError("Ricoh truncated back reference")
                first, second = frame[pos:pos + 2]
                pos += 2
                distance = ((first & 0xF8) << 5) | second
                length = first & 7
                if length == 7:
                    while True:
                        if pos >= size:
                            raise ValueError("Ricoh truncated extended reference length")
                        extension = frame[pos]
                        pos += 1
                        length += extension
                        if extension != 255:
                            break
                if distance == 0:
                    if pos != size:
                        raise ValueError("Ricoh frame terminator precedes trailing frame data")
                    break
                output.copy(distance, length + 3)
            if literals:
                output.emit(literals)
    if not frames or not output.size:
        raise ValueError("Ricoh container has no decoded frames")
    return frames, stream.tell()


def _romfs_files(data, base: int, size: int, task: Task) -> list[tuple[str, int, int]]:
    """Validate inode/name metadata and follow directories, never host paths.

    Layout: https://docs.kernel.org/filesystems/romfs.html . Hard links, device
    nodes and symlinks are not extracted or followed.
    """
    end = base + size
    volume_end = data.find(b"\0", base + 16, min(end, base + 272))
    if volume_end < 0:
        raise ValueError("ROMFS volume name is not bounded")
    root = (volume_end - base + 16) & ~15
    entries, visited = [], set()

    def directory(offset, parent, depth):
        if depth > 32:
            raise ValueError("ROMFS directory depth limit")
        while offset:
            task.check()
            if offset in visited or len(visited) >= 10000:
                raise ValueError("ROMFS inode cycle/count limit")
            visited.add(offset)
            if offset % 16 or offset < root or offset + 32 > size:
                raise ValueError("ROMFS inode pointer is outside the filesystem")
            absolute = base + offset
            following, special, length, _ = struct.unpack_from(">IIII", data, absolute)
            name_end = data.find(b"\0", absolute + 16, min(end, absolute + 272))
            if name_end < 0:
                raise ValueError("ROMFS inode name is not bounded")
            content = base + ((name_end - base + 16) & ~15)
            metadata = data[absolute:content]
            if sum(word[0] for word in struct.iter_unpack(">I", metadata)) & 0xFFFFFFFF:
                raise ValueError("ROMFS inode metadata checksum mismatch")
            name = data[absolute + 16:name_end].decode("utf-8")
            if not name or "/" in name or "\\" in name:
                raise ValueError("ROMFS inode name is invalid")
            kind = following & 7
            if kind in (2, 3) and content + length > end:
                raise ValueError("ROMFS file exceeds the filesystem")
            if kind not in (2, 3) and length:
                raise ValueError("ROMFS special inode has file contents")
            if name not in (".", ".."):
                path = parent + "/" + name
                if kind == 1:
                    directory(special, path, depth + 1)
                elif kind == 2:
                    if special:
                        raise ValueError("ROMFS regular inode has nonzero special info")
                    entries.append((path, content, length))
            offset = following & ~15

    directory(root, "", 0)
    return entries


def _valid_component(data, start: int, size: int, kind: str, task: Task):
    header = data[start:start + min(size, 64)]
    if kind == "kernel":
        if len(header) < 48 or struct.unpack_from("<I", header, 36)[0] != 0x016F2818:
            raise ValueError("ROMFS named kernel lacks an ARM zImage header")
        first, last = struct.unpack_from("<II", header, 40)
        if not 48 <= last - first <= size:
            raise ValueError("ROMFS ARM zImage extent is invalid")
    elif kind == "dtb":
        if len(header) < 40 or header[:4] != b"\xd0\x0d\xfe\xed":
            raise ValueError("ROMFS named DTB lacks an FDT header")
        _, total, struct_off, strings_off, reserved, version, compatible, _, strings_size, struct_size = struct.unpack_from(">10I", header)
        if total != size or not 16 <= version <= 17 or compatible > version:
            raise ValueError("ROMFS FDT version/size is invalid")
        if (not 40 <= reserved <= size - 16 or reserved % 8 or struct_off % 4
                or not 40 <= struct_off <= size - struct_size
                or not 40 <= strings_off <= size - strings_size
                or not struct_size):
            raise ValueError("ROMFS FDT block extent is invalid")
    else:
        if header[:3] != b"\x1f\x8b\x08":
            raise ValueError("ROMFS initramfs is not a gzip stream")
        decoder = zlib.decompressobj(31)
        expanded, initial = 0, b""
        for offset in range(start, start + size, _CHUNK):
            pending = data[offset:min(offset + _CHUNK, start + size)]
            while pending:
                task.check()
                result = decoder.decompress(pending, _CHUNK)
                pending = decoder.unconsumed_tail
                initial = (initial + result)[:6]
                expanded += len(result)
                if expanded > MAX_OUTPUT:
                    raise ValueError("ROMFS initramfs expansion exceeds 2 GiB")
        if not decoder.eof or decoder.unused_data or initial not in (b"070701", b"070702"):
            raise ValueError("ROMFS initramfs gzip/CPIO header is invalid")


def _lz4_metadata(data: bytes, task: Task) -> bytes:
    """Decode one bounded SquashFS LZ4 metadata block, without a native library."""
    output, position = bytearray(), 0
    while position < len(data):
        task.check()
        token = data[position]
        position += 1
        literal = token >> 4
        if literal == 15:
            while True:
                if position >= len(data): raise ValueError("Truncated SquashFS LZ4 literal length")
                extra = data[position]; position += 1; literal += extra
                if extra != 255: break
        if position + literal > len(data) or len(output) + literal > 8192:
            raise ValueError("SquashFS metadata literal exceeds block bounds")
        output.extend(data[position:position + literal]); position += literal
        if position == len(data): break
        if position + 2 > len(data): raise ValueError("Truncated SquashFS LZ4 distance")
        distance = int.from_bytes(data[position:position + 2], "little")
        position += 2
        length = token & 15
        if length == 15:
            while True:
                if position >= len(data): raise ValueError("Truncated SquashFS LZ4 match length")
                extra = data[position]; position += 1; length += extra
                if extra != 255: break
        length += 4
        if not 0 < distance <= len(output) or len(output) + length > 8192:
            raise ValueError("SquashFS metadata reference exceeds block bounds")
        for _ in range(length): output.append(output[-distance])
    if not output: raise ValueError("Empty SquashFS metadata block")
    return bytes(output)


def _squashfs_extent(data, base: int, task: Task) -> dict:
    """Validate the exact SquashFS 4/LZ4 extent and its root/table metadata.

    Layout comes from Linux fs/squashfs/squashfs_fs.h in Ricoh's public GPL
    source release. This reader never extracts filesystem paths on the host.
    Complete Ricoh checksums cover every compressed data byte; this structural
    check does not claim to decompress all regular files or authenticate them.
    """
    if base + 96 > len(data): raise ValueError("Truncated SquashFS superblock")
    values = struct.unpack_from("<5I6H8Q", data, base)
    magic, inodes, _, block, fragments, compression, block_log, flags, ids, major, minor, root, used, id_table, xattr_table, inode_table, directory_table, fragment_table, export_table = values
    absent = 0xFFFFFFFFFFFFFFFF
    if (magic != 0x73717368 or (major, minor) != (4, 0) or compression != 5
            or not 1 <= inodes <= 100000 or fragments > inodes or not 1 <= ids <= 65535
            or not 12 <= block_log <= 20 or block != 1 << block_log
            or not 96 < used <= len(data) - base or flags & ~0x1FFF
            or not 96 <= inode_table < directory_table < used):
        raise ValueError("Invalid SquashFS version, codec or extent")
    if xattr_table != absent:
        raise ValueError("SquashFS xattr table is outside this bounded reader's coverage")

    def metadata(offset):
        task.check()
        if not 96 <= offset <= used - 2: raise ValueError("SquashFS metadata pointer out of bounds")
        stored = struct.unpack_from("<H", data, base + offset)[0]
        size = stored & 0x7FFF
        if not 1 <= size <= 8192 or offset + 2 + size > used:
            raise ValueError("SquashFS metadata block out of bounds")
        raw = bytes(data[base + offset + 2:base + offset + 2 + size])
        return (raw if stored & 0x8000 else _lz4_metadata(raw, task)), offset + 2 + size

    # Validate all packed inode metadata blocks and locate the root by its
    # block-reference, instead of trusting a plausible embedded superblock.
    cursor, root_data = inode_table, None
    root_block, root_offset = root >> 16, root & 0xFFFF
    while cursor < directory_table:
        task.check()
        start = cursor
        decoded, cursor = metadata(cursor)
        if cursor > directory_table: raise ValueError("SquashFS inode table overlaps directory table")
        if start == inode_table + root_block:
            if root_offset + 32 > len(decoded): raise ValueError("SquashFS root inode crosses its metadata block")
            root_data = decoded[root_offset:]
    if root_data is None: raise ValueError("SquashFS root inode does not name a metadata block")
    kind, _, uid, gid, _, number = struct.unpack_from("<4H2I", root_data)
    if kind not in (1, 8) or uid >= ids or gid >= ids or not 1 <= number <= inodes:
        raise ValueError("SquashFS root is not a bounded directory inode")
    if kind == 1:
        start, _, size, offset, parent = struct.unpack_from("<IIHHI", root_data, 16)
    else:
        if len(root_data) < 40: raise ValueError("Truncated SquashFS long root inode")
        _, size, start, parent, _, offset, _ = struct.unpack_from("<4I2HI", root_data, 16)
    directory, _ = metadata(directory_table + start)
    if size < 3 or offset >= len(directory) or not 1 <= parent <= inodes + 1:
        raise ValueError("SquashFS root directory extent is invalid")

    def indexed_table(offset, count, width):
        if not count:
            if offset != absent: raise ValueError("Unexpected SquashFS empty table")
            return
        entries = (count * width + 8191) // 8192
        if not 96 <= offset <= used - entries * 8: raise ValueError("SquashFS table index out of bounds")
        remaining, previous = count * width, 0
        for index in range(entries):
            pointer = struct.unpack_from("<Q", data, base + offset + index * 8)[0]
            if pointer <= previous or pointer >= offset: raise ValueError("SquashFS metadata index order/extent is invalid")
            decoded, end = metadata(pointer)
            expected = min(remaining, 8192)
            if len(decoded) != expected or end > offset: raise ValueError("SquashFS table metadata size is invalid")
            remaining -= expected; previous = pointer
    indexed_table(id_table, ids, 4)
    indexed_table(fragment_table, fragments, 16)
    if flags & 0x80:
        indexed_table(export_table, inodes, 8)
    elif export_table != absent:
        raise ValueError("Unexpected SquashFS export table")
    return {"size": used, "inodes": inodes, "compression": "lz4", "block_size": block,
            "root_inode": root, "verification": "superblock, complete inode metadata, root directory and indexed table bounds; regular file data not decompressed"}


def _extract_components(payload: Path, destination: Path, decoded_size: int, task: Task) -> list[dict]:
    # Components are selected by validated ROMFS inode paths, never by guessing
    # how much data follows an embedded kernel/FDT magic value.
    wanted = {"/boot/Image-nolpae": ("kernel.zImage", "kernel"),
              "/boot/sc2000.dtb": ("board.dtb", "dtb"),
              "/initramfs": ("ramdisk.cpio.gz", "ramdisk")}
    selected, names, total = [], set(), decoded_size
    with payload.open("rb") as stream, mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ) as data:
        offset = 0
        while (offset := data.find(b"-rom1fs-", offset)) >= 0:
            task.check()
            base = offset
            offset += 8
            if base + 32 > decoded_size:
                continue
            size = struct.unpack_from(">I", data, base + 8)[0]
            if size < 32 or size % 4 or base + size > decoded_size:
                continue
            first = data[base:base + min(size, 512)]
            if sum(word[0] for word in struct.iter_unpack(">I", first)) & 0xFFFFFFFF:
                continue
            try:
                files = _romfs_files(data, base, size, task)
            except (ValueError, UnicodeError):
                continue
            for path, start, length in files:
                if path not in wanted:
                    continue
                name, kind = wanted[path]
                if name in names:
                    raise ValueError("Multiple Ricoh ROMFS components claim the same boot role")
                _valid_component(data, start, length, kind, task)
                if total + length > MAX_OUTPUT:
                    raise ValueError("Ricoh components exceed the total 2 GiB output limit")
                names.add(name)
                total += length
                selected.append({"output": name, "kind": kind, "romfs_path": path,
                                 "payload_offset": start, "size": length})
        offset = 0
        while (offset := data.find(b"hsqs", offset)) >= 0:
            base = offset
            offset += 4
            try:
                filesystem = _squashfs_extent(data, base, task)
            except ValueError:
                continue
            if "rootfs.squashfs" in names:
                raise ValueError("Multiple valid Ricoh SquashFS images claim the rootfs role")
            if total + filesystem["size"] > MAX_OUTPUT:
                raise ValueError("Ricoh components exceed the total 2 GiB output limit")
            names.add("rootfs.squashfs"); total += filesystem["size"]
            selected.append({"output": "rootfs.squashfs", "kind": "rootfs",
                             "payload_offset": base, **filesystem})
        for component in selected:
            digest = hashlib.sha256()
            start, length = component["payload_offset"], component["size"]
            with (destination / component["output"]).open("xb") as writer:
                for offset in range(start, start + length, _CHUNK):
                    task.check()
                    chunk = data[offset:min(offset + _CHUNK, start + length)]
                    writer.write(chunk)
                    digest.update(chunk)
            component["sha256"] = digest.hexdigest()
    return selected


def extract(source: Path, destination: Path, task: Task) -> dict:
    task.check()
    header = _header(source)
    if header is None:
        raise ValueError("Unsupported Ricoh header/model; no automatic Pentax fallback")
    input_size = source.stat().st_size
    if input_size > MAX_INPUT:
        raise ValueError("Ricoh input exceeds the 2 GiB limit")
    if _word_sum(source, task) != 0:
        raise ValueError("Ricoh complete container word checksum mismatch")
    task.report("Decoding Ricoh GR frames / 解码理光 GR 固件帧…")
    target = destination / "payload.bin"
    with source.open("rb") as stream, target.open("xb") as sink:
        output = _Output(sink, task)
        frames, consumed = _decode(stream, output, input_size, task)
        if output.remainder or output.total:
            raise ValueError("Ricoh decoded payload word checksum mismatch")
    task.check()
    components = _extract_components(target, destination, output.size, task)
    return {
        "method": "ricoh-gr-literal-lz-frame-decoder",
        "checks": [
            "Recognized Ricoh GR model header.",
            "Complete input little-endian 32-bit word sum is zero.",
            "All frame/block/back-reference extents validated; output limited to 2 GiB.",
            "Complete decoded payload little-endian 32-bit word sum is zero.",
            "Optional boot components use ROMFS size/header/inode checksums and paths, or SquashFS 4/LZ4 root/indexed-table metadata; file extents are bounded.",
        ],
        "notes": [
            f"Decoded {frames:,} frames into {output.size:,} bytes; frame terminator ends at input offset {consumed:#x}.",
            f"payload.bin SHA-256: {output.digest.hexdigest()}",
            "Firmware bytes remain local. No decoded program is executed.",
            "Published format research credited to yeahnope/gr_unpack and radium-wang/ricoh-gr4-firmware-analysis-and-feature-expansion.",
            "Checksums establish structural integrity, not vendor authenticity or QEMU compatibility.",
        ],
        "decoded_size": output.size, "frames": frames,
        "sha256": output.digest.hexdigest(),
        "components": components,
    }
