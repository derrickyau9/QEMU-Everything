"""Bounded read-only Sony UFU / FDAT decryption and payload extraction.

Protocol and public-key research: ma1co/fwtool.py, David Buchanan PR #52,
commit 72fbdd15ddcc2d2a24ccb52cf8e84b8de98c738a (MIT).
This implementation uses cryptography, validates every block and never runs an updater.
"""
from __future__ import annotations

import json
from pathlib import Path
import struct
import tarfile
import zlib

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from ..core import Task, checked_child
from ._sony_vendor import constants

MAGIC = b"\x89UFU\r\n\x1a\n"
FORMAT_IDS = ("sony-ufu",)
MAX_BYTES = 4 * 1024**3
CHUNK_SIZE = 1024 * 1024
HEADER_SIZE = 512
MODEL_NAMES = {0x01030081: "ILCE-7M4", 0x11030080: "ILCE-7RM5", 0x20030014: "ILCE-7CM2"}
KEYS = (("CXD90057_k8", constants.key_cxd90057_k8),
        ("CXD90057_k0", constants.key_cxd90057_k0),
        ("CXD90045", constants.key_cxd90045))


def _read(stream, size):
    data = stream.read(size)
    if len(data) != size:
        raise ValueError("Truncated Sony container")
    return data


def _outer(source: Path, task: Task):
    size = source.stat().st_size
    if size > MAX_BYTES or size < 32:
        raise ValueError("Sony container size is outside the supported limit")
    chunks, seen = {}, set()
    with source.open("rb") as stream:
        if _read(stream, 8) != MAGIC:
            raise ValueError("Invalid Sony UFU magic")
        while stream.tell() < size:
            task.check()
            if len(chunks) >= 32:
                raise ValueError("Too many Sony container chunks")
            length, name = struct.unpack(">I4s", _read(stream, 8))
            offset = stream.tell()
            if name in seen or length > MAX_BYTES or offset + length > size:
                raise ValueError("Invalid Sony chunk bounds or duplicate type")
            seen.add(name)
            chunks[name] = (offset, length)
            stream.seek(length, 1)
            if name == b"DEND":
                if length != 4 or stream.tell() != size:
                    raise ValueError("Invalid Sony DEND trailer")
                end_start = offset - 8
                break
        else:
            raise ValueError("Missing Sony DEND trailer")
        for required in (b"DATV", b"PROV", b"UDID", b"FDAT"):
            if required not in chunks:
                raise ValueError(f"Missing Sony {required.decode()} chunk")
        for name in (b"DATV", b"PROV"):
            offset, length = chunks[name]
            if length != 4:
                raise ValueError("Invalid Sony protocol/version chunk length")
            stream.seek(offset)
            version, flag = struct.unpack(">HH", _read(stream, 4))
            if version != 0x100 or (name == b"DATV" and flag not in (0, 1)):
                raise ValueError("Unsupported Sony DAT protocol or lens flag")
        stream.seek(chunks[b"DEND"][0])
        expected = struct.unpack(">I", _read(stream, 4))[0]
        stream.seek(0)
        actual, remaining = 0, end_start
        while remaining:
            task.check()
            data = _read(stream, min(remaining, CHUNK_SIZE))
            actual = zlib.crc32(data, actual)
            remaining -= len(data)
        if actual != expected:
            raise ValueError("Sony UFU CRC32 mismatch")
    return chunks[b"FDAT"]


def _valid_block(block, last):
    if len(block) != 1024:
        raise ValueError("Invalid Sony encrypted block size")
    checksum, word = struct.unpack_from("<HH", block)
    length = word & 0x7FFF
    if sum(struct.unpack("<511H", block[2:])) & 0xFFFF != checksum:
        raise ValueError("Sony FDAT block checksum mismatch")
    if length > 1020 or bool(word & 0x8000) != last:
        raise ValueError("Sony FDAT block length/end flag mismatch")
    return block[4:4 + length]


