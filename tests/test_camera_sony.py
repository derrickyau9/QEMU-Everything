"""Author-generated Sony fixtures: corruption, limits, archive safety and cancellation."""
import io
from pathlib import Path
import struct
import tarfile
import zlib

import pytest
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from devkit.camera_formats import sony
from devkit.camera_formats._sony_vendor import constants
from devkit.core import Task, Cancelled


def firmware_tar(entries):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as archive:
        for name, value, kind in entries:
            info = tarfile.TarInfo(name)
            if kind == "symlink":
                info.type, info.linkname = tarfile.SYMTYPE, "/etc/passwd"
                archive.addfile(info)
            else:
                info.size = len(value)
                archive.addfile(info, io.BytesIO(value))
    return stream.getvalue()


def dat_from_encrypted(encrypted):
    data = bytearray(sony.MAGIC)
    for name, payload in ((b"DATV", b"\x01\x00\x00\x00"), (b"PROV", b"\x01\x00\x00\x00"),
                          (b"UDID", b"\0\0\0\0"), (b"FDAT", encrypted)):
        data += struct.pack(">I4s", len(payload), name) + payload
    data += struct.pack(">I4sI", 4, b"DEND", zlib.crc32(data))
    return bytes(data)


def make_firmware(entries=None):
    archive = firmware_tar(entries or [("./update/dev/nflasha0", b"kernel test", "file")])
    fs = b"updater filesystem"
    header = bytearray(512)
    header[:8], header[12:16], header[16], header[20] = b"UDTRFIRM", b"0100", ord("U"), ord("N")
    header[32:34] = bytes([2, 6])
    struct.pack_into("<II", header, 36, 0x01030081, 0)
    struct.pack_into("<III", header, 48, 512 + len(fs), len(archive), 1)
    header[64] = ord("U")
    struct.pack_into("<II", header, 68, 512, len(fs))
    struct.pack_into("<I", header, 8, zlib.crc32(header[12:]))
    logical = bytes(header) + fs + archive
    blocks = []
    for position in range(0, len(logical), 1020):
        data = logical[position:position + 1020]
        word = len(data) | (0x8000 if position + 1020 >= len(logical) else 0)
        rest = struct.pack("<H", word) + data + b"\xff" * (1020 - len(data))
        blocks.append(struct.pack("<H", sum(struct.unpack("<511H", rest)) & 0xFFFF) + rest)
    plaintext = b"".join(blocks)
    iv = bytes(range(16))
    common = Cipher(algorithms.AES(constants.key_aes), modes.ECB()).encryptor()
    cbc = Cipher(algorithms.AES(constants.key_cxd90057_k8), modes.CBC(iv)).encryptor()
    encrypted = common.update(plaintext[:512]) + cbc.update(plaintext[512:]) + iv + b"\0" * 256
    return dat_from_encrypted(encrypted), encrypted


def save(tmp_path, data):
    path = tmp_path / "BODYDATA.DAT"
    path.write_bytes(data)
    return path


def test_identify_and_extract_valid_encrypted_fixture(tmp_path):
    source = save(tmp_path, make_firmware()[0])
    info = sony.identify(source, Task(lambda _: None))
    assert info["extractable"] and info["model"] == "ILCE-7M4"
    output = tmp_path / "out"
    output.mkdir()
    result = sony.extract(source, output, Task(lambda _: None))
    assert "CXD90057_k8" in result["method"]
    assert (output / "payloads/update/dev/nflasha0").read_bytes() == b"kernel test"
    assert (output / "filesystem-00.img").read_bytes() == b"updater filesystem"


def test_bad_outer_crc_blocks_extraction(tmp_path):
    data = bytearray(make_firmware()[0]); data[-1] ^= 1
    source = save(tmp_path, data)
    assert not sony.identify(source, Task())["extractable"]
    output = tmp_path / "out"; output.mkdir()
    with pytest.raises(ValueError, match="CRC32"):
        sony.extract(source, output, Task())
    assert list(output.iterdir()) == []


def test_corrupt_later_encrypted_block_rejected(tmp_path):
    _, encrypted = make_firmware()
    damaged = bytearray(encrypted); damaged[2048] ^= 1
    source = save(tmp_path, dat_from_encrypted(damaged))
    assert sony.identify(source, Task())["extractable"]
    output = tmp_path / "out"; output.mkdir()
    with pytest.raises(ValueError, match="checksum"):
        sony.extract(source, output, Task(lambda _: None))


@pytest.mark.parametrize("name", ["../../escape", "/absolute", "update/C:/escape", "update/NUL", "update/bad:ads"])
def test_unsafe_archive_paths_rejected(tmp_path, name):
    source = save(tmp_path, make_firmware([(name, b"x", "file")])[0])
    output = tmp_path / "out"; output.mkdir()
    with pytest.raises(ValueError, match="Unsafe archive path|escapes"):
        sony.extract(source, output, Task(lambda _: None))
    assert not (tmp_path.parent / "escape").exists()


def test_links_not_created(tmp_path):
    source = save(tmp_path, make_firmware([("update/link", b"", "symlink"), ("update/real", b"a", "file")])[0])
    output = tmp_path / "out"; output.mkdir()
    sony.extract(source, output, Task(lambda _: None))
    assert not (output / "payloads/update/link").exists()
    assert (output / "payloads/update/real").read_bytes() == b"a"


def test_windows_case_collision_rejected(tmp_path):
    source = save(tmp_path, make_firmware([("update/foo", b"a", "file"), ("update/FOO", b"b", "file")])[0])
    output = tmp_path / "out"; output.mkdir()
    with pytest.raises(ValueError, match="Duplicate"):
        sony.extract(source, output, Task(lambda _: None))


def test_xr2_geometry_is_reported_unsupported(tmp_path):
    source = save(tmp_path, dat_from_encrypted(b"\0" * (1024 + 0x190)))
    info = sony.identify(source, Task())
    assert not info["extractable"] and "geometry" in info["blockers"][0]


def test_cancel_does_not_write_output(tmp_path):
    source = save(tmp_path, make_firmware()[0])
    output = tmp_path / "out"; output.mkdir()
    task = Task(); task.cancelled.set()
    with pytest.raises(Cancelled):
        sony.extract(source, output, task)
    assert list(output.iterdir()) == []


def test_output_budget_enforced(tmp_path, monkeypatch):
    source = save(tmp_path, make_firmware()[0])
    # Input fits but duplicated decrypted and extracted payloads exceed the limit.
    monkeypatch.setattr(sony, "MAX_BYTES", source.stat().st_size + 65536)
    output = tmp_path / "out"; output.mkdir()
    with pytest.raises(ValueError, match="4 GiB"):
        sony.extract(source, output, Task(lambda _: None))
