"""Local Nikon legacy-container decryption using a user-supplied table file.

Format and key-recovery research: WonderLantern/nikon-fw-key-tool,
commit 33426e892afd329a3b366b73776bcf53b9ac4ee8 (GPL-3.0).
This parser is independently implemented from the documented format. It ships
no firmware, XOR tables, model digest keys, flashing or repacking functions.
"""
from __future__ import annotations

import binascii
import hashlib
import json
import re
import struct
from pathlib import Path

from ..core import Task

FORMAT_IDS = ("nikon-update",)
MAX_INPUT = 1024**3
MAX_OUTPUT = 2 * 1024**3
_CHUNK = 1024**2
_HEADER = 32
_PERIOD = 1 << 24
_NAME = re.compile(rb"[0-9A-Za-z_][0-9A-Za-z_.]{2,15}\Z")
_FILENAME = re.compile(r"^(Z_(?:[0-9]+|fc)|Z[67]_2|D[0-9]+)_[0-9]+\.bin$", re.IGNORECASE)


def identify(source: Path, task: Task) -> dict | None:
    task.check()
    with source.open("rb") as stream:
        head = stream.read(0x280)
    # These are format fingerprints, not proof of a model or authenticity.
    legacy = len(head) >= 128 and head[0x20:0x40] == b" " * 32
    newer = len(head) >= 0x280 and head[0x260:0x280] == b" " * 32
    if not (legacy or newer):
        return None
    match = _FILENAME.fullmatch(source.name)
    model = match.group(1).replace("_", " ") if match else "Unverified Nikon model"
    return {
        "id": "nikon-update", "brand": "Nikon", "model": model,
        "format": "nikon-legacy-update" if legacy else "nikon-new-update",
        "summary": "Nikon legacy XOR update candidate" if legacy else "Nikon newer encrypted update candidate",
        "extractable": legacy, "key_required": legacy,
        "findings": [
            f"32-byte plaintext space label at file offset {'0x20' if legacy else '0x260'}.",
            "A label alone is a format lead; module structure and CRC checks must confirm it.",
        ],
        "blockers": [
            "A separately recovered local XOR-table JSON is required; no keys are bundled." if legacy else
            "The newer encryption scheme is not implemented by this decoder.",
            "Decoded RTOS and controller modules need the exact camera-specific QEMU machine.",
        ],
    }


def _load_tables(key_file: Path | None) -> tuple[bytes, bytes, bytes]:
    if key_file is None:
        raise ValueError("Nikon requires a local recovered XOR-table JSON; supply --key-file")
    try:
        if key_file.stat().st_size > 64 * 1024:
            raise ValueError("Nikon key JSON exceeds the 64 KiB limit")
        document = json.loads(key_file.read_text(encoding="utf-8"))
        if not isinstance(document, dict):
            raise ValueError("Nikon key JSON must be an object")
        if document.get("body_offset") != _HEADER or document.get("period_bytes") != _PERIOD:
            raise ValueError("Nikon key JSON has incompatible body offset or period")
        checks = document.get("self_check")
        if not isinstance(checks, dict):
            raise ValueError("Nikon key JSON lacks table hash checks")
        tables = []
        for name in ("T1", "T2", "T3"):
            value = document.get(name)
            if not isinstance(value, str):
                raise ValueError(f"Nikon key JSON lacks the {name} table")
            table = bytes.fromhex(value)
            if len(table) != 256:
                raise ValueError(f"Nikon {name} must contain exactly 256 bytes")
            if checks.get(name) != hashlib.sha256(table).hexdigest():
                raise ValueError(f"Nikon {name} table hash mismatch")
            tables.append(table)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot read Nikon key JSON: {type(exc).__name__}") from exc
    return tuple(tables)


def _keystream(tables: tuple[bytes, bytes, bytes], task: Task) -> bytes:
    first, second, third = tables
    # Only 256 distinct 256-byte sectors exist. Build their repeating 16 MiB
    # stream once; large-integer XOR then avoids one Python operation per byte.
    sectors = [bytes(value ^ constant for value in first) for constant in range(256)]
    parts = []
    for high in range(256):
        task.check()
        parts.extend(sectors[second[middle] ^ third[high]] for middle in range(256))
    return b"".join(parts)


def _decrypt(source: Path, target: Path, tables: tuple[bytes, bytes, bytes], task: Task):
    stream_key = _keystream(tables, task)
    with source.open("rb") as reader, target.open("xb") as writer:
        writer.write(reader.read(_HEADER))
        offset = 0
        while data := reader.read(_CHUNK):
            task.check()
            start = offset % _PERIOD
            mask = stream_key[start:start + len(data)]
            if len(mask) != len(data):
                mask += stream_key[:len(data) - len(mask)]
            plain = (int.from_bytes(data, "little") ^ int.from_bytes(mask, "little")).to_bytes(len(data), "little")
            if offset == 0:
                plain = data[:32] + plain[32:]  # The body's space label is plaintext.
            writer.write(plain)
            offset += len(data)


def _read(stream, count: int) -> bytes:
    data = stream.read(count)
    if len(data) != count:
        raise ValueError("Nikon decrypted input is truncated")
    return data


def _crc(stream, start: int, size: int, task: Task) -> int:
    stream.seek(start)
    total = 0
    while size:
        task.check()
        data = _read(stream, min(size, _CHUNK))
        total = binascii.crc_hqx(data, total)
        size -= len(data)
    return total


