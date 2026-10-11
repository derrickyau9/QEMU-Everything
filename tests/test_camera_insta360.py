"""Author-generated X3 fixtures validate real nested hashes, boundaries and cancellation."""
import hashlib
from pathlib import Path
import struct
import zlib

import pytest

from devkit.camera_formats import insta360
from devkit.core import Task, Cancelled


def make_firmware(product=b"onex3"):
    body = bytearray(0x230)
    struct.pack_into("<I", body, 32, 0x8732DFE6)
    running = 0
    for index in range(6):
        data = bytes([index]) * (16 + index)
        header = struct.pack("<7I", zlib.crc32(data), 0x01000000, 0, len(data), 0, 0, 0xA324EB90) + bytes(228)
        running = zlib.crc32(header, running)
        running = zlib.crc32(data, running)
        struct.pack_into("<II", body, 48 + index * 8, len(header) + len(data) if index < 5 else 0, running ^ 0xFFFFFFFF)
        body += header + data
    struct.pack_into("<I", body, 36, running)
    trailer = struct.pack("<I32s32s16s8sQ", len(body), product, b"v1.test", hashlib.md5(body).digest(), b"WFNI3XNO", 1)
    return bytes(body) + trailer + hashlib.md5(body + trailer).digest()


def rehash(data):
    result = bytearray(data)
    result[-48:-32] = hashlib.md5(result[:-116]).digest()
    result[-16:] = hashlib.md5(result[:-16]).digest()
    return result


def save(tmp_path, data):
    source = tmp_path / "Insta360X3FW.bin"
    source.write_bytes(data)
    return source


def test_valid_x3_extract_fixed_artifact_names(tmp_path):
    source = save(tmp_path, make_firmware())
    info = insta360.identify(source, Task())
    assert info["extractable"] and info["model"] == "X3"
    output = tmp_path / "out"; output.mkdir()
    result = insta360.extract(source, output, Task())
    assert "no decryption" in result["notes"][0]
    for index, name in enumerate(insta360.ARTIFACT_NAMES):
        assert (output / name).read_bytes() == bytes([index]) * (16 + index)


def test_bad_md5_rejected_before_artifacts(tmp_path):
    data = bytearray(make_firmware()); data[0x331] ^= 1
    source = save(tmp_path, data)
    output = tmp_path / "out"; output.mkdir()
    with pytest.raises(ValueError, match="MD5"):
        insta360.extract(source, output, Task())
    assert list(output.iterdir()) == []


def test_segment_crc_rejected_even_with_valid_md5(tmp_path):
    data = bytearray(make_firmware()); data[0x331] ^= 1
    source = save(tmp_path, rehash(data))
    output = tmp_path / "out"; output.mkdir()
    with pytest.raises(ValueError, match="CRC32"):
        insta360.extract(source, output, Task())


def test_segment_bounds_checked_before_payload(tmp_path):
    data = bytearray(make_firmware())
    struct.pack_into("<I", data, 0x230 + 12, 0xFFFFFFFF)
    struct.pack_into("<I", data, 48, 255)
    source = save(tmp_path, rehash(data))
    info = insta360.identify(source, Task())
    assert not info["extractable"] and "size mismatch" in info["blockers"][0]


def test_not_generalized_to_other_ambarella_models(tmp_path):
    source = save(tmp_path, make_firmware(b"onex4"))
    info = insta360.identify(source, Task())
    assert not info["extractable"] and "X3 only" in info["blockers"][0]


def test_unknown_encryption_no_guessing(tmp_path):
    source = tmp_path / "LunaUltraFW.enc"; source.write_bytes(b"unknown encrypted payload")
    info = insta360.identify(source, Task())
    assert info["id"] == "insta360-opaque" and not info["extractable"]


def test_cancellation(tmp_path):
    source = save(tmp_path, make_firmware())
    output = tmp_path / "out"; output.mkdir()
    task = Task(); task.cancelled.set()
    with pytest.raises(Cancelled):
        insta360.extract(source, output, task)
    assert list(output.iterdir()) == []