def _header(logical):
    if len(logical) < HEADER_SIZE or logical[:8] != b"UDTRFIRM" or logical[12:16] != b"0100":
        raise ValueError("Invalid decrypted Sony FDAT header")
    if zlib.crc32(logical[12:HEADER_SIZE]) != struct.unpack_from("<I", logical, 8)[0]:
        raise ValueError("Sony FDAT header CRC32 mismatch")
    if logical[16:17] != b"U" or logical[20:21] != b"N":
        raise ValueError("Unsupported Sony update mode or LUW flag")
    model, region = struct.unpack_from("<II", logical, 36)
    offset, size, count = struct.unpack_from("<III", logical, 48)
    if count > 28:
        raise ValueError("Sony FDAT filesystem count exceeds header capacity")
    parts = [{"path": "firmware.tar", "offset": offset, "size": size}]
    for index in range(count):
        position = 64 + index * 16
        mode = logical[position:position + 1]
        fs_offset, fs_size = struct.unpack_from("<II", logical, position + 4)
        if mode == b"U" and fs_size:
            parts.append({"path": f"filesystem-{index:02d}.img", "offset": fs_offset, "size": fs_size})
    for part in parts:
        if part["size"] < 0 or part["offset"] < HEADER_SIZE or part["size"] > MAX_BYTES:
            raise ValueError("Invalid Sony FDAT payload bounds")
    return {"model_id": f"0x{model:08x}", "model": MODEL_NAMES.get(model), "region": region,
            "version": f"{logical[33]:x}.{logical[32]:02x}", "parts": parts}


def _probe(source: Path, offset: int, size: int, task: Task):
    task.check()
    if size <= 0x110 or (size - 0x110) % 1024:
        raise ValueError("Sony FDAT geometry is unsupported (newer XR2/unknown encryption)")
    encrypted_size = size - 0x110
    with source.open("rb") as stream:
        stream.seek(offset)
        first = _read(stream, 1024)
        stream.seek(offset + encrypted_size)
        iv = _read(stream, 16)
    common = Cipher(algorithms.AES(constants.key_aes), modes.ECB()).decryptor().update(first[:512])
    for name, key in KEYS:
        task.check()
        tail = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor().update(first[512:])
        try:
            logical = _valid_block(common + tail, encrypted_size == 1024)
            info = _header(logical)
        except ValueError:
            continue
        return name, key, iv, encrypted_size, info
    raise ValueError("No verified public Sony FDAT decryption key matched this image")


def identify(source: Path, task: Task) -> dict | None:
    source = Path(source)
    task.check()
    with source.open("rb") as stream:
        if stream.read(8) != MAGIC:
            return None
    try:
        offset, size = _outer(source, task)
        method, _, _, _, info = _probe(source, offset, size, task)
    except ValueError as error:
        return {"id": "sony-ufu", "brand": "Sony", "model": None, "format": "sony-dat",
                "summary": "Sony UFU container detected; verified decryption unavailable", "extractable": False,
                "findings": ["Sony UFU magic"], "blockers": [str(error)]}
    return {"id": "sony-ufu", "brand": "Sony", "model": info["model"], "format": "sony-dat",
            "summary": f"Sony {info['model'] or info['model_id']} {info['version']}: {method}",
            "extractable": True, "findings": ["UFU CRC32", "FDAT first-block checksum", "UDTRFIRM header", "FDAT header CRC32", method],
            "blockers": ["Sony CXD900xx board peripherals and camera services are not provided by QEMU virt", "Original camera UI boot has not been verified"]}


def _copy(stream, target, length, task):
    with target.open("xb") as output:
        while length:
            task.check()
            chunk = _read(stream, min(length, CHUNK_SIZE))
            output.write(chunk)
            length -= len(chunk)


