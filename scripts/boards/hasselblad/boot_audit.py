"""Offline X2D II main-boot prerequisites; never substitutes a recovery kernel.

Uses an existing dji-firmware-tools checkout for its published crypto parameters.
No keys or decrypted firmware are written to the report.
"""
import argparse
import array
import ctypes
import hashlib
import importlib.util
import json
from pathlib import Path
import struct
import sys
from types import SimpleNamespace


def checksum(data):
    words = array.array("I")
    words.frombytes(data + b"\0" * (-len(data) % 4))
    if sys.byteorder != "little":
        words.byteswap()
    return sum(words) & 0xffffffff


def audit_image(path, tool):
    data = path.read_bytes()
    head = tool.ImgPkgHeader.from_buffer_copy(data)
    if head.magic != b"IM*H" or head.header_version != 2:
        raise ValueError("Expected IMaH v2")
    hs, cs = ctypes.sizeof(head), ctypes.sizeof(tool.ImgChunkHeader)
    if head.header_size != hs + head.chunk_num * cs or head.chunk_num > 256:
        raise ValueError("Invalid chunk table")
    base = head.header_size + head.signature_size
    if base + head.payload_size != len(data) or head.size != len(data):
        raise ValueError("Container size mismatch")
    chunks = [tool.ImgChunkHeader.from_buffer_copy(data, hs + i*cs)
              for i in range(head.chunk_num)]
    header = bytearray(data[:head.header_size])
    header[108:112] = bytes(4)
    encrypted_valid = (-(checksum(header) + checksum(data[base:]))) & 0xffffffff
    out = {
        "path": str(path.resolve()), "sha256": hashlib.sha256(data).hexdigest(),
        "format": "IMaH v2", "key_id": head.enc_key.decode(),
        "encrypted_checksum_matches": encrypted_valid == head.encr_cksum,
        "signature_authenticated": False,
        "chunks": [{"id": c.id.decode(), "size": c.size, "address": hex(c.address),
                    "encrypted": not bool(c.attrib & 1)} for c in chunks],
        "published_key_checks": [],
    }
    if not out["encrypted_checksum_matches"]:
        raise ValueError("Encrypted container checksum failed")
    for name in tool.keys:
        if not name.startswith(head.enc_key.decode()):
            continue
        options = SimpleNamespace(key_select=[name], verbose=0, sigfile=str(path),
                                  show_multiple_keys_warn=False)
        key, mode, iv = tool.imah_get_crypto_params(options, head)
        if mode != tool.AES.MODE_CTR:
            raise ValueError("Expected IMaH v2 CTR")
        counter = tool.Counter.new(32, iv[:12], initial_value=int.from_bytes(iv[12:], "big"))
        cipher = tool.AES.new(key, mode, counter=counter)
        total = 0
        markers = {}
        for chunk in chunks:
            encrypted = not bool(chunk.attrib & 1)
            size = (chunk.size + 31) & ~31 if encrypted else chunk.size
            if chunk.offset + size > head.payload_size:
                raise ValueError("Chunk outside payload")
            raw = data[base+chunk.offset:base+chunk.offset+size]
            plain = cipher.decrypt(raw) if encrypted else raw
            total = (total + checksum(plain)) & 0xffffffff
            ident = chunk.id.decode()
            if ident == "KERN":
                markers["arm64_image_header"] = plain[56:60] == b"ARM\x64"
                markers["kernel_compression_magic"] = (
                    plain.startswith(b"\x1f\x8b") or plain.startswith(b"\x04\x22\x4d\x18"))
            if ident == "LDTB" or ident.startswith("DTB"):
                markers[ident + "_fdt_header"] = plain[:4] == b"\xd0\x0d\xfe\xed"
        out["published_key_checks"].append({
            "key_name": name, "plaintext_checksum_matches": total == head.plain_cksum,
            **markers,
        })
    out["verified_main_kernel_candidate"] = any(
        x["plaintext_checksum_matches"] and x.get("arm64_image_header") and x.get("LDTB_fdt_header")
        for x in out["published_key_checks"])
    return out


def hardware_inventory(path):
    data = path.read_bytes()
    magic, total, os, ot, _, _, _, _, ts, ss = struct.unpack_from(">10I", data)
    if magic != 0xd00dfeed or total > len(data) or os+ss > total or ot+ts > total:
        raise ValueError("Invalid FDT")
    strings = data[ot:ot+ts]
    stack, nodes = [], {}
    pos, end = os, os+ss
    while pos < end:
        token, = struct.unpack_from(">I", data, pos)
        pos += 4
        if token == 1:
            stop = data.index(0, pos, end)
            stack.append(data[pos:stop].decode())
            pos = (stop+4) & ~3
            nodes["/".join(stack) or "/"] = {}
        elif token == 2:
            stack.pop()
        elif token == 3:
            size, nameoff = struct.unpack_from(">II", data, pos)
            pos += 8
            if pos+size > end or nameoff >= len(strings):
                raise ValueError("Invalid FDT property")
            name = strings[nameoff:strings.index(0, nameoff)].decode()
            nodes["/".join(stack) or "/"][name] = data[pos:pos+size]
            pos = (pos+size+3) & ~3
        elif token == 4:
            continue
        elif token == 9:
            break
        else:
            raise ValueError("Unknown FDT token")
    def text(raw):
        return raw.rstrip(b"\0").decode(errors="replace").split("\0")
    devices = []
    for name, props in nodes.items():
        if "compatible" not in props:
            continue
        devices.append({
            "node": name, "compatible": text(props["compatible"]),
            "status": text(props.get("status", b"okay"))[0],
            "reg_cells": [hex(v[0]) for v in struct.iter_unpack(">I", props.get("reg", b""))],
            "interrupt_cells": [hex(v[0]) for v in struct.iter_unpack(">I", props.get("interrupts", b""))],
            "reg_note": "Bus-relative cells; address/size widths and ranges are not resolved here.",
            "emulation_verified": False,
        })
    return {
        "path": str(path.resolve()), "sha256": hashlib.sha256(data).hexdigest(),
        "model": text(nodes["/"]["model"])[0], "nodes": len(nodes),
        "compatible_nodes": len(devices), "devices": devices,
        "note": "A device tree describes devices; it does not implement their register behavior.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--firmware", type=Path, action="append", required=True)
    parser.add_argument("--crypto-tool", type=Path, required=True)
    parser.add_argument("--dtb", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("Use a new evidence file")
    spec = importlib.util.spec_from_file_location("imah_audit_dependency", args.crypto_tool)
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    result = {
        "physical_camera_access": False,
        "main_kernel_booted": False, "original_userspace_booted": False,
        "full_hardware_boot_verified": False,
        "crypto_tool": str(args.crypto_tool.resolve()),
        "crypto_tool_sha256": hashlib.sha256(args.crypto_tool.read_bytes()).hexdigest(),
        "crypto_reference": "https://github.com/o-gs/dji-firmware-tools/blob/master/dji_imah_fwsig.py",
        "firmware": [audit_image(path, tool) for path in args.firmware],
        "hardware": hardware_inventory(args.dtb),
    }
    result["next_required_evidence"] = [
        "Verified plaintext normal-system kernel and matching main initramfs",
        "Eagle2 register/interrupt/DMA behavior for devices required by that kernel",
        "Original main init/service and display startup without SDK-kernel substitution",
    ]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(args.output)
    for item in result["firmware"]:
        print(item["path"], "verified_main_kernel_candidate=", item["verified_main_kernel_candidate"])
    print("compatible_nodes=", result["hardware"]["compatible_nodes"])


if __name__ == "__main__":
    main()
