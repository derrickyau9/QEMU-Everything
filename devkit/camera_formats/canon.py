"""Bounded Canon FIR inspection and legacy updater extraction.

SPDX-License-Identifier: GPL-2.0-or-later
The XOR tables and algorithm are adapted from Arm.Indy's fir_tool2.py,
Copyright (C) 2010 Arm.Indy, under GPL version 2 or any later version.
Source: reticulatedpines/magiclantern_simplified, commit
142472e95054d73640f50789ac0fd66325499d82, tools/indy/fir_tool2.py.
https://www.gnu.org/licenses/old-licenses/gpl-2.0.html

This extracts the updater program, NOT a complete bootable camera ROM.
It never runs the updater or attempts firmware signing/flashing.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import struct

from ..core import Task

FORMAT_IDS = ("canon-fir",)
MAX_BYTES = 2 * 1024**3
CHUNK_BYTES = 64 * 1024
HEADER_BYTES = 0x120
MODEL_NAMES = {
    0x80000169: "EOS-1D Mark III", 0x80000176: "EOS 450D",
    0x80000190: "EOS 40D", 0x80000213: "EOS 5D",
    0x80000215: "EOS-1Ds Mark III", 0x80000218: "EOS 5D Mark II",
    0x80000234: "EOS 30D", 0x80000236: "EOS 400D",
    0x80000250: "EOS 7D", 0x80000252: "EOS 500D",
    0x80000254: "EOS 1000D", 0x80000261: "EOS 50D",
    0x80000270: "EOS 550D", 0x80000281: "EOS-1D Mark IV",
    0x80000424: "EOS R", 0x80000433: "EOS RP",
}
_OLD_FORMAT_IDS = {0x80000213, 0x80000234, 0x80000236}
_VXWORKS_IDS = {0x80000169, 0x80000176, 0x80000190, 0x80000215, 0x80000254}

_XOR512 = bytes.fromhex(
    "079ed55e19b5e62b17a5c1a2bd593868ecfe2d8c1499e6b954ad85844048ce78a4a0a74bec59cd93d88ca733b0a178660a8cb62680db49c154d9880ca28af1682abc12932374114c66679381126b045279ec90d4f71eb96beb6cf9865897daf07d3dc4ea8f48752162c75ffb93f9c39183f66446a91464cbf24cab8fe9aec9e8c35dc007d4d2d5ec7e893e6566da2ab1b7d0470a3a9352495fabd72179f0f7af90605288807f31baf32d04a2e866b5686d1558941cc41668f3e22068893e08d843a2d027555851f68b4914f6e9bd37fb80ba99ad4c55c1dd89def82d722cb9378445349dbe83429a2dd778e50faca7ccc335dc567fbdc4bfa141445fad4568657f10734b89722fdad0c33f26d95e9461f82119d5f91b18f5dd2679f4f2447744cd834412ce37b925de0f122a5dd67d1f39104f7fb9751cab8b43eb3dc18ccb2b1e457d311ac98bdb65d07550ebb365fb05cec0cf5824fb1c944b6a15ef32b39b6391c261e6660acaa8e55b989552aa9fc5b09d3a98432d7d1474346bb1111964077d110b1377e44686ef2b7f5e05f1b412c3ab34ca646276a1df378fbffab35ce4598422d1268b5f8d4492d5dd610ff2a1c9029c6fac4a369919dff320bfd902ae08f7c06da8249394d440f967e25c3b37dfc8db7018c5a2555a04b64063def64c786fc7c259b68bf43531194fe25639505c650c29f222e13551e1918952f364"
)
_XOR513 = bytes.fromhex(
    "b8e40fd5ac6b385f4f75210f383b430d9fd846cab07c26716dcab448be3c96aece6388c29a63495fefb91fb8466698f395b9bfba1547563a70880a8d203c2e1a76dee8b69a656bd893f155c565a797f3c7432eeda51980b4fe3902d2def74d316110c045269d37942c19a7c71aacd8ecfa9d2e18fb8e2625ad43c0593b6e55fa271821ed365404b99b545e12319e86bcd67a54f2028b39c14aad3d3a125a903dd56f4e30e5fcaf7510b20ee28a9f462b34ea8773b739519bab6227a8f1d7e7f2e3ae9f218f8f700d4b0d7b25fcc98cf6d521c1c8f975ade7a7b3f531b1f6667bca34dacd37ab80442f1c5bd3059465c7dcc382af8fa6566228547ef8ee4978d94ba881dd3b71d236b918c92dd778fd66e785f20ffceb8b93764853f8059493ac68e23ab2e4658b477549f45f59645a163bb2b73750a3ba4bb4e6af9ac76e155188b5e0fa09b49047dd3d8690a912302f7983dff1a82dc9bcfbbd61933f18b9385cca20583b1cf3d956be5f1a3e0f18d5f1a2fd8bb027670fb84d51eb8b2c5014dcd2ad7e06fe6a4d2638379e7716bfb4a0f90a640e7fd0f4e5728272c87ab7ec8d53142da598adbe178342d4c404e7c24d2005deaae809e7451574ef7f6e3876aa5b44ccfd8212f6da33840a6a5b34e79e2210f58ca0ca9258a1d04647daf6433cf1178f50e4fd33bd4683410ed5270db2878616821ddde7e1ef295d48f4fcf21d"
)


def _u32(data: bytes, offset: int) -> int:
    if offset < 0 or offset + 4 > len(data):
        raise ValueError("Truncated Canon FIR header")
    return struct.unpack_from("<I", data, offset)[0]


def _read(stream, length: int) -> bytes:
    data = stream.read(length)
    if len(data) != length:
        raise ValueError("Truncated Canon FIR input")
    return data


def _xor(data: bytes, seed: int, offset: int = 0) -> bytes:
    first = seed & 511
    value = (seed * 0xFF803FE1) >> 32
    second = seed - ((value >> 9) + (value & 0xFFFFFE00))
    if not 0 <= second < 513:
        raise ValueError("Invalid Canon updater XOR seed")
    return bytes(value ^ _XOR512[(first + offset + index) % 512]
                 ^ _XOR513[(second + offset + index) % 513] ^ 0x37
                 for index, value in enumerate(data))


def _inspect(source: Path, task: Task) -> dict:
    task.check()
    size = source.stat().st_size
    if size < HEADER_BYTES or size > MAX_BYTES:
        raise ValueError("Canon FIR size is outside the supported 2 GiB limit")
    with source.open("rb") as stream:
        header = _read(stream, HEADER_BYTES)
        model_id = _u32(header, 0)
        if not 0x80000100 <= model_id <= 0x8000FFFF:
            raise ValueError("Not a supported Canon EOS model ID")
        if not re.fullmatch(rb"\d\.\d\.\d", header[0x10:0x15]):
            raise ValueError("Invalid Canon FIR version field")
        version = header[0x10:0x15].decode("ascii")
        if model_id in _OLD_FORMAT_IDS:
            raise ValueError("This model uses an older FIR layout; only the model/version are recognized")
        if _u32(header, 0x38) != size:
            raise ValueError("Canon FIR embedded file length mismatch")
        updater_header = _u32(header, 0x24)
        updater_start = _u32(header, 0x28)
        firmware_start = _u32(header, 0x30)
        if updater_header not in (0xB0, 0x100):
            raise ValueError("Unsupported Canon FIR generation or updater header")
        if not updater_header + 16 <= updater_start < firmware_start <= size - 12:
            raise ValueError("Canon FIR updater/main payload offsets are out of bounds")
        updater_size = _u32(header, updater_header)
        if not 16 <= updater_size <= firmware_start - updater_start:
            raise ValueError("Canon FIR updater length is out of bounds")
        for offset in (0x2C, 0x34):
            value = _u32(header, offset)
            if value != 0xFFFFFFFF and not HEADER_BYTES <= value < size:
                raise ValueError("Canon FIR additional updater offset is out of bounds")
        checksum = sum(header[:0x20])
        stream.seek(0x24)
        remaining = size - 0x24
        while remaining:
            task.check()
            chunk = _read(stream, min(CHUNK_BYTES, remaining))
            checksum += sum(chunk)
            remaining -= len(chunk)
        if _u32(header, 0x20) != ((~checksum) & 0xFFFFFFFF):
            raise ValueError("Canon FIR additive checksum mismatch")
        stream.seek(firmware_start)
        firmware_header = _read(stream, 12)
        crypto_header, encrypted_start, firmware_size = struct.unpack("<III", firmware_header)
        if firmware_size != size - firmware_start or not 12 <= crypto_header < encrypted_start < firmware_size:
            raise ValueError("Canon FIR main payload length/offsets are out of bounds")
        if updater_header == 0xB0 and _u32(header, 0x64) != firmware_size:
            raise ValueError("Canon FIR main payload embedded length mismatch")
        is_xor = updater_header == 0xB0 and not any(header[0xC0:0x120])
        seed = _u32(header, updater_header + 12)
        signature = None
        if is_xor:
            stream.seek(updater_start)
            prefix = _xor(_read(stream, 16), seed)
            signature = "DryOS" if prefix[4:12] == b"gaonisoy" else "VxWorks" if prefix[8:16] == b"Copyrigh" else None
            if signature is None:
                raise ValueError("Canon legacy updater decoding signature mismatch")
            if model_id in _VXWORKS_IDS and signature != "VxWorks":
                raise ValueError("Canon model/updater OS signature mismatch")
        return {
            "model_id": f"0x{model_id:08x}", "model": MODEL_NAMES.get(model_id), "version": version,
            "size": size, "updater_offset": updater_start, "updater_size": updater_size,
            "updater_seed": seed, "updater_os": signature, "legacy_xor": is_xor,
            "firmware_offset": firmware_start, "firmware_size": firmware_size,
            "encrypted_payload_offset": firmware_start + encrypted_start,
            "encrypted_payload_size": firmware_size - encrypted_start,
            "generation": "legacy HMAC-SHA1" if updater_header == 0xB0 else "modern signed/AES",
            "checks": ["Canon EOS model ID range", "version field", "file length", "additive checksum",
                       "updater and firmware bounds"] + (["decoded updater OS signature"] if is_xor else []),
        }


def identify(source: Path, task: Task) -> dict | None:
    source = Path(source)
    task.check()
    with source.open("rb") as stream:
        prefix = stream.read(0x15)
    if len(prefix) < 4:
        return None
    model_id = _u32(prefix, 0)
    if not 0x80000100 <= model_id <= 0x8000FFFF:
        return None
    if model_id not in MODEL_NAMES and (len(prefix) < 0x15 or not re.fullmatch(rb"\d\.\d\.\d", prefix[0x10:0x15])):
        return None
    result = {"id": "canon-fir", "brand": "Canon", "model": MODEL_NAMES.get(model_id),
              "format": "canon-fir", "extractable": False, "findings": [], "blockers": []}
    try:
        info = _inspect(source, task)
    except ValueError as error:
        result.update(summary="Canon FIR candidate; validation failed", blockers=[str(error)])
        return result
    result.update(
        summary=f"Canon {info['model'] or info['model_id']} {info['version']}: "
                + ("legacy updater can be decoded; main firmware remains encrypted" if info["legacy_xor"] else "signed/AES update; inspection only"),
        extractable=info["legacy_xor"],
        findings=info["checks"] + [f"Generation: {info['generation']}",
                 "Additive checksum is corruption detection, not cryptographic signature verification"],
        blockers=["Main firmware payload decryption is not implemented",
                  "An updater program is not the complete ROM0/ROM1 required by qemu-eos",
                  "Original camera UI boot requires a compatible custom qemu-eos machine and user-supplied ROM dump"],
        version=info["version"], model_id=info["model_id"],
    )
    if not info["legacy_xor"]:
        result["blockers"].insert(0, "Modern updater AES decryption needs a validated model-specific ROM oracle")
    elif model_id == 0x80000261 and info["version"] == "1.0.9":
        result["blockers"][0] = ("Main firmware needs a matching user-owned ROM1 seed from 0xF87C0000; "
                                 "the optional canon-lab decrypt command validates it before writing")
    return result


def extract(source: Path, destination: Path, task: Task) -> dict:
    source, destination = Path(source), Path(destination)
    if not destination.is_dir() or any(destination.iterdir()):
        raise ValueError("Extraction destination must be an empty directory")
    info = _inspect(source, task)
    if not info["legacy_xor"]:
        raise ValueError("Only verified legacy XOR updater extraction is supported")
    artifacts = []
    produced = []
    try:
        with source.open("rb") as stream:
            for name, start, size, decoded in (
                ("updater1.bin", info["updater_offset"], info["updater_size"], True),
                ("main-payload.encrypted.bin", info["encrypted_payload_offset"], info["encrypted_payload_size"], False),
            ):
                task.check()
                path = destination / name
                digest = hashlib.sha256()
                stream.seek(start)
                with path.open("xb") as target:
                    produced.append(path)
                    remaining = size
                    offset = 0
                    while remaining:
                        task.check()
                        chunk = _read(stream, min(CHUNK_BYTES, remaining))
                        if decoded:
                            chunk = _xor(chunk, info["updater_seed"], offset)
                        target.write(chunk)
                        digest.update(chunk)
                        remaining -= len(chunk)
                        offset += len(chunk)
                artifacts.append({"path": name, "size": size, "sha256": digest.hexdigest(),
                                  "role": "updater-program" if decoded else "encrypted-main-payload"})
        metadata = {key: value for key, value in info.items() if key != "updater_seed"}
        metadata["artifacts"] = artifacts
        path = destination / "canon-metadata.json"
        task.check()
        with path.open("x", encoding="utf-8") as target:
            produced.append(path)
            json.dump(metadata, target, indent=2)
            target.write("\n")
    except BaseException:
        for path in produced:
            path.unlink(missing_ok=True)
        raise
    return {"method": "Canon legacy XOR updater-only extraction", "checks": info["checks"],
            "notes": ["Decoded updater only; the main firmware payload remains encrypted",
                      "No complete ROM or cryptographic signature verification was produced",
                      "No vendor executable was run", "Extraction is not evidence of original camera UI boot"],
            "model": info["model"], "version": info["version"], "artifacts": artifacts}