def _validate(plaintext: Path, task: Task) -> list[dict]:
    body_size = plaintext.stat().st_size - _HEADER
    if body_size < 96:
        raise ValueError("Nikon decrypted body is too short")
    with plaintext.open("rb") as stream:
        stream.seek(_HEADER)
        if _read(stream, 32) != b" " * 32:
            raise ValueError("Nikon plaintext space label is invalid")
        count, directory_size = struct.unpack(">II", _read(stream, 8))
        if not 1 <= count <= 64 or directory_size != 48 + 32 * count:
            raise ValueError("Nikon directory size/module count is invalid; check the key")
        if directory_size + 2 * count > body_size - 16:
            raise ValueError("Nikon directory exceeds the checksum trailer")
        if _read(stream, 8) != bytes(8):
            raise ValueError("Nikon directory reserved bytes are nonzero")
        modules = []
        expected = directory_size
        for index in range(count):
            task.check()
            entry = _read(stream, 32)
            raw_name = entry[:16].rstrip(b"\0")
            if not _NAME.fullmatch(raw_name):
                raise ValueError("Nikon module name/NUL padding is invalid")
            start, length = struct.unpack_from(">II", entry, 16)
            if entry[24:] != bytes(8):
                raise ValueError("Nikon module reserved bytes are nonzero")
            if start != expected or length < 2 or start + length > body_size - 16:
                raise ValueError("Nikon module extents do not form a bounded contiguous chain")
            name = raw_name.decode("ascii")
            modules.append({"name": name, "body_offset": start, "stored_size": length,
                            "payload_size": length - 2, "output": f"module-{index + 1:03d}.bin"})
            expected = start + length
        if expected != body_size - 16:
            raise ValueError("Nikon module chain does not end at the checksum trailer")
        stream.seek(_HEADER + body_size - 16)
        trailer = _read(stream, 16)
        if trailer[2:] != bytes(14):
            raise ValueError("Nikon trailer padding is nonzero")
        stored_package_crc = int.from_bytes(trailer[:2], "big")
        for module in modules:
            start = _HEADER + module["body_offset"]
            stream.seek(start + module["payload_size"])
            stored = int.from_bytes(_read(stream, 2), "big")
            if _crc(stream, start, module["payload_size"], task) != stored:
                raise ValueError(f"Nikon module CRC mismatch: {module['name']}")
        if _crc(stream, _HEADER, body_size - 16, task) != stored_package_crc:
            raise ValueError("Nikon complete package CRC mismatch")
    return modules


def extract(source: Path, destination: Path, task: Task, key_file: Path | None = None) -> dict:
    task.check()
    tables = _load_tables(key_file)
    size = source.stat().st_size
    if not 128 <= size <= MAX_INPUT:
        raise ValueError("Nikon input size is outside the supported 128-byte to 1 GiB range")
    with source.open("rb") as stream:
        header = stream.read(0x280)
    if header[0x20:0x40] != b" " * 32:
        if header[0x260:0x280] == b" " * 32:
            raise ValueError("Nikon newer encryption at 0x260 is unsupported; legacy keys cannot decode it")
        raise ValueError("Nikon legacy plaintext label at 0x20 is missing")
    task.report("Decrypting local Nikon update / 解密本地尼康升级包…")
    plaintext = destination / "decrypted.bin"
    created = []
    try:
        # Register paths before creation so an exception during writing is cleaned.
        if plaintext.exists():
            raise FileExistsError(plaintext)
        created.append(plaintext)
        _decrypt(source, plaintext, tables, task)
        modules = _validate(plaintext, task)
        if size + sum(module["payload_size"] for module in modules) > MAX_OUTPUT:
            raise ValueError("Nikon extracted output exceeds the total 2 GiB limit")
        with plaintext.open("rb") as reader:
            for module in modules:
                task.check()
                target = destination / module["output"]
                if target.exists():
                    raise FileExistsError(target)
                created.append(target)
                reader.seek(_HEADER + module["body_offset"])
                remaining = module["payload_size"]
                digest = hashlib.sha256()
                with target.open("xb") as writer:
                    while remaining:
                        task.check()
                        data = _read(reader, min(remaining, _CHUNK))
                        writer.write(data)
                        digest.update(data)
                        remaining -= len(data)
                module["sha256"] = digest.hexdigest()
        task.check()
    except BaseException:
        for target in reversed(created):
            target.unlink(missing_ok=True)
        raise
    return {
        "method": "nikon-legacy-three-table-xor",
        "checks": [
            "User-supplied table sizes and SHA-256 self checks verified.",
            "Directory count, names, reserved fields and contiguous module extents verified.",
            f"All {len(modules)} module CRC16 checks and the complete package CRC16 passed.",
            "Output capped at 2 GiB; extracted modules exclude their trailing two CRC bytes.",
        ],
        "notes": [
            "CRC16: polynomial 0x1021, initial value zero, no reflection/final XOR.",
            "The keyed SHA-1 header digest is preserved but not authenticated; structural CRCs are not authenticity checks.",
            "Original firmware, recovered tables and extracted code remain local; no decoded program is executed.",
            "Format credit: WonderLantern/nikon-fw-key-tool, GPL-3.0, commit 33426e892afd329a3b366b73776bcf53b9ac4ee8.",
            "A decoded main module needs the exact camera/version-specific QEMU machine; generic ARM virt is insufficient for its RTOS UI.",
        ],
        "modules": modules,
    }