def _unpack_tar(path: Path, destination: Path, remaining: int, task: Task):
    """Extract only regular payload blobs with checked relative paths, never links/devices."""
    try:
        archive = tarfile.open(path, "r:")
    except tarfile.ReadError:
        return 0, 0, "Firmware payload is not an uncompressed tar; preserved as a blob"
    total, skipped, seen, count = 0, 0, set(), 0
    with archive:
        for member in archive:
            task.check()
            count += 1
            if count > 10000:
                raise ValueError("Sony firmware tar has too many entries")
            name = member.name
            while name.startswith("./"):
                name = name[2:]
            if name in ("", ".") and member.isdir():
                continue
            target = checked_child(destination, name.rstrip("/") if member.isdir() else name)
            if not member.isfile():
                skipped += 1
                continue
            if member.size < 0 or member.size > remaining - total:
                raise ValueError("Sony extraction exceeds the 4 GiB output limit")
            key = str(target.relative_to(destination)).casefold()
            if key in seen or member.issparse():
                raise ValueError("Duplicate or sparse Sony archive member")
            seen.add(key)
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
            except (FileExistsError, NotADirectoryError) as error:
                raise ValueError("Sony archive file/directory path collision") from error
            payload = archive.extractfile(member)
            if payload is None:
                raise ValueError("Missing Sony tar payload")
            with payload:
                _copy(payload, target, member.size, task)
            total += member.size
    return total, skipped, "Only regular archive members were extracted; links and device nodes were skipped"


def extract(source: Path, destination: Path, task: Task) -> dict:
    source, destination = Path(source), Path(destination).resolve()
    if not destination.is_dir() or any(destination.iterdir()):
        raise ValueError("Extraction destination must be an empty directory")
    offset, size = _outer(source, task)
    method, key, iv, encrypted_size, info = _probe(source, offset, size, task)
    task.report(f"Sony FDAT: {method}, verifying all encrypted blocks")
    decoder = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    common_decoder = Cipher(algorithms.AES(constants.key_aes), modes.ECB()).decryptor()
    logical_size = 0
    fdat_path = destination / "firmware.fdat"
    with source.open("rb") as stream, fdat_path.open("xb") as output:
        stream.seek(offset)
        encrypted = _read(stream, 1024)
        plain = common_decoder.update(encrypted[:512]) + decoder.update(encrypted[512:])
        block = _valid_block(plain, encrypted_size == 1024)
        output.write(block)
        logical_size += len(block)
        consumed = 1024
        while consumed < encrypted_size:
            task.check()
            encrypted = _read(stream, min(CHUNK_SIZE, encrypted_size - consumed))
            plain = decoder.update(encrypted)
            for position in range(0, len(plain), 1024):
                consumed += 1024
                block = _valid_block(plain[position:position + 1024], consumed == encrypted_size)
                output.write(block)
                logical_size += len(block)
        decoder.finalize()
        common_decoder.finalize()
    written = logical_size
    # Reserve space for bounded metadata, in addition to the actual firmware blobs.
    remaining_budget = MAX_BYTES - 65536
    with fdat_path.open("rb") as stream:
        for part in info["parts"]:
            task.check()
            if part["offset"] + part["size"] > logical_size or part["size"] > remaining_budget - written:
                raise ValueError("Sony payload bounds or 4 GiB output limit exceeded")
            stream.seek(part["offset"])
            _copy(stream, destination / part["path"], part["size"], task)
            written += part["size"]
    tar_path = destination / "firmware.tar"
    extracted, skipped, note = _unpack_tar(tar_path, destination / "payloads", remaining_budget - written, task)
    info.update({"crypter": method, "logical_bytes": logical_size, "extracted_payload_bytes": extracted, "skipped_nonregular_members": skipped})
    (destination / "sony-metadata.json").write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")
    return {"method": f"Sony UFU / {method} AES-ECB header + AES-CBC stream decryption",
            "checks": ["UFU CRC32", "all FDAT block checksums", "all FDAT block lengths/end flags", "FDAT header CRC32", "payload bounds", "4 GiB output limit", "archive paths and regular file types"],
            "notes": [note, "No vendor updater was executed", "Decryption does not remove device-side signature or boot checks", "Generic QEMU virt is not a Sony board emulator"], "version": info["version"], "model": info["model"]}
