"""Read-only Insta360 X3 container validation and extraction.

Independently implemented from the documented Ambarella segment format.
Research credit: nneonneo/Insta360-X3-Firmware-Tools (3e95fa30f8ad10f374c98b06a950f05eeb294aaf)
and enekochan/insta360-go-firmware-tool. No upstream implementation is copied.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import struct
import zlib

from ..core import Task

MAX_BYTES = 4 * 1024**3
FORMAT_IDS = ("insta360-x3", "insta360-opaque")
HEADER_SIZE = 0x230
SEGMENT_HEADER_SIZE = 0x100
TRAILER_SIZE = 116
CHUNK_SIZE = 1024 * 1024
ARTIFACT_NAMES = ("rtos.bin", "dsp-romfs.bin", "resources-romfs.bin", "kernel.bin", "rootfs.squashfs", "board.dtb")


def _read(stream, size):
    value = stream.read(size)
    if len(value) != size:
        raise ValueError("Truncated Insta360 container")
    return value


def _inspect(source: Path, task: Task, destination: Path | None = None) -> dict:
    size = source.stat().st_size
    if size < HEADER_SIZE + TRAILER_SIZE or size > MAX_BYTES:
        raise ValueError("Insta360 container size is outside the supported limit")
    with source.open("rb") as stream:
        stream.seek(size - TRAILER_SIZE)
        trailer = _read(stream, TRAILER_SIZE)
        body_size = struct.unpack_from("<I", trailer)[0]
        product = trailer[4:36].rstrip(b"\0")
        version = trailer[36:68].rstrip(b"\0").decode("ascii", errors="strict")
        body_md5, hardware = trailer[68:84], trailer[84:92]
        revision = struct.unpack_from("<Q", trailer, 92)[0]
        if (product, hardware, revision) != (b"onex3", b"WFNI3XNO", 1):
            raise ValueError("Unsupported Insta360 model or hardware revision; this decoder supports X3 only")
        if body_size != size - TRAILER_SIZE:
            raise ValueError("Insta360 trailer size mismatch")
        stream.seek(0)
        digest = hashlib.md5()
        remaining = body_size
        while remaining:
            task.check()
            chunk = _read(stream, min(remaining, CHUNK_SIZE))
            digest.update(chunk)
            remaining -= len(chunk)
        if digest.digest() != body_md5:
            raise ValueError("Insta360 body MD5 mismatch")
        digest.update(trailer[:100])
        if digest.digest() != trailer[100:]:
            raise ValueError("Insta360 final MD5 mismatch")
        stream.seek(0)
        header = _read(stream, HEADER_SIZE)
        magic, expected_crc = struct.unpack_from("<II", header, 32)
        if magic != 0x8732DFE6 or any(header[:32]) or any(header[40:48]):
            raise ValueError("Invalid Insta360 header")
        descriptors = [struct.unpack_from("<II", header, 48 + i * 8) for i in range(16)]
        if any(pair != (0, 0) for pair in descriptors[6:]) or any(pair == (0, 0) for pair in descriptors[:6]):
            raise ValueError("Unsupported Insta360 segment table")
        running_crc = 0
        artifacts = []
        for index, (segment_size, expected_running) in enumerate(descriptors[:6]):
            task.check()
            offset = stream.tell()
            if offset + SEGMENT_HEADER_SIZE > body_size:
                raise ValueError("Insta360 segment header exceeds body")
            segment_header = _read(stream, SEGMENT_HEADER_SIZE)
            expected_data_crc, seg_version, date, data_size, address, flags, seg_magic = struct.unpack_from("<7I", segment_header)
            if seg_version != 0x01000000 or seg_magic != 0xA324EB90 or any(segment_header[28:]):
                raise ValueError("Invalid Insta360 segment header")
            if segment_size != data_size + SEGMENT_HEADER_SIZE and not (index == 5 and segment_size == 0):
                raise ValueError("Insta360 segment table size mismatch")
            if data_size <= 0 or data_size > MAX_BYTES or stream.tell() + data_size > body_size:
                raise ValueError("Insta360 segment length exceeds body")
            running_crc = zlib.crc32(segment_header, running_crc)
            data_crc, remaining = 0, data_size
            target = (destination / ARTIFACT_NAMES[index]).open("xb") if destination else None
            try:
                while remaining:
                    task.check()
                    chunk = _read(stream, min(remaining, CHUNK_SIZE))
                    running_crc = zlib.crc32(chunk, running_crc)
                    data_crc = zlib.crc32(chunk, data_crc)
                    if target:
                        target.write(chunk)
                    remaining -= len(chunk)
            finally:
                if target:
                    target.close()
            if data_crc != expected_data_crc or running_crc ^ 0xFFFFFFFF != expected_running:
                raise ValueError(f"Insta360 segment {index} CRC32 mismatch")
            artifacts.append({"path": ARTIFACT_NAMES[index], "size": data_size,
                              "load_address": address, "flags": flags, "date": date})
        if stream.tell() != body_size or running_crc != expected_crc:
            raise ValueError("Insta360 body length or CRC32 mismatch")
    return {"version": version, "artifacts": artifacts,
            "checks": ["body MD5", "final MD5", "body CRC32", "six segment CRC32 values", "six cumulative CRC32 values", "model and hardware revision", "all segment bounds"]}


def identify(source: Path, task: Task) -> dict | None:
    source = Path(source)
    task.check()
    with source.open("rb") as stream:
        head = stream.read(48)
    if len(head) < 40 or struct.unpack_from("<I", head, 32)[0] != 0x8732DFE6:
        if source.name.lower().endswith(".enc") and ("insta" in source.name.lower() or "luna" in source.name.lower()):
            return {"id": "insta360-opaque", "brand": "Insta360", "model": None,
                    "format": "opaque-encrypted", "summary": "Encrypted Insta360 input; no verified public decoder for this file",
                    "extractable": False, "findings": [], "blockers": ["No validated decryption algorithm/key for this model", "Filename alone is not proof of the encryption format"]}
        return None
    try:
        info = _inspect(source, task)
    except (ValueError, UnicodeError) as error:
        return {"id": "insta360-x3", "brand": "Insta360", "model": None, "format": "ambarella-segments",
                "summary": "Ambarella-style container detected; validation failed", "extractable": False,
                "findings": [], "blockers": [str(error)]}
    return {"id": "insta360-x3", "brand": "Insta360", "model": "X3", "format": "ambarella-segments",
            "summary": f"Insta360 X3 {info['version']}: six verified plaintext firmware segments",
            "extractable": True, "findings": info["checks"] + ["ARM64 Linux plus a separate RTOS payload"],
            "blockers": ["Ambarella peripherals and RTOS/Linux shared-memory services are not emulated by generic QEMU virt", "Full RTOS boot is unverified; exact v1.1.4.1 original GUI lab requires its separate platform adapter"]}


def extract(source: Path, destination: Path, task: Task) -> dict:
    destination = Path(destination).resolve()
    if not destination.is_dir() or any(destination.iterdir()):
        raise ValueError("Extraction destination must be an empty directory")
    info = _inspect(Path(source), task, destination)
    (destination / "insta360-metadata.json").write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")
    return {"method": "validated Insta360 X3 plaintext segment extraction", "checks": info["checks"],
            "notes": ["This format requires no decryption", "No vendor executable was run", "Kernel/rootfs/DTB extraction is not evidence of original UI boot"], "version": info["version"]}
